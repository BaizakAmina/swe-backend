"""Event staff (event admins for the mobile scanner, SRS §4.8)."""

import uuid

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import Event, StaffAssignment, Ticket, User
from app.models.enums import EventStatus, StaffRole, TicketStatus, UserStatus
from app.schemas.staff import AssignedEventRead, StaffAssignCreate, StaffRead
from app.services import audit, notifications
from app.services.audit import AuditAction


def _to_read(a: StaffAssignment) -> StaffRead:
    return StaffRead(
        user_id=a.user_id,
        full_name=a.user.full_name,
        email=a.user.email,
        role=a.role,
        assigned_at=a.created_at,
    )


def list_staff(db: Session, event: Event) -> list[StaffRead]:
    rows = db.scalars(
        select(StaffAssignment)
        .where(StaffAssignment.event_id == event.id)
        .order_by(StaffAssignment.created_at)
    )
    return [_to_read(a) for a in rows]


def assign(db: Session, actor: User, event: Event, data: StaffAssignCreate) -> StaffRead:
    if event.status == EventStatus.CANCELLED:
        raise ConflictError("event_cancelled", "Cancelled events cannot be changed")
    user = db.scalar(select(User).where(User.email == data.email))
    if user is None:
        raise AppError(
            "user_not_found",
            "No account with this email — ask them to register first",
            404,
        )
    if user.status == UserStatus.SUSPENDED:
        raise ConflictError("user_suspended", "This account is suspended")
    existing = db.scalar(
        select(StaffAssignment).where(
            StaffAssignment.event_id == event.id,
            StaffAssignment.user_id == user.id,
            StaffAssignment.role == data.role,
        )
    )
    if existing is not None:
        return _to_read(existing)

    assignment = StaffAssignment(
        event_id=event.id, user_id=user.id, role=data.role, assigned_by=actor.id
    )
    db.add(assignment)
    db.flush()
    audit.log(
        db,
        actor_id=actor.id,
        action=AuditAction.STAFF_ASSIGNED,
        entity_type="staff_assignment",
        entity_id=assignment.id,
        event_id=event.id,
        description=f"{user.full_name} assigned as {data.role}",
        metadata={"user_id": str(user.id)},
    )
    db.commit()
    db.refresh(assignment)
    notifications.send_staff_assigned(db, user, event)
    return _to_read(assignment)


def remove(db: Session, actor: User, event: Event, user_id: uuid.UUID) -> None:
    assignment = db.scalar(
        select(StaffAssignment).where(
            StaffAssignment.event_id == event.id,
            StaffAssignment.user_id == user_id,
            StaffAssignment.role == StaffRole.EVENT_ADMIN,
        )
    )
    if assignment is None:
        raise NotFoundError("staff_assignment")
    audit.log(
        db,
        actor_id=actor.id,
        action=AuditAction.STAFF_REMOVED,
        entity_type="staff_assignment",
        entity_id=assignment.id,
        event_id=event.id,
        description=f"{assignment.user.full_name} removed from staff",
        metadata={"user_id": str(user_id)},
    )
    db.delete(assignment)
    db.commit()


def assigned_events(db: Session, user: User) -> list[AssignedEventRead]:
    """Events the user scans for (not cancelled), soonest first, with check-in progress."""
    issued = func.count(Ticket.id).filter(
        Ticket.status.in_([TicketStatus.VALID, TicketStatus.CHECKED_IN])
    )
    checked = func.coalesce(
        func.sum(case((Ticket.status == TicketStatus.CHECKED_IN, 1), else_=0)), 0
    )
    rows = db.execute(
        select(Event, issued, checked)
        .join(StaffAssignment, StaffAssignment.event_id == Event.id)
        .outerjoin(Ticket, Ticket.event_id == Event.id)
        .where(
            StaffAssignment.user_id == user.id,
            StaffAssignment.role == StaffRole.EVENT_ADMIN,
            Event.status != EventStatus.CANCELLED,
        )
        .group_by(Event.id)
        .order_by(Event.starts_at)
    ).all()
    return [
        AssignedEventRead(
            id=ev.id,
            title=ev.title,
            starts_at=ev.starts_at,
            ends_at=ev.ends_at,
            timezone=ev.timezone,
            location_name=ev.location_name,
            tickets_issued=int(n_issued),
            checked_in=int(n_checked),
        )
        for ev, n_issued, n_checked in rows
    ]
