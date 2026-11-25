"""BE-15 acceptance: parallel pay of one order → exactly one succeeded payment, no double charge.

Real committed data and one connection per thread (see ``tests/committed_data.py``).
"""

import threading
from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import Order, Payment, Ticket, TicketType, User
from app.models.enums import OrderStatus, PaymentStatus
from app.schemas.orders import AttendeeIn, OrderCreate, OrderLineIn
from app.services import orders as orders_svc
from app.services.payments import checkout
from app.services.payments.gateway import SimulatedGateway
from tests.committed_data import cleanup
from tests.factories import make_event, make_organizer, make_ticket_type, make_user
from tests.test_payments import issue_test_tickets

pytestmark = pytest.mark.db

PAYERS = 5


@pytest.fixture
def parallel_engine(engine: Engine) -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, pool_size=PAYERS + 2, max_overflow=0)
    yield eng
    eng.dispose()


def test_parallel_pay_charges_once(
    parallel_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orders_svc, "on_order_paid_hooks", [issue_test_tickets])
    with Session(parallel_engine) as s:
        org = make_organizer(s)
        ev = make_event(s, org, paid_sales_active=True)
        tt = make_ticket_type(s, ev, price_minor=2_000_000, quantity_total=5)
        buyer = make_user(s)
        s.commit()
        event_id, tt_id, buyer_id = ev.id, tt.id, buyer.id
        user_ids = [org.user_id, buyer_id]

    try:
        with Session(parallel_engine) as s:
            user = s.get(User, buyer_id)
            assert user is not None
            order = orders_svc.create_order(
                s,
                user,
                OrderCreate(
                    event_id=event_id,
                    items=[OrderLineIn(ticket_type_id=tt_id, quantity=2)],
                    attendees=[
                        AttendeeIn(full_name=f"Guest {i}", email=f"g{i}@example.com")
                        for i in range(2)
                    ],
                ),
            )
            order_id = order.id

        barrier = threading.Barrier(PAYERS)
        results: list[Any] = []
        lock = threading.Lock()

        def pay(_: int) -> None:
            with Session(parallel_engine) as db:
                payer = db.get(User, buyer_id)
                assert payer is not None
                db.connection()
                barrier.wait()
                try:
                    p = checkout.pay_order(
                        db, payer, order_id, simulate="success", gateway=SimulatedGateway()
                    )
                    outcome: Any = str(p.status)
                except AppError as exc:
                    outcome = (exc.status_code, exc.code)
                except Exception as exc:  # noqa: BLE001 — reported by the assertion below
                    outcome = repr(exc)
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=pay, args=(i,)) for i in range(PAYERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert Counter(map(str, results)) == {
            "succeeded": 1,
            str((409, "order_already_paid")): PAYERS - 1,
        }, results

        with Session(parallel_engine) as s:
            payments = s.scalars(select(Payment).where(Payment.order_id == order_id)).all()
            assert [p.status for p in payments] == [PaymentStatus.SUCCEEDED]
            paid = s.get(Order, order_id)
            assert paid is not None and paid.status == OrderStatus.PAID
            tickets = s.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.event_id == event_id)
            )
            assert tickets == 2
            ticket_type = s.get(TicketType, tt_id)
            assert ticket_type is not None
            assert (ticket_type.quantity_reserved, ticket_type.quantity_sold) == (0, 2)
    finally:
        cleanup(parallel_engine, event_id, user_ids)
