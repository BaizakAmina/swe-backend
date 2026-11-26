"""Read models for orders and tickets: buyer's account, ticket page, organizer attendee list.

``order_to_read`` / ``ticket_to_read`` are the single serializers for these entities — the
orders/ticketing services reuse them for their responses.
"""

import csv
import io
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ForbiddenError, NotFoundError
from app.core.urls import public_api_url
from app.models import Attendee, Event, Order, OrderItem, PromoCode, Ticket, TicketType, User
from app.models.enums import OrderStatus, TicketStatus
from app.schemas.common import Page, Pagination
from app.schemas.orders import OrderItemRead, OrderRead
from app.schemas.tickets import AttendeeRow, TicketEventInfo, TicketRead
from app.services import permissions


def _id_map(db: Session, stmt: Select[tuple[uuid.UUID, str]]) -> dict[uuid.UUID, str]:
    return {row[0]: row[1] for row in db.execute(stmt)}


def _type_names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return _id_map(db, select(TicketType.id, TicketType.name).where(TicketType.id.in_(ids)))


def _order_options() -> list[Any]:
    return [
        selectinload(Order.items).selectinload(OrderItem.attendee),
        selectinload(Order.items).selectinload(OrderItem.ticket),
    ]


def orders_to_read(db: Session, orders: Sequence[Order]) -> list[OrderRead]:
    type_ids = {i.ticket_type_id for o in orders for i in o.items}
    names = _type_names(db, type_ids)
    event_ids = {o.event_id for o in orders}
    titles = _id_map(db, select(Event.id, Event.title).where(Event.id.in_(event_ids)))
    buyers = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_({o.buyer_user_id for o in orders})))
    }
    promo_ids = {o.promo_code_id for o in orders if o.promo_code_id}
    codes = (
        _id_map(db, select(PromoCode.id, PromoCode.code).where(PromoCode.id.in_(promo_ids)))
        if promo_ids
        else {}
    )
    result = []
    for o in orders:
        items = [
            OrderItemRead(
                id=i.id,
                ticket_type_id=i.ticket_type_id,
                ticket_type_name=names.get(i.ticket_type_id, ""),
                unit_price_minor=i.unit_price_minor,
                section=i.section,
                row_label=i.row_label,
                seat_number=i.seat_number,
                attendee_name=i.attendee.full_name if i.attendee else None,
                attendee_email=i.attendee.email if i.attendee else None,
                ticket_id=i.ticket.id if i.ticket else None,
            )
            for i in sorted(o.items, key=lambda x: x.created_at)
        ]
        result.append(
            OrderRead(
                id=o.id,
                event_id=o.event_id,
                event_title=titles.get(o.event_id, ""),
                buyer_user_id=o.buyer_user_id,
                buyer_name=buyers[o.buyer_user_id].full_name if o.buyer_user_id in buyers else None,
                buyer_email=buyers[o.buyer_user_id].email if o.buyer_user_id in buyers else None,
                status=o.status,
                currency=o.currency,
                subtotal_minor=o.subtotal_minor,
                discount_minor=o.discount_minor,
                fees_minor=o.fees_minor,
                total_minor=o.total_minor,
                promo_code=codes.get(o.promo_code_id) if o.promo_code_id else None,
                reserved_until=o.reserved_until,
                paid_at=o.paid_at,
                created_at=o.created_at,
                items=items,
            )
        )
    return result


def order_to_read(db: Session, order: Order) -> OrderRead:
    return orders_to_read(db, [order])[0]


def page_orders(db: Session, stmt: Select[tuple[Order]], page: Pagination) -> Page[OrderRead]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    orders = db.scalars(
        stmt.options(*_order_options())
        .order_by(Order.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page(items=orders_to_read(db, orders), total=total, limit=page.limit, offset=page.offset)


def my_orders(
    db: Session, user: User, page: Pagination, order_status: OrderStatus | None = None
) -> Page[OrderRead]:
    stmt = select(Order).where(Order.buyer_user_id == user.id)
    if order_status is not None:
        stmt = stmt.where(Order.status == order_status)
    return page_orders(db, stmt, page)


def event_orders(
    db: Session, event: Event, page: Pagination, order_status: OrderStatus | None = None
) -> Page[OrderRead]:
    stmt = select(Order).where(Order.event_id == event.id)
    if order_status is not None:
        stmt = stmt.where(Order.status == order_status)
    return page_orders(db, stmt, page)


# ------------------------------------------------------------------ tickets


def _ticket_options() -> list[Any]:
    return [selectinload(Ticket.order_item), selectinload(Ticket.attendee)]


def tickets_to_read(db: Session, tickets: Sequence[Ticket]) -> list[TicketRead]:
    names = _type_names(db, {t.order_item.ticket_type_id for t in tickets})
    events = {
        e.id: e
        for e in db.scalars(select(Event).where(Event.id.in_({t.event_id for t in tickets})))
    }
    out = []
    for t in tickets:
        ev = events[t.event_id]
        item = t.order_item
        out.append(
            TicketRead(
                id=t.id,
                ticket_code=t.ticket_code,
                qr_payload=t.qr_payload,
                status=t.status,
                issued_at=t.issued_at,
                checked_in_at=t.checked_in_at,
                ticket_type_name=names.get(item.ticket_type_id, ""),
                attendee_name=t.attendee.full_name,
                attendee_email=t.attendee.email,
                section=item.section,
                row_label=item.row_label,
                seat_number=item.seat_number,
                order_id=item.order_id,
                event=TicketEventInfo(
                    id=ev.id,
                    title=ev.title,
                    starts_at=ev.starts_at,
                    ends_at=ev.ends_at,
                    timezone=ev.timezone,
                    location_name=ev.location_name,
                    location_address=ev.location_address,
                ),
                pdf_url=public_api_url(f"/tickets/{t.id}/pdf"),
            )
        )
    return out


def ticket_to_read(db: Session, ticket: Ticket) -> TicketRead:
    return tickets_to_read(db, [ticket])[0]


def _mine_clause(user: User) -> ColumnElement[bool]:
    """Tickets I bought, or issued to my email."""
    bought = Ticket.order_item_id.in_(
        select(OrderItem.id).join(Order).where(Order.buyer_user_id == user.id)
    )
    for_me = Ticket.attendee_id.in_(select(Attendee.id).where(Attendee.email.ilike(user.email)))
    return or_(bought, for_me)


def my_tickets(
    db: Session, user: User, page: Pagination, upcoming: bool | None = None
) -> Page[TicketRead]:
    stmt = select(Ticket).join(Event, Event.id == Ticket.event_id).where(_mine_clause(user))
    if upcoming is True:
        stmt = stmt.where(Event.ends_at > datetime.now(UTC))
    elif upcoming is False:
        stmt = stmt.where(Event.ends_at <= datetime.now(UTC))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    tickets = db.scalars(
        stmt.options(*_ticket_options())
        .order_by(Event.starts_at, Ticket.issued_at)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page(
        items=tickets_to_read(db, tickets), total=total, limit=page.limit, offset=page.offset
    )


def can_view_ticket(db: Session, user: User, ticket: Ticket) -> bool:
    order = db.get(Order, ticket.order_item.order_id)
    if order is not None and order.buyer_user_id == user.id:
        return True
    if ticket.attendee.email.lower() == user.email.lower():
        return True
    event = db.get(Event, ticket.event_id)
    return event is not None and permissions.can_manage_event(db, user, event)


def get_ticket_for_user(db: Session, user: User, ticket_id: uuid.UUID) -> Ticket:
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id).options(*_ticket_options()))
    if ticket is None:
        raise NotFoundError("ticket")
    if not can_view_ticket(db, user, ticket):
        raise ForbiddenError("Not your ticket")
    return ticket


# ------------------------------------------- attendee list (organizer + door staff)


def _attendee_stmt(
    event: Event,
    q: str | None,
    ticket_type_id: uuid.UUID | None,
    ticket_status: TicketStatus | None,
) -> Select[tuple[Ticket, Attendee, OrderItem, TicketType]]:
    stmt = (
        select(Ticket, Attendee, OrderItem, TicketType)
        .join(Attendee, Attendee.id == Ticket.attendee_id)
        .join(OrderItem, OrderItem.id == Ticket.order_item_id)
        .join(TicketType, TicketType.id == OrderItem.ticket_type_id)
        .where(Ticket.event_id == event.id)
    )
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Attendee.full_name.ilike(pattern),
                Attendee.email.ilike(pattern),
                Ticket.ticket_code.ilike(pattern),
            )
        )
    if ticket_type_id:
        stmt = stmt.where(OrderItem.ticket_type_id == ticket_type_id)
    if ticket_status:
        stmt = stmt.where(Ticket.status == ticket_status)
    return stmt


def _row(t: Ticket, a: Attendee, i: OrderItem, tt: TicketType) -> AttendeeRow:
    return AttendeeRow(
        ticket_id=t.id,
        ticket_code=t.ticket_code,
        attendee_name=a.full_name,
        attendee_email=a.email,
        ticket_type_name=tt.name,
        ticket_status=t.status,
        order_id=i.order_id,
        section=i.section,
        row_label=i.row_label,
        seat_number=i.seat_number,
        checked_in_at=t.checked_in_at,
    )


def attendees(
    db: Session,
    event: Event,
    page: Pagination,
    q: str | None = None,
    ticket_type_id: uuid.UUID | None = None,
    ticket_status: TicketStatus | None = None,
) -> Page[AttendeeRow]:
    stmt = _attendee_stmt(event, q, ticket_type_id, ticket_status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(Attendee.full_name, Ticket.issued_at).limit(page.limit).offset(page.offset)
    ).all()
    return Page(items=[_row(*r) for r in rows], total=total, limit=page.limit, offset=page.offset)


def attendees_csv(db: Session, event: Event) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["attendee_name", "attendee_email", "ticket_type", "ticket_code", "status", "checked_in_at"]
    )
    rows = db.execute(_attendee_stmt(event, None, None, None).order_by(Attendee.full_name)).all()
    for t, a, _, tt in rows:
        writer.writerow(
            [
                a.full_name,
                a.email,
                tt.name,
                t.ticket_code,
                t.status.value,
                t.checked_in_at.isoformat() if t.checked_in_at else "",
            ]
        )
    return buf.getvalue()
