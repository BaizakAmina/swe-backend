from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ticket, User
from app.models.enums import Role
from tests.factories import (
    auth_headers,
    make_event,
    make_organizer,
    make_paid_order,
    make_ticket_type,
    make_user,
)

pytestmark = pytest.mark.db

API = "/api/v1/support"


@pytest.fixture
def world(db: Session):
    org = make_organizer(db)
    event = make_event(db, org, title="Jazz")
    tt = make_ticket_type(db, event)
    buyer = make_user(db, email="buyer@example.com", full_name="Buyer")
    order = make_paid_order(db, buyer, tt)
    ticket = db.scalars(select(Ticket).where(Ticket.event_id == event.id)).one()
    owner = db.get(User, org.user_id)
    assert owner
    return {
        "event": event,
        "order": order,
        "ticket": ticket,
        "buyer": buyer,
        "owner": owner,
        "buyer_h": auth_headers(buyer),
        "owner_h": auth_headers(owner),
    }


def open_case(client: TestClient, h: dict[str, str], **kw: Any):
    body = {"category": "ticket_delivery", "subject": "No email", "message": "Didn't get it", **kw}
    return client.post(f"{API}/cases", json=body, headers=h)


def test_open_with_ticket_fills_context(client: TestClient, world, outbox) -> None:
    resp = open_case(client, world["buyer_h"], ticket_id=str(world["ticket"].id))
    assert resp.status_code == 201
    case = resp.json()
    assert case["event_id"] == str(world["event"].id)
    assert case["order_id"] == str(world["order"].id)
    assert case["status"] == "open" and len(case["messages"]) == 1
    # the organizer is told about the new case
    assert any(m["kind"] == "support_reply" and m["to"] == world["owner"].email for m in outbox)


def test_cannot_reference_someone_elses_order(client: TestClient, world, db: Session) -> None:
    stranger = auth_headers(make_user(db))
    assert open_case(client, stranger, order_id=str(world["order"].id)).status_code == 403
    assert open_case(client, stranger, ticket_id=str(world["ticket"].id)).status_code == 403
    # a general question about a public event is fine
    assert (
        open_case(
            client, stranger, event_id=str(world["event"].id), category="event_info"
        ).status_code
        == 201
    )


def test_access_by_role_and_relationship(client: TestClient, world, db: Session) -> None:
    case = open_case(client, world["buyer_h"], order_id=str(world["order"].id)).json()
    url = f"{API}/cases/{case['id']}"
    other_org = auth_headers(make_organizer(db).user)
    admin = auth_headers(make_user(db, roles=(Role.PLATFORM_ADMIN,)))

    assert client.get(url, headers=world["owner_h"]).status_code == 200
    assert client.get(url, headers=admin).status_code == 200
    assert client.get(url, headers=other_org).status_code == 403
    assert client.get(url, headers=auth_headers(make_user(db))).status_code == 403

    assert client.get(f"{API}/cases", headers=world["owner_h"]).json()["total"] == 1
    assert client.get(f"{API}/cases", headers=other_org).json()["total"] == 0
    assert client.get(f"{API}/cases", headers=admin).json()["total"] == 1


def test_conversation_flow_and_polling(client: TestClient, world, outbox) -> None:
    case = open_case(client, world["buyer_h"], event_id=str(world["event"].id)).json()
    url = f"{API}/cases/{case['id']}"
    first_sent = case["messages"][0]["sent_at"]

    reply = client.post(
        f"{url}/messages", json={"body": "Resent your ticket"}, headers=world["owner_h"]
    )
    assert reply.status_code == 201 and reply.json()["sender_name"]
    detail = client.get(url, headers=world["buyer_h"]).json()
    assert detail["status"] == "waiting_customer"
    assert detail["assigned_to_user_id"] == str(world["owner"].id)
    assert any(m["kind"] == "support_reply" and m["to"] == "buyer@example.com" for m in outbox)

    new = client.get(
        f"{url}/messages", params={"after": first_sent}, headers=world["buyer_h"]
    ).json()
    assert [m["body"] for m in new] == ["Resent your ticket"]

    client.post(f"{url}/messages", json={"body": "Got it, thanks"}, headers=world["buyer_h"])
    assert client.get(url, headers=world["buyer_h"]).json()["status"] == "in_progress"


def test_status_and_assignment_rules(client: TestClient, world, db: Session) -> None:
    case = open_case(client, world["buyer_h"], event_id=str(world["event"].id)).json()
    url = f"{API}/cases/{case['id']}"

    resp = client.patch(url, json={"status": "in_progress"}, headers=world["buyer_h"])
    assert resp.status_code == 403
    resp = client.patch(
        url, json={"assigned_to_user_id": str(world["buyer"].id)}, headers=world["owner_h"]
    )
    assert resp.json()["error"]["code"] == "invalid_assignee"
    admin = make_user(db, roles=(Role.PLATFORM_ADMIN,))
    resp = client.patch(url, json={"assigned_to_user_id": str(admin.id)}, headers=world["owner_h"])
    assert resp.status_code == 200

    resp = client.patch(url, json={"status": "resolved"}, headers=world["buyer_h"])
    assert resp.status_code == 200 and resp.json()["resolved_at"]
    # writing again reopens it
    client.post(
        f"{url}/messages", json={"body": "Actually, one more thing"}, headers=world["buyer_h"]
    )
    assert client.get(url, headers=world["buyer_h"]).json()["status"] == "in_progress"


def test_organizer_case_requires_organizer_role(client: TestClient, world, db: Session) -> None:
    resp = open_case(client, world["buyer_h"], kind="organizer", category="account")
    assert resp.status_code == 403
    resp = open_case(client, world["owner_h"], kind="organizer", category="payment")
    assert resp.status_code == 201
