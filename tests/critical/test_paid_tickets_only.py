"""No ticket without a succeeded payment (SRS §4.6; BACKEND_ARCHITECTURE.md §5.2)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Order, Payment, Ticket
from app.models.enums import OrderStatus, PaymentPurpose, PaymentStatus, TicketKind
from app.services import orders as orders_svc
from app.services import ticketing
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user
from tests.test_orders import order_body, post_order

pytestmark = [pytest.mark.db, pytest.mark.critical]

API = "/api/v1"


def _tickets(db: Session, order_id: str) -> list[Ticket]:
    return ticketing.tickets_of(db, uuid.UUID(order_id))


def test_failed_payment_issues_no_ticket(client: TestClient, db: Session) -> None:
    ev = make_event(db, make_organizer(db), paid_sales_active=True)
    tt = make_ticket_type(db, ev, price_minor=1_000_000)
    buyer = make_user(db)
    order = post_order(client, buyer, order_body(ev.id, (tt, 2))).json()
    assert _tickets(db, order["id"]) == []  # pending: none

    for _ in range(3):
        resp = client.post(
            f"{API}/orders/{order['id']}/pay",
            json={"simulate": "fail"},
            headers=auth_headers(buyer),
        )
        assert resp.json()["status"] == "failed"
        assert _tickets(db, order["id"]) == []

    stored = db.get(Order, uuid.UUID(order["id"]))
    assert stored is not None and stored.status == OrderStatus.PENDING
    payments = db.scalars(select(Payment).where(Payment.order_id == stored.id)).all()
    assert {p.status for p in payments} == {PaymentStatus.FAILED}

    # forcing "paid" without a succeeded payment doesn't help either
    with pytest.raises(AppError) as exc:
        orders_svc.mark_paid(db, stored)
    assert exc.value.code == "payment_required"
    stored.status = OrderStatus.PAID
    with pytest.raises(AppError) as exc:
        ticketing.issue_tickets(db, stored)
    assert exc.value.code == "payment_required"
    assert _tickets(db, order["id"]) == []


def test_partial_payment_issues_no_ticket(client: TestClient, db: Session) -> None:
    ev = make_event(db, make_organizer(db), paid_sales_active=True)
    tt = make_ticket_type(db, ev, price_minor=1_000_000)
    buyer = make_user(db)
    order = db.get(
        Order, uuid.UUID(post_order(client, buyer, order_body(ev.id, (tt, 1))).json()["id"])
    )
    assert order is not None
    db.add(
        Payment(
            order_id=order.id,
            payer_user_id=buyer.id,
            purpose=PaymentPurpose.TICKET_PURCHASE,
            amount_minor=order.total_minor - 1,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    with pytest.raises(AppError) as exc:
        orders_svc.mark_paid(db, order)
    assert exc.value.code == "payment_required"
    assert _tickets(db, str(order.id)) == []


def test_tickets_appear_only_with_success(client: TestClient, db: Session) -> None:
    ev = make_event(db, make_organizer(db), paid_sales_active=True)
    paid_tt = make_ticket_type(db, ev, price_minor=500_000)
    free_tt = make_ticket_type(db, ev, kind=TicketKind.FREE)
    buyer = make_user(db)

    order = post_order(client, buyer, order_body(ev.id, (paid_tt, 2))).json()
    client.post(f"{API}/orders/{order['id']}/pay", json={}, headers=auth_headers(buyer))
    assert len(_tickets(db, order["id"])) == 2

    free = post_order(client, buyer, order_body(ev.id, (free_tt, 1))).json()
    assert free["status"] == "paid" and len(_tickets(db, free["id"])) == 1
