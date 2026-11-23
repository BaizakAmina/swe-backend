import uuid

from fastapi import APIRouter, status

from app.api.deps import DbSession, OrganizerUser
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
from app.services import organizers as svc

router = APIRouter(tags=["organizers"])


@router.get("/organizers/me", response_model=OrganizerProfileRead)
def get_my_profile(user: OrganizerUser, db: DbSession) -> OrganizerProfileRead:
    return OrganizerProfileRead.model_validate(svc.require_profile(db, user))


@router.post(
    "/organizers/me", response_model=OrganizerProfileRead, status_code=status.HTTP_201_CREATED
)
def create_my_profile(
    data: OrganizerProfileCreate, user: OrganizerUser, db: DbSession
) -> OrganizerProfileRead:
    return OrganizerProfileRead.model_validate(svc.create_profile(db, user, data))


@router.patch("/organizers/me", response_model=OrganizerProfileRead)
def update_my_profile(
    data: OrganizerProfileUpdate, user: OrganizerUser, db: DbSession
) -> OrganizerProfileRead:
    return OrganizerProfileRead.model_validate(svc.update_profile(db, user, data))


@router.post("/organizers/me/verify", response_model=OrganizerProfileRead)
def verify_my_profile(user: OrganizerUser, db: DbSession) -> OrganizerProfileRead:
    """Simulated organizer verification (completes immediately for a complete profile)."""
    return OrganizerProfileRead.model_validate(svc.verify_profile(db, user))


@router.get("/organizers/me/activation-checklist", response_model=ActivationChecklist)
def activation_checklist(user: OrganizerUser, db: DbSession) -> ActivationChecklist:
    return svc.get_activation_checklist(db, svc.require_profile(db, user))


@router.get("/organizers/me/payout-accounts", response_model=list[PayoutAccountRead])
def list_payout_accounts(user: OrganizerUser, db: DbSession) -> list[PayoutAccountRead]:
    return [PayoutAccountRead.model_validate(a) for a in svc.list_payout_accounts(db, user)]


@router.post(
    "/organizers/me/payout-accounts",
    response_model=PayoutAccountRead,
    status_code=status.HTTP_201_CREATED,
)
def create_payout_account(
    data: PayoutAccountCreate, user: OrganizerUser, db: DbSession
) -> PayoutAccountRead:
    """Sandbox reference only (e.g. ``sandbox_acc_123``) — no card numbers or IBANs."""
    return PayoutAccountRead.model_validate(svc.create_payout_account(db, user, data))


@router.patch("/payout-accounts/{account_id}", response_model=PayoutAccountRead)
def update_payout_account(
    account_id: uuid.UUID, data: PayoutAccountUpdate, user: OrganizerUser, db: DbSession
) -> PayoutAccountRead:
    return PayoutAccountRead.model_validate(svc.update_payout_account(db, user, account_id, data))


@router.get("/organizers/me/events", response_model=list[EventSummary])
def my_events(user: OrganizerUser, db: DbSession) -> list[EventSummary]:
    """Own events, filter ``?lifecycle=&status=``."""
    not_implemented("BE-11")
