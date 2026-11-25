import uuid

from pydantic import BaseModel


class PriceCategoryCount(BaseModel):
    name: str
    seats: int


class VenueSummary(BaseModel):
    """A predefined hall that assigned-seating events can use (``venue_id`` on the event)."""

    id: uuid.UUID
    name: str
    address: str | None
    city: str | None
    sections: int
    seats_total: int
    accessible_seats: int
    price_categories: list[PriceCategoryCount]


class VenueSeatRead(BaseModel):
    id: uuid.UUID
    seat_number: str
    price_category: str | None
    is_accessible: bool


class VenueRowRead(BaseModel):
    id: uuid.UUID
    label: str
    seats: list[VenueSeatRead]


class VenueSectionRead(BaseModel):
    id: uuid.UUID
    name: str
    rows: list[VenueRowRead]


class VenueRead(VenueSummary):
    """Full layout: sections → rows → seats, in display order."""

    layout: list[VenueSectionRead]
