import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import EventStatus, Role, UserStatus


class SuspendRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdminUserRead(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    status: UserStatus
    roles: list[Role]
    email_verified: bool
    suspended_reason: str | None
    created_at: datetime


class AdminEventRead(BaseModel):
    id: uuid.UUID
    title: str
    organizer_id: uuid.UUID
    organizer_name: str
    status: EventStatus
    starts_at: datetime
    paid_sales_active: bool
    suspended_at: datetime | None
    suspended_reason: str | None


class PlatformSettingsRead(BaseModel):
    activation_fee_minor: int
    currency: str = "KZT"


class PlatformSettingsUpdate(BaseModel):
    activation_fee_minor: int = Field(ge=0)
