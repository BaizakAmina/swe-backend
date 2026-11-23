import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response

from app.api.deps import DbSession, PlatformAdmin
from app.models.enums import EventStatus, PaymentPurpose, Role, UserStatus
from app.schemas.admin import (
    AdminEventRead,
    AdminUserRead,
    PlatformSettingsRead,
    PlatformSettingsUpdate,
    SuspendRequest,
)
from app.schemas.common import Page, Pagination, pagination
from app.schemas.orders import OrderRead, PaymentRead
from app.services import admin as svc

router = APIRouter(prefix="/admin", tags=["admin"])

PageParams = Annotated[Pagination, Depends(pagination)]


@router.get("/users", response_model=Page[AdminUserRead])
def list_users(
    admin: PlatformAdmin,
    db: DbSession,
    page: PageParams,
    q: str | None = None,
    role: Role | None = None,
    user_status: UserStatus | None = None,
) -> Page[AdminUserRead]:
    """Search by email or name."""
    return svc.list_users(db, page, q, role, user_status)


@router.get("/users/{user_id}", response_model=AdminUserRead)
def get_user(user_id: uuid.UUID, admin: PlatformAdmin, db: DbSession) -> AdminUserRead:
    return svc.user_to_read(svc.get_user(db, user_id))


@router.post("/users/{user_id}/suspend", response_model=AdminUserRead)
def suspend_user(
    user_id: uuid.UUID, data: SuspendRequest, admin: PlatformAdmin, db: DbSession
) -> AdminUserRead:
    """Blocks login and revokes all tokens immediately. Reason is required and audited."""
    return svc.suspend_user(db, admin, user_id, data.reason)


@router.post("/users/{user_id}/unsuspend", response_model=AdminUserRead)
def unsuspend_user(user_id: uuid.UUID, admin: PlatformAdmin, db: DbSession) -> AdminUserRead:
    return svc.unsuspend_user(db, admin, user_id)


@router.get("/events", response_model=Page[AdminEventRead])
def list_events(
    admin: PlatformAdmin,
    db: DbSession,
    page: PageParams,
    q: str | None = None,
    event_status: EventStatus | None = None,
    organizer_id: uuid.UUID | None = None,
    suspended: bool | None = None,
) -> Page[AdminEventRead]:
    """Search by title or organizer name."""
    return svc.list_events(db, page, q, event_status, organizer_id, suspended)


@router.post("/events/{event_id}/suspend", response_model=AdminEventRead)
def suspend_event(
    event_id: uuid.UUID, data: SuspendRequest, admin: PlatformAdmin, db: DbSession
) -> AdminEventRead:
    """Hides the event from the catalog and stops sales."""
    return svc.suspend_event(db, admin, event_id, data.reason)


@router.post("/events/{event_id}/unsuspend", response_model=AdminEventRead)
def unsuspend_event(event_id: uuid.UUID, admin: PlatformAdmin, db: DbSession) -> AdminEventRead:
    return svc.unsuspend_event(db, admin, event_id)


@router.get("/orders", response_model=Page[OrderRead])
def list_orders(
    admin: PlatformAdmin, db: DbSession, page: PageParams, event_id: uuid.UUID | None = None
) -> Page[OrderRead]:
    return svc.list_orders(db, page, event_id)


@router.get("/payments", response_model=Page[PaymentRead])
def list_payments(
    admin: PlatformAdmin,
    db: DbSession,
    page: PageParams,
    purpose: PaymentPurpose | None = None,
) -> Page[PaymentRead]:
    """All payments are simulated (``is_simulated: true``)."""
    return svc.list_payments(db, page, purpose)


@router.get("/activation-records", response_model=Page[PaymentRead])
def activation_records(admin: PlatformAdmin, db: DbSession, page: PageParams) -> Page[PaymentRead]:
    """Paid-sales activation fee payments."""
    return svc.list_payments(db, page, PaymentPurpose.ACTIVATION_FEE)


@router.get("/settings", response_model=PlatformSettingsRead)
def get_settings(admin: PlatformAdmin, db: DbSession) -> PlatformSettingsRead:
    return svc.get_settings(db)


@router.patch("/settings", response_model=PlatformSettingsRead)
def update_settings(
    data: PlatformSettingsUpdate, admin: PlatformAdmin, db: DbSession
) -> PlatformSettingsRead:
    """Activation fee (KZT minor units) used for new paid-sales activations."""
    return svc.update_settings(db, admin, data.activation_fee_minor)


@router.get(
    "/reports/{report}.csv", response_model=None, responses={200: {"content": {"text/csv": {}}}}
)
def report_csv(
    report: Literal["users", "events", "orders", "payments"], admin: PlatformAdmin, db: DbSession
) -> Response:
    return Response(
        svc.report_csv(db, report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report}.csv"'},
    )
