"""Support cases with async message threads (SRS §4.13, §7).

Access is decided by role **and** relationship (single entry point: :func:`can_access_case`):

- the person who opened the case;
- the organizer of the referenced event (and whoever the case is assigned to);
- platform admins (they also handle cases without an event).

Clients poll ``GET /support/cases/{id}/messages?after=…`` — no WebSockets.
"""

import uuid
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError, ForbiddenError, NotFoundError
from app.models import (
    Event,
    Order,
    OrganizerProfile,
    SupportCase,
    SupportMessage,
    User,
)
from app.models.enums import Role, SupportCaseKind, SupportCategory, SupportStatus
from app.schemas.common import Page, Pagination
from app.schemas.support import (
    SupportCaseCreate,
    SupportCaseDetail,
    SupportCaseRead,
    SupportCaseUpdate,
    SupportMessageRead,
)
from app.services import audit, events, notifications, permissions, views
from app.services.audit import AuditAction


def _now() -> datetime:
    return datetime.now(UTC)


def _unprocessable(code: str, message: str) -> AppError:
    return AppError(code, message, status.HTTP_422_UNPROCESSABLE_ENTITY)


# ------------------------------------------------------------------ access


def _event_organizer_user_id(db: Session, event_id: uuid.UUID | None) -> uuid.UUID | None:
    if event_id is None:
        return None
    return db.scalar(
        select(OrganizerProfile.user_id)
        .join(Event, Event.organizer_id == OrganizerProfile.id)
        .where(Event.id == event_id)
    )


def is_case_staff(db: Session, user: User, case: SupportCase) -> bool:
    """Can answer, assign and change status: event organizer, assignee or platform admin."""
    if permissions.is_platform_admin(user):
        return True
    if case.assigned_to_user_id == user.id:
        return True
    return _event_organizer_user_id(db, case.event_id) == user.id


def can_access_case(db: Session, user: User, case: SupportCase) -> bool:
    return case.opened_by_user_id == user.id or is_case_staff(db, user, case)


def _visible_clause(db: Session, user: User) -> ColumnElement[bool]:
    if permissions.is_platform_admin(user):
        return SupportCase.id.is_not(None)
    my_events = select(Event.id).join(OrganizerProfile).where(OrganizerProfile.user_id == user.id)
    return or_(
        SupportCase.opened_by_user_id == user.id,
        SupportCase.assigned_to_user_id == user.id,
        SupportCase.event_id.in_(my_events),
    )


def load_for_user(db: Session, user: User, case_id: uuid.UUID) -> SupportCase:
    case = db.scalar(
        select(SupportCase)
        .where(SupportCase.id == case_id)
        .options(selectinload(SupportCase.messages))
    )
    if case is None:
        raise NotFoundError("support_case")
    if not can_access_case(db, user, case):
        raise ForbiddenError("You don't have access to this case")
    return case


# ------------------------------------------------------------------ serialization


def _names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {
        row[0]: row[1]
        for row in db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    }


def messages_to_read(db: Session, msgs: list[SupportMessage]) -> list[SupportMessageRead]:
    names = _names(db, {m.sender_user_id for m in msgs})
    return [
        SupportMessageRead(
            id=m.id,
            sender_user_id=m.sender_user_id,
            sender_name=names.get(m.sender_user_id, ""),
            body=m.body,
            sent_at=m.sent_at,
        )
        for m in msgs
    ]


def to_detail(db: Session, case: SupportCase) -> SupportCaseDetail:
    return SupportCaseDetail.model_validate(
        {
            **SupportCaseRead.model_validate(case).model_dump(),
            "messages": messages_to_read(db, sorted(case.messages, key=lambda m: m.sent_at)),
        }
    )


# ------------------------------------------------------------------ actions


def _resolve_context(
    db: Session, user: User, data: SupportCaseCreate
) -> tuple[uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]:
    """Fill event/order from the ticket or order, and check the user is related to them."""
    event_id, order_id, ticket_id = data.event_id, data.order_id, data.ticket_id

    if ticket_id is not None:
        ticket = views.get_ticket_for_user(db, user, ticket_id)
        order_id = order_id or ticket.order_item.order_id
        event_id = event_id or ticket.event_id
        if order_id != ticket.order_item.order_id or event_id != ticket.event_id:
            raise _unprocessable("context_mismatch", "Ticket does not belong to that order/event")

    if order_id is not None:
        order = db.get(Order, order_id)
        if order is None:
            raise NotFoundError("order")
        event_id = event_id or order.event_id
        if order.event_id != event_id:
            raise _unprocessable("context_mismatch", "Order does not belong to that event")
        owner = order.buyer_user_id == user.id
        event = db.get(Event, order.event_id)
        if not owner and not (event and permissions.can_manage_event(db, user, event)):
            raise ForbiddenError("Not your order")

    if event_id is not None:
        events.get_for_viewer(db, event_id, user)  # 404 for drafts/private of others
    return event_id, order_id, ticket_id


def open_case(db: Session, user: User, data: SupportCaseCreate) -> SupportCaseDetail:
    if data.kind == SupportCaseKind.ORGANIZER and Role.ORGANIZER not in user.role_names:
        raise ForbiddenError("Organizer cases can only be opened by organizers")
    event_id, order_id, ticket_id = _resolve_context(db, user, data)

    case = SupportCase(
        opened_by_user_id=user.id,
        kind=data.kind,
        category=data.category,
        subject=data.subject.strip(),
        event_id=event_id,
        order_id=order_id,
        ticket_id=ticket_id,
    )
    db.add(case)
    db.flush()
    db.add(
        SupportMessage(
            case_id=case.id, sender_user_id=user.id, body=data.message.strip(), sent_at=_now()
        )
    )
    audit.log(
        db,
        actor_id=user.id,
        action=AuditAction.SUPPORT_CASE_OPENED,
        entity_type="support_case",
        entity_id=case.id,
        event_id=event_id,
        description=f"Support case opened: {case.subject}",
        metadata={"category": data.category.value},
    )
    db.commit()
    _notify_other_side(db, user, case, data.message)
    return to_detail(db, load_for_user(db, user, case.id))


def list_cases(
    db: Session,
    user: User,
    page: Pagination,
    case_status: SupportStatus | None = None,
    category: SupportCategory | None = None,
    event_id: uuid.UUID | None = None,
) -> Page[SupportCaseRead]:
    stmt: Select[tuple[SupportCase]] = select(SupportCase).where(_visible_clause(db, user))
    if case_status:
        stmt = stmt.where(SupportCase.status == case_status)
    if category:
        stmt = stmt.where(SupportCase.category == category)
    if event_id:
        stmt = stmt.where(SupportCase.event_id == event_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(SupportCase.updated_at.desc()).limit(page.limit).offset(page.offset)
    ).all()
    return Page(
        items=[SupportCaseRead.model_validate(c) for c in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


def update_case(
    db: Session, user: User, case_id: uuid.UUID, data: SupportCaseUpdate
) -> SupportCaseRead:
    case = load_for_user(db, user, case_id)
    changes = data.model_dump(exclude_unset=True)
    staff = is_case_staff(db, user, case)

    if "assigned_to_user_id" in changes:
        if not staff:
            raise ForbiddenError("Only support staff can assign cases")
        assignee_id = changes["assigned_to_user_id"]
        if assignee_id is not None:
            assignee = db.get(User, assignee_id)
            organizer_id = _event_organizer_user_id(db, case.event_id)
            if assignee is None or not (
                permissions.is_platform_admin(assignee) or assignee.id == organizer_id
            ):
                raise _unprocessable(
                    "invalid_assignee", "Cases can be assigned to the organizer or a platform admin"
                )
        case.assigned_to_user_id = assignee_id

    if "status" in changes and changes["status"] is not None:
        new_status = changes["status"]
        # The customer may only close their own case; everything else is for staff.
        if not staff and new_status != SupportStatus.RESOLVED:
            raise ForbiddenError("Only support staff can change the status")
        case.status = new_status
        case.resolved_at = _now() if new_status == SupportStatus.RESOLVED else None

    if changes:
        audit.log(
            db,
            actor_id=user.id,
            action=AuditAction.SUPPORT_CASE_UPDATED,
            entity_type="support_case",
            entity_id=case.id,
            event_id=case.event_id,
            description=f"Support case updated: {', '.join(sorted(changes))}",
            metadata={k: str(v) for k, v in changes.items()},
        )
        db.commit()
    return SupportCaseRead.model_validate(case)


def list_messages(
    db: Session, user: User, case_id: uuid.UUID, after: datetime | None = None
) -> list[SupportMessageRead]:
    case = load_for_user(db, user, case_id)
    stmt = select(SupportMessage).where(SupportMessage.case_id == case.id)
    if after is not None:
        stmt = stmt.where(SupportMessage.sent_at > after)
    msgs = list(db.scalars(stmt.order_by(SupportMessage.sent_at)))
    return messages_to_read(db, msgs)


def post_message(db: Session, user: User, case_id: uuid.UUID, body: str) -> SupportMessageRead:
    case = load_for_user(db, user, case_id)
    staff = is_case_staff(db, user, case) and case.opened_by_user_id != user.id
    msg = SupportMessage(case_id=case.id, sender_user_id=user.id, body=body.strip(), sent_at=_now())
    db.add(msg)
    # Status follows the conversation.
    if staff:
        case.status = SupportStatus.WAITING_CUSTOMER
        if case.assigned_to_user_id is None:
            case.assigned_to_user_id = user.id
    elif case.status in (SupportStatus.WAITING_CUSTOMER, SupportStatus.RESOLVED):
        case.status = SupportStatus.IN_PROGRESS if case.assigned_to_user_id else SupportStatus.OPEN
        case.resolved_at = None
    case.updated_at = _now()
    db.commit()
    db.refresh(msg)
    _notify_other_side(db, user, case, body)
    return messages_to_read(db, [msg])[0]


def _notify_other_side(db: Session, sender: User, case: SupportCase, body: str) -> None:
    if sender.id == case.opened_by_user_id:
        recipient_id = case.assigned_to_user_id or _event_organizer_user_id(db, case.event_id)
    else:
        recipient_id = case.opened_by_user_id
    if recipient_id is None or recipient_id == sender.id:
        return
    recipient = db.get(User, recipient_id)
    if recipient is not None:
        notifications.send_support_reply(db, recipient, sender, case.id, case.subject, body)
