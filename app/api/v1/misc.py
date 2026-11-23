"""Notifications (in-app) and calendar export."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.api.v1._stub import not_implemented
from app.schemas.common import Page
from app.schemas.notifications import NotificationRead

router = APIRouter()


@router.get("/me/notifications", response_model=Page[NotificationRead], tags=["notifications"])
def my_notifications(
    user: CurrentUser, db: DbSession, unread: bool | None = None, limit: int = 20, offset: int = 0
) -> Page[NotificationRead]:
    not_implemented("BE-19")


@router.post(
    "/me/notifications/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["notifications"],
)
def mark_read(notification_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    not_implemented("BE-19")


@router.get(
    "/events/{event_id}/calendar.ics",
    response_model=None,
    responses={200: {"content": {"text/calendar": {}}}},
    tags=["calendar"],
)
def event_ics(event_id: uuid.UUID, db: DbSession) -> str:
    not_implemented("BE-29")
