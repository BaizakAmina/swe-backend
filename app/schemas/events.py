import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from app.models.enums import EventLifecycle, EventStatus, EventVisibility, SeatingMode
from app.schemas.common import ORMModel


def _check_tz(v: str) -> str:
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown IANA timezone") from exc
    return v


class EventBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    category: str | None = Field(default=None, max_length=50)
    cover_image_key: str | None = None
    refund_policy: str | None = Field(default=None, max_length=2000)
    location_name: str | None = Field(default=None, max_length=200)
    location_address: str | None = Field(default=None, max_length=500)
    venue_id: uuid.UUID | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    timezone: str = "Asia/Almaty"
    visibility: EventVisibility = EventVisibility.PUBLIC
    capacity: int = Field(gt=0, le=1_000_000)
    seating_mode: SeatingMode = SeatingMode.GENERAL_ADMISSION
    registration_opens_at: AwareDatetime | None = None
    registration_closes_at: AwareDatetime | None = None

    _tz = field_validator("timezone")(_check_tz)


class EventCreate(EventBase):
    @model_validator(mode="after")
    def _dates(self) -> "EventCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    category: str | None = Field(default=None, max_length=50)
    cover_image_key: str | None = None
    refund_policy: str | None = Field(default=None, max_length=2000)
    location_name: str | None = Field(default=None, max_length=200)
    location_address: str | None = Field(default=None, max_length=500)
    venue_id: uuid.UUID | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    timezone: str | None = None
    visibility: EventVisibility | None = None
    capacity: int | None = Field(default=None, gt=0, le=1_000_000)
    seating_mode: SeatingMode | None = None
    registration_opens_at: AwareDatetime | None = None
    registration_closes_at: AwareDatetime | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return None if v is None else _check_tz(v)


class EventOrganizer(ORMModel):
    id: uuid.UUID
    display_name: str


class CalendarLinks(BaseModel):
    ics: str
    google: str
    outlook: str


class EventRead(ORMModel):
    id: uuid.UUID
    organizer: EventOrganizer
    title: str
    description: str | None
    category: str | None
    cover_image_key: str | None
    cover_image_url: str | None = None
    refund_policy: str | None
    location_name: str | None
    location_address: str | None
    venue_id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime
    timezone: str
    visibility: EventVisibility
    capacity: int
    seating_mode: SeatingMode
    status: EventStatus
    lifecycle: EventLifecycle
    paid_sales_active: bool
    registration_opens_at: datetime | None
    registration_closes_at: datetime | None
    published_at: datetime | None
    cancelled_at: datetime | None
    is_suspended: bool
    calendar_links: CalendarLinks
    created_at: datetime
    updated_at: datetime


class EventSummary(ORMModel):
    """Card in catalog / organizer list."""

    id: uuid.UUID
    title: str
    category: str | None
    cover_image_key: str | None
    cover_image_url: str | None = None
    location_name: str | None
    starts_at: datetime
    ends_at: datetime
    timezone: str
    status: EventStatus
    lifecycle: EventLifecycle
    visibility: EventVisibility
    capacity: int
    sold: int = 0
    min_price_minor: int | None = None
    currency: str = "KZT"


class TimelineEntry(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    action_type: str
    entity_type: str
    entity_id: uuid.UUID
    description: str
    actor_id: uuid.UUID | None
    actor_name: str | None
    metadata: dict[str, object]


class EventCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
