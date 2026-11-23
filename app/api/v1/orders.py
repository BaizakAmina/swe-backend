import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, VerifiedUser
from app.api.v1._stub import not_implemented
from app.models.enums import OrderStatus
from app.schemas.common import Page, Pagination
from app.schemas.orders import (
    ActivationStatusRead,
    OrderCreate,
    OrderRead,
    PaymentRead,
    PayRequest,
    RefundRead,
    RefundRequest,
)
from app.services import orders as svc
from app.services import views

router = APIRouter(tags=["orders & payments"])


@router.post("/orders", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_order(data: OrderCreate, user: VerifiedUser, db: DbSession) -> OrderRead:
    """Reserves inventory atomically for ``reserved_until``. Free orders are completed at once.

    Prices are taken from the server; 409 ``sold_out`` when not enough tickets are left.
    """
    return views.order_to_read(db, svc.create_order(db, user, data))


@router.get("/orders/{order_id}", response_model=OrderRead)
def get_order(order_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OrderRead:
    """Buyer, the event organizer or a platform admin."""
    return views.order_to_read(db, svc.get_order(db, user, order_id))


@router.post("/orders/{order_id}/cancel", response_model=OrderRead)
def cancel_pending_order(order_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OrderRead:
    """Cancel a pending order and return its reservation."""
    return views.order_to_read(db, svc.cancel(db, user, order_id))


@router.post("/orders/{order_id}/register-free", response_model=OrderRead)
def register_free(order_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OrderRead:
    """Complete a pending order with a zero total without payment."""
    return views.order_to_read(db, svc.register_free(db, user, order_id))


@router.post("/orders/{order_id}/pay", response_model=PaymentRead)
def pay_order(
    order_id: uuid.UUID, data: PayRequest, user: CurrentUser, db: DbSession
) -> PaymentRead:
    """Simulated payment; tickets are issued only on success."""
    not_implemented("BE-15")


@router.post("/orders/{order_id}/cancel-registration", response_model=OrderRead)
def cancel_registration(order_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OrderRead:
    not_implemented("BE-24")


@router.post("/orders/{order_id}/refund", response_model=RefundRead)
def refund_order(
    order_id: uuid.UUID, data: RefundRequest, user: CurrentUser, db: DbSession
) -> RefundRead:
    not_implemented("BE-24")


@router.get("/me/orders", response_model=Page[OrderRead])
def my_orders(
    user: CurrentUser,
    db: DbSession,
    order_status: OrderStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> Page[OrderRead]:
    """My orders, newest first."""
    page = Pagination(limit=max(1, min(limit, 100)), offset=max(offset, 0))
    return views.my_orders(db, user, page, order_status)


@router.get("/events/{event_id}/orders", response_model=Page[OrderRead])
def event_orders(
    event: ManagedEvent,
    db: DbSession,
    order_status: OrderStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[OrderRead]:
    page = Pagination(limit=max(1, min(limit, 100)), offset=max(offset, 0))
    return views.event_orders(db, event, page, order_status)


@router.get("/events/{event_id}/activation", response_model=ActivationStatusRead)
def activation_status(event: ManagedEvent, db: DbSession) -> ActivationStatusRead:
    not_implemented("BE-16")


@router.post("/events/{event_id}/activate-paid-sales", response_model=ActivationStatusRead)
def activate_paid_sales(
    data: PayRequest, event: ManagedEvent, user: CurrentUser, db: DbSession
) -> ActivationStatusRead:
    not_implemented("BE-16")
