from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentUser, DbSession
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    ResendVerificationRequest,
    TokenPair,
    UserRead,
    UserUpdate,
    VerifyEmailRequest,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(tags=["me"])


def to_user_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        locale=user.locale,
        status=user.status,
        email_verified=user.email_verified_at is not None,
        roles=sorted(user.role_names),
        created_at=user.created_at,
    )


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(data: RegisterRequest, db: DbSession) -> UserRead:
    """Create an account and send an email-verification link."""
    return to_user_read(auth_service.register(db, data))


@router.post("/verify-email", response_model=UserRead)
def verify_email(data: VerifyEmailRequest, db: DbSession) -> UserRead:
    return to_user_read(auth_service.verify_email(db, data.token))


@router.post(
    "/verify-email/resend", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED
)
def resend_verification(data: ResendVerificationRequest, db: DbSession) -> MessageResponse:
    auth_service.resend_verification(db, data.email)
    return MessageResponse(message="If the account exists and is unverified, an email was sent")


@router.post("/login", response_model=TokenPair)
def login(data: LoginRequest, db: DbSession) -> TokenPair:
    return auth_service.login(db, data.email, data.password)


@router.post("/token", response_model=TokenPair, include_in_schema=False)
def token(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession) -> TokenPair:
    """OAuth2 password flow — used by the "Authorize" button in /docs."""
    return auth_service.login(db, form.username, form.password)


@router.post("/refresh", response_model=TokenPair)
def refresh(data: RefreshRequest, db: DbSession) -> TokenPair:
    return auth_service.refresh(db, data.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(data: RefreshRequest, db: DbSession) -> None:
    auth_service.logout(db, data.refresh_token)


@router.post(
    "/password-reset/request",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def password_reset_request(data: PasswordResetRequest, db: DbSession) -> MessageResponse:
    auth_service.request_password_reset(db, data.email)
    return MessageResponse(message="If the account exists, a reset link was sent")


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(data: PasswordResetConfirm, db: DbSession) -> None:
    auth_service.confirm_password_reset(db, data.token, data.new_password)


@me_router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> UserRead:
    return to_user_read(user)


@me_router.patch("/me", response_model=UserRead)
def update_me(data: UserUpdate, user: CurrentUser, db: DbSession) -> UserRead:
    if data.full_name is not None:
        user.full_name = data.full_name.strip()
    if data.locale is not None:
        user.locale = data.locale
    db.commit()
    return to_user_read(user)
