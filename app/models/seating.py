import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TZ, TimestampMixin, UUIDPkMixin


class SeatHold(UUIDPkMixin, TimestampMixin, Base):
    """Temporary hold on a seat for one event.

    At most one *active* hold (``released_at IS NULL``) per seat and event is guaranteed by a
    partial unique index. ``now()`` cannot be used in an index predicate (not IMMUTABLE), so
    expired holds must get ``released_at`` set — by the sweep job and, before inserting a new
    hold, inside the same transaction.
    """

    __tablename__ = "seat_hold"
    __table_args__ = (
        Index(
            "uq_seat_hold_active",
            "seat_id",
            "event_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
        Index(
            "ix_seat_hold_expires_at_active",
            "expires_at",
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    seat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("seat.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    held_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(TZ)
