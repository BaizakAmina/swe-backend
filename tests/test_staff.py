from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, StaffAssignment, User
from app.models.enums import EventStatus, UserStatus
from tests.factories import auth_headers, make_event, make_organizer, make_user

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture
def setup(db: Session):
    org = make_organizer(db)
    event = make_event(db, org, title="Jazz")
    owner = db.get(User, org.user_id)
    assert owner
    staffer = make_user(db, email="door@example.com", full_name="Door Staff")
    return event, auth_headers(owner), staffer


def test_assign_list_remove(client: TestClient, setup, db: Session) -> None:
    event, h, staffer = setup
    url = f"{API}/events/{event.id}/staff"

    resp = client.post(url, json={"email": "DOOR@example.com"}, headers=h)
    assert resp.status_code == 201
    assert resp.json()["full_name"] == "Door Staff" and resp.json()["role"] == "event_admin"

    # idempotent
    assert client.post(url, json={"email": "door@example.com"}, headers=h).status_code == 201
    assert len(client.get(url, headers=h).json()) == 1

    mine = client.get(f"{API}/me/assigned-events", headers=auth_headers(staffer)).json()
    assert [e["title"] for e in mine] == ["Jazz"]
    assert mine[0]["tickets_issued"] == 0 and mine[0]["checked_in"] == 0

    assert client.delete(f"{url}/{staffer.id}", headers=h).status_code == 204
    assert client.get(f"{API}/me/assigned-events", headers=auth_headers(staffer)).json() == []
    assert client.delete(f"{url}/{staffer.id}", headers=h).status_code == 404

    actions = db.scalars(select(AuditLog.action_type).where(AuditLog.event_id == event.id)).all()
    assert "staff.assigned" in actions and "staff.removed" in actions


def test_unknown_or_suspended_user(client: TestClient, setup, db: Session) -> None:
    event, h, _ = setup
    url = f"{API}/events/{event.id}/staff"
    resp = client.post(url, json={"email": "ghost@example.com"}, headers=h)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "user_not_found"

    make_user(db, email="banned@example.com", status=UserStatus.SUSPENDED)
    resp = client.post(url, json={"email": "banned@example.com"}, headers=h)
    assert resp.status_code == 409


def test_only_organizer_manages_staff(client: TestClient, setup, db: Session) -> None:
    event, _, staffer = setup
    other = auth_headers(make_organizer(db).user)
    url = f"{API}/events/{event.id}/staff"
    assert client.post(url, json={"email": "door@example.com"}, headers=other).status_code == 403
    assert client.get(url, headers=auth_headers(staffer)).status_code == 403


def test_assigned_events_skip_cancelled(client: TestClient, setup, db: Session) -> None:
    event, h, staffer = setup
    cancelled = make_event(db, make_organizer(db), title="Off", status=EventStatus.CANCELLED)
    db.add(StaffAssignment(event_id=cancelled.id, user_id=staffer.id))
    db.flush()
    client.post(f"{API}/events/{event.id}/staff", json={"email": "door@example.com"}, headers=h)

    resp = client.get(f"{API}/me/assigned-events", headers=auth_headers(staffer))
    assert [e["title"] for e in resp.json()] == ["Jazz"]

    # cannot assign staff to a cancelled event
    org_user = db.get(User, cancelled.organizer.user_id)
    assert org_user
    resp = client.post(
        f"{API}/events/{cancelled.id}/staff",
        json={"email": "door@example.com"},
        headers=auth_headers(org_user),
    )
    assert resp.status_code == 409


def test_assigned_event_card_fields(client: TestClient, setup, db: Session) -> None:
    event, _, staffer = setup
    event.location_address = "12 Abay Ave, Almaty"
    now = datetime.now(UTC)
    org = make_organizer(db)
    live = make_event(db, org, title="Live", starts_at=now - timedelta(hours=1))
    past = make_event(
        db, org, title="Past", starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1)
    )
    draft = make_event(db, org, title="Draft", status=EventStatus.DRAFT)
    for ev in (event, live, past, draft):
        db.add(StaffAssignment(event_id=ev.id, user_id=staffer.id))
    db.flush()

    rows = client.get(f"{API}/me/assigned-events", headers=auth_headers(staffer)).json()
    cards = {e["title"]: e for e in rows}
    assert cards["Jazz"]["lifecycle"] == "upcoming"
    assert cards["Jazz"]["location_address"] == "12 Abay Ave, Almaty"
    assert cards["Live"]["lifecycle"] == "active"
    assert cards["Past"]["lifecycle"] == "completed"
    assert {t: c["can_check_in"] for t, c in cards.items()} == {
        "Jazz": True,
        "Live": True,
        "Past": False,
        "Draft": False,
    }
    assert all(c["can_undo"] for c in rows)
