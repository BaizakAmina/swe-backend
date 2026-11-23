import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TZ, UUIDPkMixin


class AuditLog(UUIDPkMixin, Base):
    """Append-only. UPDATE/DELETE are blocked by a trigger (see migration)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity_type", "entity_id", "occurred_at"),
        Index("ix_audit_log_event_id_occurred_at", "event_id", "occurred_at"),
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Denormalized for the event activity timeline (no FK: history outlives the event row).
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
