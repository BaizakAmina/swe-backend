import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.enums import TicketStatus
from app.schemas.common import ORMModel


class TicketEventInfo(BaseModel):
    id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    location_name: str | None
    location_address: str | None


class TicketRead(ORMModel):
    """No payment data here — tickets are shown to door staff and printed."""

    id: uuid.UUID
    ticket_code: str
    qr_payload: str
    status: TicketStatus
    issued_at: datetime
    checked_in_at: datetime | None
    ticket_type_name: str
    attendee_name: str
    attendee_email: str
    section: str | None
    row_label: str | None
    seat_number: str | None
    order_id: uuid.UUID
    event: TicketEventInfo
    pdf_url: str


class AttendeeRow(BaseModel):
    ticket_id: uuid.UUID
    attendee_name: str
    attendee_email: str
    ticket_type_name: str
    ticket_status: TicketStatus
    order_id: uuid.UUID
    checked_in_at: datetime | None


# ---- check-in (mobile scanner) ----


class CheckInRequest(BaseModel):
    event_id: uuid.UUID
    qr_payload: str
    device_info: str | None = None


CheckInResult = Literal[
    "valid",
    "already_checked_in",
    "cancelled",
    "refunded",
    "wrong_event",
    "invalid_qr",
    "campaign_qr_not_admission",
]


class CheckInTicketInfo(BaseModel):
    ticket_id: uuid.UUID
    attendee_name: str
    ticket_type_name: str
    section: str | None
    row_label: str | None
    seat_number: str | None
    checked_in_at: datetime | None


class CheckInResponse(BaseModel):
    result: CheckInResult
    message: str
    ticket: CheckInTicketInfo | None = None


class CheckInStatsByType(BaseModel):
    ticket_type_id: uuid.UUID
    name: str
    issued: int
    checked_in: int


class CheckInStats(BaseModel):
    event_id: uuid.UUID
    issued: int
    checked_in: int
    remaining: int
    by_ticket_type: list[CheckInStatsByType]
