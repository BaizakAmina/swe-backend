from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Event, Order, OrderItem, User
from app.models.enums import EventStatus, Role, TicketKind
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture
def setup(db: Session) -> tuple[Event, dict[str, str]]:
    org = make_organizer(db)
    event = make_event(db, org, capacity=100)
    owner = db.get(User, org.user_id)
    assert owner
    return event, auth_headers(owner)


def create(client: TestClient, event: Event, h: dict[str, str], **kw: object):
    body = {"name": "Standard", "kind": "paid", "price_minor": 500_000, "quantity_total": 50, **kw}
    return client.post(f"{API}/events/{event.id}/ticket-types", json=body, headers=h)


def test_create_and_validation(client: TestClient, setup) -> None:
    event, h = setup
    resp = create(client, event, h)
    assert resp.status_code == 201
    body = resp.json()
    assert body["quantity_available"] == 50 and body["quantity_sold"] == 0
    assert body["on_sale"] is True and body["sellable"] is False  # paid sales not activated

    assert create(client, event, h, kind="free", price_minor=100).status_code == 422
    assert create(client, event, h, kind="paid", price_minor=0).status_code == 422
    resp = create(client, event, h, name="Too many", quantity_total=60)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "exceeds_capacity"

    now = datetime.now(UTC)
    resp = create(
        client,
        event,
        h,
        name="Bad window",
        quantity_total=1,
        sales_start_at=now.isoformat(),
        sales_end_at=(now - timedelta(hours=1)).isoformat(),
    )
    assert resp.status_code == 422


def test_free_type_is_sellable_without_activation(client: TestClient, setup) -> None:
    event, h = setup
    body = create(client, event, h, kind="free", price_minor=0).json()
    assert body["sellable"] is True


def test_quantity_cannot_drop_below_sold(client: TestClient, setup, db: Session) -> None:
    event, h = setup
    tt = make_ticket_type(db, event, quantity_total=20, quantity_sold=8, quantity_reserved=2)
    resp = client.patch(f"{API}/ticket-types/{tt.id}", json={"quantity_total": 9}, headers=h)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "quantity_below_sold"
    resp = client.patch(f"{API}/ticket-types/{tt.id}", json={"quantity_total": 10}, headers=h)
    assert resp.status_code == 200 and resp.json()["quantity_available"] == 0


def test_price_locked_after_sales(client: TestClient, setup, db: Session) -> None:
    event, h = setup
    unsold = make_ticket_type(db, event, quantity_total=5)
    sold = make_ticket_type(db, event, name="Sold", quantity_total=5, quantity_sold=1)
    assert (
        client.patch(f"{API}/ticket-types/{unsold.id}", json={"price_minor": 1}, headers=h)
    ).status_code == 200
    resp = client.patch(f"{API}/ticket-types/{sold.id}", json={"price_minor": 1}, headers=h)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "price_locked"
    resp = client.patch(f"{API}/ticket-types/{unsold.id}", json={"price_minor": 0}, headers=h)
    assert resp.json()["error"]["code"] == "price_kind_mismatch"


def test_hidden_types_not_public(client: TestClient, setup, db: Session) -> None:
    event, h = setup
    tt = make_ticket_type(db, event, name="Secret", quantity_total=5)
    make_ticket_type(db, event, name="Open", quantity_total=5)

    assert client.post(f"{API}/ticket-types/{tt.id}/hide", headers=h).json()["is_hidden"]
    public = client.get(f"{API}/events/{event.id}/ticket-types").json()
    assert [t["name"] for t in public] == ["Open"]
    assert public[0]["quantity_sold"] is None  # counters are organizer-only

    mine = client.get(f"{API}/events/{event.id}/ticket-types", headers=h).json()
    assert {t["name"] for t in mine} == {"Secret", "Open"}
    assert mine[0]["quantity_sold"] == 0

    client.post(f"{API}/ticket-types/{tt.id}/unhide", headers=h)
    assert len(client.get(f"{API}/events/{event.id}/ticket-types").json()) == 2


def test_on_sale_respects_window_and_event_status(client: TestClient, db: Session) -> None:
    org = make_organizer(db)
    now = datetime.now(UTC)
    event = make_event(db, org)
    make_ticket_type(db, event, name="Later", sales_start_at=now + timedelta(days=1))
    make_ticket_type(db, event, name="Over", sales_end_at=now - timedelta(minutes=1))
    make_ticket_type(db, event, name="Now")
    by_name = {t["name"]: t for t in client.get(f"{API}/events/{event.id}/ticket-types").json()}
    assert not by_name["Later"]["on_sale"] and not by_name["Over"]["on_sale"]
    assert by_name["Now"]["on_sale"]

    draft = make_event(db, org, status=EventStatus.DRAFT)
    make_ticket_type(db, draft)
    owner = db.get(User, org.user_id)
    assert owner
    resp = client.get(f"{API}/events/{draft.id}/ticket-types", headers=auth_headers(owner))
    assert resp.json()[0]["on_sale"] is False
    assert client.get(f"{API}/events/{draft.id}/ticket-types").status_code == 404


def test_delete_only_without_orders(client: TestClient, setup, db: Session) -> None:
    event, h = setup
    empty = make_ticket_type(db, event, name="Empty", kind=TicketKind.FREE, quantity_total=5)
    used = make_ticket_type(db, event, name="Used", quantity_total=5)
    buyer = make_user(db)
    order = Order(buyer_user_id=buyer.id, event_id=event.id, subtotal_minor=0, total_minor=0)
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, ticket_type_id=used.id, unit_price_minor=0))
    db.flush()

    resp = client.delete(f"{API}/ticket-types/{used.id}", headers=h)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "ticket_type_has_orders"
    assert client.delete(f"{API}/ticket-types/{empty.id}", headers=h).status_code == 204


def test_other_organizer_forbidden_and_audited(client: TestClient, setup, db: Session) -> None:
    event, h = setup
    tt = create(client, event, h).json()
    other = auth_headers(make_user(db, roles=(Role.ORGANIZER,)))
    assert create(client, event, other).status_code == 403
    resp = client.patch(f"{API}/ticket-types/{tt['id']}", json={"name": "x"}, headers=other)
    assert resp.status_code == 403
    assert client.delete(f"{API}/ticket-types/{tt['id']}", headers=other).status_code == 403

    client.patch(f"{API}/ticket-types/{tt['id']}", json={"name": "Renamed"}, headers=h)
    actions = db.scalars(select(AuditLog.action_type).where(AuditLog.event_id == event.id)).all()
    assert "ticket_type.created" in actions and "ticket_type.updated" in actions
