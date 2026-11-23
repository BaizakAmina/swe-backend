"""Notifications (in-app) and calendar export."""

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession, OptionalUser
from app.schemas.common import Page
from app.schemas.notifications import NotificationRead
from app.services import calendar, events, notifications

router = APIRouter()


@router.get("/me/notifications", response_model=Page[NotificationRead], tags=["notifications"])
def my_notifications(
    user: CurrentUser, db: DbSession, unread: bool | None = None, limit: int = 20, offset: int = 0
) -> Page[NotificationRead]:
    """In-app notifications, newest first. ``?unread=true`` for the badge."""
    limit = max(1, min(limit, 100))
    items, total = notifications.list_in_app(db, user, unread, limit, max(offset, 0))
    return Page(
        items=[NotificationRead.model_validate(n) for n in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/me/notifications/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["notifications"],
)
def mark_read(notification_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    notifications.mark_read(db, user, notification_id)


@router.get(
    "/events/{event_id}/calendar.ics",
    response_model=None,
    responses={200: {"content": {"text/calendar": {}}}},
    tags=["calendar"],
)
def event_ics(event_id: uuid.UUID, db: DbSession, user: OptionalUser) -> Response:
    """Stable UID, event timezone; a cancelled event exports a CANCEL with the same UID."""
    event = events.get_for_viewer(db, event_id, user)
    return Response(
        calendar.build_ics(event),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="event-{event.id}.ics"'},
    )
