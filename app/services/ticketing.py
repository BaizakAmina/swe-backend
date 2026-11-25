"""Ticket issuance (SRS §4.6, §4.7).

Tickets (one per order item, bound to its attendee) exist only for a **paid** order: paid via a
succeeded payment (BE-15) or completed as a free registration. :func:`issue_tickets` runs inside
the transaction that marks the order paid (``orders.on_order_paid_hooks``), so the order never
ends up paid without tickets or with tickets but unpaid.

The digital ticket, the QR and the printed PDF all carry the same ``ticket_code``; the QR holds
the signed payload from ``qr_sign``. Email + PDF happen after the commit (:func:`deliver`).
"""

import logging
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models import Order, OrderItem, Ticket
from app.models.enums import OrderStatus, TicketStatus
from app.services import audit, notifications, pdf
from app.services.audit import AuditAction
from app.services.payments.records import has_succeeded_ticket_payment
from app.services.qr_sign import sign_ticket_payload

logger = logging.getLogger("biletflow.ticketing")


def new_ticket_code() -> str:
    """16 URL-safe characters (96 random bits); never contains ``|``."""
    return secrets.token_urlsafe(12)


def issue_tickets(db: Session, order: Order) -> list[Ticket]:
    """Create the missing tickets of a paid order. Idempotent; never commits.

    Returns only the tickets created by this call (``[]`` if all already existed).
    """
    if order.status != OrderStatus.PAID:
        raise ConflictError("order_not_paid", "Tickets are issued only for paid orders")
    if order.total_minor > 0 and not has_succeeded_ticket_payment(db, order):
        raise ConflictError("payment_required", "The order has no succeeded payment")

    item_ids = [item.id for item in order.items]
    existing = set(
        db.scalars(select(Ticket.order_item_id).where(Ticket.order_item_id.in_(item_ids)))
    )
    issued: list[Ticket] = []
    for item in order.items:
        if item.id in existing:
            continue
        if item.attendee is None:  # BE-14 creates one per item; never issue an anonymous ticket
            raise ConflictError("attendee_missing", "Every ticket needs attendee details")
        code = new_ticket_code()
        ticket = Ticket(
            event_id=order.event_id,
            attendee_id=item.attendee.id,
            ticket_code=code,
            qr_payload=sign_ticket_payload(code, order.event_id),
            status=TicketStatus.VALID,
        )
        item.ticket = ticket  # sets order_item_id (UNIQUE: a second ticket per item can't exist)
        issued.append(ticket)
    if not issued:
        return []

    db.flush()
    audit.log(
        db,
        actor_id=None,
        action=AuditAction.TICKETS_ISSUED,
        entity_type="order",
        entity_id=order.id,
        event_id=order.event_id,
        description=f"{len(issued)} ticket(s) issued",
        metadata={"ticket_ids": [str(t.id) for t in issued]},
    )
    return issued


def tickets_of(db: Session, order_id: uuid.UUID) -> list[Ticket]:
    stmt = (
        select(Ticket)
        .join(OrderItem, OrderItem.id == Ticket.order_item_id)
        .where(OrderItem.order_id == order_id)
        .order_by(OrderItem.created_at)
    )
    return list(db.scalars(stmt))


def deliver(db: Session, order: Order) -> None:
    """After the commit: email each attendee their ticket and pre-render the PDF.

    Best effort — a failure here never undoes the purchase: the ticket is in the account,
    the PDF is rendered on demand by ``GET /tickets/{id}/pdf``.
    """
    for ticket in tickets_of(db, order.id):
        if ticket.status != TicketStatus.VALID:
            continue
        try:
            notifications.send_ticket(db, ticket)
        except Exception:  # noqa: BLE001
            logger.exception("could not send ticket %s", ticket.id)
        try:
            pdf.get_or_create_ticket_pdf(db, ticket)
        except Exception:  # noqa: BLE001
            logger.exception("could not pre-render PDF for ticket %s", ticket.id)
