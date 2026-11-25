"""Cancellations and refunds (SRS §4.9). Refunds are simulated, full refunds only (MVP).

- ``cancel_registration`` — a **free** paid order (``total = 0``) is cancelled by its buyer
  (or the organizer / a platform admin): tickets → ``cancelled``, inventory back, order →
  ``cancelled``.
- ``refund`` — a **paid** order is refunded in full by the event's organizer or a platform
  admin, immediately. Buyers don't refund themselves: they ask through support and the
  organizer decides. ``gateway.refund`` → ``refund`` row → tickets ``refunded`` → inventory
  back → order ``refunded``. A second refund → 409, there is only ever one refund.
- A ticket that was already checked in blocks a single-order refund / cancellation (409
  ``ticket_already_checked_in``). Cancelling the whole event refunds everything anyway — the
  event didn't happen.
- Promo uses are given back on refund / cancellation (``promo.release``): an order that was
  refunded doesn't count as a redemption, consistent with analytics.
- :func:`on_event_cancelled` runs inside ``events.cancel``: paid orders → refund, free →
  cancelled, pending → released. :func:`after_event_cancelled` sends the refund emails once the
  cancellation is committed.

Lock order: order(s) → tickets (by id) → ticket types (by id) → campaigns, like everywhere else.
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError
from app.models import Event, Order, OrderItem, Payment, Refund, Ticket, User
from app.models.enums import (
    OrderStatus,
    PaymentPurpose,
    PaymentStatus,
    RefundStatus,
    TicketStatus,
)
from app.services import audit, notifications, orders, permissions
from app.services.audit import AuditAction
from app.services.payments.gateway import PaymentGateway, get_gateway

logger = logging.getLogger("biletflow.refunds")

EVENT_CANCELLED_REASON = "Event cancelled"


def _lock_tickets(db: Session, order: Order) -> list[Ticket]:
    return list(
        db.scalars(
            select(Ticket)
            .join(OrderItem, OrderItem.id == Ticket.order_item_id)
            .where(OrderItem.order_id == order.id)
            .order_by(Ticket.id)
            .with_for_update(of=Ticket)
            .execution_options(populate_existing=True)
        )
    )


def _ensure_not_used(tickets: Sequence[Ticket]) -> None:
    if any(t.status == TicketStatus.CHECKED_IN for t in tickets):
        raise ConflictError(
            "ticket_already_checked_in",
            "A ticket of this order was already used at the door",
        )


def _close(
    db: Session,
    order: Order,
    tickets: Sequence[Ticket],
    *,
    order_status: OrderStatus,
    ticket_status: TicketStatus,
) -> None:
    """Invalidate the tickets, give the inventory and the promo use back, close the order."""
    for ticket in tickets:
        if ticket.status in (TicketStatus.VALID, TicketStatus.CHECKED_IN):
            ticket.status = ticket_status
    orders.return_sold(db, [order])
    order.status = order_status
    if order_status == OrderStatus.CANCELLED:
        order.cancelled_at = datetime.now(UTC)


def _can_act(db: Session, user: User, order: Order, *, buyer_allowed: bool) -> None:
    if buyer_allowed and order.buyer_user_id == user.id:
        return
    event = permissions.get_event(db, order.event_id)
    if not permissions.can_manage_event(db, user, event):
        raise ForbiddenError(
            "Only the event organizer can do this" if not buyer_allowed else "Not your order"
        )


# ------------------------------------------------------------------ free registrations


def _cancel_registration_locked(
    db: Session, order: Order, actor_id: uuid.UUID | None, *, allow_checked_in: bool
) -> None:
    tickets = _lock_tickets(db, order)
    if not allow_checked_in:
        _ensure_not_used(tickets)
    _close(
        db, order, tickets, order_status=OrderStatus.CANCELLED, ticket_status=TicketStatus.CANCELLED
    )
    audit.log(
        db,
        actor_id=actor_id,
        action=AuditAction.ORDER_CANCELLED,
        entity_type="order",
        entity_id=order.id,
        event_id=order.event_id,
        description=f"Free registration cancelled, {len(tickets)} ticket(s) cancelled",
        metadata={"ticket_ids": [str(t.id) for t in tickets]},
    )


def cancel_registration(db: Session, user: User, order_id: uuid.UUID) -> Order:
    """Buyer (or organizer / platform admin) cancels a free registration."""
    order = orders.lock_order(db, order_id)
    _can_act(db, user, order, buyer_allowed=True)
    if order.status == OrderStatus.CANCELLED and order.paid_at is not None:
        raise ConflictError("order_already_cancelled", "This registration is already cancelled")
    if order.status != OrderStatus.PAID:
        raise ConflictError("order_not_paid", f"Order is {order.status}, nothing to cancel")
    if order.total_minor != 0:
        raise ConflictError("order_not_free", "Paid orders are refunded, not cancelled")
    _cancel_registration_locked(db, order, user.id, allow_checked_in=False)
    db.commit()
    return order


# ------------------------------------------------------------------ refunds


def _purchase_payment(db: Session, order: Order) -> Payment:
    payment = db.scalar(
        select(Payment).where(
            Payment.order_id == order.id,
            Payment.purpose == PaymentPurpose.TICKET_PURCHASE,
            Payment.status == PaymentStatus.SUCCEEDED,
        )
    )
    if payment is None:  # mark_paid requires one, so this means broken data
        raise ConflictError("payment_not_found", "No succeeded payment to refund")
    return payment


def _refund_locked(
    db: Session,
    order: Order,
    actor_id: uuid.UUID | None,
    reason: str | None,
    gateway: PaymentGateway,
    *,
    allow_checked_in: bool,
) -> Refund:
    """Full refund of a locked, paid order with ``total > 0``. Never commits."""
    payment = _purchase_payment(db, order)
    tickets = _lock_tickets(db, order)
    if not allow_checked_in:
        _ensure_not_used(tickets)

    result = gateway.refund(
        provider_ref=payment.provider_ref or "", amount_minor=payment.amount_minor
    )
    refund = Refund(
        payment_id=payment.id,
        requested_by_user_id=actor_id,
        amount_minor=payment.amount_minor,
        reason=reason,
        status=RefundStatus.SUCCEEDED if result.succeeded else RefundStatus.FAILED,
        provider_ref=result.provider_ref,
        is_simulated=gateway.is_simulated,
        processed_at=datetime.now(UTC),
    )
    db.add(refund)
    db.flush()
    amount = notifications.format_money(refund.amount_minor, payment.currency)
    if not result.succeeded:
        logger.warning("refund of order %s failed: %s", order.id, result.failure_reason)
        return refund

    _close(
        db, order, tickets, order_status=OrderStatus.REFUNDED, ticket_status=TicketStatus.REFUNDED
    )
    audit.log(
        db,
        actor_id=actor_id,
        action=AuditAction.REFUND_PROCESSED,
        entity_type="order",
        entity_id=order.id,
        event_id=order.event_id,
        description=f"Refunded {amount} (simulated), {len(tickets)} ticket(s) refunded"
        + (f": {reason}" if reason else ""),
        metadata={
            "refund_id": str(refund.id),
            "payment_id": str(payment.id),
            "amount_minor": refund.amount_minor,
            "is_simulated": refund.is_simulated,
        },
    )
    return refund


def refund(
    db: Session,
    user: User,
    order_id: uuid.UUID,
    reason: str | None,
    *,
    gateway: PaymentGateway,
) -> Refund:
    """Organizer / platform admin refunds a paid order in full (simulated)."""
    order = orders.lock_order(db, order_id)
    _can_act(db, user, order, buyer_allowed=False)
    if order.status == OrderStatus.REFUNDED:
        raise ConflictError("order_already_refunded", "This order is already refunded")
    if order.status != OrderStatus.PAID:
        raise ConflictError("order_not_paid", f"Order is {order.status}, nothing to refund")
    if order.total_minor == 0:
        raise ConflictError("order_is_free", "Free registrations are cancelled, not refunded")
    result = _refund_locked(db, order, user.id, reason, gateway, allow_checked_in=False)
    db.commit()
    if result.status == RefundStatus.SUCCEEDED:
        notifications.send_refund_processed(db, order, result.amount_minor)
    return result


# ------------------------------------------------------------------ event cancellation


def on_event_cancelled(db: Session, event: Event, user: User) -> None:
    """Inside ``events.cancel`` (before its commit): close every open order of the event.

    All orders of the event are locked first; anyone else who could touch their tickets or
    inventory (payment, refund, sweep) locks an order first too, so this can't deadlock.
    """
    db.flush()  # the event row update → nobody can start a new checkout for it now
    open_orders = db.scalars(
        select(Order)
        .where(
            Order.event_id == event.id,
            Order.status.in_((OrderStatus.PENDING, OrderStatus.PAID)),
        )
        .order_by(Order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    gateway = get_gateway()
    for order in open_orders:
        if order.status == OrderStatus.PENDING:
            orders.release(db, order, new_status=OrderStatus.CANCELLED, actor_id=user.id)
        elif order.total_minor == 0:
            _cancel_registration_locked(db, order, user.id, allow_checked_in=True)
        else:
            _refund_locked(
                db, order, user.id, EVENT_CANCELLED_REASON, gateway, allow_checked_in=True
            )


def after_event_cancelled(db: Session, event: Event) -> None:
    """After ``events.cancel`` committed: one refund email per order refunded by it."""
    rows = db.execute(
        select(Order, Refund.amount_minor)
        .join(Payment, Payment.order_id == Order.id)
        .join(Refund, Refund.payment_id == Payment.id)
        .where(
            Order.event_id == event.id,
            Refund.status == RefundStatus.SUCCEEDED,
            Refund.reason == EVENT_CANCELLED_REASON,
        )
        .order_by(Order.created_at)
    ).all()
    for order, amount in rows:
        try:
            notifications.send_refund_processed(db, order, amount)
        except Exception:  # noqa: BLE001 — an email must not undo the cancellation
            logger.exception("could not send refund email for order %s", order.id)
