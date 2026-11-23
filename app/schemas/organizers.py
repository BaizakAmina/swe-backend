import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import PayoutAccountStatus, VerificationStatus
from app.schemas.common import ORMModel


class OrganizerProfileCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=32)


class OrganizerProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=32)


class OrganizerProfileRead(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    contact_email: str | None
    contact_phone: str | None
    verification_status: VerificationStatus
    created_at: datetime


class PayoutAccountCreate(BaseModel):
    """Sandbox reference only — no card numbers, IBANs or other bank data."""

    provider_ref: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_\-]+$")


class PayoutAccountUpdate(BaseModel):
    status: PayoutAccountStatus


class PayoutAccountRead(ORMModel):
    id: uuid.UUID
    provider_ref: str
    currency: str
    status: PayoutAccountStatus
    created_at: datetime


class ActivationChecklist(BaseModel):
    profile_complete: bool
    verified: bool
    payout_account_connected: bool

    @property
    def ready(self) -> bool:
        return self.profile_complete and self.verified and self.payout_account_connected
