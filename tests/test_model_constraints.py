"""Database-level guarantees from BACKEND_ARCHITECTURE.md §4.2 / §5."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Order,
    OrderItem,
    PromoCode,
    PromoRedemption,
    PromotionalCampaign,
    Seat,
    SeatHold,
    StaffAssignment,
    Venue,
    VenueRow,
    VenueSection,
)
from app.models.enums import DiscountType, TicketKind
from tests.factories import make_event, make_ticket_type, make_user

pytestmark = pytest.mark.db


def expect_violation(db: Session, action: Callable[[], object]) -> None:
    """Run `action` inside a SAVEPOINT and assert the DB rejects it."""
    with pytest.raises((IntegrityError, DBAPIError)), db.begin_nested():
        action()
        db.flush()


def _seat(db: Session) -> Seat:
    venue = Venue(name="Hall")
    db.add(venue)
    db.flush()
    section = VenueSection(venue_id=venue.id, name="A")
    db.add(section)
    db.flush()
    row = VenueRow(section_id=section.id, label="1")
    db.add(row)
    db.flush()
    seat = Seat(row_id=row.id, seat_number="1")
    db.add(seat)
    db.flush()
    return seat


def _order(db: Session) -> Order:
    buyer = make_user(db)
    event = make_event(db)
    order = Order(buyer_user_id=buyer.id, event_id=event.id, subtotal_minor=0, total_minor=0)
    db.add(order)
    db.flush()
    return order


def test_email_is_unique_case_insensitive(db: Session) -> None:
    make_user(db, email="Ayana@Example.com")
    expect_violation(db, lambda: make_user(db, email="ayana@example.com"))


def test_inventory_cannot_be_oversold(db: Session) -> None:
    tt = make_ticket_type(db, quantity_total=5)

    def oversell() -> None:
        tt.quantity_sold = 4
        tt.quantity_reserved = 2

    expect_violation(db, oversell)


def test_ticket_price_must_match_kind(db: Session) -> None:
    expect_violation(db, lambda: make_ticket_type(db, kind=TicketKind.FREE, price_minor=500))
    expect_violation(db, lambda: make_ticket_type(db, kind=TicketKind.PAID, price_minor=0))


def test_event_must_end_after_start(db: Session) -> None:
    now = datetime.now(UTC)
    expect_violation(db, lambda: make_event(db, starts_at=now, ends_at=now))


def test_only_one_active_hold_per_seat(db: Session) -> None:
    seat = _seat(db)
    event = make_event(db)
    user = make_user(db)
    expires = datetime.now(UTC) + timedelta(minutes=10)

    first = SeatHold(
        seat_id=seat.id, event_id=event.id, held_by_user_id=user.id, expires_at=expires
    )
    db.add(first)
    db.flush()

    expect_violation(
        db,
        lambda: db.add(
            SeatHold(
                seat_id=seat.id, event_id=event.id, held_by_user_id=user.id, expires_at=expires
            )
        ),
    )

    # Once released, the seat can be held again.
    first.released_at = datetime.now(UTC)
    db.flush()
    db.add(
        SeatHold(seat_id=seat.id, event_id=event.id, held_by_user_id=user.id, expires_at=expires)
    )
    db.flush()


def test_seat_number_unique_within_row(db: Session) -> None:
    seat = _seat(db)
    expect_violation(db, lambda: db.add(Seat(row_id=seat.row_id, seat_number="1")))


def test_one_promo_redemption_per_order(db: Session) -> None:
    order = _order(db)
    campaign = PromotionalCampaign(
        event_id=order.event_id,
        name="Launch",
        discount_type=DiscountType.PERCENT,
        discount_value=10,
    )
    db.add(campaign)
    db.flush()
    code = PromoCode(campaign_id=campaign.id, code="DEMO10", qr_token="tok-1")
    db.add(code)
    db.flush()
    db.add(PromoRedemption(promo_code_id=code.id, order_id=order.id, discount_applied_minor=100))
    db.flush()

    expect_violation(
        db,
        lambda: db.add(
            PromoRedemption(promo_code_id=code.id, order_id=order.id, discount_applied_minor=100)
        ),
    )


def test_campaign_redemptions_within_limit(db: Session) -> None:
    event = make_event(db)
    campaign = PromotionalCampaign(
        event_id=event.id,
        name="Limited",
        discount_type=DiscountType.FIXED,
        discount_value=1000,
        max_redemptions=1,
        redemption_count=1,
    )
    db.add(campaign)
    db.flush()

    def exceed() -> None:
        campaign.redemption_count = 2

    expect_violation(db, exceed)


def test_percent_discount_range(db: Session) -> None:
    event = make_event(db)
    expect_violation(
        db,
        lambda: db.add(
            PromotionalCampaign(
                event_id=event.id,
                name="Bad",
                discount_type=DiscountType.PERCENT,
                discount_value=150,
            )
        ),
    )


def test_order_total_must_be_consistent(db: Session) -> None:
    order = _order(db)

    def corrupt() -> None:
        order.subtotal_minor = 1000
        order.total_minor = 5

    expect_violation(db, corrupt)


def test_order_item_ticket_type_required(db: Session) -> None:
    order = _order(db)
    expect_violation(db, lambda: db.add(OrderItem(order_id=order.id, unit_price_minor=0)))


def test_staff_assignment_unique(db: Session) -> None:
    event = make_event(db)
    user = make_user(db)
    db.add(StaffAssignment(event_id=event.id, user_id=user.id))
    db.flush()
    expect_violation(db, lambda: db.add(StaffAssignment(event_id=event.id, user_id=user.id)))


def test_enum_values_enforced_by_check(db: Session) -> None:
    user = make_user(db)
    expect_violation(
        db,
        lambda: db.execute(
            text("UPDATE user_account SET status = 'banana' WHERE id = :id"), {"id": user.id}
        ),
    )


def test_audit_log_is_append_only(db: Session) -> None:
    user = make_user(db)
    entry = AuditLog(
        actor_user_id=user.id,
        action_type="test.created",
        entity_type="user",
        entity_id=user.id,
        description="created",
    )
    db.add(entry)
    db.flush()

    expect_violation(
        db,
        lambda: db.execute(
            text("UPDATE audit_log SET description = 'x' WHERE id = :id"), {"id": entry.id}
        ),
    )
    expect_violation(
        db, lambda: db.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": entry.id})
    )
