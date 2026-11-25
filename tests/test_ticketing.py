"""BE-17: ticket issuance + signed admission QR payload."""

import base64
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import AuditLog, Order, Ticket
from app.models.enums import OrderStatus, TicketKind, TicketStatus
from app.services import campaigns, qr_sign, storage, ticketing
from app.services.qr_sign import (
    CampaignQRError,
    InvalidAdmissionQRError,
    sign_ticket_payload,
    verify_ticket_payload,
)
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user
from tests.test_orders import _pending_free_order, order_body, post_order

API = "/api/v1"
EVENT = uuid.UUID("3f1b2c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d")


# ------------------------------------------------------------------ QR signing (no DB)


def test_sign_and_verify_roundtrip() -> None:
    payload = sign_ticket_payload("AbC-123_xyz", EVENT)
    assert payload.startswith("T1.") and payload.count(".") == 2
    assert verify_ticket_payload(payload) == ("AbC-123_xyz", EVENT)
    assert verify_ticket_payload(f"  {payload}\n") == ("AbC-123_xyz", EVENT)  # scanner noise


def _b64(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode()).rstrip(b"=").decode()


def test_forged_payloads_are_rejected() -> None:
    payload = sign_ticket_payload("CODE1", EVENT)
    prefix, body, sig = payload.split(".")
    other_event = uuid.uuid4()
    forged = [
        f"{prefix}.{body}.{sig[:-2]}AA",  # tampered signature
        f"{prefix}.{_b64(f'CODE2|{EVENT}')}.{sig}",  # another ticket code, old signature
        f"{prefix}.{_b64(f'CODE1|{other_event}')}.{sig}",  # another event, old signature
        f"T2.{body}.{sig}",  # unknown version
        f"{prefix}.{body}",  # signature missing
        f"{payload}.extra",
    ]
    for value in forged:
        with pytest.raises(InvalidAdmissionQRError):
            verify_ticket_payload(value)


def test_payload_from_another_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "qr_secret", "someone-elses-secret")
    foreign = sign_ticket_payload("CODE1", EVENT)
    monkeypatch.setattr(get_settings(), "qr_secret", "our-secret")
    with pytest.raises(InvalidAdmissionQRError):
        verify_ticket_payload(foreign)
    assert verify_ticket_payload(sign_ticket_payload("CODE1", EVENT)) == ("CODE1", EVENT)


def test_signed_for_another_event_is_detected() -> None:
    """The signature binds the event: the caller sees which event the ticket belongs to."""
    _, event_id = verify_ticket_payload(sign_ticket_payload("CODE1", uuid.uuid4()))
    assert event_id != EVENT


def test_campaign_qr_is_not_an_admission_ticket() -> None:
    token = campaigns.generate_qr_token()
    for value in (token, campaigns.qr_link(EVENT, token)):
        with pytest.raises(CampaignQRError):
            verify_ticket_payload(value)
    assert issubclass(CampaignQRError, InvalidAdmissionQRError)


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "   ",
        "hello",
        "T1..",
        "T1.!!!.###",
        "https://biletflow.kz/events/123",
        "T1." + "A" * 600 + ".sig",
        "0123456789",
    ],
)
def test_garbage_is_rejected(garbage: str) -> None:
    with pytest.raises(InvalidAdmissionQRError):
        verify_ticket_payload(garbage)


def test_secret_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    first = sign_ticket_payload("CODE1", EVENT)
    monkeypatch.setattr(get_settings(), "qr_secret", "rotated-secret")
    assert sign_ticket_payload("CODE1", EVENT) != first

    monkeypatch.setattr(get_settings(), "qr_secret", "")
    with pytest.raises(RuntimeError):
        sign_ticket_payload("CODE1", EVENT)

    monkeypatch.setattr(get_settings(), "qr_secret", qr_sign._DEFAULT_SECRET)
    monkeypatch.setattr(get_settings(), "environment", "production")
    with pytest.raises(RuntimeError):
        sign_ticket_payload("CODE1", EVENT)


# ------------------------------------------------------------------ issuance (DB)


@pytest.fixture
def shop(db: Session) -> SimpleNamespace:
    ev = make_event(db, make_organizer(db), paid_sales_active=True)
    paid = make_ticket_type(db, ev, name="Standard", price_minor=900_000, quantity_total=10)
    free = make_ticket_type(db, ev, name="Free", kind=TicketKind.FREE, quantity_total=10)
    return SimpleNamespace(event=ev, paid=paid, free=free, buyer=make_user(db))


def _tickets(db: Session, order_id: Any) -> list[Ticket]:
    return ticketing.tickets_of(db, uuid.UUID(str(order_id)))


@pytest.mark.db
def test_paid_order_gets_one_signed_ticket_per_attendee(
    client: TestClient, shop, db: Session, outbox: list[dict[str, Any]]
) -> None:
    order = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 2))).json()
    assert _tickets(db, order["id"]) == []  # pending: nothing yet
    resp = client.post(f"{API}/orders/{order['id']}/pay", json={}, headers=auth_headers(shop.buyer))
    assert resp.json()["status"] == "succeeded"

    tickets = _tickets(db, order["id"])
    assert len(tickets) == 2 and len({t.ticket_code for t in tickets}) == 2
    for ticket, guest in zip(tickets, ["guest0@example.com", "guest1@example.com"], strict=True):
        assert ticket.status == TicketStatus.VALID and ticket.attendee.email == guest
        assert verify_ticket_payload(ticket.qr_payload) == (ticket.ticket_code, shop.event.id)
        assert ticket.pdf_key, "PDF is pre-rendered after the commit"
        assert (storage.get(ticket.pdf_key) or b"").startswith(b"%PDF")

    emails = [m for m in outbox if m["kind"] == "ticket"]
    assert sorted(m["to"] for m in emails) == ["guest0@example.com", "guest1@example.com"]

    # the API exposes the same payload and the order shows the ticket ids
    read = client.get(f"{API}/tickets/{tickets[0].id}", headers=auth_headers(shop.buyer)).json()
    assert read["qr_payload"] == tickets[0].qr_payload
    assert read["ticket_code"] == tickets[0].ticket_code
    paid = client.get(f"{API}/orders/{order['id']}", headers=auth_headers(shop.buyer)).json()
    assert {i["ticket_id"] for i in paid["items"]} == {str(t.id) for t in tickets}

    issued = db.scalars(
        select(AuditLog.action_type).where(AuditLog.entity_id == uuid.UUID(order["id"]))
    ).all()
    assert issued.count("ticket.issued") == 1


@pytest.mark.db
def test_free_registration_issues_tickets(client: TestClient, shop, db: Session) -> None:
    order = post_order(client, shop.buyer, order_body(shop.event.id, (shop.free, 2))).json()
    assert order["status"] == "paid"
    assert len(_tickets(db, order["id"])) == 2

    pending = _pending_free_order(db, shop.buyer, shop.free)
    client.post(f"{API}/orders/{pending.id}/register-free", headers=auth_headers(shop.buyer))
    assert len(_tickets(db, pending.id)) == 1


@pytest.mark.db
def test_tickets_only_from_a_paid_order(client: TestClient, shop, db: Session) -> None:
    order_json = post_order(client, shop.buyer, order_body(shop.event.id, (shop.paid, 1))).json()
    order = db.get(Order, uuid.UUID(order_json["id"]))
    assert order is not None

    with pytest.raises(AppError) as exc:
        ticketing.issue_tickets(db, order)  # pending
    assert exc.value.code == "order_not_paid"

    client.post(
        f"{API}/orders/{order.id}/pay", json={"simulate": "fail"}, headers=auth_headers(shop.buyer)
    )
    order.status = OrderStatus.PAID  # forced, without a succeeded payment
    with pytest.raises(AppError) as exc:
        ticketing.issue_tickets(db, order)
    assert exc.value.code == "payment_required"
    assert _tickets(db, order.id) == []


@pytest.mark.db
def test_issue_tickets_is_idempotent(client: TestClient, shop, db: Session) -> None:
    order_json = post_order(client, shop.buyer, order_body(shop.event.id, (shop.free, 2))).json()
    order = db.get(Order, uuid.UUID(order_json["id"]))
    assert order is not None
    before = {t.id for t in _tickets(db, order.id)}
    assert ticketing.issue_tickets(db, order) == []
    assert {t.id for t in _tickets(db, order.id)} == before


@pytest.mark.db
def test_delivery_failure_does_not_undo_the_purchase(
    client: TestClient, shop, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(ticketing.notifications, "send_ticket", boom)
    monkeypatch.setattr(ticketing.pdf, "get_or_create_ticket_pdf", boom)
    resp = post_order(client, shop.buyer, order_body(shop.event.id, (shop.free, 1)))
    assert resp.status_code == 201 and resp.json()["status"] == "paid"
    assert len(_tickets(db, resp.json()["id"])) == 1
