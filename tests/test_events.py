from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Event, OrganizerProfile, TicketType, User
from app.models.enums import EventLifecycle, EventStatus, EventVisibility, Role
from app.services import events as events_svc
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user

pytestmark = pytest.mark.db

API = "/api/v1"


def iso(dt: datetime) -> str:
    return dt.isoformat()


def future(days: int = 10) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0)


def event_payload(**kw: Any) -> dict[str, Any]:
    starts = kw.pop("starts_at", future())
    return {
        "title": "Jazz Night",
        "description": "Live jazz",
        "category": "music",
        "location_name": "Philharmonic",
        "starts_at": iso(starts),
        "ends_at": iso(kw.pop("ends_at", starts + timedelta(hours=3))),
        "capacity": 100,
        **kw,
    }


@pytest.fixture
def organizer(db: Session) -> tuple[OrganizerProfile, dict[str, str]]:
    user = make_user(db, roles=(Role.ATTENDEE, Role.ORGANIZER))
    profile = make_organizer(db, user)
    return profile, auth_headers(user)


def create(client: TestClient, headers: dict[str, str], **kw: Any) -> dict[str, Any]:
    resp = client.post(f"{API}/events", json=event_payload(**kw), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------------ create / update


def test_create_draft(client: TestClient, organizer) -> None:
    _, h = organizer
    ev = create(client, h)
    assert ev["status"] == "draft"
    assert ev["lifecycle"] == "upcoming"
    assert ev["timezone"] == "Asia/Almaty"
    assert ev["organizer"]["display_name"] == "Test Organizer"


def test_create_requires_profile_and_valid_dates(client: TestClient, db: Session) -> None:
    h = auth_headers(make_user(db, roles=(Role.ORGANIZER,)))
    resp = client.post(f"{API}/events", json=event_payload(), headers=h)
    assert resp.json()["error"]["code"] == "organizer_profile_required"

    start = future()
    resp = client.post(
        f"{API}/events", json=event_payload(starts_at=start, ends_at=start), headers=h
    )
    assert resp.status_code == 422

    resp = client.post(f"{API}/events", json=event_payload(timezone="Mars/Olympus"), headers=h)
    assert resp.status_code == 422


def test_attendee_cannot_create(client: TestClient, db: Session) -> None:
    h = auth_headers(make_user(db))
    assert client.post(f"{API}/events", json=event_payload(), headers=h).status_code == 403


def test_update_and_other_organizer_forbidden(client: TestClient, organizer, db: Session) -> None:
    _, h = organizer
    ev = create(client, h)
    resp = client.patch(f"{API}/events/{ev['id']}", json={"title": "Renamed"}, headers=h)
    assert resp.status_code == 200 and resp.json()["title"] == "Renamed"

    other = auth_headers(make_organizer(db).user)
    resp = client.patch(f"{API}/events/{ev['id']}", json={"title": "Hijack"}, headers=other)
    assert resp.status_code == 403


def test_capacity_rules(client: TestClient, organizer, db: Session) -> None:
    profile, h = organizer
    event = make_event(db, profile, capacity=100, status=EventStatus.DRAFT)
    make_ticket_type(db, event, quantity_total=60, quantity_sold=30)

    resp = client.patch(f"{API}/events/{event.id}", json={"capacity": 20}, headers=h)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "capacity_below_sold"
    resp = client.patch(f"{API}/events/{event.id}", json={"capacity": 50}, headers=h)
    assert resp.json()["error"]["code"] == "capacity_below_ticket_types"
    resp = client.patch(f"{API}/events/{event.id}", json={"capacity": 80}, headers=h)
    assert resp.status_code == 200


def test_update_dates_validated_against_existing(client: TestClient, organizer) -> None:
    _, h = organizer
    ev = create(client, h)
    before_start = datetime.fromisoformat(ev["starts_at"]) - timedelta(hours=1)
    resp = client.patch(f"{API}/events/{ev['id']}", json={"ends_at": iso(before_start)}, headers=h)
    assert resp.json()["error"]["code"] == "invalid_dates"


# ------------------------------------------------------------------ status transitions


def test_publish_requires_ticket_type(client: TestClient, organizer, db: Session) -> None:
    profile, h = organizer
    ev = create(client, h)
    resp = client.post(f"{API}/events/{ev['id']}/publish", headers=h)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "cannot_publish"

    event = db.get(Event, ev["id"])
    assert event
    make_ticket_type(db, event)
    resp = client.post(f"{API}/events/{ev['id']}/publish", headers=h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "published" and resp.json()["published_at"]


def test_unpublish_only_without_sales(client: TestClient, organizer, db: Session) -> None:
    profile, h = organizer
    event = make_event(db, profile)
    tt = make_ticket_type(db, event)

    tt.quantity_reserved = 1
    db.flush()
    resp = client.post(f"{API}/events/{event.id}/unpublish", headers=h)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "event_has_sales"

    tt.quantity_reserved = 0
    db.flush()
    resp = client.post(f"{API}/events/{event.id}/unpublish", headers=h)
    assert resp.status_code == 200 and resp.json()["status"] == "draft"


def test_cancel_freezes_event(client: TestClient, organizer, db: Session, monkeypatch) -> None:
    profile, h = organizer
    event = make_event(db, profile)
    calls: list[Any] = []
    monkeypatch.setattr(
        events_svc.notifications, "send_event_cancelled", lambda db, ev: calls.append(ev.id)
    )

    resp = client.post(f"{API}/events/{event.id}/cancel", json={"reason": "Storm"}, headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled" and body["lifecycle"] == "cancelled"
    assert calls == [event.id]

    resp = client.patch(f"{API}/events/{event.id}", json={"title": "x"}, headers=h)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "event_cancelled"
    assert client.post(f"{API}/events/{event.id}/publish", headers=h).status_code == 409


def test_duplicate_copies_ticket_types_without_sales(
    client: TestClient, organizer, db: Session
) -> None:
    profile, h = organizer
    event = make_event(db, profile, paid_sales_active=True)
    make_ticket_type(db, event, name="VIP", quantity_total=10, quantity_sold=4)

    resp = client.post(f"{API}/events/{event.id}/duplicate", headers=h)
    assert resp.status_code == 201
    copy = resp.json()
    assert copy["status"] == "draft" and copy["title"].endswith("(copy)")
    assert copy["paid_sales_active"] is False

    types = db.scalars(select(TicketType).where(TicketType.event_id == copy["id"])).all()
    assert [(t.name, t.quantity_total, t.quantity_sold) for t in types] == [("VIP", 10, 0)]


def test_actions_are_audited(client: TestClient, organizer, db: Session) -> None:
    _, h = organizer
    ev = create(client, h)
    client.patch(f"{API}/events/{ev['id']}", json={"title": "Renamed"}, headers=h)
    actions = db.scalars(select(AuditLog.action_type).where(AuditLog.event_id == ev["id"])).all()
    assert actions.count("event.created") == 1 and actions.count("event.updated") == 1


# ------------------------------------------------------------------ visibility & catalog


def test_public_catalog_and_visibility(client: TestClient, db: Session) -> None:
    org = make_organizer(db)
    public = make_event(db, org, title="Public Show")
    make_ticket_type(db, public, price_minor=500_000)
    make_ticket_type(db, public, price_minor=200_000, name="Cheap")
    unlisted = make_event(db, org, title="Secret Link", visibility=EventVisibility.UNLISTED)
    private = make_event(db, org, title="Private", visibility=EventVisibility.PRIVATE)
    draft = make_event(db, org, title="Draft", status=EventStatus.DRAFT)
    past_start = datetime.now(UTC) - timedelta(days=3)
    make_event(db, org, title="Old", starts_at=past_start, ends_at=past_start + timedelta(hours=2))
    suspended = make_event(db, org, title="Suspended", suspended_at=datetime.now(UTC))

    resp = client.get(f"{API}/events")
    titles = [e["title"] for e in resp.json()["items"]]
    assert titles == ["Public Show"]
    assert resp.json()["items"][0]["min_price_minor"] == 200_000

    assert client.get(f"{API}/events", params={"q": "public"}).json()["total"] == 1
    assert client.get(f"{API}/events", params={"category": "nope"}).json()["total"] == 0
    assert client.get(f"{API}/events", params={"include_past": True}).json()["total"] == 2

    assert client.get(f"{API}/events/{unlisted.id}").status_code == 200
    for hidden in (private, draft, suspended):
        assert client.get(f"{API}/events/{hidden.id}").status_code == 404

    owner = db.get(User, org.user_id)
    assert owner
    assert client.get(f"{API}/events/{draft.id}", headers=auth_headers(owner)).status_code == 200


def test_organizer_list_filters_by_lifecycle(client: TestClient, organizer, db: Session) -> None:
    profile, h = organizer
    now = datetime.now(UTC)
    make_event(db, profile, title="Soon")
    make_event(
        db,
        profile,
        title="Now",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1),
    )
    make_event(
        db,
        profile,
        title="Done",
        starts_at=now - timedelta(days=2),
        ends_at=now - timedelta(days=1),
    )
    make_event(db, profile, title="Off", status=EventStatus.CANCELLED)
    make_event(db, make_organizer(db), title="Not mine")

    def titles(**params: str) -> set[str]:
        resp = client.get(f"{API}/organizers/me/events", params=params, headers=h)
        return {e["title"] for e in resp.json()["items"]}

    assert titles() == {"Soon", "Now", "Done", "Off"}
    assert titles(lifecycle="upcoming") == {"Soon"}
    assert titles(lifecycle="active") == {"Now"}
    assert titles(lifecycle="completed") == {"Done"}
    assert titles(lifecycle="cancelled") == {"Off"}


def test_compute_lifecycle_matches_sql(db: Session) -> None:
    now = datetime.now(UTC)
    org = make_organizer(db)
    cases = {
        EventLifecycle.UPCOMING: make_event(db, org),
        EventLifecycle.ACTIVE: make_event(
            db, org, starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1)
        ),
        EventLifecycle.COMPLETED: make_event(
            db, org, starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1)
        ),
        EventLifecycle.CANCELLED: make_event(db, org, status=EventStatus.CANCELLED),
    }
    for lifecycle, event in cases.items():
        assert events_svc.compute_lifecycle(event, now) == lifecycle
        ids = db.scalars(
            select(Event.id).where(
                Event.organizer_id == org.id, events_svc.lifecycle_clause(lifecycle, now)
            )
        ).all()
        assert ids == [event.id]
