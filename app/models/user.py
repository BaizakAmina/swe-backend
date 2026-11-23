import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TZ, TimestampMixin, UUIDPkMixin, str_enum
from app.models.enums import Locale, Role, TokenPurpose, UserStatus


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "user_account"  # "user" is reserved in PostgreSQL

    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    locale: Mapped[Locale] = mapped_column(
        str_enum(Locale, "locale"), nullable=False, default=Locale.RU, server_default="ru"
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(TZ)
    status: Mapped[UserStatus] = mapped_column(
        str_enum(UserStatus, "user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default="active",
    )
    suspended_reason: Mapped[str | None] = mapped_column(String(500))
    # Bumped on logout-all / suspension / password change → invalidates issued refresh tokens.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def role_names(self) -> set[Role]:
        return {r.role for r in self.roles}


class UserRole(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "user_role"
    __table_args__ = (UniqueConstraint("user_id", "role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = mapped_column(str_enum(Role, "role"), nullable=False)

    user: Mapped[User] = relationship(back_populates="roles")


class UserToken(UUIDPkMixin, TimestampMixin, Base):
    """One-time tokens for email verification and password reset (only the hash is stored)."""

    __tablename__ = "user_token"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[TokenPurpose] = mapped_column(
        str_enum(TokenPurpose, "token_purpose"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZ)


class RefreshToken(UUIDPkMixin, TimestampMixin, Base):
    """Issued refresh tokens by `jti`; revoked on logout / rotation."""

    __tablename__ = "refresh_token"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
