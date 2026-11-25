"""Read-only questions about payments, shared by orders and ticketing (no circular imports)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order, Payment
from app.models.enums import PaymentPurpose, PaymentStatus


def has_succeeded_ticket_payment(db: Session, order: Order) -> bool:
    """A succeeded ticket-purchase payment covering the order's full total exists."""
    db.flush()
    stmt = select(Payment.id).where(
        Payment.order_id == order.id,
        Payment.purpose == PaymentPurpose.TICKET_PURCHASE,
        Payment.status == PaymentStatus.SUCCEEDED,
        Payment.amount_minor == order.total_minor,
    )
    return db.scalar(stmt.limit(1)) is not None
