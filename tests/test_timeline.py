import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.models.enums import Role
from tests.factories import auth_headers, make_organizer, make_user

pytestmark = pytest.mark.db

API = "/api/v1"


def test_timeline_records_event_activity_in_order(client: TestClient, db: Session) -> None:
    org = make_organizer(db)
    owner = db.get(User, org.user_id)
    assert owner
    h = auth_headers(owner)

    from datetime import UTC, datetime, timedelta

    start = datetime.now(UTC) + timedelta(days=5)
    ev = client.post(
        f"{API}/events",
        json={
            "title": "Jazz",
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=2)).isoformat(),
            "capacity": 50,
        },
        headers=h,
    ).json()
    eid = ev["id"]
    tt = client.post(
        f"{API}/events/{eid}/ticket-types",
        json={"name": "Free", "kind": "free", "quantity_total": 10},
        headers=h,
    ).json()
    client.patch(f"{API}/ticket-types/{tt['id']}", json={"name": "Free entry"}, headers=h)
    client.post(f"{API}/events/{eid}/publish", headers=h)
    staff = make_user(db, email="door@example.com")
    client.post(f"{API}/events/{eid}/staff", json={"email": staff.email}, headers=h)

    page = client.get(f"{API}/events/{eid}/timeline", headers=h).json()
    assert [e["action_type"] for e in page["items"]] == [
        "staff.assigned",
        "event.published",
        "ticket_type.updated",
        "ticket_type.created",
        "event.created",
    ]
    assert page["items"][0]["actor_name"] == owner.full_name

    only_tt = client.get(
        f"{API}/events/{eid}/timeline", params={"action_type": "ticket_type"}, headers=h
    ).json()
    assert only_tt["total"] == 2
    exact = client.get(
        f"{API}/events/{eid}/timeline", params={"action_type": "event.published"}, headers=h
    ).json()
    assert exact["total"] == 1

    other = auth_headers(make_user(db, roles=(Role.ORGANIZER,)))
    assert client.get(f"{API}/events/{eid}/timeline", headers=other).status_code == 403
