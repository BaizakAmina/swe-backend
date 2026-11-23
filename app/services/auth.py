"""Registration, email verification, login, token rotation, logout and password reset."""

from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError
from app.models import RefreshToken, User, UserRole, UserToken
from app.models.enums import Role, TokenPurpose, UserStatus
from app.schemas.auth import RegisterRequest, TokenPair
from app.services import audit, notifications
from app.services.audit import AuditAction

VERIFY_TOKEN_TTL = timedelta(hours=48)
RESET_TOKEN_TTL = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(UTC)


def _invalid_credentials() -> AppError:
    return AppError(
        "invalid_credentials", "Invalid email or password", status.HTTP_401_UNAUTHORIZED
    )


def _invalid_token(code: str = "invalid_token") -> AppError:
    messages = {"token_expired": "Token has expired", "invalid_token": "Token is invalid"}
    return AppError(code, messages.get(code, "Token is invalid"), status.HTTP_400_BAD_REQUEST)


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def _issue_one_time_token(db: Session, user: User, purpose: TokenPurpose, ttl: timedelta) -> str:
    # Invalidate earlier unused tokens of the same purpose.
    db.execute(
        update(UserToken)
        .where(
            UserToken.user_id == user.id,
            UserToken.purpose == purpose,
            UserToken.used_at.is_(None),
        )
        .values(used_at=_now())
    )
    raw = security.generate_opaque_token()
    db.add(
        UserToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=security.hash_opaque_token(raw),
            expires_at=_now() + ttl,
        )
    )
    return raw


def _consume_one_time_token(db: Session, raw: str, purpose: TokenPurpose) -> User:
    token = db.scalar(
        select(UserToken)
        .where(
            UserToken.token_hash == security.hash_opaque_token(raw), UserToken.purpose == purpose
        )
        .with_for_update()
    )
    if token is None or token.used_at is not None:
        raise _invalid_token()
    if token.expires_at <= _now():
        raise _invalid_token("token_expired")
    token.used_at = _now()
    user = db.get(User, token.user_id)
    if user is None:
        raise _invalid_token()
    return user


def _issue_token_pair(db: Session, user: User) -> TokenPair:
    access = security.create_access_token(user.id, user.token_version)
    refresh = security.create_refresh_token(user.id, user.token_version)
    db.add(RefreshToken(user_id=user.id, jti=refresh.jti, expires_at=refresh.expires_at))
    return TokenPair(
        access_token=access.token,
        refresh_token=refresh.token,
        expires_in=get_settings().access_token_ttl_minutes * 60,
    )


def _ensure_active(user: User) -> None:
    if user.status == UserStatus.SUSPENDED:
        raise AppError("user_suspended", "Account is suspended", status.HTTP_403_FORBIDDEN)


def register(db: Session, data: RegisterRequest) -> User:
    if get_user_by_email(db, data.email) is not None:
        raise ConflictError("email_taken", "An account with this email already exists")

    roles = [Role.ATTENDEE] if data.role == "attendee" else [Role.ATTENDEE, Role.ORGANIZER]
    user = User(
        email=data.email,
        password_hash=security.hash_password(data.password),
        full_name=data.full_name,
        locale=data.locale,
    )
    user.roles = [UserRole(role=r) for r in roles]
    db.add(user)
    db.flush()

    raw = _issue_one_time_token(db, user, TokenPurpose.VERIFY_EMAIL, VERIFY_TOKEN_TTL)
    audit.log(
        db,
        actor_id=user.id,
        action=AuditAction.USER_REGISTERED,
        entity_type="user",
        entity_id=user.id,
        description=f"Registered as {data.role}",
        metadata={"roles": [str(r) for r in roles]},
    )
    db.commit()
    notifications.send_verify_email(user, raw)
    return user


def verify_email(db: Session, raw_token: str) -> User:
    user = _consume_one_time_token(db, raw_token, TokenPurpose.VERIFY_EMAIL)
    if user.email_verified_at is None:
        user.email_verified_at = _now()
        audit.log(
            db,
            actor_id=user.id,
            action=AuditAction.USER_EMAIL_VERIFIED,
            entity_type="user",
            entity_id=user.id,
            description="Email verified",
        )
    db.commit()
    return user


def resend_verification(db: Session, email: str) -> None:
    """Always succeeds silently (no account enumeration)."""
    user = get_user_by_email(db, email)
    if user is None or user.email_verified_at is not None:
        return
    raw = _issue_one_time_token(db, user, TokenPurpose.VERIFY_EMAIL, VERIFY_TOKEN_TTL)
    db.commit()
    notifications.send_verify_email(user, raw)


def authenticate(db: Session, email: str, password: str) -> User:
    user = get_user_by_email(db, email)
    if user is None or not security.verify_password(password, user.password_hash):
        raise _invalid_credentials()
    _ensure_active(user)
    if security.password_needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
    return user


def login(db: Session, email: str, password: str) -> TokenPair:
    user = authenticate(db, email, password)
    pair = _issue_token_pair(db, user)
    db.commit()
    return pair


def refresh(db: Session, raw_refresh: str) -> TokenPair:
    """Rotate: the presented refresh token is revoked and a new pair is issued."""
    try:
        claims = security.decode_token(raw_refresh, "refresh")
    except security.InvalidTokenError as exc:
        raise AppError(str(exc), "Refresh token is invalid", status.HTTP_401_UNAUTHORIZED) from exc

    stored = db.scalar(select(RefreshToken).where(RefreshToken.jti == claims.jti).with_for_update())
    user = db.get(User, claims.user_id)
    if (
        stored is None
        or stored.revoked_at is not None
        or user is None
        or claims.version != user.token_version
    ):
        raise AppError("invalid_token", "Refresh token is invalid", status.HTTP_401_UNAUTHORIZED)
    _ensure_active(user)

    stored.revoked_at = _now()
    pair = _issue_token_pair(db, user)
    db.commit()
    return pair


def logout(db: Session, raw_refresh: str) -> None:
    """Revoke the given refresh token. Idempotent; never reveals whether the token existed."""
    try:
        claims = security.decode_token(raw_refresh, "refresh")
    except security.InvalidTokenError:
        return
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.jti == claims.jti, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    db.commit()


def revoke_all_sessions(db: Session, user: User) -> None:
    """Invalidate every access and refresh token of the user (no commit)."""
    user.token_version += 1
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )


def request_password_reset(db: Session, email: str) -> None:
    """Always succeeds silently (no account enumeration)."""
    user = get_user_by_email(db, email)
    if user is None or user.status == UserStatus.SUSPENDED:
        return
    raw = _issue_one_time_token(db, user, TokenPurpose.PASSWORD_RESET, RESET_TOKEN_TTL)
    db.commit()
    notifications.send_password_reset(user, raw)


def confirm_password_reset(db: Session, raw_token: str, new_password: str) -> None:
    user = _consume_one_time_token(db, raw_token, TokenPurpose.PASSWORD_RESET)
    _ensure_active(user)
    user.password_hash = security.hash_password(new_password)
    # Reset link proves email ownership.
    if user.email_verified_at is None:
        user.email_verified_at = _now()
    revoke_all_sessions(db, user)
    audit.log(
        db,
        actor_id=user.id,
        action=AuditAction.USER_PASSWORD_CHANGED,
        entity_type="user",
        entity_id=user.id,
        description="Password reset via email link",
    )
    db.commit()
