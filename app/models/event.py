import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TZ, TimestampMixin, UUIDPkMixin, str_enum
from app.models.enums import EventStatus, EventVisibility, SeatingMode, TicketKind
from app.models.organizer import OrganizerProfile


class Event(UUIDPkMixin, TimestampMixin, Base):
    """Lifecycle (upcoming/active/completed/cancelled) is derived from status + dates."""

    __tablename__ = "event"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
        Index("ix_event_status_starts_at", "status", "starts_at"),
    )

    organizer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizer_profile.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50), index=True)
    cover_image_key: Mapped[str | None] = mapped_column(String(500))
    venue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("venue.id"))
    location_name: Mapped[str | None] = mapped_column(String(200))
    location_address: Mapped[str | None] = mapped_column(String(500))
    starts_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Almaty", server_default="Asia/Almaty"
    )
    visibility: Mapped[EventVisibility] = mapped_column(
        str_enum(EventVisibility, "event_visibility"),
        nullable=False,
        default=EventVisibility.PUBLIC,
        server_default="public",
    )
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    seating_mode: Mapped[SeatingMode] = mapped_column(
        str_enum(SeatingMode, "seating_mode"),
        nullable=False,
        default=SeatingMode.GENERAL_ADMISSION,
        server_default="general_admission",
    )
    status: Mapped[EventStatus] = mapped_column(
        str_enum(EventStatus, "event_status"),
        nullable=False,
        default=EventStatus.DRAFT,
        server_default="draft",
    )
    paid_sales_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    registration_opens_at: Mapped[datetime | None] = mapped_column(TZ)
    registration_closes_at: Mapped[datetime | None] = mapped_column(TZ)
    published_at: Mapped[datetime | None] = mapped_column(TZ)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZ)
    # Platform-admin moderation: a suspended event is hidden and cannot sell.
    suspended_at: Mapped[datetime | None] = mapped_column(TZ)
    suspended_reason: Mapped[str | None] = mapped_column(String(500))
    # Incremented on each change; used as SEQUENCE in .ics export.
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    organizer: Mapped[OrganizerProfile] = relationship()
    ticket_types: Mapped[list["TicketType"]] = relationship(
        back_populates="event", order_by="TicketType.created_at"
    )


class TicketType(UUIDPkMixin, TimestampMixin, Base):
    """`quantity_sold` / `quantity_reserved` change only in the orders service, under row lock."""

    __tablename__ = "ticket_type"
    __table_args__ = (
        CheckConstraint(
            "quantity_sold + quantity_reserved <= quantity_total", name="inventory_not_oversold"
        ),
        CheckConstraint(
            "quantity_total >= 0 AND quantity_sold >= 0 AND quantity_reserved >= 0",
            name="quantities_non_negative",
        ),
        CheckConstraint(
            "(kind = 'free' AND price_minor = 0) OR (kind = 'paid' AND price_minor > 0)",
            name="price_matches_kind",
        ),
        CheckConstraint("max_per_order > 0", name="max_per_order_positive"),
        CheckConstraint(
            "sales_end_at IS NULL OR sales_start_at IS NULL OR sales_end_at > sales_start_at",
            name="sales_window_valid",
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[TicketKind] = mapped_column(str_enum(TicketKind, "ticket_kind"), nullable=False)
    price_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KZT")
    quantity_total: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_sold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    quantity_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_per_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default=text("10")
    )
    sales_start_at: Mapped[datetime | None] = mapped_column(TZ)
    sales_end_at: Mapped[datetime | None] = mapped_column(TZ)
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    event: Mapped[Event] = relationship(back_populates="ticket_types")

    @property
    def quantity_available(self) -> int:
        return self.quantity_total - self.quantity_sold - self.quantity_reserved
