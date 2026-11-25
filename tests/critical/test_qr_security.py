"""Admission QR security at the door (SRS §4.7, §4.8, §11; BACKEND_ARCHITECTURE.md §5.4).

Through the real check-in API: only a correctly signed ticket of *this* event is admitted;
Campaign QR codes, forged signatures, foreign event ids, tickets of other events, garbage and
empty strings are rejected — by ``/checkin/validate`` and by ``/checkin``, which then admits
nobody.
"""

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CheckInRecord, StaffAssignment, Ticket
from app.models.enums import TicketStatus
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

pytestmark = [pytest.mark.db, pytest.mark.critical]

API = "/api/v1"


@pytest.fixture
def door(db: Session) -> SimpleNamespace:
    ev = make_event(db, make_organizer(db))
    tt = make_ticket_type(db, ev)
    make_paid_order(db, make_user(db), tt, [("Guest", "guest@example.com")])
    other = make_event(db, make_organizer(db))
    make_paid_order(db, make_user(db), make_ticket_type(db, other), [("Other", "o@example.com")])
    staff = make_user(db)
    db.add(StaffAssignment(event_id=ev.id, user_id=staff.id))
    db.flush()
    ticket = db.scalar(select(Ticket).where(Ticket.event_id == ev.id))
    foreign = db.scalar(select(Ticket).where(Ticket.event_id == other.id))
    assert ticket and foreign
    return SimpleNamespace(event=ev, staff=staff, ticket=ticket, foreign=foreign)


def scan(client: TestClient, door: SimpleNamespace, qr: str, path: str = "") -> Any:
    return client.post(
        f"{API}/checkin{path}",
        json={"event_id": str(door.event.id), "qr_payload": qr},
        headers=auth_headers(door.staff),
    )


def verdict(resp: Any) -> str:
    data = resp.json()
    return str(data.get("result") or data["error"]["code"])


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def test_genuine_admission_qr_is_accepted_once(client: TestClient, door, db: Session) -> None:
    assert verdict(scan(client, door, door.ticket.qr_payload, "/validate")) == "valid"
    first = scan(client, door, door.ticket.qr_payload)
    assert first.status_code == 200 and verdict(first) == "valid"
    again = scan(client, door, door.ticket.qr_payload)
    assert again.status_code == 409 and verdict(again) == "already_checked_in"


def _rejections(door: SimpleNamespace) -> dict[str, tuple[str, str]]:
    """label → (QR payload, expected verdict)."""
    real = door.ticket.qr_payload
    prefix, body, sig = real.split(".")
    token = campaigns.generate_qr_token()
    return {
        "campaign token": (token, "campaign_qr_not_admission"),
        "campaign link": (campaigns.qr_link(door.event.id, token), "campaign_qr_not_admission"),
        "forged signature": (f"{prefix}.{body}.{sig[::-1]}", "invalid_qr"),
        "foreign event id, old signature": (
            f"{prefix}.{_b64(f'{door.ticket.ticket_code}|{door.foreign.event_id}')}.{sig}",
            "invalid_qr",
        ),
        "signed with another secret": (
            "T1." + body + "." + _b64("x" * 32),
            "invalid_qr",
        ),
        "unknown ticket, valid signature": (
            sign_ticket_payload("NO-SUCH-CODE", door.event.id),
            "invalid_qr",
        ),
        "ticket of another event": (door.foreign.qr_payload, "wrong_event"),
        "garbage": ("hello world", "invalid_qr"),
        "url": ("https://example.com/t/123", "invalid_qr"),
        "empty string": ("", "invalid_qr"),
        "whitespace": ("   ", "invalid_qr"),
    }


@pytest.mark.parametrize(
    "label",
    [
        "campaign token",
        "campaign link",
        "forged signature",
        "foreign event id, old signature",
        "signed with another secret",
        "unknown ticket, valid signature",
        "ticket of another event",
        "garbage",
        "url",
        "empty string",
        "whitespace",
    ],
)
def test_everything_else_is_rejected(client: TestClient, door, db: Session, label: str) -> None:
    qr, expected = _rejections(door)[label]
    assert verdict(scan(client, door, qr, "/validate")) == expected
    resp = scan(client, door, qr)
    assert resp.status_code in (409, 422) and verdict(resp) == expected

    # nobody was admitted, nothing was recorded
    for t in (door.ticket, door.foreign):
        db.refresh(t)
        assert t.status == TicketStatus.VALID
    assert db.scalars(select(CheckInRecord)).all() == []
