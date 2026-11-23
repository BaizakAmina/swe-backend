"""Password hashing, JWT access/refresh tokens and one-time opaque tokens."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

TokenType = Literal["access", "refresh"]

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True)
class IssuedToken:
    token: str
    jti: str
    expires_at: datetime


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    type: TokenType
    jti: str
    version: int
    expires_at: datetime


class InvalidTokenError(Exception):
    pass


def _encode(
    user_id: uuid.UUID, token_version: int, type_: TokenType, ttl: timedelta
) -> IssuedToken:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + ttl
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": type_,
        "ver": token_version,
        "jti": jti,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return IssuedToken(token=token, jti=jti, expires_at=expires_at)


def create_access_token(user_id: uuid.UUID, token_version: int) -> IssuedToken:
    ttl = timedelta(minutes=get_settings().access_token_ttl_minutes)
    return _encode(user_id, token_version, "access", ttl)


def create_refresh_token(user_id: uuid.UUID, token_version: int) -> IssuedToken:
    ttl = timedelta(days=get_settings().refresh_token_ttl_days)
    return _encode(user_id, token_version, "refresh", ttl)


def decode_token(token: str, expected_type: TokenType) -> TokenClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "type", "jti", "exp", "ver"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("token_expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("invalid_token") from exc

    if payload["type"] != expected_type:
        raise InvalidTokenError("invalid_token")
    try:
        user_id = uuid.UUID(payload["sub"])
    except ValueError as exc:
        raise InvalidTokenError("invalid_token") from exc
    return TokenClaims(
        user_id=user_id,
        type=payload["type"],
        jti=payload["jti"],
        version=int(payload["ver"]),
        expires_at=datetime.fromtimestamp(payload["exp"], UTC),
    )


def generate_opaque_token() -> str:
    """URL-safe random token for email verification / password reset links."""
    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
