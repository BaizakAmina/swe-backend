"""Shared FastAPI dependencies: database session and the authenticated user."""

from typing import Annotated

from fastapi import Depends, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core import security
from app.core.db import get_db
from app.core.errors import AppError
from app.models import User
from app.models.enums import UserStatus

DbSession = Annotated[Session, Depends(get_db)]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def _unauthorized(code: str = "not_authenticated", message: str = "Not authenticated") -> AppError:
    err = AppError(code, message, status.HTTP_401_UNAUTHORIZED)
    return err


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
