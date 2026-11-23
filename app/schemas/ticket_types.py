import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.models.enums import TicketKind
from app.schemas.common import ORMModel


class TicketTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    kind: TicketKind
    price_minor: int = Field(default=0, ge=0, description="KZT minor units; 0 for free")
    quantity_total: int = Field(gt=0, le=1_000_000)
    max_per_order: int = Field(default=10, gt=0, le=100)
    sales_start_at: AwareDatetime | None = None
    sales_end_at: AwareDatetime | None = None
    is_hidden: bool = False

    @model_validator(mode="after")
    def _rules(self) -> "TicketTypeCreate":
        if self.kind == TicketKind.FREE and self.price_minor != 0:
            raise ValueError("free tickets must have price_minor = 0")
        if self.kind == TicketKind.PAID and self.price_minor <= 0:
            raise ValueError("paid tickets must have price_minor > 0")
        if self.sales_start_at and self.sales_end_at and self.sales_end_at <= self.sales_start_at:
            raise ValueError("sales_end_at must be after sales_start_at")
        return self


class TicketTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    price_minor: int | None = Field(default=None, ge=0)
    quantity_total: int | None = Field(default=None, gt=0, le=1_000_000)
    max_per_order: int | None = Field(default=None, gt=0, le=100)
    sales_start_at: AwareDatetime | None = None
    sales_end_at: AwareDatetime | None = None


class TicketTypeRead(ORMModel):
    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    description: str | None
    kind: TicketKind
    price_minor: int
    currency: str
    quantity_total: int
    quantity_available: int
    max_per_order: int
    sales_start_at: datetime | None
    sales_end_at: datetime | None
    is_hidden: bool
    on_sale: bool
    sellable: bool = Field(description="false for paid tickets until paid sales are activated")


class TicketTypeStats(TicketTypeRead):
    """Organizer view with counters (read-only here; changed only by orders)."""

    quantity_sold: int
    quantity_reserved: int
    quantity_refunded: int
    quantity_checked_in: int
