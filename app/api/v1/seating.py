import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, OptionalUser
from app.schemas.seating import SeatHoldCreate, SeatHoldRead, SeatMapRead
from app.services import seating

router = APIRouter(tags=["seating"])


@router.get("/events/{event_id}/seatmap", response_model=SeatMapRead)
def seatmap(event_id: uuid.UUID, db: DbSession, user: OptionalUser) -> SeatMapRead:
    """Seats with ``available`` / ``held`` / ``mine`` (logged-in holder) / ``sold``."""
    return seating.seatmap(db, event_id, user)


@router.post(
    "/events/{event_id}/seat-holds",
    response_model=list[SeatHoldRead],
    status_code=status.HTTP_201_CREATED,
)
def hold_seats(
    event_id: uuid.UUID, data: SeatHoldCreate, user: CurrentUser, db: DbSession
) -> list[SeatHoldRead]:
    """Hold seats for ``seat_hold_ttl_minutes`` (10). Pass the hold ids to ``POST /orders``.

    409 ``seat_unavailable`` (``details.seat_ids`` = the taken seats) if any seat is already
    held or sold; 422 ``seat_hold_limit`` above ``max_seat_holds_per_user``.
    """
    holds = seating.hold(db, user, event_id, data.seat_ids)
    return [SeatHoldRead(id=h.id, seat_id=h.seat_id, expires_at=h.expires_at) for h in holds]


@router.delete("/seat-holds/{hold_id}", status_code=status.HTTP_204_NO_CONTENT)
def release_hold(hold_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Give a held seat back. Seats already in an order go with the order (409)."""
    seating.drop_hold(db, user, hold_id)
