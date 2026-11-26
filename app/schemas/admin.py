import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import EventStatus, OrderStatus, Role, UserStatus


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
    reservation_ttl_minutes: int
    seat_hold_ttl_minutes: int
    max_seat_holds_per_user: int
    paid_terms_version: str
    currency: str = "KZT"


class PlatformSettingsUpdate(BaseModel):
    """Only the fields sent are changed."""

    activation_fee_minor: int | None = Field(default=None, ge=0, le=100_000_000)
    reservation_ttl_minutes: int | None = Field(default=None, ge=1, le=120)
    seat_hold_ttl_minutes: int | None = Field(default=None, ge=1, le=120)
    max_seat_holds_per_user: int | None = Field(default=None, ge=1, le=50)
    paid_terms_version: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$"
    )


class ReportedEventRead(BaseModel):
    """An event people opened support cases about."""

    event_id: uuid.UUID
    title: str
    organizer_id: uuid.UUID
    organizer_name: str
    status: EventStatus
    suspended: bool
    open_cases: int
    total_cases: int
    last_reported_at: datetime


class PromoActivityRead(BaseModel):
    """One promo-code use (redemption) on an order."""

    id: uuid.UUID
    redeemed_at: datetime
    released: bool
    code: str
    campaign_id: uuid.UUID
    campaign_name: str
    event_id: uuid.UUID
    event_title: str
    order_id: uuid.UUID
    order_status: OrderStatus
    buyer_email: str
    discount_minor: int
