import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, OptionalUser
from app.api.v1._stub import not_implemented
from app.schemas.ticket_types import TicketTypeCreate, TicketTypeRead, TicketTypeUpdate

router = APIRouter(tags=["ticket types"])


@router.get("/events/{event_id}/ticket-types", response_model=list[TicketTypeRead])
def list_ticket_types(
    event_id: uuid.UUID, db: DbSession, user: OptionalUser
) -> list[TicketTypeRead]:
    """Public: on-sale, not hidden. Organizer: all, with counters."""
    not_implemented("BE-12")


@router.post(
    "/events/{event_id}/ticket-types",
    response_model=TicketTypeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_ticket_type(
    data: TicketTypeCreate, event: ManagedEvent, db: DbSession
) -> TicketTypeRead:
    not_implemented("BE-12")


@router.patch("/ticket-types/{ticket_type_id}", response_model=TicketTypeRead)
def update_ticket_type(
    ticket_type_id: uuid.UUID, data: TicketTypeUpdate, user: CurrentUser, db: DbSession
) -> TicketTypeRead:
    not_implemented("BE-12")


@router.post("/ticket-types/{ticket_type_id}/hide", response_model=TicketTypeRead)
def hide_ticket_type(ticket_type_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TicketTypeRead:
    not_implemented("BE-12")


@router.post("/ticket-types/{ticket_type_id}/unhide", response_model=TicketTypeRead)
def unhide_ticket_type(
    ticket_type_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> TicketTypeRead:
    not_implemented("BE-12")


@router.delete("/ticket-types/{ticket_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket_type(ticket_type_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    not_implemented("BE-12")
