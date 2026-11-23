import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TZ, TimestampMixin, UUIDPkMixin, str_enum
from app.models.enums import OrderStatus, TicketStatus


class Order(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "orders"  # "order" is reserved in SQL
    __table_args__ = (
        CheckConstraint(
            "subtotal_minor >= 0 AND discount_minor >= 0 AND fees_minor >= 0 AND total_minor >= 0",
            name="amounts_non_negative",
        ),
        CheckConstraint(
            "total_minor = subtotal_minor - discount_minor + fees_minor", name="total_consistent"
        ),
        CheckConstraint("discount_minor <= subtotal_minor", name="discount_le_subtotal"),
        Index(
            "ix_orders_pending_reserved_until",
            "reserved_until",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    buyer_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        str_enum(OrderStatus, "order_status"),
        nullable=False,
        default=OrderStatus.PENDING,
        server_default="pending",
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KZT")
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    discount_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    fees_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    promo_code_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("promo_code.id"))
    reserved_until: Mapped[datetime | None] = mapped_column(TZ)
    paid_at: Mapped[datetime | None] = mapped_column(TZ)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZ)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(UUIDPkMixin, TimestampMixin, Base):
    """One admission = one order item = one ticket. Seat/price are snapshotted here."""

    __tablename__ = "order_item"
    __table_args__ = (CheckConstraint("unit_price_minor >= 0", name="unit_price_non_negative"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticket_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ticket_type.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    seat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seat.id"))
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    section: Mapped[str | None] = mapped_column(String(100))
    row_label: Mapped[str | None] = mapped_column(String(20))
    seat_number: Mapped[str | None] = mapped_column(String(10))

    order: Mapped[Order] = relationship(back_populates="items")
    attendee: Mapped["Attendee | None"] = relationship(back_populates="order_item", uselist=False)
    ticket: Mapped["Ticket | None"] = relationship(back_populates="order_item", uselist=False)


class Attendee(UUIDPkMixin, TimestampMixin, Base):
    """Attendee details captured at checkout, one per order item."""

    __tablename__ = "attendee"

    order_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_item.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)

    order_item: Mapped[OrderItem] = relationship(back_populates="attendee")


class Ticket(UUIDPkMixin, TimestampMixin, Base):
    """Issued only after successful payment. Digital and printed copies share `ticket_code`."""

    __tablename__ = "ticket"
    __table_args__ = (Index("ix_ticket_event_id_status", "event_id", "status"),)

    order_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_item.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="RESTRICT"), nullable=False
    )
    attendee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attendee.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    ticket_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    qr_payload: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        str_enum(TicketStatus, "ticket_status"),
        nullable=False,
        default=TicketStatus.VALID,
        server_default="valid",
    )
    issued_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    checked_in_at: Mapped[datetime | None] = mapped_column(TZ)
    pdf_key: Mapped[str | None] = mapped_column(String(500))

    order_item: Mapped[OrderItem] = relationship(back_populates="ticket")
    attendee: Mapped[Attendee] = relationship()
