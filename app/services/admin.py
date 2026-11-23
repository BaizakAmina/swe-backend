"""Platform-admin moderation and reports (SRS §4.12).

Suspending an **event** hides it from the catalog and stops sales: ``ticket_types.is_on_sale``
is false while ``event.suspended_at`` is set, and the orders service must reject new orders.
Suspending a **user** blocks login and revokes every issued token immediately.
"""

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, NotFoundError
from app.models import Event, Order, OrganizerProfile, Payment, User, UserRole
from app.models.enums import EventStatus, PaymentPurpose, Role, UserStatus
from app.schemas.admin import AdminEventRead, AdminUserRead, PlatformSettingsRead
from app.schemas.common import Page, Pagination
from app.schemas.orders import OrderRead, PaymentRead
from app.services import audit, platform_settings, views
from app.services.audit import AuditAction
from app.services.auth import revoke_all_sessions


def _now() -> datetime:
    return datetime.now(UTC)


def _count(db: Session, stmt: Select) -> int:  # type: ignore[type-arg]
    return int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)


# ------------------------------------------------------------------ users


def user_to_read(u: User) -> AdminUserRead:
    return AdminUserRead(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        status=u.status,
        roles=sorted(u.role_names),
        email_verified=u.email_verified_at is not None,
        suspended_reason=u.suspended_reason,
        created_at=u.created_at,
    )


def list_users(
    db: Session,
    page: Pagination,
    q: str | None = None,
    role: Role | None = None,
    user_status: UserStatus | None = None,
) -> Page[AdminUserRead]:
    stmt = select(User)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.email.ilike(pattern), User.full_name.ilike(pattern)))
    if role:
        stmt = stmt.where(User.id.in_(select(UserRole.user_id).where(UserRole.role == role)))
    if user_status:
        stmt = stmt.where(User.status == user_status)
    total = _count(db, stmt)
    users = db.scalars(stmt.order_by(User.created_at.desc()).limit(page.limit).offset(page.offset))
    return Page(
        items=[user_to_read(u) for u in users], total=total, limit=page.limit, offset=page.offset
    )


def get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("user")
    return user


def suspend_user(db: Session, admin: User, user_id: uuid.UUID, reason: str) -> AdminUserRead:
    user = get_user(db, user_id)
    if user.id == admin.id:
        raise ConflictError("cannot_suspend_self", "You cannot suspend your own account")
    if user.status != UserStatus.SUSPENDED:
        user.status = UserStatus.SUSPENDED
        user.suspended_reason = reason
        revoke_all_sessions(db, user)
        audit.log(
            db,
            actor_id=admin.id,
            action=AuditAction.USER_SUSPENDED,
            entity_type="user",
            entity_id=user.id,
            description=f"User {user.email} suspended: {reason}",
            metadata={"reason": reason},
        )
        db.commit()
    return user_to_read(user)


def unsuspend_user(db: Session, admin: User, user_id: uuid.UUID) -> AdminUserRead:
    user = get_user(db, user_id)
    if user.status == UserStatus.SUSPENDED:
        user.status = UserStatus.ACTIVE
        user.suspended_reason = None
        audit.log(
            db,
            actor_id=admin.id,
            action=AuditAction.USER_UNSUSPENDED,
            entity_type="user",
            entity_id=user.id,
            description=f"User {user.email} reactivated",
        )
        db.commit()
    return user_to_read(user)


# ------------------------------------------------------------------ events


def _event_rows(db: Session, stmt: Select[tuple[Event]]) -> list[AdminEventRead]:
    events = db.scalars(stmt.options(selectinload(Event.organizer))).all()
    return [
        AdminEventRead(
            id=e.id,
            title=e.title,
            organizer_id=e.organizer_id,
            organizer_name=e.organizer.display_name,
            status=e.status,
            starts_at=e.starts_at,
            paid_sales_active=e.paid_sales_active,
            suspended_at=e.suspended_at,
            suspended_reason=e.suspended_reason,
        )
        for e in events
    ]


def list_events(
    db: Session,
    page: Pagination,
    q: str | None = None,
    event_status: EventStatus | None = None,
    organizer_id: uuid.UUID | None = None,
    suspended: bool | None = None,
) -> Page[AdminEventRead]:
    stmt = select(Event)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Event.title.ilike(pattern),
                Event.organizer_id.in_(
                    select(OrganizerProfile.id).where(OrganizerProfile.display_name.ilike(pattern))
                ),
            )
        )
    if event_status:
        stmt = stmt.where(Event.status == event_status)
    if organizer_id:
        stmt = stmt.where(Event.organizer_id == organizer_id)
    if suspended is True:
        stmt = stmt.where(Event.suspended_at.is_not(None))
    elif suspended is False:
        stmt = stmt.where(Event.suspended_at.is_(None))
    total = _count(db, stmt)
    items = _event_rows(
        db, stmt.order_by(Event.starts_at.desc()).limit(page.limit).offset(page.offset)
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


def _get_event(db: Session, event_id: uuid.UUID) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise NotFoundError("event")
    return event


def suspend_event(db: Session, admin: User, event_id: uuid.UUID, reason: str) -> AdminEventRead:
    event = _get_event(db, event_id)
    if event.suspended_at is None:
        event.suspended_at = _now()
        event.suspended_reason = reason
        event.revision += 1
        audit.log(
            db,
            actor_id=admin.id,
            action=AuditAction.EVENT_SUSPENDED,
            entity_type="event",
            entity_id=event.id,
            event_id=event.id,
            description=f"Suspended by platform admin: {reason}",
            metadata={"reason": reason},
        )
        db.commit()
    return _event_rows(db, select(Event).where(Event.id == event.id))[0]


def unsuspend_event(db: Session, admin: User, event_id: uuid.UUID) -> AdminEventRead:
    event = _get_event(db, event_id)
    if event.suspended_at is not None:
        event.suspended_at = None
        event.suspended_reason = None
        event.revision += 1
        audit.log(
            db,
            actor_id=admin.id,
            action=AuditAction.EVENT_UNSUSPENDED,
            entity_type="event",
            entity_id=event.id,
            event_id=event.id,
            description="Suspension lifted by platform admin",
        )
        db.commit()
    return _event_rows(db, select(Event).where(Event.id == event.id))[0]


# ------------------------------------------------------------------ orders & payments


def list_orders(
    db: Session, page: Pagination, event_id: uuid.UUID | None = None
) -> Page[OrderRead]:
    stmt = select(Order)
    if event_id:
        stmt = stmt.where(Order.event_id == event_id)
    return views.page_orders(db, stmt, page)


def list_payments(
    db: Session, page: Pagination, purpose: PaymentPurpose | None = None
) -> Page[PaymentRead]:
    stmt = select(Payment)
    if purpose:
        stmt = stmt.where(Payment.purpose == purpose)
    total = _count(db, stmt)
    rows = db.scalars(
        stmt.order_by(Payment.created_at.desc()).limit(page.limit).offset(page.offset)
    )
    return Page(
        items=[PaymentRead.model_validate(p) for p in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


# ------------------------------------------------------------------ settings


def get_settings(db: Session) -> PlatformSettingsRead:
    return PlatformSettingsRead(activation_fee_minor=platform_settings.activation_fee_minor(db))


def update_settings(db: Session, admin: User, activation_fee_minor: int) -> PlatformSettingsRead:
    old = platform_settings.activation_fee_minor(db)
    platform_settings.set_value(db, platform_settings.ACTIVATION_FEE, activation_fee_minor)
    audit.log(
        db,
        actor_id=admin.id,
        action=AuditAction.PLATFORM_SETTING_UPDATED,
        entity_type="platform_setting",
        entity_id=uuid.uuid5(uuid.NAMESPACE_URL, platform_settings.ACTIVATION_FEE),
        description=f"Activation fee {old} → {activation_fee_minor}",
        metadata={"key": platform_settings.ACTIVATION_FEE, "old": old, "new": activation_fee_minor},
    )
    db.commit()
    return get_settings(db)


# ------------------------------------------------------------------ CSV reports

Report = Literal["users", "events", "orders", "payments"]


def report_csv(db: Session, report: Report) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    if report == "users":
        w.writerow(["id", "email", "full_name", "roles", "status", "email_verified", "created_at"])
        for u in db.scalars(select(User).order_by(User.created_at)):
            w.writerow(
                [
                    u.id,
                    u.email,
                    u.full_name,
                    " ".join(sorted(r.value for r in u.role_names)),
                    u.status.value,
                    u.email_verified_at is not None,
                    u.created_at.isoformat(),
                ]
            )
    elif report == "events":
        w.writerow(["id", "title", "organizer", "status", "starts_at", "capacity", "suspended"])
        for e in db.scalars(
            select(Event).options(selectinload(Event.organizer)).order_by(Event.starts_at)
        ):
            w.writerow(
                [
                    e.id,
                    e.title,
                    e.organizer.display_name,
                    e.status.value,
                    e.starts_at.isoformat(),
                    e.capacity,
                    e.suspended_at is not None,
                ]
            )
    elif report == "orders":
        w.writerow(
            ["id", "event_id", "buyer_user_id", "status", "total_minor", "currency", "paid_at"]
        )
        for o in db.scalars(select(Order).order_by(Order.created_at)):
            w.writerow(
                [
                    o.id,
                    o.event_id,
                    o.buyer_user_id,
                    o.status.value,
                    o.total_minor,
                    o.currency,
                    o.paid_at.isoformat() if o.paid_at else "",
                ]
            )
    else:
        w.writerow(
            ["id", "purpose", "order_id", "event_id", "amount_minor", "status", "is_simulated"]
        )
        for p in db.scalars(select(Payment).order_by(Payment.created_at)):
            w.writerow(
                [
                    p.id,
                    p.purpose.value,
                    p.order_id or "",
                    p.event_id or "",
                    p.amount_minor,
                    p.status.value,
                    p.is_simulated,
                ]
            )
    return buf.getvalue()
