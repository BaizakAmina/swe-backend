"""Event activity timeline built from the append-only audit log (SRS §4.16)."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Event, User
from app.schemas.common import Page, Pagination
from app.schemas.events import TimelineEntry


def event_timeline(
    db: Session,
    event: Event,
    page: Pagination,
    action_type: str | None = None,
    actor_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Page[TimelineEntry]:
    """Newest first. ``action_type`` matches exactly (``event.published``) or by module
    prefix (``ticket_type`` → every ``ticket_type.*``)."""
    stmt = select(AuditLog, User.full_name).outerjoin(User, User.id == AuditLog.actor_user_id)
    stmt = stmt.where(AuditLog.event_id == event.id)
    if action_type:
        if "." in action_type:
            stmt = stmt.where(AuditLog.action_type == action_type)
        else:
            stmt = stmt.where(AuditLog.action_type.like(f"{action_type}.%"))
    if actor_id:
        stmt = stmt.where(AuditLog.actor_user_id == actor_id)
    if date_from:
        stmt = stmt.where(AuditLog.occurred_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.occurred_at <= date_to)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(AuditLog.occurred_at.desc()).limit(page.limit).offset(page.offset)
    ).all()
    items = [
        TimelineEntry(
            id=entry.id,
            occurred_at=entry.occurred_at,
            action_type=entry.action_type,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            description=entry.description,
            actor_id=entry.actor_user_id,
            actor_name=actor_name,
            metadata=entry.metadata_,
        )
        for entry, actor_name in rows
    ]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)
