import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, StaffedEvent
from app.api.v1._stub import not_implemented
from app.models.enums import TicketStatus
from app.schemas.common import Page
from app.schemas.tickets import (
    AttendeeRow,
    CheckInRequest,
    CheckInResponse,
    CheckInStats,
    TicketRead,
)

router = APIRouter(tags=["tickets"])
checkin_router = APIRouter(tags=["check-in (mobile)"])


@router.get("/me/tickets", response_model=Page[TicketRead])
def my_tickets(
    user: CurrentUser, db: DbSession, upcoming: bool | None = None, limit: int = 20, offset: int = 0
) -> Page[TicketRead]:
    not_implemented("BE-20")


@router.get("/tickets/{ticket_id}", response_model=TicketRead)
def get_ticket(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TicketRead:
    not_implemented("BE-20")


@router.get(
    "/tickets/{ticket_id}/pdf",
    response_model=None,
    responses={200: {"content": {"application/pdf": {}}}},
)
def ticket_pdf(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> bytes:
    not_implemented("BE-21")


@router.get(
    "/tickets/{ticket_id}/qr.png",
    response_model=None,
    responses={200: {"content": {"image/png": {}}}},
)
def ticket_qr(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> bytes:
    not_implemented("BE-21")


@router.get("/events/{event_id}/attendees", response_model=Page[AttendeeRow])
def event_attendees(
    event: ManagedEvent,
    db: DbSession,
    q: str | None = None,
    ticket_type_id: uuid.UUID | None = None,
    ticket_status: TicketStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[AttendeeRow]:
    not_implemented("BE-20")


@router.get(
    "/events/{event_id}/attendees.csv",
    response_model=None,
    responses={200: {"content": {"text/csv": {}}}},
)
def event_attendees_csv(event: ManagedEvent, db: DbSession) -> str:
    not_implemented("BE-20")


@checkin_router.post("/checkin/validate", response_model=CheckInResponse)
def validate_ticket(data: CheckInRequest, user: CurrentUser, db: DbSession) -> CheckInResponse:
    """Read-only check of a scanned QR. Campaign QR codes are rejected."""
    not_implemented("BE-18")


@checkin_router.post("/checkin", response_model=CheckInResponse)
def check_in(data: CheckInRequest, user: CurrentUser, db: DbSession) -> CheckInResponse:
    not_implemented("BE-18")


@checkin_router.post(
    "/checkin/{ticket_id}/reverse",
    response_model=CheckInResponse,
    status_code=status.HTTP_200_OK,
)
def reverse_check_in(ticket_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CheckInResponse:
    not_implemented("BE-18")


@checkin_router.get("/events/{event_id}/checkin-stats", response_model=CheckInStats)
def checkin_stats(event: StaffedEvent, db: DbSession) -> CheckInStats:
    not_implemented("BE-18")
