import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.models.enums import StaffRole


class StaffAssignCreate(BaseModel):
    email: EmailStr
    role: StaffRole = StaffRole.EVENT_ADMIN


class StaffRead(BaseModel):
    user_id: uuid.UUID
    full_name: str
    email: str
    role: StaffRole
    assigned_at: datetime


class AssignedEventRead(BaseModel):
    """Event card for the mobile scanner."""

    id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    location_name: str | None
    tickets_issued: int
    checked_in: int
