"""BE-16: paid-sales activation — checklist + simulated activation fee."""

import threading
from collections import Counter
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import AuditLog, Event, OrganizerProfile, Payment, PayoutAccount, User
from app.models.enums import PaymentPurpose, PaymentStatus, Role, VerificationStatus
from app.services import platform_settings
from app.services.payments import activation
from app.services.payments.gateway import SimulatedGateway
from tests.committed_data import cleanup
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user
from tests.test_orders import error_code, order_body, post_order

pytestmark = pytest.mark.db

API = "/api/v1"


def ready_organizer(db: Session) -> OrganizerProfile:
    """Profile complete, verified, payout account connected."""
    org = make_organizer(
        db, contact_email="org@example.com", verification_status=VerificationStatus.VERIFIED
    )
    db.add(PayoutAccount(organizer_id=org.id, provider_ref="sandbox_acct_1"))
    db.flush()
    return org


@pytest.fixture
def ready(db: Session) -> SimpleNamespace:
    org = ready_organizer(db)
    owner = db.get(User, org.user_id)
    assert owner
    ev = make_event(db, org, paid_sales_active=False)
    paid = make_ticket_type(db, ev, price_minor=1_000_000, quantity_total=10)
    return SimpleNamespace(org=org, owner=owner, event=ev, paid=paid)


def activate(client: TestClient, user: User, event_id: Any, simulate: str = "success") -> Any:
    return client.post(
        f"{API}/events/{event_id}/activate-paid-sales",
        json={"simulate": simulate},
        headers=auth_headers(user),
    )


def test_status_shows_checklist_and_fee(client: TestClient, ready, db: Session) -> None:
    platform_settings.set_value(db, platform_settings.ACTIVATION_FEE, 750_000)
    db.flush()
    resp = client.get(
        f"{API}/events/{ready.event.id}/activation", headers=auth_headers(ready.owner)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "event_id": str(ready.event.id),
        "paid_sales_active": False,
        "fee_minor": 750_000,
        "currency": "KZT",
        "profile_complete": True,
        "verified": True,
        "payout_account_connected": True,
        "activation_payment": None,
    }


def test_incomplete_checklist_lists_missing_steps(client: TestClient, db: Session) -> None:
    org = make_organizer(db)  # display name only: no contact, not verified, no payout account
    owner = db.get(User, org.user_id)
    assert owner
    ev = make_event(db, org, paid_sales_active=False)

    resp = activate(client, owner, ev.id)
    assert resp.status_code == 422 and error_code(resp) == "activation_checklist_incomplete"
    error = resp.json()["error"]
    assert error["details"]["missing"] == [
        "profile_complete",
        "verified",
        "payout_account_connected",
    ]
    assert "payout account" in error["message"]

    # a verified, complete profile still needs the payout account
    org.contact_email = "org@example.com"
    org.verification_status = VerificationStatus.VERIFIED
    db.flush()
    resp = activate(client, owner, ev.id)
    assert resp.json()["error"]["details"]["missing"] == ["payout_account_connected"]

    db.refresh(ev)
    assert ev.paid_sales_active is False
    assert db.scalars(select(Payment).where(Payment.event_id == ev.id)).all() == []


def test_activation_enables_paid_ticket_sales(client: TestClient, ready, db: Session) -> None:
    buyer = make_user(db)
    body = order_body(ready.event.id, (ready.paid, 1))
    resp = post_order(client, buyer, body)
    assert resp.status_code == 409 and error_code(resp) == "paid_sales_inactive"

    resp = activate(client, ready.owner, ready.event.id)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["paid_sales_active"] is True
    payment = data["activation_payment"]
    assert payment["status"] == "succeeded" and payment["purpose"] == "activation_fee"
    assert payment["is_simulated"] is True and payment["event_id"] == str(ready.event.id)
    assert payment["amount_minor"] == get_settings().activation_fee_minor

    assert post_order(client, buyer, body).status_code == 201

    actions = set(
        db.scalars(select(AuditLog.action_type).where(AuditLog.event_id == ready.event.id))
    )
    assert {"event.paid_sales_activated", "payment.succeeded"} <= actions


def test_second_activation_is_409(client: TestClient, ready, db: Session) -> None:
    assert activate(client, ready.owner, ready.event.id).status_code == 200
    resp = activate(client, ready.owner, ready.event.id)
    assert resp.status_code == 409 and error_code(resp) == "paid_sales_already_active"
    fees = db.scalars(select(Payment).where(Payment.event_id == ready.event.id)).all()
    assert len(fees) == 1


def test_failed_fee_keeps_sales_inactive_and_can_retry(
    client: TestClient, ready, db: Session
) -> None:
    resp = activate(client, ready.owner, ready.event.id, "fail")
    assert resp.status_code == 200
    data = resp.json()
    assert data["paid_sales_active"] is False
    assert data["activation_payment"]["status"] == "failed"
    assert data["activation_payment"]["failure_reason"]

    data = activate(client, ready.owner, ready.event.id).json()
    assert data["paid_sales_active"] is True
    assert data["activation_payment"]["status"] == "succeeded"
    statuses = db.scalars(
        select(Payment.status)
        .where(Payment.event_id == ready.event.id)
        .order_by(Payment.created_at)
    ).all()
    assert statuses == [PaymentStatus.FAILED, PaymentStatus.SUCCEEDED]


def test_fee_comes_from_platform_settings(client: TestClient, ready, db: Session) -> None:
    platform_settings.set_value(db, platform_settings.ACTIVATION_FEE, 123_400)
    db.flush()
    payment = activate(client, ready.owner, ready.event.id).json()["activation_payment"]
    assert payment["amount_minor"] == 123_400


def test_zero_fee_activates_without_payment(client: TestClient, ready, db: Session) -> None:
    platform_settings.set_value(db, platform_settings.ACTIVATION_FEE, 0)
    db.flush()
    data = activate(client, ready.owner, ready.event.id).json()
    assert data["paid_sales_active"] is True and data["activation_payment"] is None


def test_only_the_organizer(client: TestClient, ready, db: Session) -> None:
    other = make_user(db, roles=(Role.ORGANIZER,))
    for method, url in [
        ("GET", f"{API}/events/{ready.event.id}/activation"),
        ("POST", f"{API}/events/{ready.event.id}/activate-paid-sales"),
    ]:
        resp = client.request(method, url, json={}, headers=auth_headers(other))
        assert resp.status_code == 403
    admin = make_user(db, roles=(Role.PLATFORM_ADMIN,))
    resp = client.get(f"{API}/events/{ready.event.id}/activation", headers=auth_headers(admin))
    assert resp.status_code == 200


def test_cancelled_event_cannot_be_activated(client: TestClient, ready, db: Session) -> None:
    ready.event.status = "cancelled"
    db.flush()
    resp = activate(client, ready.owner, ready.event.id)
    assert resp.status_code == 409 and error_code(resp) == "event_cancelled"


def test_activation_fee_visible_in_admin(client: TestClient, ready, db: Session) -> None:
    activate(client, ready.owner, ready.event.id)
    admin = make_user(db, roles=(Role.PLATFORM_ADMIN,))
    records = client.get(f"{API}/admin/activation-records", headers=auth_headers(admin)).json()
    mine = [r for r in records["items"] if r["event_id"] == str(ready.event.id)]
    assert len(mine) == 1
    assert mine[0]["purpose"] == PaymentPurpose.ACTIVATION_FEE and mine[0]["is_simulated"] is True


# ------------------------------------------------------------------ concurrency

PARALLEL = 4


@pytest.fixture
def parallel_engine(engine: Engine) -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url, pool_size=PARALLEL + 2, max_overflow=0)
    yield eng
    eng.dispose()


def test_parallel_activation_charges_once(parallel_engine: Engine) -> None:
    with Session(parallel_engine) as s:
        org = ready_organizer(s)
        ev = make_event(s, org, paid_sales_active=False)
        s.commit()
        event_id, owner_id = ev.id, org.user_id

    try:
        barrier = threading.Barrier(PARALLEL)
        results: list[Any] = []
        lock = threading.Lock()

        def run() -> None:
            with Session(parallel_engine) as db:
                owner = db.get(User, owner_id)
                assert owner is not None
                db.connection()
                barrier.wait()
                try:
                    status = activation.activate(
                        db, owner, event_id, simulate="success", gateway=SimulatedGateway()
                    )
                    outcome: Any = status.paid_sales_active
                except AppError as exc:
                    outcome = exc.code
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=run) for _ in range(PARALLEL)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert Counter(map(str, results)) == {
            "True": 1,
            "paid_sales_already_active": PARALLEL - 1,
        }, results
        with Session(parallel_engine) as s:
            fees = s.scalars(select(Payment).where(Payment.event_id == event_id)).all()
            assert [p.status for p in fees] == [PaymentStatus.SUCCEEDED]
            event = s.get(Event, event_id)
            assert event is not None and event.paid_sales_active
    finally:
        cleanup(parallel_engine, event_id, [owner_id])
