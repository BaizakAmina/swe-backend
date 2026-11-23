import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, OptionalUser
from app.schemas.ticket_types import TicketTypeCreate, TicketTypeRead, TicketTypeUpdate
from app.services import events as events_svc
from app.services import ticket_types as svc

router = APIRouter(tags=["ticket types"])


@router.get("/events/{event_id}/ticket-types", response_model=list[TicketTypeRead])
def list_ticket_types(
    event_id: uuid.UUID, db: DbSession, user: OptionalUser
) -> list[TicketTypeRead]:
    """Public: visible types with ``on_sale`` / ``sellable`` / availability.
    Organizer: all types (incl. hidden) with sold / reserved / refunded / checked-in counters."""
    event = events_svc.get_for_viewer(db, event_id, user)
    return svc.list_for_event(db, event, user)


@router.post(
    "/events/{event_id}/ticket-types",
    response_model=TicketTypeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_ticket_type(
    data: TicketTypeCreate, event: ManagedEvent, user: CurrentUser, db: DbSession
) -> TicketTypeRead:
    """Sum of ``quantity_total`` over all types may not exceed the event capacity."""
    return svc.create(db, user, event, data)


@router.patch("/ticket-types/{ticket_type_id}", response_model=TicketTypeRead)
def update_ticket_type(
    ticket_type_id: uuid.UUID, data: TicketTypeUpdate, user: CurrentUser, db: DbSession
) -> TicketTypeRead:
    """``quantity_total`` can't go below sold + reserved; price is locked after the first sale."""
    return svc.update(db, user, ticket_type_id, data)


@router.post("/ticket-types/{ticket_type_id}/hide", response_model=TicketTypeRead)
def hide_ticket_type(ticket_type_id: uuid.UUID, user: CurrentUser, db: DbSession) -> TicketTypeRead:
    return svc.set_hidden(db, user, ticket_type_id, True)


@router.post("/ticket-types/{ticket_type_id}/unhide", response_model=TicketTypeRead)
def unhide_ticket_type(
    ticket_type_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> TicketTypeRead:
    return svc.set_hidden(db, user, ticket_type_id, False)


@router.delete("/ticket-types/{ticket_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket_type(ticket_type_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Only types without any orders; otherwise hide them."""
    svc.delete(db, user, ticket_type_id)
