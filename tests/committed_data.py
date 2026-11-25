"""Cleanup for tests that need real, committed data and parallel transactions.

The regular tests run inside a rolled-back transaction (``conftest.db``). Concurrency tests
can't: every thread needs its own connection and must see what the others committed.
"""

import uuid

from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine

from app.models import (
    Attendee,
    AuditLog,
    CheckInRecord,
    Event,
    Notification,
    Order,
    OrderItem,
    OrganizerProfile,
    Payment,
    PayoutAccount,
    StaffAssignment,
    Ticket,
    TicketType,
    User,
    UserRole,
)


def cleanup(eng: Engine, event_id: uuid.UUID, user_ids: list[uuid.UUID]) -> None:
    """Delete everything a concurrency test committed for ``event_id`` and ``user_ids``.

    ``audit_log`` is append-only (trigger) and references users, so the delete runs with
    triggers off for this one transaction (needs the DB owner/superuser — as in CI and the
    docker-compose database). Children are deleted explicitly because FK cascades are
    triggers too.
    """
    with eng.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        order_ids = select(Order.id).where(Order.event_id == event_id)
        item_ids = select(OrderItem.id).where(OrderItem.order_id.in_(order_ids))
        conn.execute(delete(AuditLog).where(AuditLog.event_id == event_id))
        conn.execute(delete(AuditLog).where(AuditLog.actor_user_id.in_(user_ids)))
        conn.execute(delete(Notification).where(Notification.user_id.in_(user_ids)))
        conn.execute(delete(CheckInRecord).where(CheckInRecord.event_id == event_id))
        conn.execute(delete(Ticket).where(Ticket.event_id == event_id))
        conn.execute(delete(Payment).where(Payment.order_id.in_(order_ids)))
        conn.execute(delete(Payment).where(Payment.event_id == event_id))
        conn.execute(delete(Attendee).where(Attendee.order_item_id.in_(item_ids)))
        conn.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
        conn.execute(delete(Order).where(Order.event_id == event_id))
        conn.execute(delete(TicketType).where(TicketType.event_id == event_id))
        conn.execute(delete(StaffAssignment).where(StaffAssignment.event_id == event_id))
        conn.execute(delete(Event).where(Event.id == event_id))
        profile_ids = select(OrganizerProfile.id).where(OrganizerProfile.user_id.in_(user_ids))
        conn.execute(delete(PayoutAccount).where(PayoutAccount.organizer_id.in_(profile_ids)))
        conn.execute(delete(OrganizerProfile).where(OrganizerProfile.user_id.in_(user_ids)))
        conn.execute(delete(UserRole).where(UserRole.user_id.in_(user_ids)))
        conn.execute(delete(User).where(User.id.in_(user_ids)))
