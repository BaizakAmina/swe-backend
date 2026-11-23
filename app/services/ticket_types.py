"""Ticket types (SRS §4.3).

``quantity_sold`` / ``quantity_reserved`` are changed **only** by the orders service under a row
lock. Here they are read, and rows are locked (``FOR UPDATE``) before ``quantity_total`` changes
so an edit cannot race a concurrent checkout.
"""

import uuid
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import Event, OrderItem, Ticket, TicketType, User
from app.models.enums import EventStatus, TicketKind, TicketStatus
from app.schemas.ticket_types import TicketTypeCreate, TicketTypeRead, TicketTypeUpdate
from app.services import audit, permissions
from app.services.audit import AuditAction


def _now() -> datetime:
    return datetime.now(UTC)


def is_on_sale(tt: TicketType, event: Event, now: datetime | None = None) -> bool:
    now = now or _now()
    return (
        not tt.is_hidden
        and event.status == EventStatus.PUBLISHED
        and event.suspended_at is None
        and now < event.ends_at
        and (tt.sales_start_at is None or tt.sales_start_at <= now)
        and (tt.sales_end_at is None or now < tt.sales_end_at)
    )


def is_sellable(tt: TicketType, event: Event) -> bool:
    return tt.kind == TicketKind.FREE or event.paid_sales_active


def _ticket_counts(db: Session, event_id: uuid.UUID) -> dict[uuid.UUID, dict[TicketStatus, int]]:
    rows = db.execute(
        select(OrderItem.ticket_type_id, Ticket.status, func.count())
        .join(Ticket, Ticket.order_item_id == OrderItem.id)
        .where(Ticket.event_id == event_id)
        .group_by(OrderItem.ticket_type_id, Ticket.status)
    ).all()
    counts: dict[uuid.UUID, dict[TicketStatus, int]] = {}
    for tt_id, st, n in rows:
        counts.setdefault(tt_id, {})[st] = n
    return counts


def to_read(
    tt: TicketType,
    event: Event,
    counts: dict[TicketStatus, int] | None = None,
    with_stats: bool = False,
) -> TicketTypeRead:
    data = {
        **{c.key: getattr(tt, c.key) for c in TicketType.__table__.columns},
        "quantity_available": tt.quantity_available,
        "on_sale": is_on_sale(tt, event),
        "sellable": is_sellable(tt, event),
    }
    if not with_stats:
        data.pop("quantity_sold")
        data.pop("quantity_reserved")
    else:
        counts = counts or {}
        data["quantity_refunded"] = counts.get(TicketStatus.REFUNDED, 0)
        data["quantity_checked_in"] = counts.get(TicketStatus.CHECKED_IN, 0)
    return TicketTypeRead.model_validate(data)


def list_for_event(db: Session, event: Event, user: User | None) -> list[TicketTypeRead]:
    manager = user is not None and permissions.can_manage_event(db, user, event)
    stmt = select(TicketType).where(TicketType.event_id == event.id).order_by(TicketType.created_at)
    if not manager:
        stmt = stmt.where(TicketType.is_hidden.is_(False))
    types = list(db.scalars(stmt))
    if not manager:
        return [to_read(tt, event) for tt in types]
    counts = _ticket_counts(db, event.id)
    return [to_read(tt, event, counts.get(tt.id), with_stats=True) for tt in types]


def load_managed(
    db: Session, user: User, ticket_type_id: uuid.UUID, *, lock: bool = False
) -> tuple[TicketType, Event]:
    tt = db.get(TicketType, ticket_type_id, with_for_update=lock)
    if tt is None:
        raise NotFoundError("ticket_type")
    event = permissions.get_event(db, tt.event_id)
    permissions.ensure_can_manage_event(db, user, event)
    return tt, event


def _allocated(db: Session, event_id: uuid.UUID, exclude: uuid.UUID | None = None) -> int:
    stmt = select(func.coalesce(func.sum(TicketType.quantity_total), 0)).where(
        TicketType.event_id == event_id
    )
    if exclude is not None:
        stmt = stmt.where(TicketType.id != exclude)
    return int(db.scalar(stmt) or 0)


def _check_capacity(db: Session, event: Event, new_total: int, exclude: uuid.UUID | None) -> None:
    allocated = _allocated(db, event.id, exclude) + new_total
    if allocated > event.capacity:
        raise AppError(
            "exceeds_capacity",
            f"Ticket types would allocate {allocated} places, event capacity is {event.capacity}",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


def _ensure_editable(event: Event) -> None:
    if event.status == EventStatus.CANCELLED:
        raise ConflictError("event_cancelled", "Cancelled events cannot be changed")


def _log(db: Session, user: User, action: AuditAction, tt: TicketType, text: str) -> None:
    audit.log(
        db,
        actor_id=user.id,
        action=action,
        entity_type="ticket_type",
        entity_id=tt.id,
        event_id=tt.event_id,
        description=text,
    )


def create(db: Session, user: User, event: Event, data: TicketTypeCreate) -> TicketTypeRead:
    _ensure_editable(event)
    _check_capacity(db, event, data.quantity_total, exclude=None)
    tt = TicketType(event_id=event.id, **data.model_dump())
    db.add(tt)
    db.flush()
    _log(db, user, AuditAction.TICKET_TYPE_CREATED, tt, f"Ticket type “{tt.name}” created")
    event.revision += 1
    db.commit()
    return to_read(tt, event, {}, with_stats=True)


def update(
    db: Session, user: User, ticket_type_id: uuid.UUID, data: TicketTypeUpdate
) -> TicketTypeRead:
    tt, event = load_managed(db, user, ticket_type_id, lock=True)
    _ensure_editable(event)
    changes = data.model_dump(exclude_unset=True)
    committed = tt.quantity_sold + tt.quantity_reserved

    if "quantity_total" in changes:
        if changes["quantity_total"] < committed:
            raise ConflictError(
                "quantity_below_sold", f"{committed} tickets are already sold or reserved"
            )
        _check_capacity(db, event, changes["quantity_total"], exclude=tt.id)

    if "price_minor" in changes and changes["price_minor"] != tt.price_minor:
        if committed > 0:
            raise ConflictError("price_locked", "Price cannot change after tickets were sold")
        price = changes["price_minor"]
        if (tt.kind == TicketKind.FREE) != (price == 0):
            raise AppError(
                "price_kind_mismatch",
                "Free tickets cost 0, paid tickets must cost more than 0",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

    start = changes.get("sales_start_at", tt.sales_start_at)
    end = changes.get("sales_end_at", tt.sales_end_at)
    if start and end and end <= start:
        raise AppError(
            "invalid_sales_window",
            "sales_end_at must be after sales_start_at",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    for field, value in changes.items():
        setattr(tt, field, value)
    if changes:
        _log(
            db,
            user,
            AuditAction.TICKET_TYPE_UPDATED,
            tt,
            f"Ticket type “{tt.name}” updated: {', '.join(sorted(changes))}",
        )
        event.revision += 1
    db.commit()
    return to_read(tt, event, _ticket_counts(db, event.id).get(tt.id), with_stats=True)


def set_hidden(db: Session, user: User, ticket_type_id: uuid.UUID, hidden: bool) -> TicketTypeRead:
    tt, event = load_managed(db, user, ticket_type_id)
    if tt.is_hidden != hidden:
        tt.is_hidden = hidden
        action = AuditAction.TICKET_TYPE_HIDDEN if hidden else AuditAction.TICKET_TYPE_UNHIDDEN
        _log(db, user, action, tt, f"Ticket type “{tt.name}” {'hidden' if hidden else 'shown'}")
        db.commit()
    return to_read(tt, event, _ticket_counts(db, event.id).get(tt.id), with_stats=True)


def delete(db: Session, user: User, ticket_type_id: uuid.UUID) -> None:
    tt, _ = load_managed(db, user, ticket_type_id, lock=True)
    used = db.scalar(select(exists().where(OrderItem.ticket_type_id == tt.id)))
    if used or tt.quantity_sold or tt.quantity_reserved:
        raise ConflictError(
            "ticket_type_has_orders", "Ticket type has orders — hide it instead of deleting"
        )
    _log(db, user, AuditAction.TICKET_TYPE_DELETED, tt, f"Ticket type “{tt.name}” deleted")
    db.delete(tt)
    db.commit()
