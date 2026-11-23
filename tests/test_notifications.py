import socketserver
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Attendee, Notification, Order, OrderItem, Ticket, User
from app.models.enums import Locale, NotificationChannel, OrderStatus, TicketStatus
from app.services import notifications
from tests.factories import auth_headers, make_event, make_ticket_type, make_user

pytestmark = pytest.mark.db

API = "/api/v1"


def _paid_ticket(db: Session, buyer: User) -> Ticket:
    event = make_event(db, title="Jazz Night")
    tt = make_ticket_type(db, event, name="VIP")
    order = Order(
        buyer_user_id=buyer.id,
        event_id=event.id,
        status=OrderStatus.PAID,
        subtotal_minor=1_000_000,
        total_minor=1_000_000,
    )
    db.add(order)
    db.flush()
    item = OrderItem(order_id=order.id, ticket_type_id=tt.id, unit_price_minor=1_000_000)
    db.add(item)
    db.flush()
    attendee = Attendee(order_item_id=item.id, full_name="Aru", email="aru@example.com")
    db.add(attendee)
    db.flush()
    ticket = Ticket(
        order_item_id=item.id,
        event_id=event.id,
        attendee_id=attendee.id,
        ticket_code="TCODE123",
        qr_payload="T1.x.y",
        status=TicketStatus.VALID,
    )
    db.add(ticket)
    db.flush()
    return ticket


def test_ticket_email_goes_to_attendee_and_is_recorded_for_buyer(
    db: Session, outbox: list[dict[str, Any]]
) -> None:
    buyer = make_user(db, email="buyer@example.com")
    ticket = _paid_ticket(db, buyer)
    notifications.send_ticket(db, ticket)

    mail = outbox[-1]
    assert mail["kind"] == "ticket" and mail["to"] == "aru@example.com"
    assert "TCODE123" in mail["text"] and f"/tickets/{ticket.id}/pdf" in mail["text"]
    record = db.scalars(select(Notification).where(Notification.user_id == buyer.id)).one()
    assert record.type == "ticket" and record.sent_at is not None


def test_localized_subjects(db: Session, outbox: list[dict[str, Any]]) -> None:
    for locale, word in [
        (Locale.RU, "Подтвердите"),
        (Locale.EN, "Confirm"),
        (Locale.KK, "растаңыз"),
    ]:
        user = make_user(db, locale=locale)
        notifications.send_verify_email(db, user, "tok")
        assert word in outbox[-1]["subject"]


def test_tokens_are_not_persisted(db: Session) -> None:
    user = make_user(db)
    notifications.send_password_reset(db, user, "super-secret-token")
    record = db.scalars(select(Notification).where(Notification.user_id == user.id)).one()
    assert "super-secret-token" not in str(record.payload)


def test_event_cancelled_notifies_each_buyer_once(
    db: Session, outbox: list[dict[str, Any]]
) -> None:
    buyer = make_user(db, email="b1@example.com")
    ticket = _paid_ticket(db, buyer)
    event = ticket.order_item.order.event_id
    from app.models import Event

    ev = db.get(Event, event)
    assert ev
    db.add(
        Order(
            buyer_user_id=buyer.id,
            event_id=ev.id,
            status=OrderStatus.PAID,
            subtotal_minor=0,
            total_minor=0,
        )
    )
    db.flush()
    notifications.send_event_cancelled(db, ev)
    assert [m["to"] for m in outbox if m["kind"] == "event_cancelled"] == ["b1@example.com"]


def test_smtp_failure_is_recorded_not_raised(db: Session, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "email_backend", "smtp")
    monkeypatch.setattr(get_settings(), "smtp_port", 1)  # nothing listens there
    monkeypatch.setattr(get_settings(), "smtp_timeout_seconds", 0.5)
    user = make_user(db)
    notifications.send_staff_assigned(db, user, make_event(db))
    email = db.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.channel == NotificationChannel.EMAIL
        )
    ).one()
    assert email.sent_at is None and email.error


class _SMTPHandler(socketserver.StreamRequestHandler):
    received: list[bytes] = []

    def handle(self) -> None:
        w = self.wfile.write
        w(b"220 test\r\n")
        data_mode = False
        buf = b""
        for line in self.rfile:
            if data_mode:
                if line == b".\r\n":
                    data_mode = False
                    type(self).received.append(buf)
                    w(b"250 OK\r\n")
                else:
                    buf += line
                continue
            cmd = line.strip().upper()
            if cmd.startswith((b"EHLO", b"HELO")):
                w(b"250 test\r\n")
            elif cmd == b"DATA":
                data_mode = True
                w(b"354 go\r\n")
            elif cmd == b"QUIT":
                w(b"221 bye\r\n")
                return
            else:
                w(b"250 OK\r\n")


def test_real_smtp_delivery(db: Session, monkeypatch) -> None:
    server = socketserver.TCPServer(("127.0.0.1", 0), _SMTPHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(get_settings(), "email_backend", "smtp")
        monkeypatch.setattr(get_settings(), "smtp_host", "127.0.0.1")
        monkeypatch.setattr(get_settings(), "smtp_port", server.server_address[1])
        _SMTPHandler.received.clear()
        user = make_user(db, email="smtp@example.com")
        notifications.send_verify_email(db, user, "abc")
        assert _SMTPHandler.received and b"smtp@example.com" in _SMTPHandler.received[0]
    finally:
        server.shutdown()
        server.server_close()


def test_in_app_inbox(client: TestClient, db: Session) -> None:
    user = make_user(db)
    other = make_user(db)
    notifications.send_staff_assigned(db, user, make_event(db))
    h = auth_headers(user)

    inbox = client.get(f"{API}/me/notifications", headers=h).json()
    assert inbox["total"] == 1 and inbox["items"][0]["channel"] == "in_app"
    nid = inbox["items"][0]["id"]

    assert (
        client.post(f"{API}/me/notifications/{nid}/read", headers=auth_headers(other)).status_code
        == 404
    )
    assert client.post(f"{API}/me/notifications/{nid}/read", headers=h).status_code == 204
    assert (
        client.get(f"{API}/me/notifications", params={"unread": True}, headers=h).json()["total"]
        == 0
    )


def test_format_money() -> None:
    assert notifications.format_money(1_000_000) == "10 000 ₸"
    assert notifications.format_money(150) == "1,50 ₸"
