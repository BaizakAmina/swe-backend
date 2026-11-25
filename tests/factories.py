"""Minimal data builders for tests. Each helper flushes so ids are available immediately."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import Event, OrganizerProfile, TicketType, User, UserRole
from app.models.enums import EventStatus, Role, TicketKind
from app.services.qr_sign import sign_ticket_payload


def make_user(db: Session, *, roles: tuple[Role, ...] = (Role.ATTENDEE,), **kw: Any) -> User:
    user = User(
        email=kw.pop("email", f"user-{uuid.uuid4().hex[:8]}@example.com"),
        password_hash=kw.pop("password_hash", "not-a-real-hash"),
        full_name=kw.pop("full_name", "Test User"),
        email_verified_at=kw.pop("email_verified_at", datetime.now(UTC)),
        **kw,
    )
    user.roles = [UserRole(role=r) for r in roles]
    db.add(user)
    db.flush()
    return user


def make_organizer(db: Session, user: User | None = None, **kw: Any) -> OrganizerProfile:
    user = user or make_user(db, roles=(Role.ORGANIZER,))
    profile = OrganizerProfile(
        user_id=user.id, display_name=kw.pop("display_name", "Test Organizer"), **kw
    )
    db.add(profile)
    db.flush()
    return profile


def make_event(db: Session, organizer: OrganizerProfile | None = None, **kw: Any) -> Event:
    organizer = organizer or make_organizer(db)
    starts = kw.pop("starts_at", datetime.now(UTC) + timedelta(days=7))
    event = Event(
        organizer_id=organizer.id,
        title=kw.pop("title", "Test Event"),
        starts_at=starts,
        ends_at=kw.pop("ends_at", starts + timedelta(hours=3)),
        capacity=kw.pop("capacity", 100),
        status=kw.pop("status", EventStatus.PUBLISHED),
        **kw,
    )
    db.add(event)
    db.flush()
    return event


def make_ticket_type(db: Session, event: Event | None = None, **kw: Any) -> TicketType:
    event = event or make_event(db)
    kind = kw.pop("kind", TicketKind.PAID)
    tt = TicketType(
        event_id=event.id,
        name=kw.pop("name", "Standard"),
        kind=kind,
        price_minor=kw.pop("price_minor", 0 if kind == TicketKind.FREE else 1_000_000),
        quantity_total=kw.pop("quantity_total", 10),
        **kw,
    )
    db.add(tt)
    db.flush()
    return tt


def auth_headers(user: User) -> dict[str, str]:
    """Bearer header with a fresh access token for ``user``."""
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user.id, user.token_version).token}"}


def make_paid_order(
    db: Session,
    buyer: User,
    ticket_type: TicketType,
    attendees: list[tuple[str, str]] | None = None,
    *,
    status: Any = None,
    discount_minor: int = 0,
    promo_code_id: uuid.UUID | None = None,
    paid_at: datetime | None = None,
) -> Any:
    """Paid order with one item + attendee + valid ticket per attendee (bypasses the orders
    service — for read-side tests). Updates ``quantity_sold``."""
    from app.models import Attendee, Order, OrderItem, Ticket
    from app.models.enums import OrderStatus, TicketStatus

    attendees = attendees or [(buyer.full_name, buyer.email)]
    subtotal = ticket_type.price_minor * len(attendees)
    order = Order(
        buyer_user_id=buyer.id,
        event_id=ticket_type.event_id,
        status=status or OrderStatus.PAID,
        subtotal_minor=subtotal,
        discount_minor=discount_minor,
        total_minor=subtotal - discount_minor,
        promo_code_id=promo_code_id,
        paid_at=paid_at or datetime.now(UTC),
    )
    db.add(order)
    db.flush()
    for name, email in attendees:
        item = OrderItem(
            order_id=order.id,
            ticket_type_id=ticket_type.id,
            unit_price_minor=ticket_type.price_minor,
        )
        db.add(item)
        db.flush()
        attendee = Attendee(order_item_id=item.id, full_name=name, email=email)
        db.add(attendee)
        db.flush()
        code = uuid.uuid4().hex[:12].upper()
        db.add(
            Ticket(
                order_item_id=item.id,
                event_id=ticket_type.event_id,
                attendee_id=attendee.id,
                ticket_code=code,
                qr_payload=sign_ticket_payload(code, ticket_type.event_id),
                status=TicketStatus.VALID,
            )
        )
    ticket_type.quantity_sold += len(attendees)
    db.flush()
    db.refresh(order)
    return order
