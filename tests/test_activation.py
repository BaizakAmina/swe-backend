"""BE-16: paid-sales activation — checklist + simulated activation fee."""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditLog, OrganizerProfile, Payment, PayoutAccount, User
from app.models.enums import PaymentPurpose, PaymentStatus, Role, VerificationStatus
from app.services import platform_settings
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user
from tests.test_orders import error_code, order_body, post_order

pytestmark = pytest.mark.db

API = "/api/v1"


def ready_organizer(db: Session) -> OrganizerProfile:
    """Profile complete, verified, payout account connected, terms accepted."""
    org = make_organizer(
        db,
        contact_email="org@example.com",
        verification_status=VerificationStatus.VERIFIED,
        paid_terms_version=get_settings().paid_terms_version,
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
        "terms_accepted": True,
        "terms_version": get_settings().paid_terms_version,
        "terms_url": "http://localhost:3000/terms/paid-events",
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
        "terms_accepted",
    ]
    assert "payout account" in error["message"] and "terms" in error["message"]

    # a verified, complete profile still needs the payout account
    org.contact_email = "org@example.com"
    org.verification_status = VerificationStatus.VERIFIED
    db.flush()
    resp = activate(client, owner, ev.id)
    assert resp.json()["error"]["details"]["missing"] == [
        "payout_account_connected",
        "terms_accepted",
    ]

    # … and the paid-event terms
    db.add(PayoutAccount(organizer_id=org.id, provider_ref="sandbox_acct_9"))
    db.flush()
    resp = activate(client, owner, ev.id)
    assert resp.json()["error"]["details"]["missing"] == ["terms_accepted"]

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


# ------------------------------------------------------------------ paid-event terms


def test_accept_terms_completes_checklist_and_unlocks_activation(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = ready_organizer(db)
    org.paid_terms_version = None
    db.flush()
    owner = db.get(User, org.user_id)
    assert owner
    ev = make_event(db, org, paid_sales_active=False)
    h = auth_headers(owner)

    status = client.get(f"{API}/events/{ev.id}/activation", headers=h).json()
    assert status["terms_accepted"] is False
    assert status["terms_url"].endswith("/terms/paid-events")
    assert activate(client, owner, ev.id).json()["error"]["details"]["missing"] == [
        "terms_accepted"
    ]

    version = get_settings().paid_terms_version
    stale = client.post(
        f"{API}/organizers/me/paid-terms/accept", json={"version": "1999-01"}, headers=h
    )
    assert stale.status_code == 409 and error_code(stale) == "terms_version_outdated"
    assert stale.json()["error"]["details"] == {"current_version": version}

    for _ in range(2):  # idempotent
        resp = client.post(
            f"{API}/organizers/me/paid-terms/accept", json={"version": version}, headers=h
        )
        assert resp.status_code == 200 and resp.json()["terms_accepted"] is True
    db.refresh(org)
    assert org.paid_terms_version == version and org.paid_terms_accepted_at is not None
    logs = db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == org.id, AuditLog.action_type == "organizer.terms_accepted"
        )
    ).all()
    assert len(logs) == 1 and logs[0].metadata_ == {"version": version}

    assert activate(client, owner, ev.id).status_code == 200

    # new terms version → must accept again before the next activation
    monkeypatch.setattr(get_settings(), "paid_terms_version", "2027-01")
    other = make_event(db, org, paid_sales_active=False)
    assert (
        client.get(f"{API}/organizers/me/activation-checklist", headers=h).json()["terms_accepted"]
        is False
    )
    assert error_code(activate(client, owner, other.id)) == "activation_checklist_incomplete"
    assert client.post(f"{API}/organizers/me/paid-terms/accept", headers=h).status_code == 200
    assert activate(client, owner, other.id).status_code == 200


def test_accept_terms_needs_organizer_profile(client: TestClient, db: Session) -> None:
    user = make_user(db, roles=(Role.ORGANIZER,))
    resp = client.post(f"{API}/organizers/me/paid-terms/accept", headers=auth_headers(user))
    assert resp.status_code == 404
    attendee = make_user(db)
    resp = client.post(f"{API}/organizers/me/paid-terms/accept", headers=auth_headers(attendee))
    assert resp.status_code == 403
