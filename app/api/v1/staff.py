import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent
from app.api.v1._stub import not_implemented
from app.schemas.staff import AssignedEventRead, StaffAssignCreate, StaffRead

router = APIRouter(tags=["staff"])


@router.get("/events/{event_id}/staff", response_model=list[StaffRead])
def list_staff(event: ManagedEvent, db: DbSession) -> list[StaffRead]:
    not_implemented("BE-13")


@router.post(
    "/events/{event_id}/staff", response_model=StaffRead, status_code=status.HTTP_201_CREATED
)
def assign_staff(data: StaffAssignCreate, event: ManagedEvent, db: DbSession) -> StaffRead:
    not_implemented("BE-13")


@router.delete("/events/{event_id}/staff/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_staff(user_id: uuid.UUID, event: ManagedEvent, db: DbSession) -> None:
    not_implemented("BE-13")


@router.get("/me/assigned-events", response_model=list[AssignedEventRead])
def my_assigned_events(user: CurrentUser, db: DbSession) -> list[AssignedEventRead]:
    """Mobile scanner: events where I am an event admin."""
    not_implemented("BE-13")
