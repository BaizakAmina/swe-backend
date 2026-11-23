import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Ticket
from app.models.enums import TicketStatus
from app.services import storage
from app.services.qr_image import make_png
from tests.factories import auth_headers, make_event, make_paid_order, make_ticket_type, make_user

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture
def ticket_and_buyer(db: Session):
    event = make_event(
        db,
        title="Қазақ эстрадасы — Жаңа жыл",
        location_name="Филармония",
        location_address="Калдаякова 35, Алматы",
    )
    tt = make_ticket_type(db, event, name="VIP")
    buyer = make_user(db)
    order = make_paid_order(db, buyer, tt, [("Әсел Нұрланқызы", "asel@example.com")])
    ticket = db.scalars(select(Ticket).where(Ticket.event_id == order.event_id)).one()
    return ticket, buyer


def test_pdf_ticket_content_and_caching(client: TestClient, ticket_and_buyer, db: Session) -> None:
    ticket, buyer = ticket_and_buyer
    resp = client.get(f"{API}/tickets/{ticket.id}/pdf", headers=auth_headers(buyer))
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")

    reader = PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    for expected in (
        ticket.ticket_code,
        "Қазақ эстрадасы",
        "Әсел Нұрланқызы",
        "VIP",
        "Asia/Almaty",
    ):
        assert expected in text, expected
    assert "₸" not in text and "KZT" not in text  # no price / payment data

    db.refresh(ticket)
    assert ticket.pdf_key and storage.get(ticket.pdf_key) == resp.content
    again = client.get(f"{API}/tickets/{ticket.id}/pdf", headers=auth_headers(buyer))
    assert again.content == resp.content  # served from storage


def test_pdf_access_and_cancelled(client: TestClient, ticket_and_buyer, db: Session) -> None:
    ticket, buyer = ticket_and_buyer
    stranger = auth_headers(make_user(db))
    assert client.get(f"{API}/tickets/{ticket.id}/pdf", headers=stranger).status_code == 403
    ticket.status = TicketStatus.REFUNDED
    db.flush()
    resp = client.get(f"{API}/tickets/{ticket.id}/pdf", headers=auth_headers(buyer))
    assert resp.status_code == 410 and resp.json()["error"]["code"] == "ticket_not_valid"


def test_qr_png(client: TestClient, ticket_and_buyer) -> None:
    ticket, buyer = ticket_and_buyer
    resp = client.get(f"{API}/tickets/{ticket.id}/qr.png", headers=auth_headers(buyer))
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(resp.content))
    assert img.size[0] == img.size[1] >= 200


def test_make_png_is_deterministic() -> None:
    assert make_png("T1.abc.def") == make_png("T1.abc.def")
