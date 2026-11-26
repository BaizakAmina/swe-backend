import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, Query, Response, UploadFile, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, OptionalUser, OrganizerUser
from app.models.enums import EventLifecycle, EventStatus
from app.schemas.common import Page, Pagination, pagination, wide_pagination
from app.schemas.events import (
    EventCancelRequest,
    EventCreate,
    EventRead,
    EventSummary,
    EventUpdate,
    TimelineEntry,
)
from app.services import covers, timeline
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


@router.post("/events/{event_id}/cover", response_model=EventRead)
def upload_cover(
    event: ManagedEvent,
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File(description="JPEG, PNG or WebP, up to 5 MB")],
) -> EventRead:
    """Replaces the cover image; the response carries ``cover_image_url`` for display."""
    data = file.file.read(covers.MAX_BYTES + 1)
    covers.upload(db, user, event, data)
    return svc.to_read(svc.load(db, event.id))


@router.delete("/events/{event_id}/cover", response_model=EventRead)
def delete_cover(event: ManagedEvent, user: CurrentUser, db: DbSession) -> EventRead:
    covers.remove(db, user, event)
    return svc.to_read(svc.load(db, event.id))


@router.get(
    "/files/covers/{event_id}/{name}",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}}}},
)
def cover_file(event_id: str, name: str) -> Response:
    """Public cover image. Keys are unique per upload, so it is cached for a year."""
    data, content_type = covers.read(f"covers/{event_id}/{name}")
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


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
    q: Annotated[str | None, Query(max_length=200, description="Search in title / venue")] = None,
) -> Page[EventSummary]:
    return svc.list_organizer_events(db, user, page, lifecycle, event_status, q)


@router.get("/events/{event_id}/timeline", response_model=Page[TimelineEntry])
def event_timeline(
    event: ManagedEvent,
    db: DbSession,
    page: Annotated[Pagination, Depends(wide_pagination)],
    action_type: str | None = None,
    actor_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Page[TimelineEntry]:
    """Activity log of the event, newest first. ``action_type`` = exact (``event.published``)
    or a module prefix (``ticket_type``, ``order``, ``campaign``, ``staff``…)."""
    return timeline.event_timeline(db, event, page, action_type, actor_id, date_from, date_to)
