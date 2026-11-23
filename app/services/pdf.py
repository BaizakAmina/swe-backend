"""Printable PDF ticket (SRS §4.7).

Rendered from the canonical ticket row, so the printed copy carries the same ``ticket_code`` and
QR payload as the digital one. A4, black & white only, large QR, no price or payment data.
"""

import io
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Event, Ticket, TicketType
from app.models.enums import TicketStatus
from app.services import storage
from app.services.qr_image import make_qr

logger = logging.getLogger("biletflow.pdf")

_FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT, FONT_BOLD = "DejaVuSans", "DejaVuSans-Bold"
_registered = False


def _register_fonts() -> None:
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont(FONT, str(_FONTS / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(_FONTS / "DejaVuSans-Bold.ttf")))
        _registered = True


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if stringWidth(candidate, font, size) <= width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines or [""]


def render_ticket_pdf(
    *,
    event: Event,
    ticket: Ticket,
    ticket_type_name: str,
) -> bytes:
    _register_fonts()
    buf = io.BytesIO()
    page_w, page_h = A4
    margin = 20 * mm
    width = page_w - 2 * margin
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{event.title} — {ticket.ticket_code}")
    c.setAuthor("BiletFlow")

    y = page_h - margin
    c.setFont(FONT_BOLD, 14)
    c.drawString(margin, y - 4 * mm, "BiletFlow")
    c.setFont(FONT, 9)
    c.drawRightString(page_w - margin, y - 4 * mm, "Электронный билет · E-ticket")
    y -= 10 * mm
    c.setLineWidth(1.2)
    c.line(margin, y, page_w - margin, y)
    y -= 12 * mm

    c.setFont(FONT_BOLD, 22)
    for line in _wrap(event.title, FONT_BOLD, 22, width)[:3]:
        c.drawString(margin, y, line)
        y -= 9 * mm

    local = event.starts_at.astimezone(ZoneInfo(event.timezone))
    end_local = event.ends_at.astimezone(ZoneInfo(event.timezone))
    details: list[tuple[str, str]] = [
        ("Дата и время / Date", f"{local:%d.%m.%Y %H:%M} – {end_local:%H:%M} ({event.timezone})"),
        ("Место / Venue", event.location_name or "—"),
    ]
    if event.location_address:
        details.append(("Адрес / Address", event.location_address))
    details.append(("Билет / Ticket", ticket_type_name))
    item = ticket.order_item
    if item.seat_number:
        seat = " · ".join(
            p
            for p in (
                item.section,
                f"ряд {item.row_label}" if item.row_label else None,
                f"место {item.seat_number}",
            )
            if p
        )
        details.append(("Место в зале / Seat", seat))
    details.append(("Участник / Attendee", ticket.attendee.full_name))

    y -= 2 * mm
    for label, value in details:
        c.setFont(FONT, 9)
        c.drawString(margin, y, label)
        y -= 5 * mm
        c.setFont(FONT_BOLD, 13)
        for line in _wrap(value, FONT_BOLD, 13, width)[:2]:
            c.drawString(margin, y, line)
            y -= 6 * mm
        y -= 2 * mm

    qr_size = 85 * mm
    qr_x = (page_w - qr_size) / 2
    qr_y = max(y - qr_size - 6 * mm, margin + 30 * mm)
    c.setLineWidth(0.8)
    c.rect(qr_x - 4 * mm, qr_y - 14 * mm, qr_size + 8 * mm, qr_size + 18 * mm)
    image = make_qr(ticket.qr_payload, box_size=12, border=2).get_image()
    c.drawImage(ImageReader(image), qr_x, qr_y, qr_size, qr_size)
    c.setFont(FONT_BOLD, 16)
    c.drawCentredString(page_w / 2, qr_y - 9 * mm, ticket.ticket_code)

    c.setFont(FONT, 9)
    notes = [
        "Покажите QR-код на входе — с экрана телефона или на распечатке.",
        "Show this QR code at the entrance — on your phone or printed.",
        "Билет действителен для одного прохода. Не публикуйте QR-код.",
    ]
    ny = margin + 12 * mm
    for note in notes:
        c.drawCentredString(page_w / 2, ny, note)
        ny -= 4.5 * mm

    c.showPage()
    c.save()
    return buf.getvalue()


def pdf_key(ticket: Ticket) -> str:
    return f"tickets/{ticket.event_id}/{ticket.id}.pdf"


def get_or_create_ticket_pdf(db: Session, ticket: Ticket) -> bytes:
    """Cached in object storage; regenerated if missing. Cancelled/refunded → 410."""
    if ticket.status in (TicketStatus.CANCELLED, TicketStatus.REFUNDED):
        raise AppError("ticket_not_valid", f"Ticket is {ticket.status.value}", 410)

    if ticket.pdf_key:
        try:
            cached = storage.get(ticket.pdf_key)
            if cached:
                return cached
        except storage.StorageError:
            logger.warning("storage unavailable, rendering ticket %s on the fly", ticket.id)

    event = db.get(Event, ticket.event_id)
    ticket_type = db.get(TicketType, ticket.order_item.ticket_type_id)
    assert event is not None and ticket_type is not None
    data = render_ticket_pdf(event=event, ticket=ticket, ticket_type_name=ticket_type.name)
    try:
        key = pdf_key(ticket)
        storage.put(key, data, "application/pdf")
        if ticket.pdf_key != key:
            ticket.pdf_key = key
            db.commit()
    except storage.StorageError:
        logger.warning("could not store PDF for ticket %s", ticket.id)
    return data
