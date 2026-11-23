import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SeatRead(BaseModel):
    id: uuid.UUID
    seat_number: str
    price_category: str | None
    is_accessible: bool
    status: Literal["available", "held", "mine", "sold"]


class RowRead(BaseModel):
    id: uuid.UUID
    label: str
    seats: list[SeatRead]


class SectionRead(BaseModel):
    id: uuid.UUID
    name: str
    rows: list[RowRead]


class SeatMapRead(BaseModel):
    event_id: uuid.UUID
    venue_id: uuid.UUID
    venue_name: str
    sections: list[SectionRead]


class SeatHoldCreate(BaseModel):
    seat_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)


class SeatHoldRead(BaseModel):
    id: uuid.UUID
    seat_id: uuid.UUID
    expires_at: datetime
