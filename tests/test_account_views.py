import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StaffAssignment, Ticket, User
from app.models.enums import TicketStatus
from tests.factories import (
    auth_headers,
    make_event,
    make_organizer,
    make_paid_order,
    make_ticket_type,
    make_user,
)

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture
def world(db: Session):
    org = make_organizer(db)
    event = make_event(db, org, title="Jazz")
    vip = make_ticket_type(db, event, name="VIP", price_minor=2_000_000)
    buyer = make_user(db, email="buyer@example.com", full_name="Buyer")
    order = make_paid_order(
        db, buyer, vip, [("Aru Sapar", "aru@example.com"), ("Buyer", "buyer@example.com")]
    )
    owner = db.get(User, org.user_id)
    assert owner
    return {"event": event, "vip": vip, "buyer": buyer, "order": order, "owner": owner}


def test_my_orders(client: TestClient, world, db: Session) -> None:
    resp = client.get(f"{API}/me/orders", headers=auth_headers(world["buyer"]))
    assert resp.status_code == 200
    page = resp.json()
    assert page["total"] == 1
    order = page["items"][0]
    assert order["event_title"] == "Jazz" and order["total_minor"] == 4_000_000
    assert {i["attendee_name"] for i in order["items"]} == {"Aru Sapar", "Buyer"}
    assert all(i["ticket_type_name"] == "VIP" and i["ticket_id"] for i in order["items"])

    stranger = auth_headers(make_user(db))
    assert client.get(f"{API}/me/orders", headers=stranger).json()["total"] == 0
    resp = client.get(
        f"{API}/me/orders",
        params={"order_status": "refunded"},
        headers=auth_headers(world["buyer"]),
    )
    assert resp.json()["total"] == 0


def test_my_tickets_and_ticket_access(client: TestClient, world, db: Session) -> None:
    buyer_h = auth_headers(world["buyer"])
    tickets = client.get(f"{API}/me/tickets", headers=buyer_h).json()
    assert tickets["total"] == 2
    t = tickets["items"][0]
    assert t["event"]["title"] == "Jazz" and t["qr_payload"].startswith("T1.")
    assert t["pdf_url"].endswith(f"/api/v1/tickets/{t['id']}/pdf")
    assert "price" not in str(t) and "payment" not in str(t)

    # attendee with an account sees the ticket issued to their email
    aru = make_user(db, email="ARU@example.com")
    aru_tickets = client.get(f"{API}/me/tickets", headers=auth_headers(aru)).json()["items"]
    assert [x["attendee_name"] for x in aru_tickets] == ["Aru Sapar"]
    assert (
        client.get(f"{API}/tickets/{aru_tickets[0]['id']}", headers=auth_headers(aru)).status_code
        == 200
    )

    # organizer can open it, a stranger cannot
    tid = t["id"]
    assert (
        client.get(f"{API}/tickets/{tid}", headers=auth_headers(world["owner"])).status_code == 200
    )
    assert (
        client.get(f"{API}/tickets/{tid}", headers=auth_headers(make_user(db))).status_code == 403
    )
    assert (
        client.get(
            f"{API}/tickets/00000000-0000-0000-0000-000000000000", headers=buyer_h
        ).status_code
        == 404
    )


def test_upcoming_filter(client: TestClient, world, db: Session) -> None:
    past_start = datetime.now(UTC) - timedelta(days=5)
    old = make_event(db, title="Old", starts_at=past_start, ends_at=past_start + timedelta(hours=2))
    make_paid_order(db, world["buyer"], make_ticket_type(db, old))
    h = auth_headers(world["buyer"])
    assert (
        client.get(f"{API}/me/tickets", params={"upcoming": True}, headers=h).json()["total"] == 2
    )
    past = client.get(f"{API}/me/tickets", params={"upcoming": False}, headers=h).json()
    assert [x["event"]["title"] for x in past["items"]] == ["Old"]


def test_attendee_list_search_and_csv(client: TestClient, world, db: Session) -> None:
    h = auth_headers(world["owner"])
    url = f"{API}/events/{world['event'].id}/attendees"
    page = client.get(url, headers=h).json()
    assert page["total"] == 2
    assert [r["attendee_name"] for r in page["items"]] == ["Aru Sapar", "Buyer"]

    assert client.get(url, params={"q": "aru"}, headers=h).json()["total"] == 1
    ticket = db.scalars(select(Ticket).where(Ticket.event_id == world["event"].id)).first()
    assert ticket
    ticket.status = TicketStatus.CHECKED_IN
    ticket.checked_in_at = datetime.now(UTC)
    db.flush()
    resp = client.get(url, params={"ticket_status": "checked_in"}, headers=h)
    assert resp.json()["total"] == 1 and resp.json()["items"][0]["checked_in_at"]
    assert client.get(url, params={"q": ticket.ticket_code}, headers=h).json()["total"] == 1

    resp = client.get(f"{url}.csv", headers=h)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 2 and rows[0]["ticket_type"] == "VIP"


def test_attendees_and_orders_only_for_organizer(client: TestClient, world, db: Session) -> None:
    other = auth_headers(make_organizer(db).user)
    eid = world["event"].id
    assert client.get(f"{API}/events/{eid}/attendees", headers=other).status_code == 403
    assert client.get(f"{API}/events/{eid}/attendees.csv", headers=other).status_code == 403
    assert client.get(f"{API}/events/{eid}/orders", headers=other).status_code == 403
    orders = client.get(f"{API}/events/{eid}/orders", headers=auth_headers(world["owner"])).json()
    assert orders["total"] == 1


def test_attendee_search_open_to_assigned_event_admin(
    client: TestClient, world, db: Session
) -> None:
    eid = world["event"].id
    url = f"{API}/events/{eid}/attendees"
    door = make_user(db, email="door@example.com")
    assert client.get(url, headers=auth_headers(door)).status_code == 403

    db.add(StaffAssignment(event_id=eid, user_id=door.id))
    db.flush()
    h = auth_headers(door)
    page = client.get(url, params={"q": "aru"}, headers=h).json()
    assert page["total"] == 1
    row = page["items"][0]
    assert row["ticket_code"] and {"section", "row_label", "seat_number"} <= row.keys()

    # door staff search, but do not export or see orders
    assert client.get(f"{url}.csv", headers=h).status_code == 403
    assert client.get(f"{API}/events/{eid}/orders", headers=h).status_code == 403
