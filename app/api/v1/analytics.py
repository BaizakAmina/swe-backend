import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter

from app.api.deps import DbSession, ManagedEvent, OrganizerUser
from app.api.v1._stub import not_implemented
from app.schemas.analytics import EventAnalytics, OrganizerAnalyticsSummary

router = APIRouter(tags=["analytics"])


@router.get("/events/{event_id}/analytics", response_model=EventAnalytics)
def event_analytics(
    event: ManagedEvent,
    db: DbSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    ticket_type_id: uuid.UUID | None = None,
    bucket: Literal["hour", "day"] = "day",
) -> EventAnalytics:
    not_implemented("BE-26")


@router.get("/organizers/me/analytics/summary", response_model=OrganizerAnalyticsSummary)
def organizer_summary(user: OrganizerUser, db: DbSession) -> OrganizerAnalyticsSummary:
    not_implemented("BE-26")
