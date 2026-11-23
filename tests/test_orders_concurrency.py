"""BE-14 acceptance: the last ticket can't be sold twice.

Unlike the other tests this one needs real, committed data and real parallel transactions —
every thread has its own connection and session. The data is removed afterwards.
"""

import threading
import uuid
from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import (
    Attendee,
    AuditLog,
    Event,
    Order,
    OrderItem,
    OrganizerProfile,
    TicketType,
    User,
    UserRole,
)
from app.models.enums import OrderStatus
from app.schemas.orders import AttendeeIn, OrderCreate, OrderLineIn
from app.services import orders as orders_svc
from tests.factories import make_event, make_organizer, make_ticket_type, make_user

pytestmark = pytest.mark.db

BUYERS = 20


@pytest.fixture
def parallel_engine(engine: Engine) -> Iterator[Engine]:
    """A pool big enough that all buyers really hold a connection at the same time."""
    eng = create_engine(get_settings().database_url, pool_size=BUYERS + 2, max_overflow=0)
    yield eng
    eng.dispose()


def _cleanup(eng: Engine, event_id: uuid.UUID, user_ids: list[uuid.UUID]) -> None:
    """Delete everything the test committed.

    ``audit_log`` is append-only (trigger) and references users, so the delete runs with
    triggers off for this one transaction (needs the DB owner/superuser — as in CI and the
    docker-compose database). Children are deleted explicitly because FK cascades are
    triggers too.
    """
    with eng.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        order_ids = select(Order.id).where(Order.event_id == event_id)
        item_ids = select(OrderItem.id).where(OrderItem.order_id.in_(order_ids))
        conn.execute(delete(AuditLog).where(AuditLog.event_id == event_id))
        conn.execute(delete(AuditLog).where(AuditLog.actor_user_id.in_(user_ids)))
        conn.execute(delete(Attendee).where(Attendee.order_item_id.in_(item_ids)))
        conn.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
        conn.execute(delete(Order).where(Order.event_id == event_id))
        conn.execute(delete(TicketType).where(TicketType.event_id == event_id))
        conn.execute(delete(Event).where(Event.id == event_id))
        conn.execute(delete(OrganizerProfile).where(OrganizerProfile.user_id.in_(user_ids)))
        conn.execute(delete(UserRole).where(UserRole.user_id.in_(user_ids)))
        conn.execute(delete(User).where(User.id.in_(user_ids)))


def test_last_ticket_twenty_parallel_buyers(parallel_engine: Engine) -> None:
    with Session(parallel_engine) as s:
        org = make_organizer(s)
        ev = make_event(s, org, paid_sales_active=True)
        last = make_ticket_type(s, ev, name="Last one", quantity_total=1)
        buyers = [make_user(s, full_name=f"Buyer {i}") for i in range(BUYERS)]
        s.commit()
        event_id, tt_id = ev.id, last.id
        buyer_ids = [b.id for b in buyers]
        user_ids = [org.user_id, *buyer_ids]

    try:
        barrier = threading.Barrier(BUYERS)
        results: list[Any] = []
        lock = threading.Lock()

        def buy(buyer_id: uuid.UUID) -> None:
            data = OrderCreate(
                event_id=event_id,
                items=[OrderLineIn(ticket_type_id=tt_id, quantity=1)],
                attendees=[AttendeeIn(full_name="Guest", email="guest@example.com")],
            )
            with Session(parallel_engine) as db:
                buyer = db.get(User, buyer_id)
                assert buyer is not None
                db.connection()  # check out the connection before the race starts
                barrier.wait()
                try:
                    orders_svc.create_order(db, buyer, data)
                    outcome: Any = "ok"
                except AppError as exc:
                    outcome = (exc.status_code, exc.code)
                except Exception as exc:  # noqa: BLE001 — reported by the assertion below
                    outcome = repr(exc)
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=buy, args=(b,)) for b in buyer_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert Counter(map(str, results)) == {
            "ok": 1,
            str((409, "sold_out")): BUYERS - 1,
        }, results

        with Session(parallel_engine) as s:
            tt = s.get(TicketType, tt_id)
            assert tt is not None
            assert (tt.quantity_reserved, tt.quantity_sold) == (1, 0)
            pending = s.scalars(
                select(Order).where(Order.event_id == event_id, Order.status == OrderStatus.PENDING)
            ).all()
            assert len(pending) == 1
    finally:
        _cleanup(parallel_engine, event_id, user_ids)
