import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, OptionalUser
from app.api.v1._stub import not_implemented
from app.schemas.seating import SeatHoldCreate, SeatHoldRead, SeatMapRead

router = APIRouter(tags=["seating"])


@router.get("/events/{event_id}/seatmap", response_model=SeatMapRead)
def seatmap(event_id: uuid.UUID, db: DbSession, user: OptionalUser) -> SeatMapRead:
    not_implemented("BE-30")


@router.post(
    "/events/{event_id}/seat-holds",
    response_model=list[SeatHoldRead],
    status_code=status.HTTP_201_CREATED,
)
def hold_seats(
    event_id: uuid.UUID, data: SeatHoldCreate, user: CurrentUser, db: DbSession
) -> list[SeatHoldRead]:
    """409 ``seat_unavailable`` if any seat is already held or sold."""
    not_implemented("BE-30")


@router.delete("/seat-holds/{hold_id}", status_code=status.HTTP_204_NO_CONTENT)
def release_hold(hold_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    not_implemented("BE-30")
