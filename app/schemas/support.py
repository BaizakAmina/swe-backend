import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import SupportCaseKind, SupportCategory, SupportStatus
from app.schemas.common import ORMModel


class SupportCaseCreate(BaseModel):
    kind: SupportCaseKind = SupportCaseKind.ATTENDEE
    category: SupportCategory
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=5000)
    event_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    ticket_id: uuid.UUID | None = None


class SupportCaseUpdate(BaseModel):
    status: SupportStatus | None = None
    assigned_to_user_id: uuid.UUID | None = None


class SupportMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class SupportMessageRead(ORMModel):
    id: uuid.UUID
    sender_user_id: uuid.UUID
    sender_name: str
    body: str
    sent_at: datetime


class SupportCaseRead(ORMModel):
    id: uuid.UUID
    opened_by_user_id: uuid.UUID
    kind: SupportCaseKind
    category: SupportCategory
    subject: str
    status: SupportStatus
    event_id: uuid.UUID | None
    order_id: uuid.UUID | None
    ticket_id: uuid.UUID | None
    assigned_to_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class SupportCaseDetail(SupportCaseRead):
    messages: list[SupportMessageRead]
