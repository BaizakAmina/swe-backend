import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, OptionalUser, OrganizerUser
from app.api.v1._stub import not_implemented
from app.models.enums import EventLifecycle, EventStatus
from app.schemas.common import Page, Pagination, pagination
from app.schemas.events import (
    EventCancelRequest,
    EventCreate,
    EventRead,
    EventSummary,
    EventUpdate,
    TimelineEntry,
)
from app.services import events as svc

router = APIRouter(tags=["events"])

PageParams = Annotated[Pagination, Depends(pagination)]


@router.get("/events", response_model=Page[EventSummary])
def list_events(
    db: DbSession,
    page: PageParams,
    q: str | None = None,
    category: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_past: bool = False,
) -> Page[EventSummary]:
    """Public catalog: published, public, not suspended; upcoming and running by default."""
    return svc.list_public(db, page, q, category, date_from, date_to, include_past)


@router.get("/events/{event_id}", response_model=EventRead)
def get_event(event_id: uuid.UUID, db: DbSession, user: OptionalUser) -> EventRead:
    """Published public/unlisted events for everyone; drafts/private only for the organizer."""
    return svc.to_read(svc.get_for_viewer(db, event_id, user))


@router.post("/events", response_model=EventRead, status_code=status.HTTP_201_CREATED)
def create_event(data: EventCreate, user: OrganizerUser, db: DbSession) -> EventRead:
    """Creates a draft. Requires a verified email and an organizer profile."""
    return svc.to_read(svc.create_event(db, user, data))


@router.patch("/events/{event_id}", response_model=EventRead)
def update_event(
    data: EventUpdate, event: ManagedEvent, user: CurrentUser, db: DbSession
) -> EventRead:
    return svc.to_read(svc.update_event(db, user, event, data))


@router.post("/events/{event_id}/publish", response_model=EventRead)
def publish_event(event: ManagedEvent, user: CurrentUser, db: DbSession) -> EventRead:
    return svc.to_read(svc.publish(db, user, event))


@router.post("/events/{event_id}/unpublish", response_model=EventRead)
def unpublish_event(event: ManagedEvent, user: CurrentUser, db: DbSession) -> EventRead:
    """Back to draft — only while nothing has been sold or reserved."""
    return svc.to_read(svc.unpublish(db, user, event))


@router.post("/events/{event_id}/cancel", response_model=EventRead)
def cancel_event(
    event: ManagedEvent,
    user: CurrentUser,
    db: DbSession,
    data: Annotated[EventCancelRequest | None, Body()] = None,
) -> EventRead:
    """Cancels the event and notifies ticket holders. Refunds are processed by BE-24."""
    return svc.to_read(svc.cancel(db, user, event, data.reason if data else None))


@router.post(
    "/events/{event_id}/duplicate", response_model=EventRead, status_code=status.HTTP_201_CREATED
)
def duplicate_event(event: ManagedEvent, user: CurrentUser, db: DbSession) -> EventRead:
    """Draft copy with the same ticket types and no sales."""
    return svc.to_read(svc.duplicate(db, user, event))


@router.get("/events/{event_id}/preview", response_model=EventRead)
def preview_event(event: ManagedEvent) -> EventRead:
    """How buyers will see the event (works for drafts)."""
    return svc.to_read(event)


@router.get("/organizers/me/events", response_model=Page[EventSummary], tags=["organizers"])
def my_events(
    user: OrganizerUser,
    db: DbSession,
    page: PageParams,
    lifecycle: EventLifecycle | None = None,
    event_status: EventStatus | None = None,
) -> Page[EventSummary]:
    return svc.list_organizer_events(db, user, page, lifecycle, event_status)


@router.get("/events/{event_id}/timeline", response_model=Page[TimelineEntry])
def event_timeline(
    event: ManagedEvent,
    db: DbSession,
    action_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[TimelineEntry]:
    not_implemented("BE-27")
