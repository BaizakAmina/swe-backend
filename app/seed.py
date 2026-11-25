"""Demo / development data.

    python -m app.seed            # idempotent: creates missing demo data only
    python -m app.seed --reset    # wipes ALL data first (dev databases only!)

All demo accounts use the password ``demo12345``.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import Base, SessionLocal
from app.core.security import hash_password
from app.models import (
    CampaignTicketType,
    Event,
    OrganizerProfile,
    PayoutAccount,
    PromoCode,
    PromotionalCampaign,
    Seat,
    StaffAssignment,
    TicketType,
    User,
    UserRole,
    Venue,
    VenueRow,
    VenueSection,
)
from app.models.enums import (
    DiscountType,
    EventStatus,
    Locale,
    PayoutAccountStatus,
    Role,
    TicketKind,
    VerificationStatus,
)

DEMO_PASSWORD = "demo12345"

DEMO_USERS: list[tuple[str, str, tuple[Role, ...]]] = [
    ("admin@biletflow.kz", "Platform Admin", (Role.PLATFORM_ADMIN,)),
    ("organizer@biletflow.kz", "Aru Events", (Role.ATTENDEE, Role.ORGANIZER)),
    ("organizer2@biletflow.kz", "Other Organizer", (Role.ATTENDEE, Role.ORGANIZER)),
    ("eventadmin@biletflow.kz", "Door Staff", (Role.ATTENDEE,)),
    ("attendee@biletflow.kz", "Demo Attendee", (Role.ATTENDEE,)),
]


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def reset(db: Session) -> None:
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    # TRUNCATE does not fire the row-level append-only trigger on audit_log.
    db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.commit()


def _user(db: Session, email: str, name: str, roles: tuple[Role, ...]) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            password_hash=hash_password(DEMO_PASSWORD),
            full_name=name,
            locale=Locale.RU,
            email_verified_at=_now(),
        )
        user.roles = [UserRole(role=r) for r in roles]
        db.add(user)
        db.flush()
    return user


def _organizer(db: Session, user: User, name: str, verified: bool) -> OrganizerProfile:
    profile = db.scalar(select(OrganizerProfile).where(OrganizerProfile.user_id == user.id))
    if profile is None:
        profile = OrganizerProfile(
            user_id=user.id,
            display_name=name,
            contact_email=user.email,
            contact_phone="+77010000000",
            verification_status=(
                VerificationStatus.VERIFIED if verified else VerificationStatus.UNVERIFIED
            ),
        )
        db.add(profile)
        db.flush()
        if verified:
            db.add(
                PayoutAccount(
                    organizer_id=profile.id,
                    provider_ref=f"sandbox_{profile.id.hex[:12]}",
                    status=PayoutAccountStatus.CONNECTED,
                )
            )
    return profile


def _event(db: Session, organizer: OrganizerProfile, title: str, **kw: object) -> Event | None:
    """Returns the new event, or None if it already exists."""
    existing = db.scalar(
        select(Event).where(Event.organizer_id == organizer.id, Event.title == title)
    )
    if existing is not None:
        return None
    event = Event(organizer_id=organizer.id, title=title, **kw)
    db.add(event)
    db.flush()
    return event


DEMO_VENUE = "BiletFlow Hall"
# section → (rows, seats per row, price category); row A seats 1–2 are wheelchair places
DEMO_LAYOUT: list[tuple[str, list[str], int, str]] = [
    ("Parter", ["A", "B", "C", "D", "E", "F"], 12, "standard"),
    ("Balcony", ["G", "H"], 10, "economy"),
]


def _venue(db: Session) -> int:
    """One predefined seat layout (assigned seating, BE-30). Returns seats created."""
    if db.scalar(select(Venue.id).where(Venue.name == DEMO_VENUE)) is not None:
        return 0
    venue = Venue(name=DEMO_VENUE, address="Abay Ave 1, Almaty", city="Almaty")
    db.add(venue)
    db.flush()
    seats = 0
    for s_order, (name, labels, per_row, category) in enumerate(DEMO_LAYOUT):
        section = VenueSection(venue_id=venue.id, name=name, sort_order=s_order)
        db.add(section)
        db.flush()
        for r_order, label in enumerate(labels):
            row = VenueRow(section_id=section.id, label=label, sort_order=r_order)
            db.add(row)
            db.flush()
            for number in range(1, per_row + 1):
                db.add(
                    Seat(
                        row_id=row.id,
                        seat_number=str(number),
                        price_category=category,
                        is_accessible=label == "A" and number <= 2,
                    )
                )
                seats += 1
    db.flush()
    return seats


def seed(db: Session) -> dict[str, int]:
    users = {email: _user(db, email, name, roles) for email, name, roles in DEMO_USERS}
    org = _organizer(db, users["organizer@biletflow.kz"], "Aru Events", verified=True)
    _organizer(db, users["organizer2@biletflow.kz"], "Other Organizer", verified=False)

    starts = (_now() + timedelta(days=14)).replace(hour=13, minute=0, second=0)  # 19:00 Almaty
    created = 0

    concert = _event(
        db,
        org,
        "Almaty Jazz Night",
        description="Вечер джаза под открытым небом. Demo event.",
        category="music",
        location_name="Kazakh National Philharmonic",
        location_address="Kaldayakov St 35, Almaty",
        starts_at=starts,
        ends_at=starts + timedelta(hours=3),
        capacity=300,
        status=EventStatus.PUBLISHED,
        published_at=_now(),
        paid_sales_active=True,
    )
    if concert is not None:
        created += 1
        free = TicketType(
            event_id=concert.id,
            name="Student (free)",
            kind=TicketKind.FREE,
            price_minor=0,
            quantity_total=50,
            max_per_order=2,
        )
        standard = TicketType(
            event_id=concert.id,
            name="Standard",
            kind=TicketKind.PAID,
            price_minor=1_000_000,  # 10 000 KZT
            quantity_total=200,
            max_per_order=6,
        )
        vip = TicketType(
            event_id=concert.id,
            name="VIP",
            kind=TicketKind.PAID,
            price_minor=2_500_000,  # 25 000 KZT
            quantity_total=50,
            max_per_order=4,
        )
        db.add_all([free, standard, vip])
        db.flush()

        campaign = PromotionalCampaign(
            event_id=concert.id,
            name="Launch -10%",
            discount_type=DiscountType.PERCENT,
            discount_value=10,
            max_redemptions=100,
            ticket_types=[CampaignTicketType(ticket_type_id=standard.id)],
        )
        db.add(campaign)
        db.flush()
        db.add(PromoCode(campaign_id=campaign.id, code="DEMO10", qr_token="demo-campaign-token"))
        db.add(StaffAssignment(event_id=concert.id, user_id=users["eventadmin@biletflow.kz"].id))

    draft = _event(
        db,
        org,
        "Tech Meetup Astana (draft)",
        description="Черновик события. Demo event.",
        category="tech",
        location_name="Astana Hub",
        starts_at=starts + timedelta(days=10),
        ends_at=starts + timedelta(days=10, hours=4),
        capacity=120,
        status=EventStatus.DRAFT,
    )
    if draft is not None:
        created += 1
        db.add(
            TicketType(
                event_id=draft.id,
                name="Free entry",
                kind=TicketKind.FREE,
                price_minor=0,
                quantity_total=120,
            )
        )

    seats = _venue(db)
    db.commit()
    return {"users": len(users), "events_created": created, "seats_created": seats}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed BiletFlow demo data")
    parser.add_argument("--reset", action="store_true", help="wipe all data first (dev only)")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        if args.reset:
            reset(db)
            print("Database wiped.")
        result = seed(db)

    print(
        f"Seed done: {result['users']} demo users, {result['events_created']} new events, "
        f"{result['seats_created']} new seats in “{DEMO_VENUE}”."
    )
    print(f"Password for all demo accounts: {DEMO_PASSWORD}")
    for email, _, roles in DEMO_USERS:
        print(f"  {email:<28} {', '.join(r.value for r in roles)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
