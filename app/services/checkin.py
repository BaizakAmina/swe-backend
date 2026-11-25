"""Check-in at the door (SRS §4.8, §11; BACKEND_ARCHITECTURE.md §5.4).

Hard rules:

- a valid ticket is admitted **once**: ``SELECT … FOR UPDATE`` on the ticket row, then
  ``valid → checked_in``; a parallel or repeated scan sees ``checked_in`` → 409;
- a Campaign QR is never an admission ticket (``campaign_qr_not_admission``);
- forged / foreign QR codes are rejected (``invalid_qr``), tickets of another event too
  (``wrong_event``); cancelled and refunded tickets don't pass.

Who may scan: an event admin assigned to the event, its organizer or a platform admin.
``validate`` never changes anything; ``check_in`` and ``reverse`` write a ``check_in_record``
(append-only scan log) and the audit log.
"""

import uuid
from datetime import UTC, datetime

from fastapi import status as http
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.models import Attendee, CheckInRecord, Event, OrderItem, Ticket, TicketType, User
from app.models.enums import CheckInAction, TicketStatus
from app.schemas.tickets import (
    CheckInByTicketRequest,
    CheckInFeedItem,
    CheckInRequest,
    CheckInResponse,
    CheckInResult,
    CheckInStats,
    CheckInStatsByType,
    CheckInTicketInfo,
)
from app.services import audit, events, permissions
from app.services.audit import AuditAction
from app.services.qr_sign import CampaignQRError, InvalidAdmissionQRError, verify_ticket_payload

MESSAGES: dict[str, str] = {
    "valid": "Valid ticket",
    "already_checked_in": "Ticket already checked in",
    "cancelled": "Ticket is cancelled",
    "refunded": "Ticket was refunded",
    "wrong_event": "Ticket is for another event",
    "invalid_qr": "Not a valid ticket QR code",
    "campaign_qr_not_admission": "This is a promo / campaign QR code, not a ticket",
}

_BY_STATUS: dict[TicketStatus, CheckInResult] = {
    TicketStatus.VALID: "valid",
    TicketStatus.CHECKED_IN: "already_checked_in",
    TicketStatus.CANCELLED: "cancelled",
    TicketStatus.REFUNDED: "refunded",
}


def _staffed_event(db: Session, user: User, event_id: uuid.UUID) -> Event:
    return permissions.ensure_can_staff_event(db, user, permissions.get_event(db, event_id))


def _info(db: Session, ticket: Ticket) -> CheckInTicketInfo:
    item = ticket.order_item
    ticket_type = db.get(TicketType, item.ticket_type_id)
    return CheckInTicketInfo(
        ticket_id=ticket.id,
        attendee_name=ticket.attendee.full_name,
        ticket_type_name=ticket_type.name if ticket_type else "",
        section=item.section,
        row_label=item.row_label,
        seat_number=item.seat_number,
        checked_in_at=ticket.checked_in_at,
    )


def _resolve(
    db: Session, event: Event, qr_payload: str, *, lock: bool
) -> tuple[CheckInResult, Ticket | None]:
    """What a scan of ``qr_payload`` at ``event`` means. ``lock`` → ticket row FOR UPDATE."""
    try:
        code, signed_event_id = verify_ticket_payload(qr_payload)
    except CampaignQRError:
        return "campaign_qr_not_admission", None
    except InvalidAdmissionQRError:
        return "invalid_qr", None

    stmt = select(Ticket).where(Ticket.ticket_code == code)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    ticket = db.scalar(stmt)
    if ticket is None or ticket.event_id != signed_event_id:
        return "invalid_qr", None  # correctly signed, but no such ticket here
    if ticket.event_id != event.id:
        return "wrong_event", None  # don't show another event's attendee to this scanner
    return _BY_STATUS[ticket.status], ticket


def _response(db: Session, result: CheckInResult, ticket: Ticket | None) -> CheckInResponse:
    message = MESSAGES[result]
    if result == "already_checked_in" and ticket and ticket.checked_in_at:
        message = f"{message} at {ticket.checked_in_at.isoformat(timespec='seconds')}"
    return CheckInResponse(
        result=result, message=message, ticket=_info(db, ticket) if ticket else None
    )


def validate(db: Session, user: User, data: CheckInRequest) -> CheckInResponse:
    """Read-only: what would happen on check-in. Always 200; the verdict is ``result``."""
    event = _staffed_event(db, user, data.event_id)
    result, ticket = _resolve(db, event, data.qr_payload, lock=False)
    return _response(db, result, ticket)


def _record(
    db: Session,
    user: User,
    ticket: Ticket,
    action: CheckInAction,
    device_info: str | None,
    *,
    manual: bool = False,
) -> None:
    db.add(
        CheckInRecord(
            ticket_id=ticket.id,
            event_id=ticket.event_id,
            event_admin_user_id=user.id,
            action=action,
            # the moment of the scan itself (not the transaction start): the feed orders by it
            scanned_at=datetime.now(UTC),
            device_info=(device_info or None) and device_info[:200],
        )
    )
    checked_in = action == CheckInAction.CHECK_IN
    what = "Check-in reversed"
    if checked_in:
        what = "Checked in manually (attendee search)" if manual else "Checked in"
    meta: dict[str, object] = {"method": "manual" if manual else "qr"} if checked_in else {}
    if device_info:
        meta["device_info"] = device_info
    audit.log(
        db,
        actor_id=user.id,
        action=AuditAction.TICKET_CHECKED_IN if checked_in else AuditAction.CHECK_IN_REVERSED,
        entity_type="ticket",
        entity_id=ticket.id,
        event_id=ticket.event_id,
        description=f"{what}: {ticket.attendee.full_name} ({ticket.ticket_code})",
        metadata=meta or None,
    )


def _admit(
    db: Session,
    user: User,
    result: CheckInResult,
    ticket: Ticket | None,
    device_info: str | None,
    *,
    manual: bool,
) -> CheckInResponse:
    """``valid`` + a locked ticket → checked in; anything else → the matching error."""
    if result != "valid" or ticket is None:
        unreadable = result in ("invalid_qr", "campaign_qr_not_admission")
        response = _response(db, result, ticket)
        raise AppError(
            result,
            response.message,
            http.HTTP_422_UNPROCESSABLE_ENTITY if unreadable else http.HTTP_409_CONFLICT,
            response.model_dump(mode="json", exclude={"message"}),
        )
    ticket.status = TicketStatus.CHECKED_IN
    ticket.checked_in_at = datetime.now(UTC)
    _record(db, user, ticket, CheckInAction.CHECK_IN, device_info, manual=manual)
    db.commit()
    return CheckInResponse(result="valid", message="Checked in", ticket=_info(db, ticket))


def _ensure_open(event: Event) -> None:
    if not events.check_in_open(event):
        raise ConflictError("check_in_closed", "Check-in is closed for this event")


def check_in(db: Session, user: User, data: CheckInRequest) -> CheckInResponse:
    """Admit the ticket once. Anything but a valid ticket of this event → error.

    409 ``check_in_closed`` outside the scanning window (``events.check_in_open`` — the same
    rule as the scanner's ``can_check_in`` flag). 422 ``invalid_qr`` /
    ``campaign_qr_not_admission``; 409 ``already_checked_in`` (with the
    time of the first scan), ``cancelled``, ``refunded``, ``wrong_event``. ``details`` carries
    the same ``result`` / ``ticket`` as ``validate``.
    """
    event = _staffed_event(db, user, data.event_id)
    _ensure_open(event)
    result, ticket = _resolve(db, event, data.qr_payload, lock=True)
    return _admit(db, user, result, ticket, data.device_info, manual=False)


def check_in_by_ticket(db: Session, user: User, data: CheckInByTicketRequest) -> CheckInResponse:
    """Manual check-in from the attendee search when the QR can't be read (BE-34).

    Same rules, lock and responses as :func:`check_in`; the ticket is picked by id instead of a
    signed QR. A ticket of another event → 409 ``wrong_event`` without its attendee data.
    """
    event = _staffed_event(db, user, data.event_id)
    _ensure_open(event)
    ticket = db.scalar(
        select(Ticket)
        .where(Ticket.id == data.ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ticket is None:
        raise NotFoundError("ticket")
    if ticket.event_id != event.id:
        return _admit(db, user, "wrong_event", None, data.device_info, manual=True)
    return _admit(db, user, _BY_STATUS[ticket.status], ticket, data.device_info, manual=True)


def reverse(
    db: Session, user: User, ticket_id: uuid.UUID, device_info: str | None = None
) -> CheckInResponse:
    """Undo a mistaken check-in: ``checked_in → valid``."""
    ticket = db.scalar(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ticket is None:
        raise NotFoundError("ticket")
    event = permissions.get_event(db, ticket.event_id)
    if not permissions.can_reverse_check_in(db, user, event):
        raise ForbiddenError("You may not undo check-ins for this event")
    if ticket.status != TicketStatus.CHECKED_IN:
        raise ConflictError("not_checked_in", f"Ticket is {ticket.status.value}, not checked in")

    ticket.status = TicketStatus.VALID
    ticket.checked_in_at = None
    _record(db, user, ticket, CheckInAction.REVERSE, device_info)
    db.commit()
    return CheckInResponse(result="valid", message="Check-in reversed", ticket=_info(db, ticket))


FEED_MAX = 200


def feed(db: Session, event: Event, limit: int = 50) -> list[CheckInFeedItem]:
    """Latest check-ins and undos of the event, newest first (BE-35). One query, no N+1."""
    limit = max(1, min(limit, FEED_MAX))
    rows = db.execute(
        select(
            CheckInRecord.ticket_id,
            Attendee.full_name,
            User.full_name,
            CheckInRecord.scanned_at,
            CheckInRecord.action,
        )
        .join(Ticket, Ticket.id == CheckInRecord.ticket_id)
        .join(Attendee, Attendee.id == Ticket.attendee_id)
        .join(User, User.id == CheckInRecord.event_admin_user_id)
        .where(CheckInRecord.event_id == event.id)
        .order_by(CheckInRecord.scanned_at.desc(), CheckInRecord.created_at.desc())
        .limit(limit)
    ).tuples()
    return [
        CheckInFeedItem(
            ticket_id=ticket_id, attendee_name=attendee, actor_name=actor, at=at, action=action
        )
        for ticket_id, attendee, actor, at, action in rows
    ]


def stats(db: Session, event: Event) -> CheckInStats:
    """Issued = valid + checked in (cancelled / refunded tickets don't count)."""
    issued = func.count(Ticket.id)
    checked_in = func.count(case((Ticket.status == TicketStatus.CHECKED_IN, Ticket.id)))
    active = Ticket.status.in_((TicketStatus.VALID, TicketStatus.CHECKED_IN))
    rows = db.execute(
        select(TicketType.id, TicketType.name, issued, checked_in)
        .select_from(TicketType)
        .outerjoin(OrderItem, OrderItem.ticket_type_id == TicketType.id)
        .outerjoin(Ticket, (Ticket.order_item_id == OrderItem.id) & active)
        .where(TicketType.event_id == event.id)
        .group_by(TicketType.id, TicketType.name, TicketType.created_at)
        .order_by(TicketType.created_at, TicketType.name)
    ).all()
    by_type = [
        CheckInStatsByType(ticket_type_id=tt_id, name=name, issued=n, checked_in=c)
        for tt_id, name, n, c in rows
    ]
    total = sum(t.issued for t in by_type)
    done = sum(t.checked_in for t in by_type)
    return CheckInStats(
        event_id=event.id,
        issued=total,
        checked_in=done,
        remaining=total - done,
        by_ticket_type=by_type,
    )
