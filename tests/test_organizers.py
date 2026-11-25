import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditLog
from app.models.enums import Role
from tests.factories import auth_headers, make_user

pytestmark = pytest.mark.db

BASE = "/api/v1/organizers/me"


@pytest.fixture
def org_headers(db: Session) -> dict[str, str]:
    return auth_headers(make_user(db, roles=(Role.ATTENDEE, Role.ORGANIZER)))


def test_profile_crud_and_checklist(client: TestClient, org_headers: dict[str, str], db: Session):
    assert client.get(BASE, headers=org_headers).status_code == 404

    resp = client.post(BASE, json={"display_name": "Aru Events"}, headers=org_headers)
    assert resp.status_code == 201
    assert resp.json()["verification_status"] == "unverified"
    assert client.post(BASE, json={"display_name": "Again"}, headers=org_headers).status_code == 409

    checklist = client.get(f"{BASE}/activation-checklist", headers=org_headers).json()
    assert checklist == {
        "profile_complete": False,
        "verified": False,
        "payout_account_connected": False,
        "terms_accepted": False,
        "terms_version": get_settings().paid_terms_version,
        "terms_url": "http://localhost:3000/terms/paid-events",
    }

    # verification needs contact details
    resp = client.post(f"{BASE}/verify", headers=org_headers)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "profile_incomplete"

    resp = client.patch(BASE, json={"contact_email": "hi@aru.kz"}, headers=org_headers)
    assert resp.status_code == 200
    resp = client.post(f"{BASE}/verify", headers=org_headers)
    assert resp.json()["verification_status"] == "verified"

    resp = client.post(
        f"{BASE}/payout-accounts", json={"provider_ref": "sandbox_acc_1"}, headers=org_headers
    )
    assert resp.status_code == 201 and resp.json()["status"] == "connected"

    checklist = client.get(f"{BASE}/activation-checklist", headers=org_headers).json()
    assert checklist["terms_accepted"] is False
    resp = client.post(f"{BASE}/paid-terms/accept", headers=org_headers)
    assert resp.status_code == 200 and resp.json()["terms_accepted"] is True

    checklist = client.get(f"{BASE}/activation-checklist", headers=org_headers).json()
    assert all(checklist.values())

    actions = set(db.scalars(select(AuditLog.action_type)))
    assert {
        "organizer.profile_created",
        "organizer.profile_updated",
        "organizer.verified",
        "payout_account.connected",
    } <= actions


@pytest.mark.parametrize("ref", ["4111111111111111", "KZ86125KZT5004100100"])
def test_real_bank_data_rejected(client: TestClient, org_headers: dict[str, str], ref: str):
    client.post(BASE, json={"display_name": "Org"}, headers=org_headers)
    resp = client.post(f"{BASE}/payout-accounts", json={"provider_ref": ref}, headers=org_headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "bank_data_not_allowed"


def test_attendee_cannot_create_profile(client: TestClient, db: Session):
    headers = auth_headers(make_user(db))
    resp = client.post(BASE, json={"display_name": "Nope"}, headers=headers)
    assert resp.status_code == 403


def test_cannot_touch_someone_elses_payout_account(client: TestClient, db: Session):
    a = auth_headers(make_user(db, roles=(Role.ORGANIZER,)))
    b = auth_headers(make_user(db, roles=(Role.ORGANIZER,)))
    for h in (a, b):
        client.post(BASE, json={"display_name": "Org"}, headers=h)
    acc = client.post(
        f"{BASE}/payout-accounts", json={"provider_ref": "sandbox_a"}, headers=a
    ).json()

    resp = client.patch(
        f"/api/v1/payout-accounts/{acc['id']}", json={"status": "disabled"}, headers=b
    )
    assert resp.status_code == 403
    assert client.get(f"{BASE}/payout-accounts", headers=b).json() == []

    resp = client.patch(
        f"/api/v1/payout-accounts/{acc['id']}", json={"status": "disabled"}, headers=a
    )
    assert resp.status_code == 200 and resp.json()["status"] == "disabled"
