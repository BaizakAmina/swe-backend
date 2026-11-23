"""Minimal data builders for tests. Each helper flushes so ids are available immediately."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import Event, OrganizerProfile, TicketType, User, UserRole
from app.models.enums import EventStatus, Role, TicketKind


def make_user(db: Session, *, roles: tuple[Role, ...] = (Role.ATTENDEE,), **kw: Any) -> User:
    user = User(
        email=kw.pop("email", f"user-{uuid.uuid4().hex[:8]}@test.local"),
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
