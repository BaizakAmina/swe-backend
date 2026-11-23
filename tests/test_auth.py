from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import security
from app.models import AuditLog, User, UserToken
from app.models.enums import UserStatus

pytestmark = pytest.mark.db

PASSWORD = "correct-horse-battery"


def register(client: TestClient, email: str = "ayana@example.com", **kw: object) -> dict:
    body = {"email": email, "password": PASSWORD, "full_name": "Ayana", **kw}
    resp = client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def login(client: TestClient, email: str = "ayana@example.com", password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_full_cycle_register_verify_login_refresh_logout(
    client: TestClient, outbox: list[dict], db: Session
) -> None:
    user = register(client, role="organizer")
    assert user["email_verified"] is False
    assert set(user["roles"]) == {"attendee", "organizer"}
    assert "password" not in str(user)

    # verify email with the token from the "sent" email
    verify = [m for m in outbox if m["kind"] == "verify_email"][-1]
    resp = client.post("/api/v1/auth/verify-email", json={"token": verify["token"]})
    assert resp.status_code == 200 and resp.json()["email_verified"] is True

    # token is single-use
    resp = client.post("/api/v1/auth/verify-email", json={"token": verify["token"]})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_token"

    # login → /me
    pair = login(client).json()
    me = client.get("/api/v1/me", headers=bearer(pair["access_token"]))
    assert me.status_code == 200 and me.json()["email"] == "ayana@example.com"

    # refresh rotates: new pair works, old refresh is rejected
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert resp.status_code == 200
    new_pair = resp.json()
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert resp.status_code == 401

    # logout revokes the refresh token
    resp = client.post("/api/v1/auth/logout", json={"refresh_token": new_pair["refresh_token"]})
    assert resp.status_code == 204
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": new_pair["refresh_token"]})
    assert resp.status_code == 401

    # audit trail
    actions = set(db.scalars(select(AuditLog.action_type)).all())
    assert {"user.registered", "user.email_verified"} <= actions


def test_email_taken_is_case_insensitive(client: TestClient) -> None:
    register(client, email="Ayana@Example.com")
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "ayana@example.com", "password": PASSWORD, "full_name": "Other"},
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "email_taken"


def test_cannot_self_register_as_platform_admin(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "x@example.com",
            "password": PASSWORD,
            "full_name": "X",
            "role": "platform_admin",
        },
    )
    assert resp.status_code == 422


def test_short_password_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com", "password": "short", "full_name": "X"},
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "validation_error"


def test_wrong_password_and_unknown_email_look_the_same(client: TestClient) -> None:
    register(client)
    a = login(client, password="wrong-password")
    b = login(client, email="nobody@example.com")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_suspended_user_cannot_login_and_tokens_stop_working(
    client: TestClient, db: Session
) -> None:
    register(client)
    pair = login(client).json()
    db.execute(update(User).values(status=UserStatus.SUSPENDED))
    db.flush()

    assert login(client).json()["error"]["code"] == "user_suspended"
    me = client.get("/api/v1/me", headers=bearer(pair["access_token"]))
    assert me.status_code == 403
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert resp.status_code == 403


def test_password_reset_flow_revokes_sessions(client: TestClient, outbox: list[dict]) -> None:
    register(client)
    old = login(client).json()

    resp = client.post("/api/v1/auth/password-reset/request", json={"email": "ayana@example.com"})
    assert resp.status_code == 202
    token = [m for m in outbox if m["kind"] == "password_reset"][-1]["token"]

    resp = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "a-brand-new-password"},
    )
    assert resp.status_code == 204

    assert login(client).status_code == 401
    assert login(client, password="a-brand-new-password").status_code == 200
    # every previously issued token is dead
    assert client.get("/api/v1/me", headers=bearer(old["access_token"])).status_code == 401
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]})
    assert resp.status_code == 401


def test_password_reset_for_unknown_email_is_silent(client: TestClient, outbox: list[dict]) -> None:
    resp = client.post("/api/v1/auth/password-reset/request", json={"email": "ghost@example.com"})
    assert resp.status_code == 202
    assert outbox == []


def test_expired_reset_token(client: TestClient, outbox: list[dict], db: Session) -> None:
    register(client)
    client.post("/api/v1/auth/password-reset/request", json={"email": "ayana@example.com"})
    token = [m for m in outbox if m["kind"] == "password_reset"][-1]["token"]
    db.execute(
        update(UserToken)
        .where(UserToken.token_hash == security.hash_opaque_token(token))
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    db.flush()
    resp = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "whatever-123"}
    )
    assert resp.json()["error"]["code"] == "token_expired"


def test_access_token_cannot_be_used_as_refresh(client: TestClient) -> None:
    register(client)
    pair = login(client).json()
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["access_token"]})
    assert resp.status_code == 401
    me = client.get("/api/v1/me", headers=bearer(pair["refresh_token"]))
    assert me.status_code == 401


def test_me_requires_auth_and_can_be_updated(client: TestClient) -> None:
    assert client.get("/api/v1/me").status_code == 401
    register(client)
    token = login(client).json()["access_token"]
    resp = client.patch("/api/v1/me", json={"locale": "kk"}, headers=bearer(token))
    assert resp.status_code == 200 and resp.json()["locale"] == "kk"


def test_docs_oauth_token_endpoint(client: TestClient) -> None:
    register(client)
    resp = client.post(
        "/api/v1/auth/token", data={"username": "ayana@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 200 and resp.json()["token_type"] == "bearer"
