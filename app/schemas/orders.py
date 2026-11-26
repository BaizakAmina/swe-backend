import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import OrderStatus, PaymentPurpose, PaymentStatus, RefundStatus
from app.schemas.common import ORMModel


class OrderLineIn(BaseModel):
    ticket_type_id: uuid.UUID
    quantity: int = Field(gt=0, le=100)


class AttendeeIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr


class OrderCreate(BaseModel):
    """Prices and discounts are always computed on the server."""

    event_id: uuid.UUID
    items: list[OrderLineIn] = Field(min_length=1)
    attendees: list[AttendeeIn] = Field(
        min_length=1, description="One per ticket, in the same order as items × quantity"
    )
    promo_code: str | None = Field(default=None, max_length=32)
    seat_hold_ids: list[uuid.UUID] | None = Field(
        default=None, description="Assigned-seating events only"
    )


class OrderItemRead(ORMModel):
    id: uuid.UUID
    ticket_type_id: uuid.UUID
    ticket_type_name: str
    unit_price_minor: int
    section: str | None
    row_label: str | None
    seat_number: str | None
    attendee_name: str | None
    attendee_email: str | None
    ticket_id: uuid.UUID | None


class OrderRead(ORMModel):
    id: uuid.UUID
    event_id: uuid.UUID
    event_title: str
    buyer_user_id: uuid.UUID
    status: OrderStatus
    currency: str
    subtotal_minor: int
    discount_minor: int
    fees_minor: int
    total_minor: int
    promo_code: str | None
    reserved_until: datetime | None
    paid_at: datetime | None
    created_at: datetime
    items: list[OrderItemRead]


class PayRequest(BaseModel):
    """Payment simulation: choose the outcome for the demo."""

    simulate: Literal["success", "fail"] = "success"


class PaymentRead(ORMModel):
    id: uuid.UUID
    order_id: uuid.UUID | None
    event_id: uuid.UUID | None
    purpose: PaymentPurpose
    amount_minor: int
    currency: str
    status: PaymentStatus
    provider_ref: str | None
    failure_reason: str | None
    is_simulated: bool
    created_at: datetime
    completed_at: datetime | None


class RefundRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class RefundRead(ORMModel):
    id: uuid.UUID
    payment_id: uuid.UUID
    amount_minor: int
    reason: str | None
    status: RefundStatus
    is_simulated: bool
    processed_at: datetime | None


class ActivationStatusRead(BaseModel):
    event_id: uuid.UUID
    paid_sales_active: bool
    fee_minor: int
    currency: str = "KZT"
    profile_complete: bool
    verified: bool
    payout_account_connected: bool
    terms_accepted: bool
    terms_version: str
    terms_url: str
    activation_payment: PaymentRead | None
