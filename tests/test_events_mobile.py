"""Cover images, venues, refund policy, organizer search and sold/capacity in event cards."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditLog, Event, OrganizerProfile, Seat, Venue, VenueRow, VenueSection
from app.models.enums import EventStatus, Role
from app.services import storage
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user

pytestmark = pytest.mark.db

API = "/api/v1"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


@pytest.fixture(autouse=True)
def memory_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "storage_backend", "memory")
    storage.clear_memory()


@pytest.fixture
def organizer(db: Session) -> tuple[OrganizerProfile, dict[str, str]]:
    user = make_user(db, roles=(Role.ATTENDEE, Role.ORGANIZER))
    return make_organizer(db, user), auth_headers(user)


def upload(client: TestClient, event_id: Any, headers: dict[str, str], data: bytes) -> Any:
    return client.post(
        f"{API}/events/{event_id}/cover",
        files={"file": ("cover.bin", data, "application/octet-stream")},
        headers=headers,
    )


def payload(**kw: Any) -> dict[str, Any]:
    starts = datetime.now(UTC).replace(microsecond=0) + timedelta(days=10)
    return {
        "title": "Jazz Night",
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=3)).isoformat(),
        "capacity": 100,
        **kw,
    }


# ------------------------------------------------------------------ cover images


@pytest.mark.parametrize(
    ("data", "ext", "ctype"),
    [(PNG, "png", "image/png"), (JPEG, "jpg", "image/jpeg"), (WEBP, "webp", "image/webp")],
)
def test_upload_cover_returns_public_url_served_without_auth(
    client: TestClient, db: Session, organizer, data: bytes, ext: str, ctype: str
) -> None:
    profile, h = organizer
    ev = make_event(db, profile)

    resp = upload(client, ev.id, h, data)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    key = body["cover_image_key"]
    assert key.startswith(f"covers/{ev.id}/") and key.endswith(f".{ext}")
    assert body["cover_image_url"] == f"http://testserver{API}/files/{key}"

    img = client.get(body["cover_image_url"].removeprefix("http://testserver"))
    assert img.status_code == 200
    assert img.content == data
    assert img.headers["content-type"] == ctype
    assert "immutable" in img.headers["cache-control"]

    card = client.get(f"{API}/events").json()["items"]
    assert [c["cover_image_url"] for c in card if c["id"] == str(ev.id)] == [
        body["cover_image_url"]
    ]
    assert db.query(AuditLog).filter_by(event_id=ev.id, action_type="event.updated").count() == 1


def test_replacing_cover_deletes_old_file(client: TestClient, db: Session, organizer) -> None:
    profile, h = organizer
    ev = make_event(db, profile)
    first = upload(client, ev.id, h, PNG).json()["cover_image_key"]
    second = upload(client, ev.id, h, JPEG).json()["cover_image_key"]
    assert first != second
    assert storage.get(first) is None
    assert storage.get(second) == JPEG
    assert client.get(f"{API}/files/{first}").status_code == 404

    resp = client.delete(f"{API}/events/{ev.id}/cover", headers=h)
    assert resp.status_code == 200
    assert resp.json()["cover_image_key"] is None and resp.json()["cover_image_url"] is None
    assert storage.get(second) is None


def test_cover_rejects_non_images_and_large_files(
    client: TestClient, db: Session, organizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, h = organizer
    ev = make_event(db, profile)
    bad = upload(client, ev.id, h, b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert bad.status_code == 415
    assert bad.json()["error"]["code"] == "unsupported_image"
    assert upload(client, ev.id, h, b"").status_code == 422

    from app.services import covers

    monkeypatch.setattr(covers, "MAX_BYTES", 32)
    big = upload(client, ev.id, h, PNG)
    assert big.status_code == 413
    db.refresh(ev)
    assert ev.cover_image_key is None


def test_cover_only_for_organizer_and_live_events(
    client: TestClient, db: Session, organizer
) -> None:
    profile, h = organizer
    ev = make_event(db, profile)
    stranger = auth_headers(make_user(db, roles=(Role.ORGANIZER,)))
    assert upload(client, ev.id, stranger, PNG).status_code == 403
    assert upload(client, ev.id, {}, PNG).status_code == 401

    ev.status = EventStatus.CANCELLED
    db.flush()
    resp = upload(client, ev.id, h, PNG)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "event_cancelled"


def test_files_endpoint_serves_only_cover_keys(client: TestClient) -> None:
    storage.put("tickets/secret.pdf", b"%PDF", "application/pdf")
    assert client.get(f"{API}/files/covers/x/../../tickets/secret.pdf").status_code == 404
    assert client.get(f"{API}/files/covers/abc/secret.pdf").status_code == 404


def test_cover_url_uses_public_api_url_when_configured(
    client: TestClient, db: Session, organizer, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile, h = organizer
    ev = make_event(db, profile)
    monkeypatch.setattr(get_settings(), "public_api_url", "https://api.biletflow.kz/")
    key = upload(client, ev.id, h, PNG).json()["cover_image_key"]
    url = client.get(f"{API}/events/{ev.id}").json()["cover_image_url"]
    assert url == f"https://api.biletflow.kz{API}/files/{key}"


def test_external_cover_url_passes_through(client: TestClient, organizer) -> None:
    _, h = organizer
    resp = client.post(
        f"{API}/events", json=payload(cover_image_key="https://cdn.example.com/a.jpg"), headers=h
    )
    assert resp.status_code == 201
    assert resp.json()["cover_image_url"] == "https://cdn.example.com/a.jpg"


# ------------------------------------------------------------------ refund policy


def test_refund_policy_create_update_read(client: TestClient, organizer) -> None:
    _, h = organizer
    created = client.post(
        f"{API}/events", json=payload(refund_policy="Full refund up to 48 h before"), headers=h
    ).json()
    assert created["refund_policy"] == "Full refund up to 48 h before"

    resp = client.patch(
        f"{API}/events/{created['id']}", json={"refund_policy": "No refunds"}, headers=h
    )
    assert resp.status_code == 200
    assert resp.json()["refund_policy"] == "No refunds"
    read = client.get(f"{API}/events/{created['id']}", headers=h).json()
    assert read["refund_policy"] == "No refunds"

    too_long = client.patch(
        f"{API}/events/{created['id']}", json={"refund_policy": "x" * 2001}, headers=h
    )
    assert too_long.status_code == 422


# ------------------------------------------------------------------ venues


def _venue(db: Session, name: str = "Arena") -> Venue:
    venue = Venue(name=name, city="Almaty")
    db.add(venue)
    db.flush()
    for s_order, (sname, rows, category) in enumerate(
        [("Parter", "AB", "vip"), ("Balcony", "C", None)]
    ):
        section = VenueSection(venue_id=venue.id, name=sname, sort_order=s_order)
        db.add(section)
        db.flush()
        for r_order, label in enumerate(rows):
            row = VenueRow(section_id=section.id, label=label, sort_order=r_order)
            db.add(row)
            db.flush()
            for n in (1, 2, 10):
                db.add(
                    Seat(
                        row_id=row.id,
                        seat_number=str(n),
                        price_category=category,
                        is_accessible=(label, n) == ("A", 1),
                    )
                )
    db.flush()
    return venue


def test_list_venues_with_layout_stats(client: TestClient, db: Session, organizer) -> None:
    _, h = organizer
    venue = _venue(db, "Zzz Arena")
    assert client.get(f"{API}/venues").status_code == 401

    page = client.get(f"{API}/venues", params={"q": "zzz"}, headers=h).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["id"] == str(venue.id)
    assert (item["sections"], item["seats_total"], item["accessible_seats"]) == (2, 9, 1)
    assert item["price_categories"] == [
        {"name": "standard", "seats": 3},
        {"name": "vip", "seats": 6},
    ]


def test_get_venue_layout_in_display_order(client: TestClient, db: Session, organizer) -> None:
    _, h = organizer
    venue = _venue(db)
    data = client.get(f"{API}/venues/{venue.id}", headers=h).json()
    assert [s["name"] for s in data["layout"]] == ["Parter", "Balcony"]
    assert [r["label"] for r in data["layout"][0]["rows"]] == ["A", "B"]
    a = data["layout"][0]["rows"][0]["seats"]
    assert [s["seat_number"] for s in a] == ["1", "2", "10"]
    assert (a[0]["is_accessible"], a[0]["price_category"]) == (True, "vip")
    assert data["seats_total"] == 9

    missing = client.get(f"{API}/venues/00000000-0000-0000-0000-000000000000", headers=h)
    assert missing.status_code == 404


def test_create_assigned_event_with_listed_venue(
    client: TestClient, db: Session, organizer
) -> None:
    _, h = organizer
    _venue(db)
    venue_id = client.get(f"{API}/venues", headers=h).json()["items"][0]["id"]
    no_venue = client.post(f"{API}/events", json=payload(seating_mode="assigned"), headers=h)
    assert no_venue.json()["error"]["code"] == "venue_required"

    resp = client.post(
        f"{API}/events", json=payload(seating_mode="assigned", venue_id=venue_id), headers=h
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["venue_id"] == venue_id
    assert client.get(f"{API}/events/{resp.json()['id']}/seatmap", headers=h).status_code == 200


# ------------------------------------------------------------------ organizer list


def test_organizer_search_and_sold_capacity(client: TestClient, db: Session, organizer) -> None:
    profile, h = organizer
    jazz = make_event(db, profile, title="Jazz Night", capacity=300)
    make_event(db, profile, title="Rock Fest", location_name="Jazz Club")
    make_event(db, profile, title="Opera")
    make_event(db, title="Jazz elsewhere")  # another organizer
    make_ticket_type(db, jazz, quantity_total=100, quantity_sold=40, quantity_reserved=5)
    make_ticket_type(db, jazz, name="VIP", quantity_total=20, quantity_sold=7)

    page = client.get(f"{API}/organizers/me/events", params={"q": " jazz "}, headers=h).json()
    assert page["total"] == 2
    assert {i["title"] for i in page["items"]} == {"Jazz Night", "Rock Fest"}
    card = next(i for i in page["items"] if i["title"] == "Jazz Night")
    assert (card["sold"], card["capacity"]) == (47, 300)
    rock = next(i for i in page["items"] if i["title"] == "Rock Fest")
    assert rock["sold"] == 0

    all_items = client.get(f"{API}/organizers/me/events", headers=h).json()
    assert all_items["total"] == 3
    assert (
        client.get(f"{API}/organizers/me/events", params={"q": "nothing"}, headers=h).json()[
            "total"
        ]
        == 0
    )


def test_public_catalog_cards_carry_sold_and_capacity(client: TestClient, db: Session) -> None:
    ev = make_event(db, title="Catalog card", capacity=50)
    make_ticket_type(db, ev, quantity_total=50, quantity_sold=12)
    items = client.get(f"{API}/events", params={"q": "Catalog card"}).json()["items"]
    assert [(i["sold"], i["capacity"]) for i in items] == [(12, 50)]


def test_event_model_has_refund_policy_column(db: Session) -> None:
    ev = make_event(db, refund_policy="Refunds until the day before")
    db.expire(ev)
    assert db.get(Event, ev.id).refund_policy == "Refunds until the day before"  # type: ignore[union-attr]
