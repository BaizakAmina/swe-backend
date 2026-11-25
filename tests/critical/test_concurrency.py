"""Correctness under concurrency (SRS §7, §11; BACKEND_ARCHITECTURE.md §5.1–5.3).

Every test commits its data, races N real transactions (``harness.run_parallel``) and checks
both the outcomes and the database afterwards. ``docs/CORRECTNESS.md`` maps each guarantee to
its mechanism and to the test here.
"""

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import (
    CheckInRecord,
    Event,
    Order,
    Payment,
    PayoutAccount,
    PromotionalCampaign,
    Refund,
    SeatHold,
    StaffAssignment,
    Ticket,
    TicketType,
    User,
)
from app.models.enums import (
    OrderStatus,
    PaymentPurpose,
    PaymentStatus,
    SeatingMode,
    VerificationStatus,
)
from app.schemas.orders import AttendeeIn, OrderCreate, OrderLineIn
from app.schemas.tickets import CheckInRequest
from app.services import checkin, refunds, seating
from app.services import orders as orders_svc
from app.services.payments import activation, checkout
from app.services.payments.gateway import SimulatedGateway
from tests.committed_data import cleanup
from tests.critical.harness import run_parallel
from tests.factories import (
    make_event,
    make_organizer,
    make_paid_order,
    make_ticket_type,
    make_user,
)
from tests.test_promo import make_campaign
from tests.test_seating import make_venue

pytestmark = [pytest.mark.db, pytest.mark.critical]

N = 20


class Committed:
    """Builds committed test data in one session; removes it (and the race's rows) at the end."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        # objects stay readable after commit: workers read ids without touching this session
        self.db = Session(engine, expire_on_commit=False)
        self.event_id: uuid.UUID | None = None
        self.venue_id: uuid.UUID | None = None
        self.user_ids: list[uuid.UUID] = []

    def user(self) -> User:
        u = make_user(self.db)
        self.user_ids.append(u.id)
        return u

    def event(self, **kw: Any) -> Event:
        org = make_organizer(self.db, **kw.pop("organizer", {}))
        self.user_ids.append(org.user_id)
        ev = make_event(self.db, org, **kw)
        self.event_id = ev.id
        return ev

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.close()
        if self.event_id is not None:
            cleanup(self.engine, self.event_id, self.user_ids, self.venue_id)


@pytest.fixture
def world(parallel_engine: Engine) -> Iterator[Committed]:
    w = Committed(parallel_engine)
    try:
        yield w
    finally:
        w.close()


def order_data(event_id: uuid.UUID, tt_id: uuid.UUID, qty: int = 1, **kw: Any) -> OrderCreate:
    return OrderCreate(
        event_id=event_id,
        items=[OrderLineIn(ticket_type_id=tt_id, quantity=qty)],
        attendees=[
            AttendeeIn(full_name=f"Guest {i}", email=f"g{i}@example.com") for i in range(qty)
        ],
        **kw,
    )


def as_user(
    user_ids: list[uuid.UUID], fn: Callable[[Session, User], Any]
) -> Callable[[Session, int], Any]:
    """Worker ``i`` acts as ``user_ids[i]`` (or the only user, if there is one)."""

    def work(db: Session, i: int) -> Any:
        user = db.get(User, user_ids[i if len(user_ids) > 1 else 0])
        assert user is not None
        return fn(db, user)

    return work


def count(engine: Engine, stmt: Any) -> int:
    with Session(engine) as s:
        return int(s.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


# ------------------------------------------------------------------ inventory (BE-14)


@pytest.mark.parametrize(("total", "expected_ok"), [(1, 1), (5, 5)])
def test_inventory_is_never_oversold(
    world: Committed, parallel_engine: Engine, total: int, expected_ok: int
) -> None:
    ev = world.event(paid_sales_active=True)
    tt = make_ticket_type(world.db, ev, quantity_total=total)
    buyers = [world.user().id for _ in range(N)]
    world.commit()
    data = order_data(ev.id, tt.id)

    def buy(db: Session, user: User) -> str:
        orders_svc.create_order(db, user, data)
        return "ok"

    outcome = run_parallel(parallel_engine, N, as_user(buyers, buy))

    assert outcome.counts == {"ok": expected_ok, "sold_out": N - expected_ok}, outcome.results
    with Session(parallel_engine) as s:
        fresh = s.get(TicketType, tt.id)
        assert fresh is not None
        assert fresh.quantity_reserved == expected_ok and fresh.quantity_sold == 0
        assert fresh.quantity_sold + fresh.quantity_reserved <= fresh.quantity_total
    pending = select(Order.id).where(Order.event_id == ev.id, Order.status == OrderStatus.PENDING)
    assert count(parallel_engine, pending) == expected_ok


# ------------------------------------------------------------------ promo limit (BE-23)


def test_promo_code_never_exceeds_max_redemptions(
    world: Committed, parallel_engine: Engine
) -> None:
    limit = 3
    ev = world.event(paid_sales_active=True)
    # one ticket type per buyer: the ticket-type locks don't serialise the race,
    # only the campaign lock can keep the limit
    types = [make_ticket_type(world.db, ev, price_minor=1_000_000) for _ in range(N)]
    campaign = make_campaign(world.db, ev, code="RACE3", value=50, max_redemptions=limit)
    buyers = [world.user().id for _ in range(N)]
    world.commit()

    def buy(db: Session, i: int) -> Any:
        user = db.get(User, buyers[i])
        assert user is not None
        order = orders_svc.create_order(
            db, user, order_data(ev.id, types[i].id, promo_code="RACE3")
        )
        return f"discount {order.discount_minor}"

    outcome = run_parallel(parallel_engine, N, buy)

    assert outcome.counts == {"discount 500000": limit, "promo_exhausted": N - limit}
    with Session(parallel_engine) as s:
        fresh = s.get(PromotionalCampaign, campaign.id)
        assert fresh is not None and fresh.redemption_count == limit
    discounted = select(Order.id).where(Order.event_id == ev.id, Order.discount_minor > 0)
    assert count(parallel_engine, discounted) == limit


# ------------------------------------------------------------------ seats (BE-30)


def test_seat_is_held_once(world: Committed, parallel_engine: Engine) -> None:
    venue, seats = make_venue(world.db)
    world.venue_id = venue.id
    ev = world.event(seating_mode=SeatingMode.ASSIGNED, venue_id=venue.id)
    seat_id = seats["A1"].id
    holders = [world.user().id for _ in range(N)]
    world.commit()

    def grab(db: Session, user: User) -> str:
        seating.hold(db, user, ev.id, [seat_id])
        return "ok"

    outcome = run_parallel(parallel_engine, N, as_user(holders, grab))

    assert outcome.counts == {"ok": 1, "seat_unavailable": N - 1}, outcome.results
    active = select(SeatHold.id).where(SeatHold.seat_id == seat_id, SeatHold.released_at.is_(None))
    assert count(parallel_engine, active) == 1


# ------------------------------------------------------------------ check-in (BE-18)


def test_ticket_is_admitted_once(world: Committed, parallel_engine: Engine) -> None:
    ev = world.event()
    tt = make_ticket_type(world.db, ev)
    make_paid_order(world.db, world.user(), tt, [("Guest", "guest@example.com")])
    scanners = [world.user() for _ in range(6)]
    for s in scanners:
        world.db.add(StaffAssignment(event_id=ev.id, user_id=s.id))
    ticket = world.db.scalar(select(Ticket).where(Ticket.event_id == ev.id))
    assert ticket is not None
    world.commit()
    request = CheckInRequest(event_id=ev.id, qr_payload=ticket.qr_payload)

    outcome = run_parallel(
        parallel_engine,
        len(scanners),
        as_user([s.id for s in scanners], lambda db, u: checkin.check_in(db, u, request).result),
    )

    assert outcome.counts == {"valid": 1, "already_checked_in": len(scanners) - 1}
    records = select(CheckInRecord.id).where(CheckInRecord.ticket_id == ticket.id)
    assert count(parallel_engine, records) == 1


# ------------------------------------------------------------------ payments (BE-15, BE-16, BE-24)


def test_order_is_paid_once(world: Committed, parallel_engine: Engine) -> None:
    ev = world.event(paid_sales_active=True)
    tt = make_ticket_type(world.db, ev, price_minor=2_000_000)
    buyer = world.user()
    world.commit()
    order = orders_svc.create_order(world.db, buyer, order_data(ev.id, tt.id, qty=2))

    outcome = run_parallel(
        parallel_engine,
        5,
        as_user(
            [buyer.id],
            lambda db, u: checkout.pay_order(
                db, u, order.id, simulate="success", gateway=SimulatedGateway()
            ).status.value,
        ),
    )

    assert outcome.counts == {"succeeded": 1, "order_already_paid": 4}, outcome.results
    payments = select(Payment.id).where(Payment.order_id == order.id)
    assert count(parallel_engine, payments) == 1
    assert count(parallel_engine, select(Ticket.id).where(Ticket.event_id == ev.id)) == 2


def test_activation_fee_is_charged_once(world: Committed, parallel_engine: Engine) -> None:
    ev = world.event(
        paid_sales_active=False,
        organizer={
            "contact_email": "org@example.com",
            "verification_status": VerificationStatus.VERIFIED,
        },
    )
    profile_id = ev.organizer_id
    world.db.add(PayoutAccount(organizer_id=profile_id, provider_ref="sandbox_1"))
    world.commit()
    owner_id = world.user_ids[0]

    outcome = run_parallel(
        parallel_engine,
        4,
        as_user(
            [owner_id],
            lambda db, u: activation.activate(
                db, u, ev.id, simulate="success", gateway=SimulatedGateway()
            ).paid_sales_active,
        ),
    )

    assert outcome.counts == {True: 1, "paid_sales_already_active": 3}, outcome.results
    fees = select(Payment.id).where(
        Payment.event_id == ev.id, Payment.purpose == PaymentPurpose.ACTIVATION_FEE
    )
    assert count(parallel_engine, fees) == 1


def test_order_is_refunded_once(world: Committed, parallel_engine: Engine) -> None:
    ev = world.event(paid_sales_active=True)
    tt = make_ticket_type(world.db, ev, price_minor=700_000)
    buyer = world.user()
    world.commit()
    owner_id = world.user_ids[0]
    order = orders_svc.create_order(world.db, buyer, order_data(ev.id, tt.id))
    checkout.pay_order(world.db, buyer, order.id, simulate="success", gateway=SimulatedGateway())

    outcome = run_parallel(
        parallel_engine,
        4,
        as_user(
            [owner_id],
            lambda db, u: refunds.refund(
                db, u, order.id, None, gateway=SimulatedGateway()
            ).status.value,
        ),
    )

    assert outcome.counts == {"succeeded": 1, "order_already_refunded": 3}, outcome.results
    refund_rows = select(Refund.id).join(Payment).where(Payment.order_id == order.id)
    assert count(parallel_engine, refund_rows) == 1
    with Session(parallel_engine) as s:
        fresh = s.get(TicketType, tt.id)
        assert fresh is not None and fresh.quantity_sold == 0
        paid = s.scalars(
            select(Payment).where(
                Payment.order_id == order.id, Payment.status == PaymentStatus.SUCCEEDED
            )
        ).all()
        assert len(paid) == 1
