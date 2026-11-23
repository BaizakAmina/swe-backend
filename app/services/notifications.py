"""Email + in-app notifications (SRS §4.10).

Every notification is stored in ``notification``; emails go through the configured backend:

- ``smtp``    — real SMTP (MailHog in dev: UI at http://localhost:8025)
- ``console`` — logged only
- ``memory``  — appended to :data:`MEMORY_OUTBOX` (tests)

Call these functions **after** the business transaction is committed, so no email is sent for a
rolled-back order. A delivery failure never raises: it is logged and stored in
``notification.error`` with ``sent_at = NULL``.
"""

import logging
import smtplib
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.models import Event, Notification, Order, Ticket, TicketType, User
from app.models.enums import Locale, NotificationChannel, OrderStatus
from app.services import calendar
from app.services.email_texts import texts

logger = logging.getLogger("biletflow.notifications")

# memory backend: rendered emails, plus a structured log (kind + context incl. tokens) for tests
MEMORY_OUTBOX: list[dict[str, Any]] = []

_env = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates" / "email"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# ------------------------------------------------------------------ formatting helpers


def format_money(amount_minor: int, currency: str = "KZT") -> str:
    whole, cents = divmod(amount_minor, 100)
    text = f"{whole:,}".replace(",", " ")
    if cents:
        text += f",{cents:02d}"
    return f"{text} ₸" if currency == "KZT" else f"{text} {currency}"


def format_event_time(event: Event) -> str:
    return event.starts_at.astimezone(ZoneInfo(event.timezone)).strftime("%d.%m.%Y %H:%M")


def _web(path: str) -> str:
    return f"{get_settings().public_web_url.rstrip('/')}{path}"


def _api(path: str) -> str:
    return f"{get_settings().api_base_url.rstrip('/')}{path}"


# ------------------------------------------------------------------ delivery


Attachment = tuple[str, bytes, str]  # filename, content, mime type


def _send_email(
    to: str,
    subject: str,
    text_body: str,
    html_body: str,
    attachments: list[Attachment] | None = None,
) -> None:
    settings = get_settings()
    if settings.email_backend == "console":
        logger.info("email to=%s subject=%s\n%s", to, subject, text_body)
        return

    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    for filename, content, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    with smtplib.SMTP(
        settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
    ) as smtp:
        if settings.smtp_user and settings.smtp_password:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def notify(
    db: Session,
    *,
    user: User | None,
    kind: str,
    context: dict[str, Any],
    email: str | None = None,
    link: str | None = None,
    in_app: bool = False,
    attachments: list[Attachment] | None = None,
) -> Notification | None:
    """Render, send and record one notification. Commits its own rows; never raises."""
    to = email or (user.email if user else None)
    locale = user.locale if user else Locale.RU
    t = texts(kind, locale, context)
    payload: dict[str, Any] = {"subject": t["subject"], "link": link}
    payload.update({k: str(v) for k, v in context.items() if k != "token"})  # no secrets in DB
    if "token" in context and link:
        payload["link"] = None

    record: Notification | None = None
    if user is not None:
        record = Notification(
            user_id=user.id, type=kind, channel=NotificationChannel.EMAIL, payload=payload
        )
        db.add(record)
        if in_app:
            db.add(
                Notification(
                    user_id=user.id, type=kind, channel=NotificationChannel.IN_APP, payload=payload
                )
            )

    if to:
        ctx = {"t": t, "link": link, "locale": str(locale)}
        text_body = _env.get_template("base.txt").render(**ctx)
        html_body = _env.get_template("base.html").render(**ctx)
        try:
            if get_settings().email_backend == "memory":
                MEMORY_OUTBOX.append(
                    {
                        "kind": kind,
                        "to": to,
                        "subject": t["subject"],
                        "text": text_body,
                        "attachments": [a[0] for a in attachments or []],
                        **context,
                    }
                )
            else:
                _send_email(to, t["subject"], text_body, html_body, attachments)
            if record is not None:
                record.sent_at = datetime.now(UTC)
        except Exception as exc:  # noqa: BLE001 — delivery must never break the request
            logger.warning("email delivery failed kind=%s to=%s: %s", kind, to, exc)
            if record is not None:
                record.error = str(exc)[:500]
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not store notification kind=%s", kind)
        db.rollback()
    return record


# ------------------------------------------------------------------ public interface


def send_verify_email(db: Session, user: User, token: str) -> None:
    notify(
        db,
        user=user,
        kind="verify_email",
        context={"name": user.full_name, "token": token},
        link=_web(f"/verify-email?token={token}"),
    )


def send_password_reset(db: Session, user: User, token: str) -> None:
    notify(
        db,
        user=user,
        kind="password_reset",
        context={"name": user.full_name, "token": token},
        link=_web(f"/reset-password?token={token}"),
    )


def send_order_confirmation(db: Session, order: Order) -> None:
    buyer = db.get(User, order.buyer_user_id)
    event = db.get(Event, order.event_id)
    if buyer is None or event is None:
        return
    notify(
        db,
        user=buyer,
        kind="order_confirmation",
        context={
            "event_title": event.title,
            "total": format_money(order.total_minor, order.currency),
            "order_id": order.id,
        },
        link=_web(f"/orders/{order.id}"),
        in_app=True,
    )


def send_ticket(db: Session, ticket: Ticket) -> None:
    """Sent to the attendee's email (with the PDF link); recorded for the buyer."""
    item = ticket.order_item
    order = db.get(Order, item.order_id)
    event = db.get(Event, ticket.event_id)
    ticket_type = db.get(TicketType, item.ticket_type_id)
    if order is None or event is None or ticket_type is None:
        return
    notify(
        db,
        user=db.get(User, order.buyer_user_id),
        email=ticket.attendee.email,
        kind="ticket",
        context={
            "attendee_name": ticket.attendee.full_name,
            "ticket_type": ticket_type.name,
            "event_title": event.title,
            "starts_at": format_event_time(event),
            "ticket_code": ticket.ticket_code,
            "ticket_id": ticket.id,
        },
        link=_api(f"/api/v1/tickets/{ticket.id}/pdf"),
        attachments=[("event.ics", calendar.build_ics(event), "text/calendar")],
    )


def send_event_cancelled(db: Session, event: Event) -> None:
    """One message per buyer who has a paid / registered order for the event."""
    buyers = db.scalars(
        select(User)
        .where(
            User.id.in_(
                select(Order.buyer_user_id).where(
                    Order.event_id == event.id,
                    Order.status.in_([OrderStatus.PAID, OrderStatus.REFUNDED]),
                )
            )
        )
        .order_by(User.email)
    ).all()
    for buyer in buyers:
        notify(
            db,
            user=buyer,
            kind="event_cancelled",
            context={
                "event_title": event.title,
                "starts_at": format_event_time(event),
                "event_id": event.id,
            },
            link=_web("/me/orders"),
            in_app=True,
        )


def send_staff_assigned(db: Session, user: User, event: Event) -> None:
    notify(
        db,
        user=user,
        kind="staff_assigned",
        context={
            "event_title": event.title,
            "starts_at": format_event_time(event),
            "event_id": event.id,
        },
        link=_web("/"),
        in_app=True,
    )


def send_support_reply(
    db: Session, recipient: User, sender: User, case_id: uuid.UUID, subject: str, body: str
) -> None:
    preview = body if len(body) <= 140 else body[:137] + "…"
    notify(
        db,
        user=recipient,
        kind="support_reply",
        context={
            "sender": sender.full_name,
            "subject_line": subject,
            "preview": preview,
            "case_id": case_id,
        },
        link=_web(f"/support/{case_id}"),
        in_app=True,
    )


def send_refund_processed(db: Session, order: Order, amount_minor: int) -> None:
    buyer = db.get(User, order.buyer_user_id)
    event = db.get(Event, order.event_id)
    if buyer is None or event is None:
        return
    notify(
        db,
        user=buyer,
        kind="refund_processed",
        context={
            "event_title": event.title,
            "amount": format_money(amount_minor, order.currency),
            "order_id": order.id,
        },
        link=_web(f"/orders/{order.id}"),
        in_app=True,
    )


# ------------------------------------------------------------------ in-app API


def list_in_app(
    db: Session, user: User, unread: bool | None, limit: int, offset: int
) -> tuple[list[Notification], int]:
    stmt = select(Notification).where(
        Notification.user_id == user.id, Notification.channel == NotificationChannel.IN_APP
    )
    if unread is True:
        stmt = stmt.where(Notification.read_at.is_(None))
    elif unread is False:
        stmt = stmt.where(Notification.read_at.is_not(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return list(items), total


def mark_read(db: Session, user: User, notification_id: uuid.UUID) -> None:
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id or n.channel != NotificationChannel.IN_APP:
        raise NotFoundError("notification")
    if n.read_at is None:
        n.read_at = datetime.now(UTC)
        db.commit()
