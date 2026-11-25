import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.common import Page, Pagination, pagination
from app.schemas.venues import VenueRead, VenueSummary
from app.services import venues as svc

router = APIRouter(tags=["venues"])


@router.get("/venues", response_model=Page[VenueSummary])
def list_venues(
    _: CurrentUser,
    db: DbSession,
    page: Annotated[Pagination, Depends(pagination)],
    q: Annotated[str | None, Query(max_length=200, description="Search by name / city")] = None,
) -> Page[VenueSummary]:
    """Predefined halls for assigned seating: pass ``id`` as ``venue_id`` when creating an event
    with ``seating_mode=assigned``."""
    return svc.list_venues(db, page, q)


@router.get("/venues/{venue_id}", response_model=VenueRead)
def get_venue(venue_id: uuid.UUID, _: CurrentUser, db: DbSession) -> VenueRead:
    """Layout: sections → rows → seats with price category and accessibility."""
    return svc.get_venue(db, venue_id)
