import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import DbSession, PlatformAdmin
from app.models.enums import (
    EventStatus,
    OrderStatus,
    PaymentPurpose,
    PaymentStatus,
    Role,
    SupportStatus,
    UserStatus,
)
from app.schemas.admin import (
    AdminEventRead,
    AdminUserRead,
    PlatformSettingsRead,
    PlatformSettingsUpdate,
    PromoActivityRead,
    ReportedEventRead,
    SuspendRequest,
)
from app.schemas.common import Page, Pagination, Period, pagination, period
from app.schemas.orders import OrderRead, PaymentRead
from app.schemas.support import SupportCaseRead
from app.services import admin as svc

router = APIRouter(prefix="/admin", tags=["admin"])

PageParams = Annotated[Pagination, Depends(pagination)]
PeriodParams = Annotated[Period, Depends(period)]
SearchParam = Annotated[str | None, Query(max_length=200)]


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
    admin: PlatformAdmin,
    db: DbSession,
    page: PageParams,
    period: PeriodParams,
    event_id: uuid.UUID | None = None,
    q: SearchParam = None,
    order_status: OrderStatus | None = None,
) -> Page[OrderRead]:
    """``q``: order id, buyer email or name, event title, promo code. Dates filter
    ``created_at``."""
    return svc.list_orders(db, page, event_id, q, order_status, period)


@router.get("/payments", response_model=Page[PaymentRead])
def list_payments(
    admin: PlatformAdmin,
    db: DbSession,
    page: PageParams,
    period: PeriodParams,
    purpose: PaymentPurpose | None = None,
    q: SearchParam = None,
    payment_status: PaymentStatus | None = None,
) -> Page[PaymentRead]:
    """All payments are simulated (``is_simulated: true``). ``q``: payment or order id,
    provider reference, payer email or name, event title. Dates filter ``created_at``."""
    return svc.list_payments(db, page, purpose, q, payment_status, period)


@router.get("/reported-events", response_model=Page[ReportedEventRead])
def reported_events(
    admin: PlatformAdmin,
    db: DbSession,
    page: PageParams,
    period: PeriodParams,
    q: SearchParam = None,
    only_open: bool = True,
) -> Page[ReportedEventRead]:
    """Events with support cases (most open cases first). ``only_open=false`` also lists events
    whose cases are all resolved. Dates filter when the cases were opened."""
    return svc.reported_events(db, page, q, only_open, period)


@router.get("/disputes", response_model=Page[SupportCaseRead])
def disputes(
    admin: PlatformAdmin,
    db: DbSession,
    page: PageParams,
    period: PeriodParams,
    q: SearchParam = None,
    case_status: SupportStatus | None = None,
) -> Page[SupportCaseRead]:
    """Payment and refund support cases. Reply / resolve them via ``/support/cases/{id}``."""
    return svc.disputes(db, page, q, case_status, period)


@router.get("/promo-activity", response_model=Page[PromoActivityRead])
def promo_activity(
    admin: PlatformAdmin,
    db: DbSession,
    page: PageParams,
    period: PeriodParams,
    q: SearchParam = None,
    event_id: uuid.UUID | None = None,
) -> Page[PromoActivityRead]:
    """Promo-code uses on orders, newest first. ``q``: code, campaign, event title, buyer
    email."""
    return svc.promo_activity(db, page, q, event_id, period)


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
    """Only the fields sent change; each change is audited. ``activation_fee_minor`` (KZT minor
    units), reservation and seat-hold times (minutes), seat-hold limit per user, and the current
    paid-event terms version (a new version asks organizers to accept again)."""
    return svc.update_settings(db, admin, data)


@router.get(
    "/reports/{report}.csv", response_model=None, responses={200: {"content": {"text/csv": {}}}}
)
def report_csv(
    report: Literal["users", "events", "orders", "payments"],
    admin: PlatformAdmin,
    db: DbSession,
    period: PeriodParams,
) -> Response:
    """Dates filter ``created_at`` (``starts_at`` for events)."""
    return Response(
        svc.report_csv(db, report, period),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report}.csv"'},
    )
