"""Platform-admin moderation and reports (SRS §4.12).

Suspending an **event** hides it from the catalog and stops sales: ``ticket_types.is_on_sale``
is false while ``event.suspended_at`` is set, and the orders service must reject new orders.
Suspending a **user** blocks login and revokes every issued token immediately.
"""

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Event,
    Order,
    OrganizerProfile,
    Payment,
    PromoCode,
    PromoRedemption,
    PromotionalCampaign,
    SupportCase,
    User,
    UserRole,
)
from app.models.enums import (
    EventStatus,
    OrderStatus,
    PaymentPurpose,
    PaymentStatus,
    Role,
    SupportCategory,
    SupportStatus,
    UserStatus,
)
from app.schemas.admin import (
    AdminEventRead,
    AdminUserRead,
    PlatformSettingsRead,
    PlatformSettingsUpdate,
    PromoActivityRead,
    ReportedEventRead,
)
from app.schemas.common import Page, Pagination, Period
from app.schemas.orders import OrderRead, PaymentRead
from app.schemas.support import SupportCaseRead
from app.services import audit, platform_settings, support, views
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


def _in_period(column: Any, period: Period | None) -> list[ColumnElement[bool]]:
    if period is None:
        return []
    clauses = []
    if period.start is not None:
        clauses.append(column >= period.start)
    if period.end is not None:
        clauses.append(column < period.end)
    return clauses


def _as_uuid(q: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(q)
    except ValueError:
        return None


def list_orders(
    db: Session,
    page: Pagination,
    event_id: uuid.UUID | None = None,
    q: str | None = None,
    order_status: OrderStatus | None = None,
    period: Period | None = None,
) -> Page[OrderRead]:
    """``q``: order id, buyer email / name, event title or promo code."""
    stmt = select(Order).where(*_in_period(Order.created_at, period))
    if event_id:
        stmt = stmt.where(Order.event_id == event_id)
    if order_status:
        stmt = stmt.where(Order.status == order_status)
    if q and q.strip():
        q = q.strip()
        exact = _as_uuid(q)
        if exact is not None:
            stmt = stmt.where(Order.id == exact)
        else:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Order.buyer_user_id.in_(
                        select(User.id).where(
                            or_(User.email.ilike(pattern), User.full_name.ilike(pattern))
                        )
                    ),
                    Order.event_id.in_(select(Event.id).where(Event.title.ilike(pattern))),
                    Order.promo_code_id.in_(
                        select(PromoCode.id).where(PromoCode.code.ilike(pattern))
                    ),
                )
            )
    return views.page_orders(db, stmt, page)


def list_payments(
    db: Session,
    page: Pagination,
    purpose: PaymentPurpose | None = None,
    q: str | None = None,
    payment_status: PaymentStatus | None = None,
    period: Period | None = None,
) -> Page[PaymentRead]:
    """``q``: payment / order id, provider reference, payer email / name or event title."""
    stmt = select(Payment).where(*_in_period(Payment.created_at, period))
    if purpose:
        stmt = stmt.where(Payment.purpose == purpose)
    if payment_status:
        stmt = stmt.where(Payment.status == payment_status)
    if q and q.strip():
        q = q.strip()
        exact = _as_uuid(q)
        if exact is not None:
            stmt = stmt.where(or_(Payment.id == exact, Payment.order_id == exact))
        else:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Payment.provider_ref.ilike(pattern),
                    Payment.payer_user_id.in_(
                        select(User.id).where(
                            or_(User.email.ilike(pattern), User.full_name.ilike(pattern))
                        )
                    ),
                    Payment.event_id.in_(select(Event.id).where(Event.title.ilike(pattern))),
                )
            )
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


# ------------------------------------------------------------------ monitoring lists

_OPEN_CASE = SupportCase.status != SupportStatus.RESOLVED
DISPUTE_CATEGORIES = (SupportCategory.PAYMENT, SupportCategory.REFUND)


def reported_events(
    db: Session,
    page: Pagination,
    q: str | None = None,
    only_open: bool = True,
    period: Period | None = None,
) -> Page[ReportedEventRead]:
    """Events with support cases, most open cases first."""
    open_count = func.count().filter(_OPEN_CASE)
    stats = (
        select(
            SupportCase.event_id.label("event_id"),
            open_count.label("open_cases"),
            func.count().label("total_cases"),
            func.max(SupportCase.created_at).label("last_reported_at"),
        )
        .where(SupportCase.event_id.is_not(None), *_in_period(SupportCase.created_at, period))
        .group_by(SupportCase.event_id)
    )
    if only_open:
        stats = stats.having(open_count > 0)
    sub = stats.subquery()
    stmt = (
        select(Event, OrganizerProfile.display_name, sub.c.open_cases, sub.c.total_cases)
        .add_columns(sub.c.last_reported_at)
        .join(sub, sub.c.event_id == Event.id)
        .join(OrganizerProfile, OrganizerProfile.id == Event.organizer_id)
    )
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Event.title.ilike(pattern), OrganizerProfile.display_name.ilike(pattern))
        )
    total = _count(db, stmt)
    rows = db.execute(
        stmt.order_by(sub.c.open_cases.desc(), sub.c.last_reported_at.desc(), Event.id)
        .limit(page.limit)
        .offset(page.offset)
    ).tuples()
    items = [
        ReportedEventRead(
            event_id=ev.id,
            title=ev.title,
            organizer_id=ev.organizer_id,
            organizer_name=organizer_name,
            status=ev.status,
            suspended=ev.suspended_at is not None,
            open_cases=open_cases,
            total_cases=total_cases,
            last_reported_at=last,
        )
        for ev, organizer_name, open_cases, total_cases, last in rows
    ]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


def disputes(
    db: Session,
    page: Pagination,
    q: str | None = None,
    case_status: SupportStatus | None = None,
    period: Period | None = None,
) -> Page[SupportCaseRead]:
    """Payment and refund support cases (the dispute queue), newest activity first."""
    stmt = select(SupportCase).where(
        SupportCase.category.in_(DISPUTE_CATEGORIES), *_in_period(SupportCase.created_at, period)
    )
    if case_status:
        stmt = stmt.where(SupportCase.status == case_status)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                SupportCase.subject.ilike(pattern),
                SupportCase.opened_by_user_id.in_(
                    select(User.id).where(
                        or_(User.email.ilike(pattern), User.full_name.ilike(pattern))
                    )
                ),
                SupportCase.event_id.in_(select(Event.id).where(Event.title.ilike(pattern))),
            )
        )
    total = _count(db, stmt)
    rows = db.scalars(
        stmt.order_by(SupportCase.updated_at.desc(), SupportCase.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page(
        items=support.cases_to_read(db, rows), total=total, limit=page.limit, offset=page.offset
    )


def promo_activity(
    db: Session,
    page: Pagination,
    q: str | None = None,
    event_id: uuid.UUID | None = None,
    period: Period | None = None,
) -> Page[PromoActivityRead]:
    """Promo-code uses, newest first; ``released`` = the order expired or was cancelled."""
    stmt = (
        select(PromoRedemption, PromoCode, PromotionalCampaign, Event, Order, User.email)
        .join(PromoCode, PromoCode.id == PromoRedemption.promo_code_id)
        .join(PromotionalCampaign, PromotionalCampaign.id == PromoCode.campaign_id)
        .join(Event, Event.id == PromotionalCampaign.event_id)
        .join(Order, Order.id == PromoRedemption.order_id)
        .join(User, User.id == Order.buyer_user_id)
        .where(*_in_period(PromoRedemption.redeemed_at, period))
    )
    if event_id:
        stmt = stmt.where(Event.id == event_id)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                PromoCode.code.ilike(pattern),
                PromotionalCampaign.name.ilike(pattern),
                Event.title.ilike(pattern),
                User.email.ilike(pattern),
            )
        )
    total = _count(db, stmt)
    rows = db.execute(
        stmt.order_by(PromoRedemption.redeemed_at.desc(), PromoRedemption.id)
        .limit(page.limit)
        .offset(page.offset)
    ).tuples()
    items = [
        PromoActivityRead(
            id=r.id,
            redeemed_at=r.redeemed_at,
            released=r.released_at is not None,
            code=code.code,
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            event_id=ev.id,
            event_title=ev.title,
            order_id=order.id,
            order_status=order.status,
            buyer_email=email,
            discount_minor=r.discount_applied_minor,
        )
        for r, code, campaign, ev, order, email in rows
    ]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


# ------------------------------------------------------------------ settings


def get_settings(db: Session) -> PlatformSettingsRead:
    return PlatformSettingsRead(
        activation_fee_minor=platform_settings.activation_fee_minor(db),
        reservation_ttl_minutes=platform_settings.reservation_ttl_minutes(db),
        seat_hold_ttl_minutes=platform_settings.seat_hold_ttl_minutes(db),
        max_seat_holds_per_user=platform_settings.max_seat_holds_per_user(db),
        paid_terms_version=platform_settings.paid_terms_version(db),
    )


def update_settings(db: Session, admin: User, data: PlatformSettingsUpdate) -> PlatformSettingsRead:
    """Each changed key is stored and audited separately."""
    for key, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        old = platform_settings.current(db, key)
        if old == value:
            continue
        platform_settings.set_value(db, key, value)
        audit.log(
            db,
            actor_id=admin.id,
            action=AuditAction.PLATFORM_SETTING_UPDATED,
            entity_type="platform_setting",
            entity_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
            description=f"{key}: {old} → {value}",
            metadata={"key": key, "old": old, "new": value},
        )
    db.commit()
    return get_settings(db)


# ------------------------------------------------------------------ CSV reports

Report = Literal["users", "events", "orders", "payments"]


def report_csv(db: Session, report: Report, period: Period | None = None) -> str:
    """Users / orders / payments by ``created_at``, events by ``starts_at``."""
    buf = io.StringIO()
    w = csv.writer(buf)
    if report == "users":
        w.writerow(["id", "email", "full_name", "roles", "status", "email_verified", "created_at"])
        for u in db.scalars(
            select(User).where(*_in_period(User.created_at, period)).order_by(User.created_at)
        ):
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
            select(Event)
            .where(*_in_period(Event.starts_at, period))
            .options(selectinload(Event.organizer))
            .order_by(Event.starts_at)
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
        for o in db.scalars(
            select(Order).where(*_in_period(Order.created_at, period)).order_by(Order.created_at)
        ):
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
        for p in db.scalars(
            select(Payment)
            .where(*_in_period(Payment.created_at, period))
            .order_by(Payment.created_at)
        ):
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
