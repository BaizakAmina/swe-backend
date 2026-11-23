from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    ManagedEvent,
    PlatformAdmin,
    StaffedEvent,
    VerifiedUser,
    require_role,
)
from app.core.db import get_db
from app.main import create_app
from app.models import StaffAssignment, User
from app.models.enums import Role
from tests.factories import auth_headers, make_event, make_organizer, make_user

pytestmark = pytest.mark.db

router = APIRouter(prefix="/t")


@router.get("/events/{event_id}/manage")
def manage(event: ManagedEvent) -> dict[str, str]:
    return {"event": str(event.id)}


@router.get("/events/{event_id}/staff")
def staff(event: StaffedEvent) -> dict[str, str]:
    return {"event": str(event.id)}


@router.get("/organizer-only")
def organizer_only(user: Annotated[User, Depends(require_role(Role.ORGANIZER))]) -> dict[str, str]:
    return {"user": str(user.id)}


@router.get("/admin-only")
def admin_only(user: PlatformAdmin) -> dict[str, str]:
    return {"user": str(user.id)}


@router.get("/verified-only")
def verified_only(user: VerifiedUser) -> dict[str, str]:
    return {"user": str(user.id)}


@router.get("/any")
def any_user(user: CurrentUser) -> dict[str, str]:
    return {"user": str(user.id)}


@pytest.fixture
def rbac_client(db: Session) -> Iterator[TestClient]:
    app = create_app()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c


def test_event_organizer_vs_other_organizer(rbac_client: TestClient, db: Session) -> None:
    owner = make_organizer(db)
    other = make_organizer(db)
    event = make_event(db, owner)

    url = f"/t/events/{event.id}/manage"
    owner_user = db.get(User, owner.user_id)
    other_user = db.get(User, other.user_id)
    assert owner_user and other_user
    assert rbac_client.get(url, headers=auth_headers(owner_user)).status_code == 200
    resp = rbac_client.get(url, headers=auth_headers(other_user))
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "forbidden"


def test_unknown_event_is_404(rbac_client: TestClient, db: Session) -> None:
    user = make_user(db, roles=(Role.ORGANIZER,))
    resp = rbac_client.get(
        "/t/events/00000000-0000-0000-0000-000000000000/manage", headers=auth_headers(user)
    )
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "event_not_found"


def test_event_admin_needs_assignment(rbac_client: TestClient, db: Session) -> None:
    event = make_event(db)
    staffer = make_user(db)
    url = f"/t/events/{event.id}/staff"

    assert rbac_client.get(url, headers=auth_headers(staffer)).status_code == 403

    db.add(StaffAssignment(event_id=event.id, user_id=staffer.id))
    db.flush()
    assert rbac_client.get(url, headers=auth_headers(staffer)).status_code == 200
    # staff can scan but not manage
    resp = rbac_client.get(f"/t/events/{event.id}/manage", headers=auth_headers(staffer))
    assert resp.status_code == 403


def test_organizer_can_also_staff_own_event(rbac_client: TestClient, db: Session) -> None:
    owner = make_organizer(db)
    event = make_event(db, owner)
    user = db.get(User, owner.user_id)
    assert user
    assert (
        rbac_client.get(f"/t/events/{event.id}/staff", headers=auth_headers(user)).status_code
        == 200
    )


def test_platform_admin_passes_everywhere(rbac_client: TestClient, db: Session) -> None:
    admin = make_user(db, roles=(Role.PLATFORM_ADMIN,))
    event = make_event(db)
    h = auth_headers(admin)
    assert rbac_client.get(f"/t/events/{event.id}/manage", headers=h).status_code == 200
    assert rbac_client.get(f"/t/events/{event.id}/staff", headers=h).status_code == 200
    assert rbac_client.get("/t/organizer-only", headers=h).status_code == 200
    assert rbac_client.get("/t/admin-only", headers=h).status_code == 200


def test_require_role(rbac_client: TestClient, db: Session) -> None:
    attendee = make_user(db)
    organizer = make_user(db, roles=(Role.ATTENDEE, Role.ORGANIZER))
    assert rbac_client.get("/t/organizer-only", headers=auth_headers(attendee)).status_code == 403
    assert rbac_client.get("/t/organizer-only", headers=auth_headers(organizer)).status_code == 200
    assert rbac_client.get("/t/admin-only", headers=auth_headers(organizer)).status_code == 403


def test_require_verified_email(rbac_client: TestClient, db: Session) -> None:
    unverified = make_user(db, email_verified_at=None)
    resp = rbac_client.get("/t/verified-only", headers=auth_headers(unverified))
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "email_not_verified"


def test_anonymous_gets_401(rbac_client: TestClient, db: Session) -> None:
    event = make_event(db)
    assert rbac_client.get("/t/any").status_code == 401
    assert rbac_client.get(f"/t/events/{event.id}/manage").status_code == 401
