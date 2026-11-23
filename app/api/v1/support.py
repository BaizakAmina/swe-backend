import uuid
from datetime import datetime

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.api.v1._stub import not_implemented
from app.models.enums import SupportCategory, SupportStatus
from app.schemas.common import Page
from app.schemas.support import (
    SupportCaseCreate,
    SupportCaseDetail,
    SupportCaseRead,
    SupportCaseUpdate,
    SupportMessageCreate,
    SupportMessageRead,
)

router = APIRouter(prefix="/support", tags=["support"])


@router.post("/cases", response_model=SupportCaseDetail, status_code=status.HTTP_201_CREATED)
def open_case(data: SupportCaseCreate, user: CurrentUser, db: DbSession) -> SupportCaseDetail:
    not_implemented("BE-25")


@router.get("/cases", response_model=Page[SupportCaseRead])
def list_cases(
    user: CurrentUser,
    db: DbSession,
    case_status: SupportStatus | None = None,
    category: SupportCategory | None = None,
    event_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> Page[SupportCaseRead]:
    not_implemented("BE-25")


@router.get("/cases/{case_id}", response_model=SupportCaseDetail)
def get_case(case_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SupportCaseDetail:
    not_implemented("BE-25")


@router.patch("/cases/{case_id}", response_model=SupportCaseRead)
def update_case(
    case_id: uuid.UUID, data: SupportCaseUpdate, user: CurrentUser, db: DbSession
) -> SupportCaseRead:
    not_implemented("BE-25")


@router.get("/cases/{case_id}/messages", response_model=list[SupportMessageRead])
def list_messages(
    case_id: uuid.UUID, user: CurrentUser, db: DbSession, after: datetime | None = None
) -> list[SupportMessageRead]:
    """Polling: pass ``after`` = last seen ``sent_at``."""
    not_implemented("BE-25")


@router.post(
    "/cases/{case_id}/messages",
    response_model=SupportMessageRead,
    status_code=status.HTTP_201_CREATED,
)
def post_message(
    case_id: uuid.UUID, data: SupportMessageCreate, user: CurrentUser, db: DbSession
) -> SupportMessageRead:
    not_implemented("BE-25")
