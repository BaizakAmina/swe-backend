"""BE-30: assigned seating — seat map, seat holds, hold → order."""

import threading
import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import (
    Event,
    OrderItem,
    Seat,
    SeatHold,
    StaffAssignment,
    User,
    Venue,
    VenueRow,
    VenueSection,
)
from app.models.enums import SeatingMode
from app.seed import seed
from app.services import orders as orders_svc
from app.services import seating, ticketing
from tests.committed_data import cleanup
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user
from tests.test_orders import _expire_now, error_code, order_body, post_order

pytestmark = pytest.mark.db

API = "/api/v1"


def make_venue(db: Session) -> tuple[Venue, dict[str, Seat]]:
    """Hall: Parter rows A–B × 3 seats, Balcony row C × 2. Seats keyed like "A1"."""
    venue = Venue(name=f"Hall {uuid.uuid4().hex[:6]}")
    db.add(venue)
    db.flush()
    seats: dict[str, Seat] = {}
    for s_order, (name, rows, per_row) in enumerate([("Parter", "AB", 3), ("Balcony", "C", 2)]):
        section = VenueSection(venue_id=venue.id, name=name, sort_order=s_order)
        db.add(section)
        db.flush()
        for r_order, label in enumerate(rows):
            row = VenueRow(section_id=section.id, label=label, sort_order=r_order)
            db.add(row)
            db.flush()
            for n in range(1, per_row + 1):
                seat = Seat(row_id=row.id, seat_number=str(n), is_accessible=(label, n) == ("A", 1))
                db.add(seat)
                db.flush()
                seats[f"{label}{n}"] = seat
    return venue, seats


@pytest.fixture
def hall(db: Session) -> SimpleNamespace:
    venue, seats = make_venue(db)
    org = make_organizer(db)
    ev = make_event(
        db, org, paid_sales_active=True, seating_mode=SeatingMode.ASSIGNED, venue_id=venue.id
    )
    tt = make_ticket_type(db, ev, name="Seat", price_minor=700_000, quantity_total=8)
    return SimpleNamespace(
        venue=venue,
        seats=seats,
        event=ev,
        tt=tt,
        owner=db.get(User, org.user_id),
        alice=make_user(db, full_name="Alice"),
        bob=make_user(db, full_name="Bob"),
    )


def hold(client: TestClient, user: User, event_id: Any, *seats: Seat) -> Any:
    return client.post(
        f"{API}/events/{event_id}/seat-holds",
        json={"seat_ids": [str(s.id) for s in seats]},
        headers=auth_headers(user),
    )


def statuses(client: TestClient, event_id: Any, user: User | None = None) -> dict[str, str]:
    headers = auth_headers(user) if user else {}
    data = client.get(f"{API}/events/{event_id}/seatmap", headers=headers).json()
    return {
        f"{row['label']}{seat['seat_number']}": seat["status"]
        for section in data["sections"]
        for row in section["rows"]
        for seat in row["seats"]
    }


def seated_order(client: TestClient, hall, user: User, *seats: Seat) -> Any:
    holds = hold(client, user, hall.event.id, *seats).json()
    body = order_body(hall.event.id, (hall.tt, len(seats)))
    body["seat_hold_ids"] = [h["id"] for h in holds]
    return post_order(client, user, body)


def test_seatmap_layout_and_statuses(client: TestClient, hall, db: Session) -> None:
    data = client.get(f"{API}/events/{hall.event.id}/seatmap").json()
    assert data["venue_id"] == str(hall.venue.id)
    assert [s["name"] for s in data["sections"]] == ["Parter", "Balcony"]
    assert [r["label"] for r in data["sections"][0]["rows"]] == ["A", "B"]
    a1 = data["sections"][0]["rows"][0]["seats"][0]
    assert (a1["seat_number"], a1["is_accessible"], a1["status"]) == ("1", True, "available")

    hold(client, hall.alice, hall.event.id, hall.seats["A1"])
    assert statuses(client, hall.event.id, hall.alice)["A1"] == "mine"
    assert statuses(client, hall.event.id, hall.bob)["A1"] == "held"
    assert statuses(client, hall.event.id)["A1"] == "held"  # anonymous
    assert set(statuses(client, hall.event.id).values()) == {"held", "available"}

    ga = make_event(db)
    resp = client.get(f"{API}/events/{ga.id}/seatmap")
    assert resp.status_code == 409 and error_code(resp) == "not_assigned_seating"


def test_hold_and_conflict(client: TestClient, hall, db: Session) -> None:
    resp = hold(client, hall.alice, hall.event.id, hall.seats["A1"], hall.seats["A2"])
    assert resp.status_code == 201, resp.text
    holds = resp.json()
    assert {h["seat_id"] for h in holds} == {str(hall.seats["A1"].id), str(hall.seats["A2"].id)}
    expires = datetime.fromisoformat(holds[0]["expires_at"])
    assert abs((expires - datetime.now(UTC) - timedelta(minutes=10)).total_seconds()) < 60

    resp = hold(client, hall.bob, hall.event.id, hall.seats["A2"], hall.seats["A3"])
    assert resp.status_code == 409 and error_code(resp) == "seat_unavailable"
    assert resp.json()["error"]["details"]["seat_ids"] == [str(hall.seats["A2"].id)]
    # nothing of Bob's request was kept: A3 is still free
    assert statuses(client, hall.event.id, hall.bob)["A3"] == "available"
    assert hold(client, hall.bob, hall.event.id, hall.seats["A3"]).status_code == 201


def test_hold_validation(client: TestClient, hall, db: Session, monkeypatch) -> None:
    _, other_seats = make_venue(db)
    resp = hold(client, hall.alice, hall.event.id, other_seats["A1"])
    assert resp.status_code == 404 and error_code(resp) == "seat_not_found"

    monkeypatch.setattr(get_settings(), "max_seat_holds_per_user", 2)
    assert hold(client, hall.alice, hall.event.id, hall.seats["A1"]).status_code == 201
    resp = hold(client, hall.alice, hall.event.id, hall.seats["A2"], hall.seats["A3"])
    assert resp.status_code == 422 and error_code(resp) == "seat_hold_limit"

    hall.event.status = "draft"
    db.flush()
    resp = hold(client, hall.bob, hall.event.id, hall.seats["B1"])
    assert resp.status_code == 409 and error_code(resp) == "event_not_on_sale"
    assert client.post(
        f"{API}/events/{hall.event.id}/seat-holds", json={"seat_ids": []}
    ).status_code in (401, 422)


def _expire_hold(db: Session, hold_id: Any) -> None:
    db.execute(
        update(SeatHold)
        .where(SeatHold.id == uuid.UUID(str(hold_id)))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.flush()


def test_expired_hold_frees_the_seat(client: TestClient, hall, db: Session) -> None:
    [h] = hold(client, hall.alice, hall.event.id, hall.seats["B1"]).json()
    _expire_hold(db, h["id"])
    assert statuses(client, hall.event.id, hall.bob)["B1"] == "available"
    # a new hold releases the expired one in the same transaction
    assert hold(client, hall.bob, hall.event.id, hall.seats["B1"]).status_code == 201
    old = db.get(SeatHold, uuid.UUID(h["id"]))
    db.refresh(old)
    assert old is not None and old.released_at is not None


def test_sweep_releases_expired_free_holds(client: TestClient, hall, db: Session) -> None:
    [h] = hold(client, hall.alice, hall.event.id, hall.seats["C1"]).json()
    in_order = seated_order(client, hall, hall.bob, hall.seats["C2"]).json()
    _expire_hold(db, h["id"])
    assert seating.expire_stale_holds(db) == 1
    held = db.get(SeatHold, uuid.UUID(h["id"]))
    db.refresh(held)
    assert held is not None and held.released_at is not None
    # holds inside an order follow the order, not the hold sweep
    assert statuses(client, hall.event.id, hall.bob)["C2"] == "mine"
    assert in_order["status"] == "pending"


def test_drop_hold(client: TestClient, hall, db: Session) -> None:
    [h] = hold(client, hall.alice, hall.event.id, hall.seats["A1"]).json()
    url = f"{API}/seat-holds/{h['id']}"
    assert client.delete(url, headers=auth_headers(hall.bob)).status_code == 403
    assert client.delete(url, headers=auth_headers(hall.alice)).status_code == 204
    assert client.delete(url, headers=auth_headers(hall.alice)).status_code == 204  # idempotent
    assert statuses(client, hall.event.id)["A1"] == "available"

    order = seated_order(client, hall, hall.alice, hall.seats["A2"]).json()
    in_order = db.scalar(select(SeatHold).where(SeatHold.order_id == uuid.UUID(order["id"])))
    assert in_order is not None
    resp = client.delete(f"{API}/seat-holds/{in_order.id}", headers=auth_headers(hall.alice))
    assert resp.status_code == 409 and error_code(resp) == "seat_hold_in_order"


def test_seat_goes_into_order_item_and_ticket(client: TestClient, hall, db: Session) -> None:
    resp = seated_order(client, hall, hall.alice, hall.seats["B2"], hall.seats["C1"])
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert [(i["section"], i["row_label"], i["seat_number"]) for i in order["items"]] == [
        ("Parter", "B", "2"),
        ("Balcony", "C", "1"),
    ]
    items = db.scalars(select(OrderItem).where(OrderItem.order_id == uuid.UUID(order["id"]))).all()
    assert {i.seat_id for i in items} == {hall.seats["B2"].id, hall.seats["C1"].id}
    holds = db.scalars(select(SeatHold).where(SeatHold.order_id == uuid.UUID(order["id"]))).all()
    assert len(holds) == 2
    assert all(h.expires_at == datetime.fromisoformat(order["reserved_until"]) for h in holds)

    paid = client.post(f"{API}/orders/{order['id']}/pay", json={}, headers=auth_headers(hall.alice))
    assert paid.json()["status"] == "succeeded"
    tickets = ticketing.tickets_of(db, uuid.UUID(order["id"]))
    read = client.get(f"{API}/tickets/{tickets[0].id}", headers=auth_headers(hall.alice)).json()
    assert (read["section"], read["row_label"], read["seat_number"]) == ("Parter", "B", "2")
    pdf = client.get(f"{API}/tickets/{tickets[0].id}/pdf", headers=auth_headers(hall.alice))
    assert pdf.content.startswith(b"%PDF")

    # the scanner sees the seat too
    staff = make_user(db)
    db.add(StaffAssignment(event_id=hall.event.id, user_id=staff.id))
    db.flush()
    scan = client.post(
        f"{API}/checkin/validate",
        json={"event_id": str(hall.event.id), "qr_payload": tickets[1].qr_payload},
        headers=auth_headers(staff),
    ).json()
    assert (scan["ticket"]["section"], scan["ticket"]["row_label"]) == ("Balcony", "C")

    seen = statuses(client, hall.event.id, hall.bob)
    assert (seen["B2"], seen["C1"]) == ("sold", "sold")


def test_paid_seat_cannot_be_held(client: TestClient, hall, db: Session) -> None:
    order = seated_order(client, hall, hall.alice, hall.seats["A3"]).json()
    client.post(f"{API}/orders/{order['id']}/pay", json={}, headers=auth_headers(hall.alice))
    hold_row = db.scalar(select(SeatHold).where(SeatHold.order_id == uuid.UUID(order["id"])))
    assert hold_row is not None
    # even long after the original hold time, the sweep leaves a sold seat alone
    _expire_hold(db, hold_row.id)
    seating.expire_stale_holds(db)
    resp = hold(client, hall.bob, hall.event.id, hall.seats["A3"])
    assert resp.status_code == 409 and error_code(resp) == "seat_unavailable"
    assert statuses(client, hall.event.id, hall.bob)["A3"] == "sold"


def test_order_checks_the_holds(client: TestClient, hall, db: Session) -> None:
    body = order_body(hall.event.id, (hall.tt, 1))
    resp = post_order(client, hall.alice, body)
    assert resp.status_code == 422 and error_code(resp) == "seat_holds_mismatch"

    [bobs] = hold(client, hall.bob, hall.event.id, hall.seats["A1"]).json()
    resp = post_order(client, hall.alice, {**body, "seat_hold_ids": [bobs["id"]]})
    assert resp.status_code == 409 and error_code(resp) == "seat_hold_invalid"

    [mine] = hold(client, hall.alice, hall.event.id, hall.seats["A2"]).json()
    _expire_hold(db, mine["id"])
    resp = post_order(client, hall.alice, {**body, "seat_hold_ids": [mine["id"]]})
    assert resp.status_code == 409 and error_code(resp) == "seat_hold_expired"

    db.refresh(hall.tt)
    assert hall.tt.quantity_reserved == 0  # a failed order reserved nothing

    ga = make_event(db, paid_sales_active=True)
    ga_tt = make_ticket_type(db, ga)
    resp = post_order(
        client, hall.alice, {**order_body(ga.id, (ga_tt, 1)), "seat_hold_ids": [mine["id"]]}
    )
    assert resp.status_code == 422 and error_code(resp) == "seat_holds_not_allowed"


def test_expired_cancelled_and_refunded_orders_free_their_seats(
    client: TestClient, hall, db: Session
) -> None:
    expired = seated_order(client, hall, hall.alice, hall.seats["A1"]).json()
    _expire_now(db, expired["id"])
    orders_svc.expire_stale_orders(db)
    assert statuses(client, hall.event.id, hall.bob)["A1"] == "available"

    cancelled = seated_order(client, hall, hall.alice, hall.seats["A2"]).json()
    client.post(f"{API}/orders/{cancelled['id']}/cancel", headers=auth_headers(hall.alice))
    assert statuses(client, hall.event.id, hall.bob)["A2"] == "available"

    refunded = seated_order(client, hall, hall.alice, hall.seats["A3"]).json()
    client.post(f"{API}/orders/{refunded['id']}/pay", json={}, headers=auth_headers(hall.alice))
    assert statuses(client, hall.event.id, hall.bob)["A3"] == "sold"
    r = client.post(
        f"{API}/orders/{refunded['id']}/refund", json={}, headers=auth_headers(hall.owner)
    )
    assert r.json()["status"] == "succeeded"
    assert statuses(client, hall.event.id, hall.bob)["A3"] == "available"
    assert hold(client, hall.bob, hall.event.id, hall.seats["A3"]).status_code == 201


def test_seed_has_one_venue_layout(db: Session) -> None:
    first = seed(db)
    assert first["seats_created"] == 6 * 12 + 2 * 10
    assert seed(db)["seats_created"] == 0  # idempotent
    venue = db.scalar(select(Venue).where(Venue.name == "BiletFlow Hall"))
    assert venue is not None
    accessible = db.scalars(
        select(Seat)
        .join(VenueRow, VenueRow.id == Seat.row_id)
        .join(VenueSection, VenueSection.id == VenueRow.section_id)
        .where(VenueSection.venue_id == venue.id, Seat.is_accessible.is_(True))
    ).all()
    assert len(accessible) == 2


# ------------------------------------------------------------------ concurrency

HOLDERS = 20


@pytest.fixture
def parallel_engine(engine: Engine) -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, pool_size=HOLDERS + 2, max_overflow=0)
    yield eng
    eng.dispose()


def test_twenty_parallel_holds_of_one_seat(parallel_engine: Engine) -> None:
    with Session(parallel_engine) as s:
        venue, seats = make_venue(s)
        org = make_organizer(s)
        ev = make_event(s, org, seating_mode=SeatingMode.ASSIGNED, venue_id=venue.id)
        users = [make_user(s) for _ in range(HOLDERS)]
        s.commit()
        event_id, venue_id, seat_id = ev.id, venue.id, seats["A1"].id
        holder_ids = [u.id for u in users]
        user_ids = [org.user_id, *holder_ids]

    try:
        barrier = threading.Barrier(HOLDERS)
        results: list[Any] = []
        lock = threading.Lock()

        def run(user_id: Any) -> None:
            with Session(parallel_engine) as db:
                user = db.get(User, user_id)
                assert user is not None
                db.connection()
                barrier.wait()
                try:
                    seating.hold(db, user, event_id, [seat_id])
                    outcome = "ok"
                except AppError as exc:
                    outcome = exc.code
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=run, args=(u,)) for u in holder_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert Counter(results) == {"ok": 1, "seat_unavailable": HOLDERS - 1}, results
        with Session(parallel_engine) as s:
            active = s.scalars(
                select(SeatHold).where(SeatHold.seat_id == seat_id, SeatHold.released_at.is_(None))
            ).all()
            assert len(active) == 1
            assert s.get(Event, event_id) is not None
    finally:
        cleanup(parallel_engine, event_id, user_ids, venue_id)
