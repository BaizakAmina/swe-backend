import pytest
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from app.models import AuditLog
from app.services import audit
from app.services.audit import AuditAction
from tests.factories import make_event, make_user

pytestmark = pytest.mark.db


def test_log_is_part_of_callers_transaction(db: Session) -> None:
    user = make_user(db)
    event = make_event(db)

    with db.begin_nested() as sp:
        audit.log(
            db,
            actor_id=user.id,
            action=AuditAction.EVENT_PUBLISHED,
            entity_type="event",
            entity_id=event.id,
            event_id=event.id,
            description="Published",
            metadata={"from": "draft"},
        )
        db.flush()
        sp.rollback()

    assert db.scalars(select(AuditLog).where(AuditLog.entity_id == event.id)).all() == []

    audit.log(
        db,
        actor_id=user.id,
        action=AuditAction.EVENT_PUBLISHED,
        entity_type="event",
        entity_id=event.id,
        event_id=event.id,
        description="Published",
        metadata={"from": "draft"},
    )
    db.flush()
    entry = db.scalars(select(AuditLog).where(AuditLog.entity_id == event.id)).one()
    assert entry.action_type == "event.published"
    assert entry.metadata_ == {"from": "draft"}
    assert entry.event_id == event.id


def test_log_does_not_commit(db: Session, connection: Connection) -> None:
    user = make_user(db)
    audit.log(
        db,
        actor_id=None,
        action="system.test",
        entity_type="user",
        entity_id=user.id,
        description="system",
    )
    # Nothing was flushed by log() itself.
    assert db.new
