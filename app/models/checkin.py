import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TZ, TimestampMixin, UUIDPkMixin, str_enum
from app.models.enums import CheckInAction


class CheckInRecord(UUIDPkMixin, TimestampMixin, Base):
    """Append-only log of scans. "Checked in once" is enforced via `ticket.status` under lock."""

    __tablename__ = "check_in_record"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_admin_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id"), nullable=False
    )
    action: Mapped[CheckInAction] = mapped_column(
        str_enum(CheckInAction, "check_in_action"), nullable=False
    )
    scanned_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    device_info: Mapped[str | None] = mapped_column(String(200))
