"""Orders: atomic inventory reservation, free registration, cancel and expiry (SRS §4.4, §4.6, §7).

The one property the whole system depends on: **the last ticket is never sold twice.**

- ``ticket_type.quantity_reserved`` / ``quantity_sold`` change only in this module, and only on
  rows locked with ``SELECT … FOR UPDATE``. Ticket types are always locked in ``id`` order
  (``ORDER BY id`` in the same statement), so two checkouts cannot deadlock each other.
  The ``inventory_not_oversold`` CHECK constraint is the last line of defence.
- Lock order is always *event (share) → order row(s) → ticket type rows by id*. No code path
  waits for an order row while holding a ticket type lock.
- Helpers for other modules (:func:`lock_order`, :func:`mark_paid`, :func:`release`,
  :func:`expire_if_stale`) never commit — the caller owns the transaction. The public entry
  points (:func:`create_order`, :func:`cancel`, :func:`register_free`, :func:`get_order`,
  :func:`expire_stale_orders`) commit once, at the end, and send emails only after the commit.

Expired reservations are released two ways: :func:`expire_stale_orders` runs in the background
(``app.workers.order_sweeper``), and any access to a single order expires it lazily.
"""

import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.models import Attendee, Event, Order, OrderItem, TicketType, User
from app.models.enums import (
    EventStatus,
    OrderStatus,
    SeatingMode,
)
from app.schemas.orders import OrderCreate
from app.services import audit, notifications, permissions, promo, ticketing
from app.services.audit import AuditAction
from app.services.payments.records import has_succeeded_ticket_payment
from app.services.ticket_types import is_on_sale, is_sellable

# Called inside the transaction that marks an order paid, after the counters moved
# reserved → sold: ticket issuance (``ticketing.issue_tickets``).
on_order_paid_hooks: list[Callable[[Session, Order], object]] = [ticketing.issue_tickets]


def _now() -> datetime:
    return datetime.now(UTC)


def _unprocessable(code: str, message: str, details: Any = None) -> AppError:
    return AppError(code, message, status.HTTP_422_UNPROCESSABLE_ENTITY, details)


# ------------------------------------------------------------------ locking


def lock_order(db: Session, order_id: uuid.UUID) -> Order:
    """``SELECT … FOR UPDATE`` on the order row, refreshing any copy already in the session."""
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise NotFoundError("order")
    return order


def _lock_ticket_types(db: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, TicketType]:
    """Lock ticket type rows in ``id`` order and return fresh values from the database."""
    wanted = set(ids)
    if not wanted:
        return {}
    db.flush()  # populate_existing must not throw away pending in-memory changes
    rows = db.scalars(
        select(TicketType)
        .where(TicketType.id.in_(wanted))
        .order_by(TicketType.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {tt.id: tt for tt in rows}


def _quantities(order: Order) -> Counter[uuid.UUID]:
    return Counter(item.ticket_type_id for item in order.items)


def _is_stale(order: Order, now: datetime | None = None) -> bool:
    return (
        order.status == OrderStatus.PENDING
        and order.reserved_until is not None
        and order.reserved_until <= (now or _now())
    )


def _log(
    db: Session,
    order: Order,
    action: AuditAction,
    actor_id: uuid.UUID | None,
    text: str,
    **meta: object,
) -> None:
    audit.log(
        db,
        actor_id=actor_id,
        action=action,
        entity_type="order",
        entity_id=order.id,
        event_id=order.event_id,
        description=text,
        metadata=dict(meta),
    )


# ------------------------------------------------------------------ create


def _ensure_event_on_sale(event: Event, now: datetime) -> None:
    if event.status != EventStatus.PUBLISHED or event.suspended_at is not None:
        raise ConflictError("event_not_on_sale", "Tickets for this event are not on sale")
    if now >= event.ends_at:
        raise ConflictError("event_not_on_sale", "This event has already ended")
    if (event.registration_opens_at and now < event.registration_opens_at) or (
        event.registration_closes_at and now >= event.registration_closes_at
    ):
        raise ConflictError("registration_closed", "Registration for this event is closed")


def _requested_quantities(data: OrderCreate) -> dict[uuid.UUID, int]:
    quantities: dict[uuid.UUID, int] = {}
    for line in data.items:
        if line.ticket_type_id in quantities:
            raise _unprocessable(
                "duplicate_ticket_type", "Each ticket type may appear only once in items"
            )
        quantities[line.ticket_type_id] = line.quantity
    tickets = sum(quantities.values())
    if len(data.attendees) != tickets:
        raise _unprocessable(
            "attendees_mismatch",
            f"Expected {tickets} attendee(s) — one per ticket, got {len(data.attendees)}",
        )
    return quantities


def _check_lines(
    event: Event,
    quantities: dict[uuid.UUID, int],
    types: dict[uuid.UUID, TicketType],
    now: datetime,
) -> str:
    """Validate every line against the *locked* ticket type rows. Returns the order currency."""
    for tt_id, qty in quantities.items():
        tt = types.get(tt_id)
        if tt is None or tt.event_id != event.id:
            raise NotFoundError("ticket_type")
        if not is_on_sale(tt, event, now):
            raise ConflictError("ticket_type_not_on_sale", f"“{tt.name}” is not on sale")
        if not is_sellable(tt, event):
            raise ConflictError(
                "paid_sales_inactive", "Paid tickets for this event are not on sale yet"
            )
        if qty > tt.max_per_order:
            raise _unprocessable(
                "max_per_order_exceeded",
                f"At most {tt.max_per_order} “{tt.name}” ticket(s) per order",
                {"ticket_type_id": str(tt.id), "max_per_order": tt.max_per_order},
            )
        if tt.quantity_available < qty:
            raise AppError(
                "sold_out",
                f"Not enough “{tt.name}” tickets left",
                status.HTTP_409_CONFLICT,
                {"ticket_type_id": str(tt.id), "available": max(tt.quantity_available, 0)},
            )
    currencies = {types[tt_id].currency for tt_id in quantities}
    if len(currencies) > 1:
        raise _unprocessable("mixed_currency", "All tickets in one order must share a currency")
    return currencies.pop()


def _build_order(
    user: User,
    event: Event,
    data: OrderCreate,
    types: dict[uuid.UUID, TicketType],
    currency: str,
    now: datetime,
    discount: int = 0,
) -> Order:
    """Order + one item (with a price snapshot) and one attendee per ticket.

    Prices come only from the locked ticket type rows; nothing price-like is read from the
    request. Items get increasing ``created_at`` so they read back in request order.
    """
    attendees = iter(data.attendees)
    items: list[OrderItem] = []
    for line in data.items:
        tt = types[line.ticket_type_id]
        for _ in range(line.quantity):
            person = next(attendees)
            item = OrderItem(
                ticket_type_id=tt.id,
                unit_price_minor=tt.price_minor,
                created_at=now + timedelta(microseconds=len(items)),
            )
            item.attendee = Attendee(full_name=person.full_name.strip(), email=str(person.email))
            items.append(item)

    subtotal = sum(i.unit_price_minor for i in items)
    fees = 0
    return Order(
        buyer_user_id=user.id,
        event_id=event.id,
        status=OrderStatus.PENDING,
        currency=currency,
        subtotal_minor=subtotal,
        discount_minor=discount,
        fees_minor=fees,
        total_minor=subtotal - discount + fees,
        reserved_until=now + timedelta(minutes=get_settings().reservation_ttl_minutes),
        items=items,
    )


def create_order(db: Session, user: User, data: OrderCreate) -> Order:
    """Reserve inventory atomically and create a pending order (one transaction, one commit).

    Free orders (``total_minor == 0``) are completed in the same transaction: reserved → sold,
    status ``paid``, tickets issued by the ``on_order_paid_hooks`` — no payment involved.
    """
    if user.email_verified_at is None:
        raise AppError(
            "email_not_verified", "Please verify your email first", status.HTTP_403_FORBIDDEN
        )
    now = _now()
    # FOR SHARE: the event can't be cancelled / suspended under us until we commit.
    event = db.get(Event, data.event_id, with_for_update={"read": True}, populate_existing=True)
    if event is None:
        raise NotFoundError("event")
    _ensure_event_on_sale(event, now)
    if event.seating_mode == SeatingMode.ASSIGNED or data.seat_hold_ids:
        raise _unprocessable(
            "seated_checkout_unavailable", "Checkout with assigned seats is not available yet"
        )
    quantities = _requested_quantities(data)

    types = _lock_ticket_types(db, quantities)
    currency = _check_lines(event, quantities, types, now)
    quote = None
    if data.promo_code:  # campaign row locked after the ticket types (see promo)
        lines = [promo.Line(t, types[t].price_minor, q) for t, q in quantities.items()]
        quote = promo.quote(db, event.id, data.promo_code, lines, lock=True, now=now)
    order = _build_order(
        user, event, data, types, currency, now, quote.discount_minor if quote else 0
    )
    db.add(order)
    for tt_id, qty in quantities.items():
        types[tt_id].quantity_reserved += qty
    try:
        db.flush()
        if quote:
            promo.redeem(db, order, quote, user.id)
            order.promo_code_id = quote.promo_code.id
            db.flush()
    except IntegrityError as exc:  # CHECK constraints — should be unreachable under the locks
        if "redemptions_within_limit" in str(exc.orig):
            raise promo.promo_error("promo_exhausted") from exc
        if "inventory_not_oversold" not in str(exc.orig):
            raise
        raise ConflictError("sold_out", "Not enough tickets left") from exc

    _log(
        db,
        order,
        AuditAction.ORDER_CREATED,
        user.id,
        f"Order created: {len(order.items)} ticket(s), "
        f"total {notifications.format_money(order.total_minor, order.currency)}",
        items={str(k): v for k, v in quantities.items()},
        discount_minor=order.discount_minor,
        total_minor=order.total_minor,
    )
    if order.total_minor == 0:  # free tickets or a 100 % promo → free registration path
        mark_paid(db, order, actor_id=user.id)
    db.commit()

    if order.status == OrderStatus.PAID:
        notify_paid(db, order)
    return order


# ------------------------------------------------------------------ helpers for other modules


def notify_paid(db: Session, order: Order) -> None:
    """After the commit that paid ``order``: order confirmation + tickets (email, PDF)."""
    notifications.send_order_confirmation(db, order)
    ticketing.deliver(db, order)


def mark_paid(db: Session, order: Order, *, actor_id: uuid.UUID | None = None) -> Order:
    """pending → paid: reserved → sold for every item, then ``on_order_paid_hooks``.

    For BE-15 (payment succeeded) and free registration. The caller holds the order row lock
    (:func:`lock_order`), handles an expired reservation first (:func:`expire_if_stale`) and
    commits afterwards. A paid order needs a succeeded payment for its full total — so tickets
    are never issued without one.
    """
    if order.status != OrderStatus.PENDING:
        raise ConflictError("order_not_pending", f"Order is {order.status}, not pending")
    if _is_stale(order):
        raise ConflictError("reservation_expired", "The reservation for this order has expired")
    if order.total_minor > 0 and not has_succeeded_ticket_payment(db, order):
        raise ConflictError("payment_required", "The order has no succeeded payment")

    quantities = _quantities(order)
    types = _lock_ticket_types(db, quantities)
    for tt_id, qty in quantities.items():
        types[tt_id].quantity_reserved -= qty
        types[tt_id].quantity_sold += qty
    order.status = OrderStatus.PAID
    order.paid_at = _now()
    order.reserved_until = None
    _log(
        db,
        order,
        AuditAction.ORDER_PAID,
        actor_id,
        (
            "Free registration completed"
            if order.total_minor == 0
            else f"Order paid: {notifications.format_money(order.total_minor, order.currency)}"
        ),
        total_minor=order.total_minor,
    )
    for hook in on_order_paid_hooks:
        hook(db, order)
    db.flush()
    return order


def _release_many(
    db: Session,
    orders: Sequence[Order],
    new_status: OrderStatus,
    actor_id: uuid.UUID | None,
) -> None:
    """Return the reserved quantity of pending ``orders`` (all locked by the caller)."""
    total: Counter[uuid.UUID] = Counter()
    for order in orders:
        total.update(_quantities(order))
    types = _lock_ticket_types(db, total)  # one statement → one consistent lock order
    now = _now()
    for order in orders:
        quantities = _quantities(order)
        for tt_id, qty in quantities.items():
            types[tt_id].quantity_reserved -= qty
        order.status = new_status
        if new_status == OrderStatus.CANCELLED:
            order.cancelled_at = now
            action, text = AuditAction.ORDER_CANCELLED, "Pending order cancelled"
        else:
            action, text = AuditAction.ORDER_EXPIRED, "Reservation expired"
        _log(
            db,
            order,
            action,
            actor_id,
            f"{text}, {sum(quantities.values())} ticket(s) released",
            released={str(k): v for k, v in quantities.items()},
        )
    promo.release(db, orders)  # campaign rows after the ticket types
    db.flush()


def release(
    db: Session,
    order: Order,
    *,
    new_status: OrderStatus = OrderStatus.CANCELLED,
    actor_id: uuid.UUID | None = None,
) -> Order:
    """pending → cancelled / expired, returning the reservation. Caller holds the order lock."""
    if new_status not in (OrderStatus.CANCELLED, OrderStatus.EXPIRED):
        raise ValueError("release() moves an order to cancelled or expired only")
    if order.status != OrderStatus.PENDING:
        raise ConflictError("order_not_pending", f"Order is {order.status}, not pending")
    _release_many(db, [order], new_status, actor_id)
    return order


def expire_if_stale(db: Session, order: Order) -> bool:
    """Lazy expiry of one *locked* order. True if it was expired now."""
    if not _is_stale(order):
        return False
    _release_many(db, [order], OrderStatus.EXPIRED, None)
    return True


def expire_stale_orders(db: Session, *, now: datetime | None = None, batch_size: int = 100) -> int:
    """Sweep: expire every pending order whose ``reserved_until`` has passed.

    ``SKIP LOCKED`` lets the sweep run next to checkouts and payments (and in several API
    processes at once) without waiting: an order someone is working on is picked up next run.
    Commits once per batch. Returns the number of expired orders.
    """
    now = now or _now()
    expired = 0
    while True:
        orders = db.scalars(
            select(Order)
            .where(Order.status == OrderStatus.PENDING, Order.reserved_until < now)
            .order_by(Order.reserved_until)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).all()
        if not orders:
            break
        _release_many(db, orders, OrderStatus.EXPIRED, None)
        db.commit()
        expired += len(orders)
        if len(orders) < batch_size:
            break
    return expired


# ------------------------------------------------------------------ order endpoints


def _ensure_can_view(db: Session, user: User, order: Order) -> None:
    if order.buyer_user_id == user.id:
        return
    event = db.get(Event, order.event_id)
    if event is not None and permissions.can_manage_event(db, user, event):
        return
    raise ForbiddenError("Not your order")


def get_order(db: Session, user: User, order_id: uuid.UUID) -> Order:
    """Buyer, the event organizer or a platform admin. Expires a stale reservation on access."""
    order = db.get(Order, order_id, populate_existing=True)
    if order is None:
        raise NotFoundError("order")
    _ensure_can_view(db, user, order)
    if _is_stale(order):
        order = lock_order(db, order.id)
        expire_if_stale(db, order)
        db.commit()
    return order


def cancel(db: Session, user: User, order_id: uuid.UUID) -> Order:
    """Buyer (or platform admin) cancels a pending order and its reservation is returned.

    Idempotent for orders that are already cancelled or expired. Paid orders → BE-24 (refund).
    """
    order = lock_order(db, order_id)
    if order.buyer_user_id != user.id and not permissions.is_platform_admin(user):
        raise ForbiddenError("Not your order")
    if expire_if_stale(db, order):
        db.commit()
        return order
    if order.status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED):
        db.commit()
        return order
    release(db, order, new_status=OrderStatus.CANCELLED, actor_id=user.id)
    db.commit()
    return order


def register_free(db: Session, user: User, order_id: uuid.UUID) -> Order:
    """Complete a pending order whose total is 0 without a payment. Idempotent."""
    order = lock_order(db, order_id)
    if order.buyer_user_id != user.id:
        raise ForbiddenError("Not your order")
    if order.total_minor != 0:
        raise ConflictError("order_not_free", "This order has to be paid")
    if order.status == OrderStatus.PAID:
        db.commit()
        return order
    if expire_if_stale(db, order):
        db.commit()
        raise ConflictError("reservation_expired", "The reservation for this order has expired")
    mark_paid(db, order, actor_id=user.id)
    db.commit()
    notify_paid(db, order)
    return order
