"""Paying for an order (SRS §4.6).

``pay_order`` runs in one transaction under the order row lock (``SELECT … FOR UPDATE``):

    payment(initiated) → gateway.charge → succeeded → orders.mark_paid → tickets (hooks, BE-17)
                                        → failed    → order stays pending

- The lock makes a second, parallel ``pay`` wait; it then sees ``paid`` and gets 409 — the order
  is never charged twice.
- A failed charge leaves the order **pending until ``reserved_until``**: the buyer can retry
  with another attempt (each attempt is its own ``payment`` row). After that the reservation
  expires as usual (sweep / lazy expiry in ``services.orders``).
- The charge happens while the order lock is held. That is fine for the simulated gateway; for a
  real one a timeout on ``charge`` keeps the lock short.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError
from app.models import Payment, User
from app.models.enums import OrderStatus, PaymentPurpose, PaymentStatus
from app.services import audit, notifications, orders
from app.services.audit import AuditAction
from app.services.payments.gateway import PaymentGateway


def _log(
    db: Session,
    payment: Payment,
    event_id: uuid.UUID,
    action: AuditAction,
    actor_id: uuid.UUID,
    text: str,
) -> None:
    audit.log(
        db,
        actor_id=actor_id,
        action=action,
        entity_type="payment",
        entity_id=payment.id,
        event_id=event_id,
        description=text,
        metadata={
            "order_id": str(payment.order_id),
            "amount_minor": payment.amount_minor,
            "provider_ref": payment.provider_ref,
            "is_simulated": payment.is_simulated,
        },
    )


def pay_order(
    db: Session,
    user: User,
    order_id: uuid.UUID,
    *,
    simulate: str,
    gateway: PaymentGateway,
) -> Payment:
    """Charge a pending order of ``user``. Returns the payment (succeeded or failed)."""
    order = orders.lock_order(db, order_id)
    if order.buyer_user_id != user.id:
        raise ForbiddenError("Not your order")
    if order.status == OrderStatus.PAID:
        raise ConflictError("order_already_paid", "This order is already paid")
    if orders.expire_if_stale(db, order):
        db.commit()
        raise ConflictError("reservation_expired", "The reservation for this order has expired")
    if order.status != OrderStatus.PENDING:
        raise ConflictError("order_not_pending", f"Order is {order.status}, not pending")
    if order.total_minor == 0:
        raise ConflictError("order_is_free", "Free orders need no payment — use register-free")

    payment = Payment(
        order_id=order.id,
        payer_user_id=user.id,
        purpose=PaymentPurpose.TICKET_PURCHASE,
        amount_minor=order.total_minor,
        currency=order.currency,
        provider=gateway.provider,
        is_simulated=gateway.is_simulated,
        status=PaymentStatus.INITIATED,
    )
    db.add(payment)
    db.flush()

    result = gateway.charge(
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        reference=str(order.id),
        simulate=simulate,
    )
    payment.provider_ref = result.provider_ref
    payment.completed_at = datetime.now(UTC)
    amount = notifications.format_money(payment.amount_minor, payment.currency)
    if result.succeeded:
        payment.status = PaymentStatus.SUCCEEDED
        db.flush()
        _log(
            db,
            payment,
            order.event_id,
            AuditAction.PAYMENT_SUCCEEDED,
            user.id,
            f"Payment of {amount} (simulated)",
        )
        orders.mark_paid(db, order, actor_id=user.id)  # reserved → sold, tickets: same transaction
    else:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = result.failure_reason
        db.flush()
        _log(
            db,
            payment,
            order.event_id,
            AuditAction.PAYMENT_FAILED,
            user.id,
            f"Payment of {amount} failed (simulated): {result.failure_reason}",
        )
    db.commit()

    if payment.status == PaymentStatus.SUCCEEDED:
        orders.notify_paid(db, order)
    return payment
