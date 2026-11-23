import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter

from app.api.deps import DbSession, ManagedEvent, OrganizerUser
from app.schemas.analytics import EventAnalytics, OrganizerAnalyticsSummary
from app.services import analytics as svc

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
    """Capacity, revenue (gross / discounts / refunds / net), sales over time (event timezone),
    breakdowns by ticket type and campaign, attendance. Filters apply to every block."""
    return svc.event_analytics(db, event, date_from, date_to, ticket_type_id, bucket)


@router.get("/organizers/me/analytics/summary", response_model=OrganizerAnalyticsSummary)
def organizer_summary(user: OrganizerUser, db: DbSession) -> OrganizerAnalyticsSummary:
    return svc.organizer_summary(db, user)
