import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, StaffedEvent
from app.models.enums import TicketStatus
from app.schemas.common import Page, Pagination
from app.schemas.tickets import (
    AttendeeRow,
    CheckInRequest,
    CheckInResponse,
    CheckInStats,
    TicketRead,
)
from app.services import checkin, pdf, qr_image, views

router = APIRouter(tags=["tickets"])
checkin_router = APIRouter(tags=["check-in (mobile)"])


@router.get("/me/tickets", response_model=Page[TicketRead])
def my_tickets(
    user: CurrentUser, db: DbSession, upcoming: bool | None = None, limit: int = 20, offset: int = 0
) -> Page[TicketRead]:
    """Tickets I bought or that were issued to my email. ``?upcoming=true`` for the wallet."""
    page = Pagination(limit=max(1, min(limit, 100)), offset=max(offset, 0))
    return views.my_tickets(db, user, page, upcoming)


@router.get("/tickets/{ticket_id}", response_model=TicketRead)
def get_ticket(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TicketRead:
    """Buyer, attendee (same email), event organizer or platform admin. No payment data."""
    return views.ticket_to_read(db, views.get_ticket_for_user(db, user, ticket_id))


@router.get(
    "/tickets/{ticket_id}/pdf",
    response_model=None,
    responses={200: {"content": {"application/pdf": {}}}},
)
def ticket_pdf(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """A4 printable ticket with the same code/QR as the digital one. Cancelled/refunded → 410."""
    ticket = views.get_ticket_for_user(db, user, ticket_id)
    return Response(
        pdf.get_or_create_ticket_pdf(db, ticket),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="ticket-{ticket.ticket_code}.pdf"'},
    )


@router.get(
    "/tickets/{ticket_id}/qr.png",
    response_model=None,
    responses={200: {"content": {"image/png": {}}}},
)
def ticket_qr(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    ticket = views.get_ticket_for_user(db, user, ticket_id)
    return Response(
        qr_image.make_png(ticket.qr_payload),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/events/{event_id}/attendees", response_model=Page[AttendeeRow])
def event_attendees(
    event: StaffedEvent,
    db: DbSession,
    q: str | None = None,
    ticket_type_id: uuid.UUID | None = None,
    ticket_status: TicketStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[AttendeeRow]:
    """Search by name, email or ticket code; filter by ticket type and status.

    Open to the event's assigned event admins too (door staff look people up when a QR
    does not scan). The CSV export stays organizer-only.
    """
    page = Pagination(limit=max(1, min(limit, 200)), offset=max(offset, 0))
    return views.attendees(db, event, page, q, ticket_type_id, ticket_status)


@router.get(
    "/events/{event_id}/attendees.csv",
    response_model=None,
    responses={200: {"content": {"text/csv": {}}}},
)
def event_attendees_csv(event: ManagedEvent, db: DbSession) -> Response:
    return Response(
        views.attendees_csv(db, event),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="attendees-{event.id}.csv"'},
    )


@checkin_router.post("/checkin/validate", response_model=CheckInResponse)
def validate_ticket(data: CheckInRequest, user: CurrentUser, db: DbSession) -> CheckInResponse:
    """Read-only check of a scanned QR. Campaign QR codes are rejected.

    Always 200 — the verdict is ``result``. Assigned event admin, organizer or platform admin.
    """
    return checkin.validate(db, user, data)


@checkin_router.post("/checkin", response_model=CheckInResponse)
def check_in(data: CheckInRequest, user: CurrentUser, db: DbSession) -> CheckInResponse:
    """Admit a ticket once (``result: valid``).

    409 ``check_in_closed`` when scanning isn't open (same rule as ``can_check_in`` on
    ``/me/assigned-events``). Other errors (``error.code`` = the ``result``;
    ``error.details`` has ``result`` and ``ticket``):
    409 ``already_checked_in`` (message has the time of the first scan), ``cancelled``,
    ``refunded``, ``wrong_event``; 422 ``invalid_qr``, ``campaign_qr_not_admission``.
    """
    return checkin.check_in(db, user, data)


@checkin_router.post(
    "/checkin/{ticket_id}/reverse",
    response_model=CheckInResponse,
    status_code=status.HTTP_200_OK,
)
def reverse_check_in(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CheckInResponse:
    """Undo a mistaken check-in (``checked_in → valid``). 409 ``not_checked_in`` otherwise."""
    return checkin.reverse(db, user, ticket_id)


@checkin_router.get("/events/{event_id}/checkin-stats", response_model=CheckInStats)
def checkin_stats(event: StaffedEvent, db: DbSession) -> CheckInStats:
    """Issued (valid + checked in), checked in, remaining — total and per ticket type."""
    return checkin.stats(db, event)
