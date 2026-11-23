import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Payment
from app.models.enums import PaymentPurpose, PaymentStatus, Role
from app.services import platform_settings
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
ADMIN = f"{API}/admin"


@pytest.fixture
def admin_h(db: Session) -> dict[str, str]:
    return auth_headers(make_user(db, roles=(Role.PLATFORM_ADMIN,), full_name="Admin"))


def test_non_admin_gets_403_everywhere(client: TestClient, db: Session) -> None:
    h = auth_headers(make_user(db, roles=(Role.ORGANIZER,)))
    for path in (
        "/users",
        "/events",
        "/orders",
        "/payments",
        "/activation-records",
        "/settings",
        "/reports/users.csv",
    ):
        assert client.get(f"{ADMIN}{path}", headers=h).status_code == 403, path


def test_suspend_user_blocks_login_and_existing_tokens(
    client: TestClient, db: Session, admin_h
) -> None:
    resp = client.post(
        f"{API}/auth/register",
        json={"email": "spam@example.com", "password": "password123", "full_name": "Spammer"},
    )
    uid = resp.json()["id"]
    token = client.post(
        f"{API}/auth/login", json={"email": "spam@example.com", "password": "password123"}
    ).json()["access_token"]

    resp = client.post(f"{ADMIN}/users/{uid}/suspend", json={"reason": "spam"}, headers=admin_h)
    assert resp.status_code == 200 and resp.json()["status"] == "suspended"
    assert client.get(f"{API}/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    login = client.post(
        f"{API}/auth/login", json={"email": "spam@example.com", "password": "password123"}
    )
    assert login.json()["error"]["code"] == "user_suspended"

    assert (
        client.post(
            f"{ADMIN}/users/{uid}/suspend", json={"reason": "x"}, headers=admin_h
        ).status_code
        == 422
    )
    client.post(f"{ADMIN}/users/{uid}/unsuspend", headers=admin_h)
    login = client.post(
        f"{API}/auth/login", json={"email": "spam@example.com", "password": "password123"}
    )
    assert login.status_code == 200


def test_cannot_suspend_self(client: TestClient, db: Session) -> None:
    admin = make_user(db, roles=(Role.PLATFORM_ADMIN,))
    resp = client.post(
        f"{ADMIN}/users/{admin.id}/suspend", json={"reason": "oops"}, headers=auth_headers(admin)
    )
    assert resp.status_code == 409


def test_suspend_event_hides_it_and_stops_sales(client: TestClient, db: Session, admin_h) -> None:
    event = make_event(db, make_organizer(db), title="Shady")
    make_ticket_type(db, event)
    assert client.get(f"{API}/events").json()["total"] == 1

    resp = client.post(
        f"{ADMIN}/events/{event.id}/suspend", json={"reason": "fraud report"}, headers=admin_h
    )
    assert resp.status_code == 200 and resp.json()["suspended_reason"] == "fraud report"
    assert client.get(f"{API}/events").json()["total"] == 0
    assert client.get(f"{API}/events/{event.id}").status_code == 404
    # owner still sees it, but nothing is on sale
    owner_h = auth_headers(event.organizer.user)
    types = client.get(f"{API}/events/{event.id}/ticket-types", headers=owner_h).json()
    assert types and not any(t["on_sale"] for t in types)

    assert (
        client.get(f"{ADMIN}/events", params={"suspended": True}, headers=admin_h).json()["total"]
        == 1
    )
    client.post(f"{ADMIN}/events/{event.id}/unsuspend", headers=admin_h)
    assert client.get(f"{API}/events").json()["total"] == 1

    actions = db.scalars(select(AuditLog.action_type).where(AuditLog.event_id == event.id)).all()
    assert "event.suspended" in actions and "event.unsuspended" in actions


def test_search_users_and_events(client: TestClient, db: Session, admin_h) -> None:
    make_user(db, email="aru@example.com", full_name="Aru Sapar")
    org = make_organizer(db, display_name="Astana Events")
    make_event(db, org, title="Tech Meetup")
    assert client.get(f"{ADMIN}/users", params={"q": "sapar"}, headers=admin_h).json()["total"] == 1
    assert (
        client.get(f"{ADMIN}/users", params={"role": "platform_admin"}, headers=admin_h).json()[
            "total"
        ]
        == 1
    )
    assert (
        client.get(f"{ADMIN}/events", params={"q": "astana"}, headers=admin_h).json()["total"] == 1
    )
    assert (
        client.get(f"{ADMIN}/events", params={"q": "meetup"}, headers=admin_h).json()["total"] == 1
    )


def test_orders_payments_activation_records(client: TestClient, db: Session, admin_h) -> None:
    event = make_event(db)
    buyer = make_user(db)
    make_paid_order(db, buyer, make_ticket_type(db, event))
    db.add(
        Payment(
            event_id=event.id,
            payer_user_id=buyer.id,
            purpose=PaymentPurpose.ACTIVATION_FEE,
            amount_minor=500_000,
            status=PaymentStatus.SUCCEEDED,
        )
    )
    db.flush()
    assert client.get(f"{ADMIN}/orders", headers=admin_h).json()["total"] == 1
    records = client.get(f"{ADMIN}/activation-records", headers=admin_h).json()
    assert records["total"] == 1 and records["items"][0]["is_simulated"] is True


def test_activation_fee_setting(client: TestClient, db: Session, admin_h) -> None:
    default = client.get(f"{ADMIN}/settings", headers=admin_h).json()["activation_fee_minor"]
    assert default == platform_settings.activation_fee_minor(db)
    resp = client.patch(
        f"{ADMIN}/settings", json={"activation_fee_minor": 750_000}, headers=admin_h
    )
    assert resp.json()["activation_fee_minor"] == 750_000
    assert platform_settings.activation_fee_minor(db) == 750_000
    assert (
        client.patch(
            f"{ADMIN}/settings", json={"activation_fee_minor": -1}, headers=admin_h
        ).status_code
        == 422
    )


@pytest.mark.parametrize("report", ["users", "events", "orders", "payments"])
def test_csv_reports(client: TestClient, db: Session, admin_h, report: str) -> None:
    make_paid_order(db, make_user(db), make_ticket_type(db, make_event(db)))
    resp = client.get(f"{ADMIN}/reports/{report}.csv", headers=admin_h)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][0] == "id"
