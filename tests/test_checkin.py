"""BE-18: check-in API — validate, check-in, reverse, stats."""

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, CheckInRecord, StaffAssignment, Ticket, User
from app.models.enums import CheckInAction, Role, TicketKind, TicketStatus
from app.services import campaigns
from app.services.qr_sign import sign_ticket_payload
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
def door(db: Session) -> SimpleNamespace:
    org = make_organizer(db)
    owner = db.get(User, org.user_id)
    ev = make_event(db, org, paid_sales_active=True)
    standard = make_ticket_type(db, ev, name="Standard", price_minor=500_000, quantity_total=20)
    vip = make_ticket_type(db, ev, name="VIP", price_minor=900_000, quantity_total=5)
    buyer = make_user(db, full_name="Buyer")
    order = make_paid_order(
        db, buyer, standard, [("Aru", "aru@example.com"), ("Bek", "bek@example.com")]
    )
    make_paid_order(db, buyer, vip, [("Vika", "vika@example.com")])
    staff = make_user(db, full_name="Door staff")
    db.add(StaffAssignment(event_id=ev.id, user_id=staff.id))
    db.flush()
    tickets = db.scalars(
        select(Ticket).where(Ticket.event_id == ev.id).order_by(Ticket.issued_at, Ticket.id)
    ).all()
    by_name = {t.attendee.full_name: t for t in tickets}
    return SimpleNamespace(
        event=ev, owner=owner, staff=staff, order=order, standard=standard, vip=vip, t=by_name
    )


def body(event_id: Any, qr: str, **extra: Any) -> dict[str, Any]:
    return {"event_id": str(event_id), "qr_payload": qr, **extra}


def scan(client: TestClient, user: User, path: str, data: dict[str, Any]) -> Any:
    return client.post(f"{API}/checkin{path}", json=data, headers=auth_headers(user))


def code(resp: Any) -> str:
    data = resp.json()
    return str(data.get("result") or data["error"]["code"])


def test_check_in_once_then_409_with_first_scan_time(client: TestClient, door, db: Session) -> None:
    ticket = door.t["Aru"]
    data = body(door.event.id, ticket.qr_payload, device_info="Pixel 8 / gate A")

    first = scan(client, door.staff, "", data)
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["result"] == "valid"
    assert payload["ticket"]["attendee_name"] == "Aru"
    assert payload["ticket"]["ticket_type_name"] == "Standard"
    assert payload["ticket"]["checked_in_at"]

    second = scan(client, door.staff, "", data)
    assert second.status_code == 409 and code(second) == "already_checked_in"
    error = second.json()["error"]
    assert error["details"]["result"] == "already_checked_in"
    first_scan = datetime.fromisoformat(payload["ticket"]["checked_in_at"])
    assert datetime.fromisoformat(error["details"]["ticket"]["checked_in_at"]) == first_scan
    shown = datetime.fromisoformat(error["message"].rsplit(" at ", 1)[1])
    assert abs((shown - first_scan).total_seconds()) < 1  # time of the first scan

    db.refresh(ticket)
    assert ticket.status == TicketStatus.CHECKED_IN
    records = db.scalars(select(CheckInRecord).where(CheckInRecord.ticket_id == ticket.id)).all()
    assert [(r.action, r.device_info, r.event_admin_user_id) for r in records] == [
        (CheckInAction.CHECK_IN, "Pixel 8 / gate A", door.staff.id)
    ]
    actions = db.scalars(select(AuditLog.action_type).where(AuditLog.entity_id == ticket.id))
    assert list(actions) == ["ticket.checked_in"]


def test_validate_changes_nothing(client: TestClient, door, db: Session) -> None:
    ticket = door.t["Bek"]
    for _ in range(2):
        resp = scan(client, door.staff, "/validate", body(door.event.id, ticket.qr_payload))
        assert resp.status_code == 200 and resp.json()["result"] == "valid"
        assert resp.json()["ticket"]["attendee_name"] == "Bek"
    db.refresh(ticket)
    assert ticket.status == TicketStatus.VALID and ticket.checked_in_at is None
    assert db.scalars(select(CheckInRecord).where(CheckInRecord.ticket_id == ticket.id)).all() == []

    scan(client, door.staff, "", body(door.event.id, ticket.qr_payload))
    resp = scan(client, door.staff, "/validate", body(door.event.id, ticket.qr_payload))
    assert resp.json()["result"] == "already_checked_in"


def test_campaign_forged_and_garbage_qr_are_rejected(client: TestClient, door) -> None:
    token = campaigns.generate_qr_token()
    real = door.t["Aru"].qr_payload
    cases = {
        token: "campaign_qr_not_admission",
        campaigns.qr_link(door.event.id, token): "campaign_qr_not_admission",
        real[:-3] + "abc": "invalid_qr",  # forged signature
        "hello world": "invalid_qr",
        "https://example.com/ticket/123": "invalid_qr",
        sign_ticket_payload("NO-SUCH-TICKET", door.event.id): "invalid_qr",
    }
    for qr, expected in cases.items():
        resp = scan(client, door.staff, "", body(door.event.id, qr))
        assert resp.status_code == 422 and code(resp) == expected, (qr, resp.text)
        assert code(scan(client, door.staff, "/validate", body(door.event.id, qr))) == expected


def test_ticket_of_another_event_is_wrong_event(client: TestClient, door, db: Session) -> None:
    other_event = make_event(db, make_organizer(db))
    tt = make_ticket_type(db, other_event)
    make_paid_order(db, make_user(db), tt, [("Stranger", "s@example.com")])
    foreign = db.scalar(select(Ticket).where(Ticket.event_id == other_event.id))
    assert foreign

    resp = scan(client, door.staff, "", body(door.event.id, foreign.qr_payload))
    assert resp.status_code == 409 and code(resp) == "wrong_event"
    assert resp.json()["error"]["details"]["ticket"] is None  # no attendee data leaks
    db.refresh(foreign)
    assert foreign.status == TicketStatus.VALID


@pytest.mark.parametrize("ticket_status", [TicketStatus.CANCELLED, TicketStatus.REFUNDED])
def test_cancelled_and_refunded_tickets_do_not_pass(
    client: TestClient, door, db: Session, ticket_status: TicketStatus
) -> None:
    ticket = door.t["Aru"]
    ticket.status = ticket_status
    db.flush()
    resp = scan(client, door.staff, "", body(door.event.id, ticket.qr_payload))
    assert resp.status_code == 409 and code(resp) == ticket_status.value
    db.refresh(ticket)
    assert ticket.status == ticket_status


def test_who_may_scan(client: TestClient, door, db: Session) -> None:
    qr = door.t["Aru"].qr_payload
    unassigned_staff = make_user(db)
    other_event = make_event(db, make_organizer(db))
    staff_elsewhere = make_user(db)
    db.add(StaffAssignment(event_id=other_event.id, user_id=staff_elsewhere.id))
    db.flush()
    for who in (unassigned_staff, staff_elsewhere, make_organizer(db).user):
        for path in ("/validate", ""):
            assert scan(client, who, path, body(door.event.id, qr)).status_code == 403
    assert client.post(f"{API}/checkin", json=body(door.event.id, qr)).status_code == 401

    # the organizer and a platform admin may scan too
    assert scan(client, door.owner, "/validate", body(door.event.id, qr)).status_code == 200
    admin = make_user(db, roles=(Role.PLATFORM_ADMIN,))
    assert scan(client, admin, "/validate", body(door.event.id, qr)).status_code == 200


def test_reverse_mistaken_check_in(client: TestClient, door, db: Session) -> None:
    ticket = door.t["Aru"]
    data = body(door.event.id, ticket.qr_payload)
    scan(client, door.staff, "", data)

    url = f"{API}/checkin/{ticket.id}/reverse"
    assert client.post(url, headers=auth_headers(make_user(db))).status_code == 403
    resp = client.post(url, headers=auth_headers(door.staff))
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"] == "valid" and resp.json()["ticket"]["checked_in_at"] is None
    db.refresh(ticket)
    assert ticket.status == TicketStatus.VALID and ticket.checked_in_at is None

    again = client.post(url, headers=auth_headers(door.staff))
    assert again.status_code == 409 and code(again) == "not_checked_in"
    assert scan(client, door.staff, "", data).status_code == 200  # can be admitted again

    actions = [
        r.action
        for r in db.scalars(
            select(CheckInRecord)
            .where(CheckInRecord.ticket_id == ticket.id)
            .order_by(CheckInRecord.scanned_at, CheckInRecord.created_at)
        )
    ]
    assert Counter(actions) == {CheckInAction.CHECK_IN: 2, CheckInAction.REVERSE: 1}
    assert "ticket.check_in_reversed" in set(
        db.scalars(select(AuditLog.action_type).where(AuditLog.entity_id == ticket.id))
    )


def test_stats(client: TestClient, door, db: Session) -> None:
    make_ticket_type(db, door.event, name="Unsold", kind=TicketKind.FREE, quantity_total=5)
    door.t["Vika"].status = TicketStatus.REFUNDED
    db.flush()
    scan(client, door.staff, "", body(door.event.id, door.t["Aru"].qr_payload))

    resp = client.get(
        f"{API}/events/{door.event.id}/checkin-stats", headers=auth_headers(door.staff)
    )
    assert resp.status_code == 200, resp.text
    stats = resp.json()
    assert (stats["issued"], stats["checked_in"], stats["remaining"]) == (2, 1, 1)
    by_type = {t["name"]: (t["issued"], t["checked_in"]) for t in stats["by_ticket_type"]}
    assert by_type == {"Standard": (2, 1), "VIP": (0, 0), "Unsold": (0, 0)}

    stranger = make_user(db)
    url = f"{API}/events/{door.event.id}/checkin-stats"
    assert client.get(url, headers=auth_headers(stranger)).status_code == 403


def test_check_in_follows_the_scanner_window(client: TestClient, door, db: Session) -> None:
    """Same rule as ``can_check_in`` on /me/assigned-events (``events.check_in_open``)."""
    ticket = door.t["Aru"]
    door.event.starts_at = datetime.now(UTC) - timedelta(hours=5)
    door.event.ends_at = datetime.now(UTC) - timedelta(hours=1)
    db.flush()

    cards = client.get(f"{API}/me/assigned-events", headers=auth_headers(door.staff)).json()
    assert [c["can_check_in"] for c in cards if c["id"] == str(door.event.id)] == [False]

    resp = scan(client, door.staff, "", body(door.event.id, ticket.qr_payload))
    assert resp.status_code == 409 and code(resp) == "check_in_closed"
    db.refresh(ticket)
    assert ticket.status == TicketStatus.VALID
    assert db.scalars(select(CheckInRecord).where(CheckInRecord.ticket_id == ticket.id)).all() == []

    # during the event it is open again
    door.event.ends_at = datetime.now(UTC) + timedelta(hours=1)
    db.flush()
    assert scan(client, door.staff, "", body(door.event.id, ticket.qr_payload)).status_code == 200


# ------------------------------------------------------------------ manual check-in (BE-34)


def manual(client: TestClient, user: User, event_id: Any, ticket_id: Any, **extra: Any) -> Any:
    return client.post(
        f"{API}/checkin/by-ticket",
        json={"event_id": str(event_id), "ticket_id": str(ticket_id), **extra},
        headers=auth_headers(user),
    )


def test_manual_check_in_from_attendee_search(client: TestClient, door, db: Session) -> None:
    rows = client.get(
        f"{API}/events/{door.event.id}/attendees",
        params={"q": "aru"},
        headers=auth_headers(door.staff),
    ).json()["items"]
    assert [r["attendee_name"] for r in rows] == ["Aru"]
    assert "qr_payload" not in rows[0]  # signed codes are never listed

    resp = manual(client, door.staff, door.event.id, rows[0]["ticket_id"], device_info="gate B")
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"] == "valid" and resp.json()["ticket"]["attendee_name"] == "Aru"

    ticket = door.t["Aru"]
    db.refresh(ticket)
    assert ticket.status == TicketStatus.CHECKED_IN
    [record] = db.scalars(select(CheckInRecord).where(CheckInRecord.ticket_id == ticket.id)).all()
    assert (record.action, record.device_info) == (CheckInAction.CHECK_IN, "gate B")
    [entry] = db.scalars(select(AuditLog).where(AuditLog.entity_id == ticket.id)).all()
    assert entry.action_type == "ticket.checked_in" and "manually" in entry.description
    assert entry.metadata_["method"] == "manual"

    # twice → 409, and the QR of the same ticket says the same
    again = manual(client, door.staff, door.event.id, ticket.id)
    assert again.status_code == 409 and code(again) == "already_checked_in"
    qr = scan(client, door.staff, "", body(door.event.id, ticket.qr_payload))
    assert qr.status_code == 409 and code(qr) == "already_checked_in"


@pytest.mark.parametrize("ticket_status", [TicketStatus.CANCELLED, TicketStatus.REFUNDED])
def test_manual_check_in_rejects_cancelled_and_refunded(
    client: TestClient, door, db: Session, ticket_status: TicketStatus
) -> None:
    ticket = door.t["Bek"]
    ticket.status = ticket_status
    db.flush()
    resp = manual(client, door.staff, door.event.id, ticket.id)
    assert resp.status_code == 409 and code(resp) == ticket_status.value
    db.refresh(ticket)
    assert ticket.status == ticket_status


def test_manual_check_in_access_and_scope(client: TestClient, door, db: Session) -> None:
    ticket = door.t["Aru"]
    unassigned = make_user(db)
    other_event = make_event(db, make_organizer(db))
    staff_elsewhere = make_user(db)
    db.add(StaffAssignment(event_id=other_event.id, user_id=staff_elsewhere.id))
    db.flush()
    for who in (unassigned, staff_elsewhere):
        assert manual(client, who, door.event.id, ticket.id).status_code == 403

    # a ticket of another event, picked for this one → wrong_event, nothing about its holder
    tt = make_ticket_type(db, other_event)
    make_paid_order(db, make_user(db), tt, [("Stranger", "s@example.com")])
    foreign = db.scalar(select(Ticket).where(Ticket.event_id == other_event.id))
    assert foreign
    resp = manual(client, door.staff, door.event.id, foreign.id)
    assert resp.status_code == 409 and code(resp) == "wrong_event"
    assert resp.json()["error"]["details"]["ticket"] is None
    db.refresh(foreign)
    assert foreign.status == TicketStatus.VALID

    assert manual(client, door.staff, door.event.id, uuid.uuid4()).status_code == 404

    door.event.ends_at = datetime.now(UTC) - timedelta(minutes=1)
    door.event.starts_at = datetime.now(UTC) - timedelta(hours=3)
    db.flush()
    resp = manual(client, door.staff, door.event.id, ticket.id)
    assert resp.status_code == 409 and code(resp) == "check_in_closed"
