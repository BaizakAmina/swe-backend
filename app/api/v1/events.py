import uuid
from datetime import datetime

from fastapi import APIRouter, status

from app.api.deps import DbSession, ManagedEvent, OptionalUser, OrganizerUser
from app.api.v1._stub import not_implemented
from app.models.enums import EventLifecycle
from app.schemas.common import Page
from app.schemas.events import EventCreate, EventRead, EventSummary, EventUpdate, TimelineEntry

router = APIRouter(tags=["events"])


@router.get("/events", response_model=Page[EventSummary])
def list_events(
    db: DbSession,
    q: str | None = None,
    category: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> Page[EventSummary]:
    """Public catalog: published, public, not suspended."""
    not_implemented("BE-11")


@router.get("/events/{event_id}", response_model=EventRead)
def get_event(event_id: uuid.UUID, db: DbSession, user: OptionalUser) -> EventRead:
    not_implemented("BE-11")


@router.post("/events", response_model=EventRead, status_code=status.HTTP_201_CREATED)
def create_event(data: EventCreate, user: OrganizerUser, db: DbSession) -> EventRead:
    not_implemented("BE-11")


@router.patch("/events/{event_id}", response_model=EventRead)
def update_event(data: EventUpdate, event: ManagedEvent, db: DbSession) -> EventRead:
    not_implemented("BE-11")


@router.post("/events/{event_id}/publish", response_model=EventRead)
def publish_event(event: ManagedEvent, db: DbSession) -> EventRead:
    not_implemented("BE-11")


@router.post("/events/{event_id}/unpublish", response_model=EventRead)
def unpublish_event(event: ManagedEvent, db: DbSession) -> EventRead:
    not_implemented("BE-11")


@router.post("/events/{event_id}/cancel", response_model=EventRead)
def cancel_event(event: ManagedEvent, db: DbSession) -> EventRead:
    not_implemented("BE-11")


@router.post(
    "/events/{event_id}/duplicate", response_model=EventRead, status_code=status.HTTP_201_CREATED
)
def duplicate_event(event: ManagedEvent, db: DbSession) -> EventRead:
    not_implemented("BE-11")


@router.get("/events/{event_id}/preview", response_model=EventRead)
def preview_event(event: ManagedEvent, db: DbSession) -> EventRead:
    not_implemented("BE-11")


@router.get("/events/{event_id}/timeline", response_model=Page[TimelineEntry])
def event_timeline(
    event: ManagedEvent,
    db: DbSession,
    action_type: str | None = None,
    lifecycle: EventLifecycle | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[TimelineEntry]:
    not_implemented("BE-27")
