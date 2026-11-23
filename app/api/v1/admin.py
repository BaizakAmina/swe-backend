import uuid
from typing import Literal

from fastapi import APIRouter

from app.api.deps import DbSession, PlatformAdmin
from app.api.v1._stub import not_implemented
from app.models.enums import EventStatus, PaymentPurpose, Role, UserStatus
from app.schemas.admin import (
    AdminEventRead,
    AdminUserRead,
    PlatformSettingsRead,
    PlatformSettingsUpdate,
    SuspendRequest,
)
from app.schemas.common import Page
from app.schemas.orders import OrderRead, PaymentRead

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=Page[AdminUserRead])
def list_users(
    admin: PlatformAdmin,
    db: DbSession,
    q: str | None = None,
    role: Role | None = None,
    user_status: UserStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[AdminUserRead]:
    not_implemented("BE-28")


@router.get("/users/{user_id}", response_model=AdminUserRead)
def get_user(user_id: uuid.UUID, admin: PlatformAdmin, db: DbSession) -> AdminUserRead:
    not_implemented("BE-28")


@router.post("/users/{user_id}/suspend", response_model=AdminUserRead)
def suspend_user(
    user_id: uuid.UUID, data: SuspendRequest, admin: PlatformAdmin, db: DbSession
) -> AdminUserRead:
    not_implemented("BE-28")


@router.post("/users/{user_id}/unsuspend", response_model=AdminUserRead)
def unsuspend_user(user_id: uuid.UUID, admin: PlatformAdmin, db: DbSession) -> AdminUserRead:
    not_implemented("BE-28")


@router.get("/events", response_model=Page[AdminEventRead])
def list_events(
    admin: PlatformAdmin,
    db: DbSession,
    q: str | None = None,
    event_status: EventStatus | None = None,
    organizer_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[AdminEventRead]:
    not_implemented("BE-28")


@router.post("/events/{event_id}/suspend", response_model=AdminEventRead)
def suspend_event(
    event_id: uuid.UUID, data: SuspendRequest, admin: PlatformAdmin, db: DbSession
) -> AdminEventRead:
    """Suspended events are hidden and stop selling."""
    not_implemented("BE-28")


@router.post("/events/{event_id}/unsuspend", response_model=AdminEventRead)
def unsuspend_event(event_id: uuid.UUID, admin: PlatformAdmin, db: DbSession) -> AdminEventRead:
    not_implemented("BE-28")


@router.get("/orders", response_model=Page[OrderRead])
def list_orders(
    admin: PlatformAdmin,
    db: DbSession,
    event_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[OrderRead]:
    not_implemented("BE-28")


@router.get("/payments", response_model=Page[PaymentRead])
def list_payments(
    admin: PlatformAdmin,
    db: DbSession,
    purpose: PaymentPurpose | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[PaymentRead]:
    not_implemented("BE-28")


@router.get("/activation-records", response_model=Page[PaymentRead])
def activation_records(
    admin: PlatformAdmin, db: DbSession, limit: int = 50, offset: int = 0
) -> Page[PaymentRead]:
    not_implemented("BE-28")


@router.get("/settings", response_model=PlatformSettingsRead)
def get_settings(admin: PlatformAdmin, db: DbSession) -> PlatformSettingsRead:
    not_implemented("BE-28")


@router.patch("/settings", response_model=PlatformSettingsRead)
def update_settings(
    data: PlatformSettingsUpdate, admin: PlatformAdmin, db: DbSession
) -> PlatformSettingsRead:
    not_implemented("BE-28")


@router.get(
    "/reports/{report}.csv", response_model=None, responses={200: {"content": {"text/csv": {}}}}
)
def report_csv(
    report: Literal["users", "events", "orders", "payments"], admin: PlatformAdmin, db: DbSession
) -> str:
    not_implemented("BE-28")
