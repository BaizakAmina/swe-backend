import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, String, Text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TZ, TimestampMixin, UUIDPkMixin, str_enum
from app.models.enums import PaymentProvider, PaymentPurpose, PaymentStatus, RefundStatus


class Payment(UUIDPkMixin, TimestampMixin, Base):
    """Simulated payment. `is_simulated` is always true in the MVP — never real money movement."""

    __tablename__ = "payment"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint(
            "(purpose = 'ticket_purchase' AND order_id IS NOT NULL)"
            " OR (purpose = 'activation_fee' AND event_id IS NOT NULL)",
            name="purpose_target",
        ),
    )

    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), index=True
    )
    # Set for activation-fee payments (which belong to an event, not an order).
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("event.id", ondelete="RESTRICT"), index=True
    )
    payer_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purpose: Mapped[PaymentPurpose] = mapped_column(
        str_enum(PaymentPurpose, "payment_purpose"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KZT")
    provider: Mapped[PaymentProvider] = mapped_column(
        str_enum(PaymentProvider, "payment_provider"),
        nullable=False,
        default=PaymentProvider.SIMULATION,
    )
    provider_ref: Mapped[str | None] = mapped_column(String(100), unique=True)
    status: Mapped[PaymentStatus] = mapped_column(
        str_enum(PaymentStatus, "payment_status"),
        nullable=False,
        default=PaymentStatus.INITIATED,
        server_default="initiated",
    )
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TZ)

    refunds: Mapped[list["Refund"]] = relationship(back_populates="payment")


class Refund(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "refund"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="amount_positive"),)

    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RefundStatus] = mapped_column(
        str_enum(RefundStatus, "refund_status"),
        nullable=False,
        default=RefundStatus.PENDING,
        server_default="pending",
    )
    provider_ref: Mapped[str | None] = mapped_column(String(100))
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    processed_at: Mapped[datetime | None] = mapped_column(TZ)

    payment: Mapped[Payment] = relationship(back_populates="refunds")
