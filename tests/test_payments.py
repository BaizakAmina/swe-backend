"""BE-15: simulated payment of an order."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditLog, Order, Payment, Ticket, User
from app.models.enums import OrderStatus, PaymentProvider, PaymentStatus, TicketKind
from app.services import orders as orders_svc
from app.services.payments.gateway import ChargeResult, SimulatedGateway, get_gateway
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user
from tests.test_orders import _expire_now, _pending_free_order, error_code, order_body, post_order

pytestmark = pytest.mark.db

API = "/api/v1"


def issue_test_tickets(db: Session, order: Order) -> None:
    """Stand-in for BE-17's issuer: one ticket per item, and only with a succeeded payment."""
    if order.total_minor > 0:
        paid = db.scalar(
            select(Payment).where(
                Payment.order_id == order.id, Payment.status == PaymentStatus.SUCCEEDED
            )
        )
        assert paid is not None, "ticket issued without a succeeded payment"
    for item in order.items:
        code = uuid.uuid4().hex[:12].upper()
        item.ticket = Ticket(
            event_id=order.event_id,
            attendee_id=item.attendee.id,
            ticket_code=code,
            qr_payload=f"T1.{code}.sig",
        )


@pytest.fixture(autouse=True)
def ticket_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orders_svc, "on_order_paid_hooks", [issue_test_tickets])


@pytest.fixture
def shop(db: Session) -> SimpleNamespace:
    org = make_organizer(db)
    ev = make_event(db, org, paid_sales_active=True)
    paid = make_ticket_type(db, ev, name="Standard", price_minor=1_500_000, quantity_total=10)
    free = make_ticket_type(db, ev, name="Free", kind=TicketKind.FREE, quantity_total=10)
    return SimpleNamespace(event=ev, paid=paid, free=free, buyer=make_user(db))


def new_order(client: TestClient, shop: SimpleNamespace, qty: int = 2) -> dict[str, Any]:
    resp = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, qty)))
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


def pay(client: TestClient, user: User, order_id: Any, simulate: str = "success") -> Any:
    return client.post(
        f"{API}/orders/{order_id}/pay", json={"simulate": simulate}, headers=auth_headers(user)
    )


def tickets_of(db: Session, order_id: Any) -> int:
    from app.models import OrderItem

    stmt = (
        select(func.count())
        .select_from(Ticket)
        .join(OrderItem, OrderItem.id == Ticket.order_item_id)
        .where(OrderItem.order_id == order_id)
    )
    return int(db.scalar(stmt) or 0)


def payments_of(db: Session, order_id: Any) -> list[Payment]:
    stmt = select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at)
    return list(db.scalars(stmt))


def actions(db: Session, entity_id: Any) -> list[str]:
    stmt = select(AuditLog.action_type).where(AuditLog.entity_id == entity_id)
    return list(db.scalars(stmt.order_by(AuditLog.occurred_at)))


def test_success_pays_order_and_issues_tickets(
    client: TestClient, shop, db: Session, outbox: list[dict[str, Any]]
) -> None:
    order = new_order(client, shop, qty=2)
    resp = pay(client, shop.buyer, order["id"])
    assert resp.status_code == 200, resp.text
    payment = resp.json()
    assert payment["status"] == "succeeded"
    assert payment["is_simulated"] is True
    assert payment["purpose"] == "ticket_purchase"
    assert payment["amount_minor"] == order["total_minor"] == 3_000_000
    assert payment["order_id"] == order["id"]
    assert payment["provider_ref"].startswith("sim_ch_")
    assert payment["completed_at"] and payment["failure_reason"] is None

    paid = client.get(f"{API}/orders/{order['id']}", headers=auth_headers(shop.buyer)).json()
    assert paid["status"] == "paid" and paid["paid_at"]
    assert all(i["ticket_id"] for i in paid["items"])
    assert tickets_of(db, order["id"]) == 2
    db.refresh(shop.paid)
    assert (shop.paid.quantity_reserved, shop.paid.quantity_sold) == (0, 2)

    assert actions(db, payment["id"]) == ["payment.succeeded"]
    assert actions(db, order["id"]) == ["order.created", "order.paid"]
    assert [m["kind"] for m in outbox] == ["order_confirmation"]


def test_fail_issues_nothing_and_can_be_retried(client: TestClient, shop, db: Session) -> None:
    order = new_order(client, shop, qty=2)
    resp = pay(client, shop.buyer, order["id"], "fail")
    assert resp.status_code == 200, resp.text
    failed = resp.json()
    assert failed["status"] == "failed" and failed["is_simulated"] is True
    assert failed["failure_reason"]
    assert actions(db, failed["id"]) == ["payment.failed"]

    # order stays pending with its reservation, no tickets
    current = client.get(f"{API}/orders/{order['id']}", headers=auth_headers(shop.buyer)).json()
    assert current["status"] == "pending" and current["reserved_until"]
    assert tickets_of(db, order["id"]) == 0
    db.refresh(shop.paid)
    assert (shop.paid.quantity_reserved, shop.paid.quantity_sold) == (2, 0)

    # retry within reserved_until
    retry = pay(client, shop.buyer, order["id"]).json()
    assert retry["status"] == "succeeded" and retry["provider_ref"] != failed["provider_ref"]
    assert [p.status for p in payments_of(db, order["id"])] == [
        PaymentStatus.FAILED,
        PaymentStatus.SUCCEEDED,
    ]
    assert tickets_of(db, order["id"]) == 2


def test_paying_twice_is_rejected(client: TestClient, shop, db: Session) -> None:
    order = new_order(client, shop)
    assert pay(client, shop.buyer, order["id"]).status_code == 200
    resp = pay(client, shop.buyer, order["id"])
    assert resp.status_code == 409 and error_code(resp) == "order_already_paid"
    assert len(payments_of(db, order["id"])) == 1
    assert tickets_of(db, order["id"]) == 2


def test_only_buyer_and_pending_orders(client: TestClient, shop, db: Session) -> None:
    order = new_order(client, shop)
    assert pay(client, make_user(db), order["id"]).status_code == 403
    assert pay(client, shop.buyer, uuid.uuid4()).status_code == 404
    assert client.post(f"{API}/orders/{order['id']}/pay", json={}).status_code == 401
    resp = client.post(
        f"{API}/orders/{order['id']}/pay",
        json={"simulate": "maybe"},
        headers=auth_headers(shop.buyer),
    )
    assert resp.status_code == 422

    cancelled = new_order(client, shop)
    client.post(f"{API}/orders/{cancelled['id']}/cancel", headers=auth_headers(shop.buyer))
    resp = pay(client, shop.buyer, cancelled["id"])
    assert resp.status_code == 409 and error_code(resp) == "order_not_pending"

    free = _pending_free_order(db, shop.buyer, shop.free)
    resp = pay(client, shop.buyer, free.id)
    assert resp.status_code == 409 and error_code(resp) == "order_is_free"

    assert payments_of(db, order["id"]) == []


def test_expired_reservation_cannot_be_paid(client: TestClient, shop, db: Session) -> None:
    order = new_order(client, shop, qty=3)
    _expire_now(db, order["id"])
    resp = pay(client, shop.buyer, order["id"])
    assert resp.status_code == 409 and error_code(resp) == "reservation_expired"
    expired = db.get(Order, order["id"])
    assert expired and expired.status == OrderStatus.EXPIRED
    db.refresh(shop.paid)
    assert shop.paid.quantity_reserved == 0
    assert payments_of(db, order["id"]) == [] and tickets_of(db, order["id"]) == 0


def test_no_tickets_without_succeeded_payment(client: TestClient, shop, db: Session) -> None:
    """mark_paid refuses a paid order that has only failed / smaller payments."""
    order_json = new_order(client, shop)
    pay(client, shop.buyer, order_json["id"], "fail")
    order = orders_svc.lock_order(db, order_json["id"])
    db.add(
        Payment(
            order_id=order.id,
            payer_user_id=shop.buyer.id,
            amount_minor=order.total_minor - 1,
            status=PaymentStatus.SUCCEEDED,
            purpose="ticket_purchase",
        )
    )
    with pytest.raises(Exception) as exc:
        orders_svc.mark_paid(db, order)
    assert getattr(exc.value, "code", None) == "payment_required"
    assert tickets_of(db, order.id) == 0


def test_gateway_comes_from_dependency(client: TestClient, shop, db: Session) -> None:
    calls: list[dict[str, Any]] = []

    class SandboxStub:
        provider = PaymentProvider.SANDBOX
        is_simulated = True

        def charge(self, **kw: Any) -> ChargeResult:
            calls.append(kw)
            return ChargeResult(succeeded=True, provider_ref="sbx_42")

        def refund(self, **kw: Any) -> Any:
            raise NotImplementedError

    order = new_order(client, shop, qty=1)
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_gateway] = SandboxStub
    try:
        payment = pay(client, shop.buyer, order["id"]).json()
    finally:
        overrides.pop(get_gateway)
    assert payment["provider_ref"] == "sbx_42" and payment["is_simulated"] is True
    assert calls == [
        {
            "amount_minor": 1_500_000,
            "currency": "KZT",
            "reference": order["id"],
            "simulate": "success",
        }
    ]
    stored = db.get(Payment, payment["id"])
    assert stored and stored.provider == PaymentProvider.SANDBOX


def test_gateway_config(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(get_gateway(), SimulatedGateway)
    monkeypatch.setattr(get_settings(), "payment_gateway", "nope")
    with pytest.raises(RuntimeError):
        get_gateway()


def test_simulated_gateway() -> None:
    gw = SimulatedGateway()
    ok = gw.charge(amount_minor=100, currency="KZT", reference="o1", simulate="success")
    bad = gw.charge(amount_minor=100, currency="KZT", reference="o1", simulate="fail")
    assert ok.succeeded and not ok.failure_reason
    assert not bad.succeeded and bad.failure_reason
    assert ok.provider_ref != bad.provider_ref
    refund = gw.refund(provider_ref=ok.provider_ref, amount_minor=100)
    assert refund.succeeded and refund.provider_ref.startswith("sim_rf_")
    for kwargs in (
        {"simulate": "maybe", "amount_minor": 100},
        {"simulate": "success", "amount_minor": 0},
    ):
        with pytest.raises(ValueError):
            gw.charge(currency="KZT", reference="o1", **kwargs)  # type: ignore[arg-type]
