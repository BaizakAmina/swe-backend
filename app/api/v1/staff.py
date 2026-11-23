import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent
from app.schemas.staff import AssignedEventRead, StaffAssignCreate, StaffRead
from app.services import staff as svc

router = APIRouter(tags=["staff"])


@router.get("/events/{event_id}/staff", response_model=list[StaffRead])
def list_staff(event: ManagedEvent, db: DbSession) -> list[StaffRead]:
    return svc.list_staff(db, event)


@router.post(
    "/events/{event_id}/staff", response_model=StaffRead, status_code=status.HTTP_201_CREATED
)
def assign_staff(
    data: StaffAssignCreate, event: ManagedEvent, user: CurrentUser, db: DbSession
) -> StaffRead:
    """Assign an existing user (by email) as event admin. Idempotent."""
    return svc.assign(db, user, event, data)


@router.delete("/events/{event_id}/staff/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_staff(user_id: uuid.UUID, event: ManagedEvent, user: CurrentUser, db: DbSession) -> None:
    svc.remove(db, user, event, user_id)


@router.get(
    "/me/assigned-events", response_model=list[AssignedEventRead], tags=["check-in (mobile)"]
)
def my_assigned_events(user: CurrentUser, db: DbSession) -> list[AssignedEventRead]:
    """Mobile scanner: events where I am an event admin, with check-in progress."""
    return svc.assigned_events(db, user)
