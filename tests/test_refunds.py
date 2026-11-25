"""BE-24: cancellations and (simulated) refunds."""

import threading
import uuid
from collections import Counter
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import (
    AuditLog,
    Order,
    Payment,
    Refund,
    StaffAssignment,
    Ticket,
    TicketType,
    User,
)
from app.models.enums import (
    OrderStatus,
    PaymentStatus,
    RefundStatus,
    Role,
    TicketKind,
    TicketStatus,
)
from app.schemas.orders import AttendeeIn, OrderCreate, OrderLineIn
from app.services import orders as orders_svc
from app.services import refunds, ticketing
from app.services.payments import checkout
from app.services.payments.gateway import SimulatedGateway
from tests.committed_data import cleanup
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user
from tests.test_orders import error_code, order_body, post_order
from tests.test_promo import make_campaign

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture
def shop(db: Session) -> SimpleNamespace:
    org = make_organizer(db)
    owner = db.get(User, org.user_id)
    ev = make_event(db, org, paid_sales_active=True)
    paid = make_ticket_type(db, ev, name="Paid", price_minor=800_000, quantity_total=4)
    free = make_ticket_type(db, ev, name="Free", kind=TicketKind.FREE, quantity_total=4)
    staff = make_user(db)
    db.add(StaffAssignment(event_id=ev.id, user_id=staff.id))
    db.flush()
    return SimpleNamespace(
        event=ev, owner=owner, paid=paid, free=free, staff=staff, buyer=make_user(db)
    )


def paid_order(client: TestClient, shop: SimpleNamespace, qty: int = 2, **kw: Any) -> Any:
    buyer = kw.pop("buyer", shop.buyer)
    body = order_body(shop.event.id, (shop.paid, qty), **kw)
    order = post_order(client, buyer, body).json()
    pay = client.post(f"{API}/orders/{order['id']}/pay", json={}, headers=auth_headers(buyer))
    assert pay.json()["status"] == "succeeded", pay.text
    return order


def free_order(client: TestClient, shop: SimpleNamespace, qty: int = 1, buyer: Any = None) -> Any:
    order = post_order(client, buyer or shop.buyer, order_body(shop.event.id, (shop.free, qty)))
    assert order.json()["status"] == "paid", order.text
    return order.json()


def refund(client: TestClient, user: User, order_id: Any, reason: str | None = None) -> Any:
    return client.post(
        f"{API}/orders/{order_id}/refund", json={"reason": reason}, headers=auth_headers(user)
    )


def tickets(db: Session, order_id: Any) -> list[Ticket]:
    found = ticketing.tickets_of(db, uuid.UUID(str(order_id)))
    for t in found:
        db.refresh(t)
    return found


def refunds_of(db: Session, order_id: Any) -> list[Refund]:
    return list(
        db.scalars(
            select(Refund)
            .join(Payment, Payment.id == Refund.payment_id)
            .where(Payment.order_id == uuid.UUID(str(order_id)))
        )
    )


def test_refund_invalidates_tickets_and_frees_places(
    client: TestClient, shop, db: Session, outbox: list[dict[str, Any]]
) -> None:
    order = paid_order(client, shop, qty=2)
    db.refresh(shop.paid)
    assert shop.paid.quantity_sold == 2

    resp = refund(client, shop.owner, order["id"], "Buyer asked via support")
    assert resp.status_code == 200, resp.text
    r = resp.json()
    assert r["status"] == "succeeded" and r["is_simulated"] is True
    assert r["amount_minor"] == order["total_minor"] == 1_600_000
    assert r["reason"] == "Buyer asked via support" and r["processed_at"]

    current = client.get(f"{API}/orders/{order['id']}", headers=auth_headers(shop.buyer)).json()
    assert current["status"] == "refunded"
    ts = tickets(db, order["id"])
    assert [t.status for t in ts] == [TicketStatus.REFUNDED] * 2

    # the ticket no longer passes check-in
    scan = client.post(
        f"{API}/checkin",
        json={"event_id": str(shop.event.id), "qr_payload": ts[0].qr_payload},
        headers=auth_headers(shop.staff),
    )
    assert scan.status_code == 409 and error_code(scan) == "refunded"

    # the places are available again
    db.refresh(shop.paid)
    assert (shop.paid.quantity_sold, shop.paid.quantity_reserved) == (0, 0)
    assert (
        post_order(client, make_user(db), order_body(shop.event.id, (shop.paid, 4))).status_code
        == 201
    )

    assert [m["to"] for m in outbox if m["kind"] == "refund_processed"] == [shop.buyer.email]
    assert "refund.processed" in set(
        db.scalars(select(AuditLog.action_type).where(AuditLog.entity_id == uuid.UUID(order["id"])))
    )


def test_double_refund_is_409_and_one_refund(client: TestClient, shop, db: Session) -> None:
    order = paid_order(client, shop)
    assert refund(client, shop.owner, order["id"]).status_code == 200
    again = refund(client, shop.owner, order["id"])
    assert again.status_code == 409 and error_code(again) == "order_already_refunded"
    assert len(refunds_of(db, order["id"])) == 1


def test_who_may_refund(client: TestClient, shop, db: Session) -> None:
    order = paid_order(client, shop)
    for who in (shop.buyer, make_user(db), make_organizer(db).user, shop.staff):
        assert refund(client, who, order["id"]).status_code == 403
    admin = make_user(db, roles=(Role.PLATFORM_ADMIN,))
    assert refund(client, admin, order["id"]).status_code == 200


def test_checked_in_order_cannot_be_refunded(client: TestClient, shop, db: Session) -> None:
    order = paid_order(client, shop, qty=2)
    first = tickets(db, order["id"])[0]
    client.post(
        f"{API}/checkin",
        json={"event_id": str(shop.event.id), "qr_payload": first.qr_payload},
        headers=auth_headers(shop.staff),
    )
    resp = refund(client, shop.owner, order["id"])
    assert resp.status_code == 409 and error_code(resp) == "ticket_already_checked_in"
    assert refunds_of(db, order["id"]) == []
    assert {t.status for t in tickets(db, order["id"])} == {
        TicketStatus.CHECKED_IN,
        TicketStatus.VALID,
    }


def test_refund_needs_a_paid_order(client: TestClient, shop, db: Session) -> None:
    free = free_order(client, shop)
    resp = refund(client, shop.owner, free["id"])
    assert resp.status_code == 409 and error_code(resp) == "order_is_free"
    pending = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 1))).json()
    resp = refund(client, shop.owner, pending["id"])
    assert resp.status_code == 409 and error_code(resp) == "order_not_paid"


def test_cancel_free_registration(client: TestClient, shop, db: Session) -> None:
    order = free_order(client, shop, qty=2)
    url = f"{API}/orders/{order['id']}/cancel-registration"
    assert client.post(url, headers=auth_headers(make_user(db))).status_code == 403

    resp = client.post(url, headers=auth_headers(shop.buyer))
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
    assert [t.status for t in tickets(db, order["id"])] == [TicketStatus.CANCELLED] * 2
    db.refresh(shop.free)
    assert shop.free.quantity_sold == 0

    again = client.post(url, headers=auth_headers(shop.buyer))
    assert again.status_code == 409 and error_code(again) == "order_already_cancelled"

    # the organizer may cancel a registration too; paid orders are refunded instead
    other = free_order(client, shop)
    resp = client.post(
        f"{API}/orders/{other['id']}/cancel-registration", headers=auth_headers(shop.owner)
    )
    assert resp.status_code == 200
    paid = paid_order(client, shop, qty=1)
    resp = client.post(
        f"{API}/orders/{paid['id']}/cancel-registration", headers=auth_headers(shop.buyer)
    )
    assert resp.status_code == 409 and error_code(resp) == "order_not_free"


def test_refund_gives_the_promo_use_back(client: TestClient, shop, db: Session) -> None:
    campaign = make_campaign(db, shop.event, code="ONCE", value=25, max_redemptions=1)
    order = paid_order(client, shop, qty=1, promo_code="ONCE")
    assert order["discount_minor"] == 200_000
    db.refresh(campaign)
    assert campaign.redemption_count == 1

    r = refund(client, shop.owner, order["id"]).json()
    assert r["amount_minor"] == 600_000  # what was actually paid
    db.refresh(campaign)
    assert campaign.redemption_count == 0
    again = post_order(
        client, make_user(db), order_body(shop.event.id, (shop.paid, 1), promo_code="ONCE")
    )
    assert again.status_code == 201


def test_analytics_after_refund(client: TestClient, shop, db: Session) -> None:
    kept = paid_order(client, shop, qty=1)
    refunded = paid_order(client, shop, qty=2, buyer=make_user(db))
    refund(client, shop.owner, refunded["id"])
    a = client.get(
        f"{API}/events/{shop.event.id}/analytics", headers=auth_headers(shop.owner)
    ).json()
    assert a["revenue"]["gross_minor"] == 2_400_000
    assert a["revenue"]["refunds_minor"] == 1_600_000
    assert a["revenue"]["net_minor"] == kept["total_minor"] == 800_000
    assert a["capacity"]["sold"] == 1
    by_type = {t["name"]: t for t in a["by_ticket_type"]}
    assert by_type["Paid"]["refunded"] == 2


def test_event_cancellation_closes_every_order(
    client: TestClient, shop, db: Session, outbox: list[dict[str, Any]]
) -> None:
    paid_buyer, free_buyer, pending_buyer = make_user(db), make_user(db), make_user(db)
    paid = paid_order(client, shop, qty=1, buyer=paid_buyer)
    used = paid_order(client, shop, qty=1)  # checked in before the cancellation
    client.post(
        f"{API}/checkin",
        json={"event_id": str(shop.event.id), "qr_payload": tickets(db, used["id"])[0].qr_payload},
        headers=auth_headers(shop.staff),
    )
    free = free_order(client, shop, buyer=free_buyer)
    pending = post_order(client, pending_buyer, order_body(shop.event.id, (shop.paid, 1))).json()

    resp = client.post(
        f"{API}/events/{shop.event.id}/cancel",
        json={"reason": "Storm"},
        headers=auth_headers(shop.owner),
    )
    assert resp.status_code == 200, resp.text

    statuses = {
        o["id"]: db.get(Order, uuid.UUID(o["id"])).status  # type: ignore[union-attr]
        for o in (paid, used, free, pending)
    }
    assert statuses == {
        paid["id"]: OrderStatus.REFUNDED,
        used["id"]: OrderStatus.REFUNDED,  # the event didn't happen: refunded anyway
        free["id"]: OrderStatus.CANCELLED,
        pending["id"]: OrderStatus.CANCELLED,
    }
    assert {t.status for t in tickets(db, paid["id"]) + tickets(db, used["id"])} == {
        TicketStatus.REFUNDED
    }
    assert [t.status for t in tickets(db, free["id"])] == [TicketStatus.CANCELLED]
    for tt in (shop.paid, shop.free):
        db.refresh(tt)
        assert (tt.quantity_sold, tt.quantity_reserved) == (0, 0)
    for o in (paid, used):
        [r] = refunds_of(db, o["id"])
        assert r.status == RefundStatus.SUCCEEDED and r.reason == refunds.EVENT_CANCELLED_REASON

    cancelled_to = sorted(m["to"] for m in outbox if m["kind"] == "event_cancelled")
    assert cancelled_to == sorted([paid_buyer.email, shop.buyer.email, free_buyer.email])
    refund_to = sorted(m["to"] for m in outbox if m["kind"] == "refund_processed")
    assert refund_to == sorted([paid_buyer.email, shop.buyer.email])


# ------------------------------------------------------------------ concurrency

PARALLEL = 4


@pytest.fixture
def parallel_engine(engine: Engine) -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, pool_size=PARALLEL + 2, max_overflow=0)
    yield eng
    eng.dispose()


def test_parallel_refunds_refund_once(parallel_engine: Engine) -> None:
    with Session(parallel_engine) as s:
        org = make_organizer(s)
        ev = make_event(s, org, paid_sales_active=True)
        tt = make_ticket_type(s, ev, price_minor=700_000)
        buyer = make_user(s)
        s.commit()
        event_id, owner_id, user_ids = ev.id, org.user_id, [org.user_id, buyer.id]
        order = orders_svc.create_order(
            s,
            buyer,
            OrderCreate(
                event_id=ev.id,
                items=[OrderLineIn(ticket_type_id=tt.id, quantity=1)],
                attendees=[AttendeeIn(full_name="Guest", email="guest@example.com")],
            ),
        )
        checkout.pay_order(s, buyer, order.id, simulate="success", gateway=SimulatedGateway())
        order_id, tt_id = order.id, tt.id

    try:
        barrier = threading.Barrier(PARALLEL)
        results: list[Any] = []
        lock = threading.Lock()

        def run() -> None:
            with Session(parallel_engine) as db:
                owner = db.get(User, owner_id)
                assert owner is not None
                db.connection()
                barrier.wait()
                try:
                    outcome: Any = refunds.refund(
                        db, owner, order_id, None, gateway=SimulatedGateway()
                    ).status
                except AppError as exc:
                    outcome = exc.code
            with lock:
                results.append(str(outcome))

        threads = [threading.Thread(target=run) for _ in range(PARALLEL)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert Counter(results) == {"succeeded": 1, "order_already_refunded": PARALLEL - 1}
        with Session(parallel_engine) as s:
            assert len(refunds_of(s, order_id)) == 1
            paid = s.scalars(
                select(Payment).where(
                    Payment.order_id == order_id, Payment.status == PaymentStatus.SUCCEEDED
                )
            ).all()
            assert len(paid) == 1
            ticket_type = s.get(TicketType, tt_id)
            assert ticket_type is not None and ticket_type.quantity_sold == 0
    finally:
        cleanup(parallel_engine, event_id, user_ids)
