import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, PaymentGatewayDep, VerifiedUser
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
from app.services.payments import activation, checkout

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
    order_id: uuid.UUID,
    data: PayRequest,
    user: CurrentUser,
    db: DbSession,
    gateway: PaymentGatewayDep,
) -> PaymentRead:
    """Simulated payment (``is_simulated: true``); tickets are issued only on success.

    Only the buyer, only a pending order within ``reserved_until``. The response is the payment:
    ``status`` is ``succeeded`` or ``failed``. After a failure the order stays pending until
    ``reserved_until`` and can be paid again. Paying a paid order → 409 ``order_already_paid``.
    """
    payment = checkout.pay_order(db, user, order_id, simulate=data.simulate, gateway=gateway)
    return PaymentRead.model_validate(payment)


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
    """Checklist, activation fee and whether paid sales are active. Organizer or platform admin."""
    return activation.get_status(db, event)


@router.post("/events/{event_id}/activate-paid-sales", response_model=ActivationStatusRead)
def activate_paid_sales(
    data: PayRequest,
    event: ManagedEvent,
    user: CurrentUser,
    db: DbSession,
    gateway: PaymentGatewayDep,
) -> ActivationStatusRead:
    """Pay the activation fee (simulated) and turn on paid ticket sales.

    422 ``activation_checklist_incomplete`` with ``details.missing`` if the checklist isn't done;
    409 if paid sales are already active. A failed (simulated) payment leaves sales inactive —
    see ``activation_payment.status`` — and can be retried.
    """
    return activation.activate(db, user, event.id, simulate=data.simulate, gateway=gateway)
