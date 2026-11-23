import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, VerifiedUser
from app.api.v1._stub import not_implemented
from app.models.enums import OrderStatus
from app.schemas.common import Page
from app.schemas.orders import (
    ActivationStatusRead,
    OrderCreate,
    OrderRead,
    PaymentRead,
    PayRequest,
    RefundRead,
    RefundRequest,
)

router = APIRouter(tags=["orders & payments"])


@router.post("/orders", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_order(data: OrderCreate, user: VerifiedUser, db: DbSession) -> OrderRead:
    """Reserves inventory atomically for ``reserved_until``. Free orders are completed at once."""
    not_implemented("BE-14")


@router.get("/orders/{order_id}", response_model=OrderRead)
def get_order(order_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OrderRead:
    not_implemented("BE-14")


@router.post("/orders/{order_id}/cancel", response_model=OrderRead)
def cancel_pending_order(order_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OrderRead:
    not_implemented("BE-14")


@router.post("/orders/{order_id}/register-free", response_model=OrderRead)
def register_free(order_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OrderRead:
    not_implemented("BE-14")


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
    not_implemented("BE-20")


@router.get("/events/{event_id}/orders", response_model=Page[OrderRead])
def event_orders(
    event: ManagedEvent, db: DbSession, limit: int = 50, offset: int = 0
) -> Page[OrderRead]:
    not_implemented("BE-20")


@router.get("/events/{event_id}/activation", response_model=ActivationStatusRead)
def activation_status(event: ManagedEvent, db: DbSession) -> ActivationStatusRead:
    not_implemented("BE-16")


@router.post("/events/{event_id}/activate-paid-sales", response_model=ActivationStatusRead)
def activate_paid_sales(
    data: PayRequest, event: ManagedEvent, user: CurrentUser, db: DbSession
) -> ActivationStatusRead:
    not_implemented("BE-16")
