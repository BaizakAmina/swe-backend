"""Authorization rules (SRS §7, BACKEND_ARCHITECTURE.md §5.5).

Single source of truth for "who may do what with an event". Routers use the dependencies in
``app.api.deps``; services that receive another id (ticket type, campaign, order …) load the
event and call these functions directly.

Policy: an authenticated user without access gets **403** (``forbidden``). Unknown ids → 404.
"""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, NotFoundError
from app.models import Event, OrganizerProfile, StaffAssignment, User
from app.models.enums import Role, StaffRole


def is_platform_admin(user: User) -> bool:
    return Role.PLATFORM_ADMIN in user.role_names


def organizer_profile_of(db: Session, user: User) -> OrganizerProfile | None:
    return db.scalar(select(OrganizerProfile).where(OrganizerProfile.user_id == user.id))


def is_event_organizer(db: Session, user: User, event: Event) -> bool:
    """User owns the event (via their organizer profile)."""
    return bool(
        db.scalar(
            select(
                exists().where(
                    OrganizerProfile.id == event.organizer_id, OrganizerProfile.user_id == user.id
                )
            )
        )
    )


def is_assigned_event_admin(db: Session, user: User, event_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    StaffAssignment.event_id == event_id,
                    StaffAssignment.user_id == user.id,
                    StaffAssignment.role == StaffRole.EVENT_ADMIN,
                )
            )
        )
    )


def can_manage_event(db: Session, user: User, event: Event) -> bool:
    """Edit event, ticket types, campaigns, refunds; see orders, analytics, history."""
    return is_platform_admin(user) or is_event_organizer(db, user, event)


def can_staff_event(db: Session, user: User, event: Event) -> bool:
    """Check-in / scanner access: assigned event admin, the organizer, or platform admin."""
    return can_manage_event(db, user, event) or is_assigned_event_admin(db, user, event.id)


def get_event(db: Session, event_id: uuid.UUID) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise NotFoundError("event")
    return event


def ensure_can_manage_event(db: Session, user: User, event: Event) -> Event:
    if not can_manage_event(db, user, event):
        raise ForbiddenError("Only the event organizer can do this")
    return event


def ensure_can_staff_event(db: Session, user: User, event: Event) -> Event:
    if not can_staff_event(db, user, event):
        raise ForbiddenError("You are not assigned to this event")
    return event
