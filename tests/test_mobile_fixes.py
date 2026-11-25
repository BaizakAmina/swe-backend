"""Page limits on attendees / timeline, links clients can open, app deep links in emails."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.main import create_app
from app.models import Notification, User
from app.models.enums import Locale
from app.services import notifications
from tests.factories import (
    auth_headers,
    make_event,
    make_organizer,
    make_paid_order,
    make_ticket_type,
    make_user,
)
from tests.test_notifications import _paid_ticket

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture
def event_with_guests(db: Session) -> tuple[Any, dict[str, str]]:
    org = make_organizer(db)
    ev = make_event(db, org)
    tt = make_ticket_type(db, ev, quantity_total=300)
    buyer = make_user(db)
    make_paid_order(db, buyer, tt, [(f"Guest {i:03}", f"g{i}@example.com") for i in range(130)])
    owner = db.get(User, org.user_id)
    assert owner
    return ev, auth_headers(owner)


# ------------------------------------------------------------------ page limits


@pytest.mark.parametrize(("asked", "applied"), [(150, 150), (200, 200), (500, 200), (5, 5)])
def test_attendees_accept_up_to_200(
    client: TestClient, event_with_guests, asked: int, applied: int
) -> None:
    ev, h = event_with_guests
    resp = client.get(f"{API}/events/{ev.id}/attendees", params={"limit": asked}, headers=h)
    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert page["limit"] == applied and page["total"] == 130
    assert len(page["items"]) == min(applied, 130)


def test_attendees_reject_bad_paging(client: TestClient, event_with_guests) -> None:
    ev, h = event_with_guests
    for params in ({"limit": 0}, {"offset": -1}):
        resp = client.get(f"{API}/events/{ev.id}/attendees", params=params, headers=h)
        assert resp.status_code == 422


@pytest.mark.parametrize(("asked", "applied"), [(200, 200), (150, 150), (1000, 200)])
def test_timeline_accepts_up_to_200(
    client: TestClient, event_with_guests, asked: int, applied: int
) -> None:
    ev, h = event_with_guests
    resp = client.get(f"{API}/events/{ev.id}/timeline", params={"limit": asked}, headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["limit"] == applied


def test_regular_lists_keep_the_100_limit(client: TestClient) -> None:
    assert client.get(f"{API}/events", params={"limit": 101}).status_code == 422


# ------------------------------------------------------------------ links in responses


def test_calendar_and_pdf_links_use_the_address_the_client_called(
    db: Session, event_with_guests
) -> None:
    from app.core.db import get_db

    ev, h = event_with_guests
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, base_url="http://192.168.1.20:8000") as phone:
        ics = phone.get(f"{API}/events/{ev.id}").json()["calendar_links"]["ics"]
        assert ics == f"http://192.168.1.20:8000{API}/events/{ev.id}/calendar.ics"
        assert phone.get(ics.removeprefix("http://192.168.1.20:8000")).status_code == 200

        buyer = make_user(db)
        make_paid_order(db, buyer, make_ticket_type(db, ev))
        ticket = phone.get(f"{API}/me/tickets", headers=auth_headers(buyer)).json()["items"][0]
        assert ticket["pdf_url"].startswith("http://192.168.1.20:8000/api/v1/tickets/")
    assert "localhost" not in ics


def test_public_api_url_overrides_request_address(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "public_api_url", "https://api.biletflow.kz")
    ev = make_event(db)
    ics = client.get(f"{API}/events/{ev.id}").json()["calendar_links"]["ics"]
    assert ics == f"https://api.biletflow.kz{API}/events/{ev.id}/calendar.ics"


# ------------------------------------------------------------------ deep links in emails


def test_verify_and_reset_emails_carry_app_deep_links(
    db: Session, outbox: list[dict[str, Any]]
) -> None:
    user = make_user(db, locale=Locale.EN)
    notifications.send_verify_email(db, user, "tok123")
    mail = outbox[-1]
    assert mail["app_link"] == "biletflow://app/verify-email?token=tok123"
    assert "http://localhost:3000/verify-email?token=tok123" in mail["text"]
    assert "Open in the BiletFlow app: biletflow://app/verify-email?token=tok123" in mail["text"]
    assert 'href="biletflow://app/verify-email?token=tok123"' in mail["html"]

    notifications.send_password_reset(db, user, "reset9")
    assert outbox[-1]["app_link"] == "biletflow://app/reset-password?token=reset9"
    # tokens never reach the database, web or app link
    for record in db.scalars(select(Notification).where(Notification.user_id == user.id)):
        assert "tok123" not in str(record.payload) and "reset9" not in str(record.payload)


def test_other_emails_link_the_same_screen_in_the_app(
    db: Session, outbox: list[dict[str, Any]]
) -> None:
    buyer = make_user(db, email="buyer@example.com", locale=Locale.RU)
    ticket = _paid_ticket(db, buyer)
    notifications.send_ticket(db, ticket)
    mail = outbox[-1]
    assert mail["app_link"] == f"biletflow://app/tickets/{ticket.id}"
    assert "Открыть в приложении BiletFlow" in mail["text"]
    record = db.scalars(select(Notification).where(Notification.user_id == buyer.id)).one()
    assert record.payload["app_link"] == f"biletflow://app/tickets/{ticket.id}"

    order = ticket.order_item.order
    notifications.send_order_confirmation(db, order)
    assert outbox[-1]["app_link"] == f"biletflow://app/orders/{order.id}"


def test_app_links_can_be_switched_off(
    db: Session, outbox: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "app_link_base", "")
    notifications.send_verify_email(db, make_user(db), "tok")
    assert outbox[-1]["app_link"] is None
    assert "biletflow://" not in outbox[-1]["text"]
