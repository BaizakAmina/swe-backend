import uuid
from datetime import datetime

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.models.enums import SupportCategory, SupportStatus
from app.schemas.common import Page, Pagination
from app.schemas.support import (
    SupportCaseCreate,
    SupportCaseDetail,
    SupportCaseRead,
    SupportCaseUpdate,
    SupportMessageCreate,
    SupportMessageRead,
)
from app.services import support as svc

router = APIRouter(prefix="/support", tags=["support"])


@router.post("/cases", response_model=SupportCaseDetail, status_code=status.HTTP_201_CREATED)
def open_case(data: SupportCaseCreate, user: CurrentUser, db: DbSession) -> SupportCaseDetail:
    """Context (event/order) is filled from the ticket or order; you must be related to it."""
    return svc.open_case(db, user, data)


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
    """Mine; organizers also see cases about their events; admins see all."""
    page = Pagination(limit=max(1, min(limit, 100)), offset=max(offset, 0))
    return svc.list_cases(db, user, page, case_status, category, event_id)


@router.get("/cases/{case_id}", response_model=SupportCaseDetail)
def get_case(case_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SupportCaseDetail:
    return svc.to_detail(db, svc.load_for_user(db, user, case_id))


@router.patch("/cases/{case_id}", response_model=SupportCaseRead)
def update_case(
    case_id: uuid.UUID, data: SupportCaseUpdate, user: CurrentUser, db: DbSession
) -> SupportCaseRead:
    """Staff: status and assignee. The customer may only mark their case resolved."""
    return svc.update_case(db, user, case_id, data)


@router.get("/cases/{case_id}/messages", response_model=list[SupportMessageRead])
def list_messages(
    case_id: uuid.UUID, user: CurrentUser, db: DbSession, after: datetime | None = None
) -> list[SupportMessageRead]:
    """Polling: pass ``after`` = last seen ``sent_at``."""
    return svc.list_messages(db, user, case_id, after)


@router.post(
    "/cases/{case_id}/messages",
    response_model=SupportMessageRead,
    status_code=status.HTTP_201_CREATED,
)
def post_message(
    case_id: uuid.UUID, data: SupportMessageCreate, user: CurrentUser, db: DbSession
) -> SupportMessageRead:
    """Notifies the other side; status moves to waiting_customer / in_progress."""
    return svc.post_message(db, user, case_id, data.body)
