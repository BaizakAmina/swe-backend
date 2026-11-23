"""Events: CRUD, publish/unpublish/cancel/duplicate, lifecycle, catalog (SRS §4.2, §4.16).

Lifecycle (upcoming / active / completed / cancelled) is *derived* from ``status`` and dates:

- cancelled  — ``status = cancelled``
- upcoming   — ``now < starts_at``
- active     — ``starts_at <= now < ends_at``
- completed  — ``ends_at <= now``
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import Event, TicketType, User, Venue
from app.models.enums import EventLifecycle, EventStatus, EventVisibility, SeatingMode
from app.schemas.common import Page, Pagination
from app.schemas.events import (
    EventCreate,
    EventOrganizer,
    EventRead,
    EventSummary,
    EventUpdate,
)
from app.services import audit, calendar, notifications, permissions
from app.services.audit import AuditAction
from app.services.organizers import get_profile


def now_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- lifecycle


def compute_lifecycle(event: Event, now: datetime | None = None) -> EventLifecycle:
    now = now or now_utc()
    if event.status == EventStatus.CANCELLED:
        return EventLifecycle.CANCELLED
    if now < event.starts_at:
        return EventLifecycle.UPCOMING
    if now < event.ends_at:
        return EventLifecycle.ACTIVE
    return EventLifecycle.COMPLETED


def lifecycle_clause(lifecycle: EventLifecycle, now: datetime | None = None) -> ColumnElement[bool]:
    """SQL condition equivalent to :func:`compute_lifecycle` (filtering happens in the DB)."""
    now = now or now_utc()
    not_cancelled = Event.status != EventStatus.CANCELLED
    match lifecycle:
        case EventLifecycle.CANCELLED:
            return Event.status == EventStatus.CANCELLED
        case EventLifecycle.UPCOMING:
            return and_(not_cancelled, Event.starts_at > now)
        case EventLifecycle.ACTIVE:
            return and_(not_cancelled, Event.starts_at <= now, Event.ends_at > now)
        case EventLifecycle.COMPLETED:
            return and_(not_cancelled, Event.ends_at <= now)


# ---------------------------------------------------------------- serialization


def to_read(event: Event) -> EventRead:
    return EventRead.model_validate(
        {
            **{c.key: getattr(event, c.key) for c in Event.__table__.columns},
            "organizer": EventOrganizer.model_validate(event.organizer),
            "lifecycle": compute_lifecycle(event),
            "is_suspended": event.suspended_at is not None,
            "calendar_links": calendar.links(event),
        }
    )


def _min_price_subquery() -> ColumnElement[int | None]:
    return (
        select(func.min(TicketType.price_minor))
        .where(TicketType.event_id == Event.id, TicketType.is_hidden.is_(False))
        .correlate(Event)
        .scalar_subquery()
    )


def _summaries(db: Session, stmt: Select[tuple[Event]], page: Pagination) -> Page[EventSummary]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(
        stmt.add_columns(_min_price_subquery().label("min_price"))
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    items = [
        EventSummary.model_validate(
            {
                **{c.key: getattr(ev, c.key) for c in Event.__table__.columns},
                "lifecycle": compute_lifecycle(ev),
                "min_price_minor": min_price,
            }
        )
        for ev, min_price in rows
    ]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


# ---------------------------------------------------------------- helpers


def _log(
    db: Session, user: User, action: AuditAction, event: Event, text: str, **meta: object
) -> None:
    audit.log(
        db,
        actor_id=user.id,
        action=action,
        entity_type="event",
        entity_id=event.id,
        event_id=event.id,
        description=text,
        metadata=dict(meta),
    )


def _inventory(db: Session, event_id: uuid.UUID) -> tuple[int, int, int]:
    """(sum quantity_total, sum sold + reserved, number of ticket types)."""
    total, committed, count = db.execute(
        select(
            func.coalesce(func.sum(TicketType.quantity_total), 0),
            func.coalesce(func.sum(TicketType.quantity_sold + TicketType.quantity_reserved), 0),
            func.count(TicketType.id),
        ).where(TicketType.event_id == event_id)
    ).one()
    return int(total), int(committed), int(count)


def has_sales(db: Session, event: Event) -> bool:
    return _inventory(db, event.id)[1] > 0


def _ensure_not_cancelled(event: Event) -> None:
    if event.status == EventStatus.CANCELLED:
        raise ConflictError("event_cancelled", "Cancelled events cannot be changed")


def _validate_venue(db: Session, venue_id: uuid.UUID | None, seating: SeatingMode) -> None:
    if venue_id is not None and db.get(Venue, venue_id) is None:
        raise NotFoundError("venue")
    if seating == SeatingMode.ASSIGNED and venue_id is None:
        raise AppError(
            "venue_required",
            "Assigned seating needs a venue layout",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


def _unprocessable(code: str, message: str) -> AppError:
    return AppError(code, message, status.HTTP_422_UNPROCESSABLE_ENTITY)


def load(db: Session, event_id: uuid.UUID) -> Event:
    event = db.scalar(
        select(Event).where(Event.id == event_id).options(selectinload(Event.organizer))
    )
    if event is None:
        raise NotFoundError("event")
    return event


# ---------------------------------------------------------------- organizer actions


def create_event(db: Session, user: User, data: EventCreate) -> Event:
    if user.email_verified_at is None:
        raise AppError("email_not_verified", "Please verify your email first", 403)
    profile = get_profile(db, user)
    if profile is None:
        raise _unprocessable(
            "organizer_profile_required", "Create your organizer profile before creating events"
        )
    _validate_venue(db, data.venue_id, data.seating_mode)

    event = Event(organizer_id=profile.id, status=EventStatus.DRAFT, **data.model_dump())
    db.add(event)
    db.flush()
    _log(db, user, AuditAction.EVENT_CREATED, event, f"Created event “{event.title}”")
    db.commit()
    return load(db, event.id)


def update_event(db: Session, user: User, event: Event, data: EventUpdate) -> Event:
    _ensure_not_cancelled(event)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return event

    starts = changes.get("starts_at", event.starts_at)
    ends = changes.get("ends_at", event.ends_at)
    if ends <= starts:
        raise _unprocessable("invalid_dates", "ends_at must be after starts_at")

    sold_any = has_sales(db, event)
    if "seating_mode" in changes and changes["seating_mode"] != event.seating_mode and sold_any:
        raise ConflictError("event_has_sales", "Seating mode cannot change after sales started")
    if "capacity" in changes:
        types_total, committed, _ = _inventory(db, event.id)
        if changes["capacity"] < committed:
            raise ConflictError(
                "capacity_below_sold", f"{committed} tickets are already sold or reserved"
            )
        if changes["capacity"] < types_total:
            raise _unprocessable(
                "capacity_below_ticket_types",
                f"Ticket types already allocate {types_total} places; lower them first",
            )
    _validate_venue(
        db,
        changes.get("venue_id", event.venue_id),
        changes.get("seating_mode", event.seating_mode),
    )

    for field, value in changes.items():
        setattr(event, field, value)
    event.revision += 1
    _log(
        db,
        user,
        AuditAction.EVENT_UPDATED,
        event,
        f"Updated: {', '.join(sorted(changes))}",
        fields=sorted(changes),
    )
    db.commit()
    return event


def publish(db: Session, user: User, event: Event) -> Event:
    if event.status == EventStatus.PUBLISHED:
        return event
    _ensure_not_cancelled(event)
    if event.suspended_at is not None:
        raise ConflictError("event_suspended", "Suspended events cannot be published")
    _, _, ticket_types = _inventory(db, event.id)
    problems = []
    if ticket_types == 0:
        problems.append("add at least one ticket type")
    if event.starts_at <= now_utc():
        problems.append("starts_at must be in the future")
    if problems:
        raise AppError(
            "cannot_publish",
            "Event is not ready to publish: " + "; ".join(problems),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=problems,
        )
    event.status = EventStatus.PUBLISHED
    event.published_at = now_utc()
    event.revision += 1
    _log(db, user, AuditAction.EVENT_PUBLISHED, event, f"Published “{event.title}”")
    db.commit()
    return event


def unpublish(db: Session, user: User, event: Event) -> Event:
    if event.status != EventStatus.PUBLISHED:
        raise ConflictError("event_not_published", "Only published events can be unpublished")
    if has_sales(db, event):
        raise ConflictError(
            "event_has_sales", "Event has orders — cancel it instead of unpublishing"
        )
    event.status = EventStatus.DRAFT
    event.revision += 1
    _log(db, user, AuditAction.EVENT_UNPUBLISHED, event, f"Unpublished “{event.title}”")
    db.commit()
    return event


# Called after an event is cancelled. Refunds/cancellations of orders are added by BE-24.
on_event_cancelled_hooks: list[Callable[[Session, Event, User], None]] = []


def cancel(db: Session, user: User, event: Event, reason: str | None = None) -> Event:
    if event.status == EventStatus.CANCELLED:
        return event
    event.status = EventStatus.CANCELLED
    event.cancelled_at = now_utc()
    event.revision += 1
    _log(
        db,
        user,
        AuditAction.EVENT_CANCELLED,
        event,
        f"Cancelled “{event.title}”" + (f": {reason}" if reason else ""),
        reason=reason,
    )
    for hook in on_event_cancelled_hooks:
        hook(db, event, user)
    db.commit()
    notifications.send_event_cancelled(db, event)
    return event


def duplicate(db: Session, user: User, event: Event) -> Event:
    skip = {
        "id",
        "created_at",
        "updated_at",
        "status",
        "paid_sales_active",
        "published_at",
        "cancelled_at",
        "suspended_at",
        "suspended_reason",
        "revision",
    }
    fields = {c.key: getattr(event, c.key) for c in Event.__table__.columns if c.key not in skip}
    fields["title"] = f"{event.title} (copy)"[:200]
    copy = Event(**fields, status=EventStatus.DRAFT)
    db.add(copy)
    db.flush()

    tt_skip = {"id", "event_id", "created_at", "updated_at", "quantity_sold", "quantity_reserved"}
    for tt in db.scalars(select(TicketType).where(TicketType.event_id == event.id)):
        db.add(
            TicketType(
                event_id=copy.id,
                **{
                    c.key: getattr(tt, c.key)
                    for c in TicketType.__table__.columns
                    if c.key not in tt_skip
                },
            )
        )
    _log(
        db,
        user,
        AuditAction.EVENT_DUPLICATED,
        copy,
        f"Duplicated from “{event.title}”",
        source_event_id=str(event.id),
    )
    db.commit()
    return load(db, copy.id)


def list_organizer_events(
    db: Session,
    user: User,
    page: Pagination,
    lifecycle: EventLifecycle | None = None,
    event_status: EventStatus | None = None,
) -> Page[EventSummary]:
    profile = get_profile(db, user)
    if profile is None:
        return Page(items=[], total=0, limit=page.limit, offset=page.offset)
    stmt = select(Event).where(Event.organizer_id == profile.id)
    if lifecycle is not None:
        stmt = stmt.where(lifecycle_clause(lifecycle))
    if event_status is not None:
        stmt = stmt.where(Event.status == event_status)
    return _summaries(db, stmt.order_by(Event.starts_at.desc()), page)


# ---------------------------------------------------------------- public


def _publicly_visible() -> ColumnElement[bool]:
    return and_(Event.status == EventStatus.PUBLISHED, Event.suspended_at.is_(None))


def list_public(
    db: Session,
    page: Pagination,
    q: str | None = None,
    category: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_past: bool = False,
) -> Page[EventSummary]:
    stmt = select(Event).where(_publicly_visible(), Event.visibility == EventVisibility.PUBLIC)
    if not include_past:
        stmt = stmt.where(Event.ends_at > now_utc())
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Event.title.ilike(pattern),
                Event.description.ilike(pattern),
                Event.location_name.ilike(pattern),
            )
        )
    if category:
        stmt = stmt.where(Event.category == category)
    if date_from:
        stmt = stmt.where(Event.starts_at >= date_from)
    if date_to:
        stmt = stmt.where(Event.starts_at <= date_to)
    return _summaries(db, stmt.order_by(Event.starts_at.asc()), page)


def get_for_viewer(db: Session, event_id: uuid.UUID, user: User | None) -> Event:
    """Published public/unlisted events are visible to everyone; anything else only to managers."""
    event = load(db, event_id)
    public = (
        event.status in (EventStatus.PUBLISHED, EventStatus.CANCELLED)
        and event.suspended_at is None
        and event.visibility in (EventVisibility.PUBLIC, EventVisibility.UNLISTED)
    )
    if public or (user is not None and permissions.can_manage_event(db, user, event)):
        return event
    raise NotFoundError("event")
