import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.models.enums import CampaignStatus, DiscountType
from app.schemas.common import ORMModel
from app.schemas.orders import OrderLineIn


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    discount_type: DiscountType
    discount_value: int = Field(gt=0, description="percent 1–100, or fixed KZT minor units")
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    max_redemptions: int | None = Field(default=None, gt=0)
    ticket_type_ids: list[uuid.UUID] = Field(
        default_factory=list, description="Empty = all ticket types of the event"
    )
    code: str | None = Field(
        default=None,
        min_length=4,
        max_length=32,
        pattern=r"^[A-Z0-9]+$",
        description="Generated if omitted",
    )

    @model_validator(mode="after")
    def _rules(self) -> "CampaignCreate":
        if self.discount_type == DiscountType.PERCENT and self.discount_value > 100:
            raise ValueError("percent discount must be 1–100")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class CampaignUpdate(BaseModel):
    """Only the fields sent change. Orders already placed keep the discount they got."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    discount_type: DiscountType | None = None
    discount_value: int | None = Field(
        default=None, gt=0, description="percent 1–100, or fixed KZT minor units"
    )
    code: str | None = Field(
        default=None,
        min_length=4,
        max_length=32,
        pattern=r"^[A-Z0-9]+$",
        description="New promo code; the old one stops working, the Campaign QR keeps working",
    )
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    max_redemptions: int | None = Field(default=None, gt=0)
    ticket_type_ids: list[uuid.UUID] | None = None


class CampaignRead(ORMModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    discount_type: DiscountType
    discount_value: int
    starts_at: datetime | None
    ends_at: datetime | None
    max_redemptions: int | None
    redemption_count: int
    remaining: int | None
    status: CampaignStatus
    ticket_type_ids: list[uuid.UUID]
    code: str
    qr_link: str
    created_at: datetime


class CampaignResolveRead(BaseModel):
    """Public: what a Campaign QR link points to (never a discount amount)."""

    event_id: uuid.UUID
    code: str


class PromoValidateRequest(BaseModel):
    event_id: uuid.UUID
    code: str = Field(max_length=32)
    items: list[OrderLineIn] = Field(min_length=1)


class PromoValidateResponse(BaseModel):
    """Preview only — nothing is redeemed."""

    valid: bool
    error_code: str | None = None
    message: str | None = None
    subtotal_minor: int
    discount_minor: int
    total_minor: int
    currency: str = "KZT"
