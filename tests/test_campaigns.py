import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, PromotionalCampaign, User
from app.models.enums import EventStatus
from app.services import campaigns as svc
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture
def setup(db: Session):
    org = make_organizer(db)
    event = make_event(db, org)
    standard = make_ticket_type(db, event, name="Standard")
    owner = db.get(User, org.user_id)
    assert owner
    return event, standard, auth_headers(owner)


def create(client: TestClient, event, h, **kw):
    body = {"name": "Launch", "discount_type": "percent", "discount_value": 10, **kw}
    return client.post(f"{API}/events/{event.id}/campaigns", json=body, headers=h)


def test_create_with_generated_code_and_qr_link(client: TestClient, setup, db: Session) -> None:
    event, standard, h = setup
    resp = create(client, event, h, ticket_type_ids=[str(standard.id)], max_redemptions=50)
    assert resp.status_code == 201
    c = resp.json()
    assert len(c["code"]) == svc.CODE_LENGTH and c["code"].isalnum()
    assert not set(c["code"]) & set("0O1IL")
    assert c["remaining"] == 50 and c["ticket_type_ids"] == [str(standard.id)]
    assert c["qr_link"].startswith("http") and f"/events/{event.id}?c=C1." in c["qr_link"]
    assert "10" not in c["qr_link"].split("?c=")[1][:3]  # token is opaque, not the discount

    token = c["qr_link"].split("?c=")[1]
    resolved = client.get(f"{API}/campaigns/resolve", params={"token": token}).json()
    assert resolved == {"event_id": str(event.id), "code": c["code"]}

    assert "campaign.created" in db.scalars(select(AuditLog.action_type)).all()


def test_custom_code_uniqueness_and_validation(client: TestClient, setup, db: Session) -> None:
    event, _, h = setup
    assert create(client, event, h, code="SPRING25").status_code == 201
    resp = create(client, event, h, code="SPRING25")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "promo_code_taken"
    assert create(client, event, h, code="bad code").status_code == 422
    assert create(client, event, h, discount_value=150).status_code == 422
    assert create(client, event, h, discount_type="fixed", discount_value=0).status_code == 422

    foreign = make_ticket_type(db, make_event(db))
    resp = create(client, event, h, ticket_type_ids=[str(foreign.id)])
    assert resp.json()["error"]["code"] == "ticket_type_not_in_event"


def test_update_disable_enable(client: TestClient, setup, db: Session) -> None:
    event, _, h = setup
    c = create(client, event, h, max_redemptions=10).json()
    campaign = db.get(PromotionalCampaign, c["id"])
    assert campaign
    campaign.redemption_count = 4
    db.flush()

    resp = client.patch(f"{API}/campaigns/{c['id']}", json={"max_redemptions": 3}, headers=h)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "limit_below_redemptions"
    resp = client.patch(f"{API}/campaigns/{c['id']}", json={"max_redemptions": 20}, headers=h)
    assert resp.json()["remaining"] == 16

    assert (
        client.post(f"{API}/campaigns/{c['id']}/disable", headers=h).json()["status"] == "disabled"
    )
    assert client.post(f"{API}/campaigns/{c['id']}/enable", headers=h).json()["status"] == "active"
    assert len(client.get(f"{API}/events/{event.id}/campaigns", headers=h).json()) == 1


def test_qr_png_and_access(client: TestClient, setup, db: Session) -> None:
    event, _, h = setup
    c = create(client, event, h).json()
    resp = client.get(f"{API}/campaigns/{c['id']}/qr.png", headers=h)
    assert resp.status_code == 200 and resp.content.startswith(b"\x89PNG")

    other = auth_headers(make_user(db))
    assert client.get(f"{API}/campaigns/{c['id']}", headers=other).status_code == 403
    assert client.get(f"{API}/campaigns/{c['id']}/qr.png", headers=other).status_code == 403
    assert create(client, event, other).status_code == 403


def test_resolve_hides_unpublished_and_unknown(client: TestClient, setup, db: Session) -> None:
    event, _, h = setup
    token = create(client, event, h).json()["qr_link"].split("?c=")[1]
    event.status = EventStatus.DRAFT
    db.flush()
    assert client.get(f"{API}/campaigns/resolve", params={"token": token}).status_code == 404
    assert client.get(f"{API}/campaigns/resolve", params={"token": "C1.nope"}).status_code == 404


def test_campaign_qr_is_distinguishable_from_ticket_qr() -> None:
    token = svc.generate_qr_token()
    assert svc.is_campaign_qr(token)
    assert svc.is_campaign_qr(f"https://biletflow.kz/events/x?c={token}")
    assert not svc.is_campaign_qr("T1.abc.def")
