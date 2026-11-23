from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ticket
from app.models.enums import EventStatus
from app.services import calendar, notifications
from tests.factories import make_event, make_paid_order, make_ticket_type, make_user

pytestmark = pytest.mark.db

API = "/api/v1"
START = datetime(2026, 12, 14, 13, 0, tzinfo=UTC)  # 18:00 in Almaty


def _event(db: Session, **kw):
    return make_event(
        db,
        title="Jazz Night",
        starts_at=START,
        ends_at=START.replace(hour=16),
        location_name="Philharmonic",
        location_address="Kaldayakov 35",
        **kw,
    )


def test_ics_keeps_timezone_and_stable_uid(client: TestClient, db: Session) -> None:
    event = _event(db)
    resp = client.get(f"{API}/events/{event.id}/calendar.ics")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/calendar")
    cal = Calendar.from_ical(resp.content)
    assert str(cal["method"]) == "PUBLISH"
    vevent = cal.walk("VEVENT")[0]
    assert str(vevent["uid"]) == f"event-{event.id}@biletflow.kz"
    start = vevent.decoded("dtstart")
    assert str(start.tzinfo) == "Asia/Almaty" and start.hour == 18
    assert vevent["dtstart"].params["TZID"] == "Asia/Almaty"
    assert cal.walk("VTIMEZONE")  # timezone definition included
    assert "Philharmonic, Kaldayakov 35" in str(vevent["location"])

    again = Calendar.from_ical(client.get(f"{API}/events/{event.id}/calendar.ics").content)
    assert str(again.walk("VEVENT")[0]["uid"]) == str(vevent["uid"])


def test_cancelled_event_exports_cancel_with_same_uid(client: TestClient, db: Session) -> None:
    event = _event(db, status=EventStatus.CANCELLED, revision=3)
    cal = Calendar.from_ical(client.get(f"{API}/events/{event.id}/calendar.ics").content)
    vevent = cal.walk("VEVENT")[0]
    assert str(cal["method"]) == "CANCEL" and str(vevent["status"]) == "CANCELLED"
    assert int(vevent["sequence"]) == 3


def test_draft_ics_is_hidden(client: TestClient, db: Session) -> None:
    event = _event(db, status=EventStatus.DRAFT)
    assert client.get(f"{API}/events/{event.id}/calendar.ics").status_code == 404


def test_calendar_links_in_event(client: TestClient, db: Session) -> None:
    event = _event(db)
    links = client.get(f"{API}/events/{event.id}").json()["calendar_links"]
    assert links["ics"].endswith(f"/events/{event.id}/calendar.ics")
    g = parse_qs(urlparse(links["google"]).query)
    assert g["dates"] == ["20261214T130000Z/20261214T160000Z"] and g["ctz"] == ["Asia/Almaty"]
    o = parse_qs(urlparse(links["outlook"]).query)
    assert o["subject"] == ["Jazz Night"]


def test_ticket_email_has_ics_attachment(db: Session, outbox) -> None:
    event = _event(db)
    order = make_paid_order(db, make_user(db), make_ticket_type(db, event))
    ticket = db.scalars(select(Ticket).where(Ticket.event_id == order.event_id)).one()
    notifications.send_ticket(db, ticket)
    assert outbox[-1]["attachments"] == ["event.ics"]
    assert calendar.uid(event) in calendar.build_ics(event).decode()
