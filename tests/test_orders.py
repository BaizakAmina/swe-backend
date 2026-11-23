"""BE-14: atomic reservation, free registration, cancel, expiry sweep."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, event, select, update
from sqlalchemy.orm import Session

from app.models import Attendee, AuditLog, Order, OrderItem, TicketType, User
from app.models.enums import EventStatus, OrderStatus, Role, TicketKind
from app.services import orders as orders_svc
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture
def shop(db: Session) -> SimpleNamespace:
    org = make_organizer(db)
    ev = make_event(db, org, title="Jazz", paid_sales_active=True)
    paid = make_ticket_type(
        db, ev, name="Standard", price_minor=1_000_000, quantity_total=10, max_per_order=4
    )
    free = make_ticket_type(
        db, ev, name="Student", kind=TicketKind.FREE, quantity_total=5, max_per_order=2
    )
    owner = db.get(User, org.user_id)
    assert owner
    return SimpleNamespace(
        event=ev, paid=paid, free=free, owner=owner, buyer=make_user(db, full_name="Buyer")
    )


def order_body(event_id: Any, *lines: tuple[TicketType, int], **extra: Any) -> dict[str, Any]:
    n = sum(q for _, q in lines)
    return {
        "event_id": str(event_id),
        "items": [{"ticket_type_id": str(tt.id), "quantity": q} for tt, q in lines],
        "attendees": [
            {"full_name": f"Guest {i}", "email": f"guest{i}@example.com"} for i in range(n)
        ],
        **extra,
    }


def post_order(client: TestClient, user: User, body: dict[str, Any]) -> Any:
    return client.post(f"{API}/orders", json=body, headers=auth_headers(user))


def error_code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


def actions_for(db: Session, order_id: Any) -> list[str]:
    stmt = select(AuditLog.action_type).where(AuditLog.entity_id == order_id)
    return list(db.scalars(stmt.order_by(AuditLog.occurred_at)))


# ------------------------------------------------------------------ create


def test_create_reserves_with_server_prices(client: TestClient, shop, db: Session) -> None:
    body = order_body(shop.event.id, (shop.paid, 2))
    # Anything price-like from the client is ignored.
    body["items"][0]["unit_price_minor"] = 1
    body.update(subtotal_minor=1, total_minor=1, discount_minor=999)

    resp = post_order(client, shop.buyer, body)
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert order["status"] == "pending"
    assert order["subtotal_minor"] == order["total_minor"] == 2_000_000
    assert order["discount_minor"] == 0 and order["fees_minor"] == 0
    assert [i["unit_price_minor"] for i in order["items"]] == [1_000_000, 1_000_000]
    assert [i["attendee_name"] for i in order["items"]] == ["Guest 0", "Guest 1"]
    assert all(i["ticket_id"] is None for i in order["items"])  # tickets only after payment

    reserved_until = datetime.fromisoformat(order["reserved_until"])
    expected = datetime.now(UTC) + timedelta(minutes=10)
    assert abs((reserved_until - expected).total_seconds()) < 60

    db.refresh(shop.paid)
    assert (shop.paid.quantity_reserved, shop.paid.quantity_sold) == (2, 0)
    assert actions_for(db, order["id"]) == ["order.created"]


def test_free_registration_completes_at_once(
    client: TestClient, shop, db: Session, outbox: list[dict[str, Any]], monkeypatch
) -> None:
    issued: list[Any] = []
    monkeypatch.setattr(orders_svc, "on_order_paid_hooks", [lambda _db, o: issued.append(o.id)])

    resp = post_order(client, shop.buyer, order_body(shop.event.id, (shop.free, 2)))
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert order["status"] == "paid" and order["total_minor"] == 0
    assert order["paid_at"] and order["reserved_until"] is None
    assert [str(x) for x in issued] == [order["id"]]

    db.refresh(shop.free)
    assert (shop.free.quantity_reserved, shop.free.quantity_sold) == (0, 2)
    assert actions_for(db, order["id"]) == ["order.created", "order.paid"]
    assert [m["kind"] for m in outbox] == ["order_confirmation"]


def test_paid_type_needs_paid_sales_active(client: TestClient, shop, db: Session) -> None:
    shop.event.paid_sales_active = False
    db.flush()
    resp = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 1)))
    assert resp.status_code == 409 and error_code(resp) == "paid_sales_inactive"
    # free tickets don't need activation
    resp = post_order(client, shop.buyer, order_body(shop.event.id, (shop.free, 1)))
    assert resp.status_code == 201


def test_max_per_order(client: TestClient, shop, db: Session) -> None:
    resp = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 5)))
    assert resp.status_code == 422 and error_code(resp) == "max_per_order_exceeded"
    db.refresh(shop.paid)
    assert shop.paid.quantity_reserved == 0


def test_sold_out(client: TestClient, shop, db: Session) -> None:
    shop.paid.quantity_sold = 9
    db.flush()
    resp = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 2)))
    assert resp.status_code == 409 and error_code(resp) == "sold_out"
    assert resp.json()["error"]["details"]["available"] == 1
    assert post_order(
        client, shop.buyer, order_body(shop.event.id, (shop.paid, 1))
    ).status_code == (201)


def test_event_must_be_on_sale(client: TestClient, shop, db: Session) -> None:
    body = order_body(shop.event.id, (shop.paid, 1))
    for field, value in [
        ("status", EventStatus.DRAFT),
        ("status", EventStatus.CANCELLED),
        ("suspended_at", datetime.now(UTC)),
        ("registration_closes_at", datetime.now(UTC) - timedelta(minutes=1)),
    ]:
        original = getattr(shop.event, field)
        setattr(shop.event, field, value)
        db.flush()
        resp = post_order(client, shop.buyer, body)
        assert resp.status_code == 409, (field, value, resp.text)
        assert error_code(resp) in {"event_not_on_sale", "registration_closed"}
        setattr(shop.event, field, original)
        db.flush()
    assert post_order(client, shop.buyer, body).status_code == 201


def test_ticket_type_must_be_on_sale(client: TestClient, shop, db: Session) -> None:
    hidden = make_ticket_type(db, shop.event, name="Hidden", quantity_total=1, is_hidden=True)
    later = make_ticket_type(
        db,
        shop.event,
        name="Later",
        quantity_total=1,
        sales_start_at=datetime.now(UTC) + timedelta(days=1),
    )
    for tt in (hidden, later):
        resp = post_order(client, shop.buyer, order_body(shop.event.id, (tt, 1)))
        assert resp.status_code == 409 and error_code(resp) == "ticket_type_not_on_sale"


def test_request_validation(client: TestClient, shop, db: Session) -> None:
    ev_id = shop.event.id
    body = order_body(ev_id, (shop.paid, 2))
    body["attendees"] = body["attendees"][:1]
    resp = post_order(client, shop.buyer, body)
    assert resp.status_code == 422 and error_code(resp) == "attendees_mismatch"

    resp = post_order(client, shop.buyer, order_body(ev_id, (shop.paid, 1), (shop.paid, 1)))
    assert error_code(resp) == "duplicate_ticket_type"

    other_type = make_ticket_type(db, make_event(db, paid_sales_active=True))
    resp = post_order(client, shop.buyer, order_body(ev_id, (other_type, 1)))
    assert resp.status_code == 404 and error_code(resp) == "ticket_type_not_found"

    resp = post_order(client, shop.buyer, order_body(ev_id, (shop.paid, 1), promo_code="DEMO10"))
    assert resp.status_code == 422 and error_code(resp) == "promo_codes_unavailable"

    unverified = make_user(db, email_verified_at=None)
    resp = post_order(client, unverified, order_body(ev_id, (shop.paid, 1)))
    assert resp.status_code == 403 and error_code(resp) == "email_not_verified"

    assert client.post(f"{API}/orders", json=order_body(ev_id, (shop.paid, 1))).status_code == 401
    db.refresh(shop.paid)
    assert shop.paid.quantity_reserved == 0


def test_reservation_uses_for_update_in_id_order(
    client: TestClient, shop, db: Session, connection: Connection
) -> None:
    statements: list[str] = []

    def capture(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(connection, "before_cursor_execute", capture)
    try:
        resp = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 1)))
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert resp.status_code == 201
    locks = [s for s in statements if "FROM ticket_type" in s and "FOR UPDATE" in s]
    assert locks and "ORDER BY ticket_type.id" in locks[0], statements
    assert any("FROM event" in s and "FOR SHARE" in s for s in statements)


# ------------------------------------------------------------------ read / cancel


def test_get_order_access(client: TestClient, shop, db: Session) -> None:
    order = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 1))).json()
    url = f"{API}/orders/{order['id']}"
    admin = make_user(db, roles=(Role.PLATFORM_ADMIN,))
    for who in (shop.buyer, shop.owner, admin):
        resp = client.get(url, headers=auth_headers(who))
        assert resp.status_code == 200 and resp.json()["id"] == order["id"]

    other_org = make_organizer(db).user
    for who in (make_user(db), other_org):
        assert client.get(url, headers=auth_headers(who)).status_code == 403
    missing = f"{API}/orders/00000000-0000-0000-0000-000000000000"
    assert client.get(missing, headers=auth_headers(shop.buyer)).status_code == 404


def test_cancel_returns_reservation(client: TestClient, shop, db: Session) -> None:
    order = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 3))).json()
    url = f"{API}/orders/{order['id']}/cancel"

    assert client.post(url, headers=auth_headers(make_user(db))).status_code == 403
    resp = client.post(url, headers=auth_headers(shop.buyer))
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
    db.refresh(shop.paid)
    assert shop.paid.quantity_reserved == 0
    # idempotent
    assert client.post(url, headers=auth_headers(shop.buyer)).json()["status"] == "cancelled"
    db.refresh(shop.paid)
    assert shop.paid.quantity_reserved == 0
    assert actions_for(db, order["id"]) == ["order.created", "order.cancelled"]

    free = post_order(client, shop.buyer, order_body(shop.event.id, (shop.free, 1))).json()
    resp = client.post(f"{API}/orders/{free['id']}/cancel", headers=auth_headers(shop.buyer))
    assert resp.status_code == 409 and error_code(resp) == "order_not_pending"


# ------------------------------------------------------------------ expiry


def _expire_now(db: Session, order_id: Any) -> None:
    db.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(reserved_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.flush()


def test_sweep_releases_expired_reservation(client: TestClient, shop, db: Session) -> None:
    last = make_ticket_type(db, shop.event, name="Last", quantity_total=1)
    first = post_order(client, shop.buyer, order_body(shop.event.id, (last, 1))).json()
    late = make_user(db)
    resp = post_order(client, late, order_body(shop.event.id, (last, 1)))
    assert resp.status_code == 409 and error_code(resp) == "sold_out"

    _expire_now(db, first["id"])
    assert orders_svc.expire_stale_orders(db) == 1
    assert orders_svc.expire_stale_orders(db) == 0

    expired = db.get(Order, first["id"])
    assert expired and expired.status == OrderStatus.EXPIRED
    db.refresh(last)
    assert last.quantity_reserved == 0
    assert actions_for(db, first["id"]) == ["order.created", "order.expired"]

    # the ticket is available again
    assert post_order(client, late, order_body(shop.event.id, (last, 1))).status_code == 201


def test_sweep_leaves_fresh_and_paid_orders(client: TestClient, shop, db: Session) -> None:
    pending = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 1))).json()
    free = post_order(client, shop.buyer, order_body(shop.event.id, (shop.free, 1))).json()
    assert orders_svc.expire_stale_orders(db) == 0
    assert client.get(f"{API}/orders/{pending['id']}", headers=auth_headers(shop.buyer)).json()[
        "status"
    ] == ("pending")
    paid = db.get(Order, free["id"])
    assert paid and paid.status == OrderStatus.PAID


def test_lazy_expiry_on_access(client: TestClient, shop, db: Session) -> None:
    order = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 2))).json()
    _expire_now(db, order["id"])
    resp = client.get(f"{API}/orders/{order['id']}", headers=auth_headers(shop.buyer))
    assert resp.status_code == 200 and resp.json()["status"] == "expired"
    db.refresh(shop.paid)
    assert shop.paid.quantity_reserved == 0
    # cancelling an expired order changes nothing
    resp = client.post(f"{API}/orders/{order['id']}/cancel", headers=auth_headers(shop.buyer))
    assert resp.json()["status"] == "expired"
    db.refresh(shop.paid)
    assert shop.paid.quantity_reserved == 0


# ------------------------------------------------------------------ register-free / mark_paid


def _pending_free_order(db: Session, buyer: User, tt: TicketType) -> Order:
    """A pending order with total 0 (e.g. after a 100 % promo, BE-23)."""
    item = OrderItem(ticket_type_id=tt.id, unit_price_minor=0)
    item.attendee = Attendee(full_name="Guest", email="guest@example.com")
    order = Order(
        buyer_user_id=buyer.id,
        event_id=tt.event_id,
        subtotal_minor=0,
        total_minor=0,
        reserved_until=datetime.now(UTC) + timedelta(minutes=10),
        items=[item],
    )
    db.add(order)
    tt.quantity_reserved += 1
    db.flush()
    return order


def test_register_free(client: TestClient, shop, db: Session) -> None:
    order = _pending_free_order(db, shop.buyer, shop.free)
    url = f"{API}/orders/{order.id}/register-free"
    assert client.post(url, headers=auth_headers(make_user(db))).status_code == 403

    resp = client.post(url, headers=auth_headers(shop.buyer))
    assert resp.status_code == 200 and resp.json()["status"] == "paid"
    db.refresh(shop.free)
    assert (shop.free.quantity_reserved, shop.free.quantity_sold) == (0, 1)
    # idempotent
    assert client.post(url, headers=auth_headers(shop.buyer)).json()["status"] == "paid"
    db.refresh(shop.free)
    assert shop.free.quantity_sold == 1

    paid = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 1))).json()
    resp = client.post(f"{API}/orders/{paid['id']}/register-free", headers=auth_headers(shop.buyer))
    assert resp.status_code == 409 and error_code(resp) == "order_not_free"


def test_register_free_after_expiry(client: TestClient, shop, db: Session) -> None:
    order = _pending_free_order(db, shop.buyer, shop.free)
    _expire_now(db, order.id)
    resp = client.post(f"{API}/orders/{order.id}/register-free", headers=auth_headers(shop.buyer))
    assert resp.status_code == 409 and error_code(resp) == "reservation_expired"
    db.refresh(shop.free)
    assert (shop.free.quantity_reserved, shop.free.quantity_sold) == (0, 0)


def test_mark_paid_and_release_helpers(client: TestClient, shop, db: Session, monkeypatch) -> None:
    issued: list[Any] = []
    monkeypatch.setattr(orders_svc, "on_order_paid_hooks", [lambda _db, o: issued.append(o.id)])
    a = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 2))).json()
    b = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 1))).json()

    # what BE-15 does after a successful payment
    order = orders_svc.lock_order(db, a["id"])
    orders_svc.mark_paid(db, order, actor_id=shop.buyer.id)
    db.commit()
    assert order.status == OrderStatus.PAID and issued == [order.id]
    with pytest.raises(Exception) as exc:
        orders_svc.mark_paid(db, order)
    assert getattr(exc.value, "code", None) == "order_not_pending"

    other = orders_svc.lock_order(db, b["id"])
    orders_svc.release(db, other, new_status=OrderStatus.EXPIRED)
    db.commit()
    db.refresh(shop.paid)
    assert (shop.paid.quantity_reserved, shop.paid.quantity_sold) == (0, 2)
