import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, OrganizerUser
from app.api.v1._stub import not_implemented
from app.schemas.events import EventSummary
from app.schemas.organizers import (
    ActivationChecklist,
    OrganizerProfileCreate,
    OrganizerProfileRead,
    OrganizerProfileUpdate,
    PayoutAccountCreate,
    PayoutAccountRead,
    PayoutAccountUpdate,
)

router = APIRouter(tags=["organizers"])


@router.get("/organizers/me", response_model=OrganizerProfileRead)
def get_my_profile(user: CurrentUser, db: DbSession) -> OrganizerProfileRead:
    not_implemented("BE-10")


@router.post(
    "/organizers/me", response_model=OrganizerProfileRead, status_code=status.HTTP_201_CREATED
)
def create_my_profile(
    data: OrganizerProfileCreate, user: OrganizerUser, db: DbSession
) -> OrganizerProfileRead:
    not_implemented("BE-10")


@router.patch("/organizers/me", response_model=OrganizerProfileRead)
def update_my_profile(
    data: OrganizerProfileUpdate, user: OrganizerUser, db: DbSession
) -> OrganizerProfileRead:
    not_implemented("BE-10")


@router.post("/organizers/me/verify", response_model=OrganizerProfileRead)
def verify_my_profile(user: OrganizerUser, db: DbSession) -> OrganizerProfileRead:
    """Simulated organizer verification."""
    not_implemented("BE-10")


@router.get("/organizers/me/activation-checklist", response_model=ActivationChecklist)
def activation_checklist(user: OrganizerUser, db: DbSession) -> ActivationChecklist:
    not_implemented("BE-10")


@router.get("/organizers/me/payout-accounts", response_model=list[PayoutAccountRead])
def list_payout_accounts(user: OrganizerUser, db: DbSession) -> list[PayoutAccountRead]:
    not_implemented("BE-10")


@router.post(
    "/organizers/me/payout-accounts",
    response_model=PayoutAccountRead,
    status_code=status.HTTP_201_CREATED,
)
def create_payout_account(
    data: PayoutAccountCreate, user: OrganizerUser, db: DbSession
) -> PayoutAccountRead:
    not_implemented("BE-10")


@router.patch("/payout-accounts/{account_id}", response_model=PayoutAccountRead)
def update_payout_account(
    account_id: uuid.UUID, data: PayoutAccountUpdate, user: OrganizerUser, db: DbSession
) -> PayoutAccountRead:
    not_implemented("BE-10")


@router.get("/organizers/me/events", response_model=list[EventSummary])
def my_events(user: OrganizerUser, db: DbSession) -> list[EventSummary]:
    """Own events, filter ``?lifecycle=&status=``."""
    not_implemented("BE-11")
