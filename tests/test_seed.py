import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Event, PromoCode, User
from app.seed import DEMO_PASSWORD, seed

pytestmark = pytest.mark.db


def test_seed_is_idempotent(db: Session) -> None:
    first = seed(db)
    assert first["events_created"] == 2
    users_before = db.scalar(select(func.count()).select_from(User))

    second = seed(db)
    assert second["events_created"] == 0
    assert db.scalar(select(func.count()).select_from(User)) == users_before
    assert db.scalar(select(func.count()).select_from(Event)) == 2
    assert db.scalar(select(PromoCode.code)) == "DEMO10"


def test_seeded_users_can_log_in(db: Session, client: TestClient) -> None:
    seed(db)
    resp = client.post(
        "/api/v1/auth/login", json={"email": "organizer@biletflow.kz", "password": DEMO_PASSWORD}
    )
    assert resp.status_code == 200
    me = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"}
    )
    assert set(me.json()["roles"]) == {"attendee", "organizer"}
