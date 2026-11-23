"""Shared FastAPI dependencies: database session, authenticated user, RBAC.

Usage in routers — never check roles by hand::

    @router.post("/events/{event_id}/publish")
    def publish(event: ManagedEvent, user: CurrentUser, db: DbSession): ...

    @router.post("/checkin")
    def check_in(..., user: Annotated[User, Depends(require_role(Role.ORGANIZER))]): ...

- ``ManagedEvent``  — organizer of ``{event_id}`` or platform admin (else 403, unknown → 404)
- ``StaffedEvent``  — assigned event admin, organizer or platform admin
- ``require_role(*roles)`` — user has at least one of the global roles
- ``VerifiedUser``  — authenticated user with a confirmed email
- ``PlatformAdmin`` — platform admin only
"""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core import security
from app.core.db import get_db
from app.core.errors import AppError, ForbiddenError
from app.models import Event, User
from app.models.enums import Role, UserStatus
from app.services import permissions

DbSession = Annotated[Session, Depends(get_db)]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def _unauthorized(code: str = "not_authenticated", message: str = "Not authenticated") -> AppError:
    return AppError(code, message, status.HTTP_401_UNAUTHORIZED)


def get_current_user_optional(
    db: DbSession, token: Annotated[str | None, Depends(oauth2_scheme)]
) -> User | None:
    if not token:
        return None
    try:
        claims = security.decode_token(token, "access")
    except security.InvalidTokenError as exc:
        raise _unauthorized(str(exc), "Access token is invalid or expired") from exc

    user = db.get(User, claims.user_id)
    if user is None or claims.version != user.token_version:
        raise _unauthorized("invalid_token", "Access token is no longer valid")
    if user.status == UserStatus.SUSPENDED:
        raise AppError("user_suspended", "Account is suspended", status.HTTP_403_FORBIDDEN)
    return user


def get_current_user(user: Annotated[User | None, Depends(get_current_user_optional)]) -> User:
    if user is None:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]


def require_role(*roles: Role) -> Callable[[User], User]:
    """Dependency factory: user holds one of ``roles`` (platform admin always passes)."""
    allowed = set(roles)

    def _dep(user: CurrentUser) -> User:
        if user.role_names & allowed or permissions.is_platform_admin(user):
            return user
        raise ForbiddenError(f"Requires role: {', '.join(sorted(allowed))}")

    return _dep


def require_verified_email(user: CurrentUser) -> User:
    if user.email_verified_at is None:
        raise AppError(
            "email_not_verified", "Please verify your email first", status.HTTP_403_FORBIDDEN
        )
    return user


def require_platform_admin(user: CurrentUser) -> User:
    if not permissions.is_platform_admin(user):
        raise ForbiddenError("Platform admin only")
    return user


VerifiedUser = Annotated[User, Depends(require_verified_email)]
PlatformAdmin = Annotated[User, Depends(require_platform_admin)]
OrganizerUser = Annotated[User, Depends(require_role(Role.ORGANIZER))]


def require_event_organizer(event_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Event:
    """Path param ``event_id``: current user organizes the event (or is platform admin)."""
    event = permissions.get_event(db, event_id)
    return permissions.ensure_can_manage_event(db, user, event)


def require_event_admin(event_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Event:
    """Path param ``event_id``: assigned event admin, the organizer, or platform admin."""
    event = permissions.get_event(db, event_id)
    return permissions.ensure_can_staff_event(db, user, event)


ManagedEvent = Annotated[Event, Depends(require_event_organizer)]
StaffedEvent = Annotated[Event, Depends(require_event_admin)]
