import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TZ, TimestampMixin, UUIDPkMixin, str_enum
from app.models.enums import PayoutAccountStatus, StaffRole, VerificationStatus
from app.models.user import User


class OrganizerProfile(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "organizer_profile"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    # Verification is simulated in the MVP.
    verification_status: Mapped[VerificationStatus] = mapped_column(
        str_enum(VerificationStatus, "verification_status"),
        nullable=False,
        default=VerificationStatus.UNVERIFIED,
        server_default="unverified",
    )
    # Paid-event terms (SRS §3.2): which version the organizer accepted, and when.
    paid_terms_version: Mapped[str | None] = mapped_column(String(32))
    paid_terms_accepted_at: Mapped[datetime | None] = mapped_column(TZ)

    user: Mapped[User] = relationship()
    payout_accounts: Mapped[list["PayoutAccount"]] = relationship(back_populates="organizer")


class PayoutAccount(UUIDPkMixin, TimestampMixin, Base):
    """Sandbox payout account. No real bank data is ever stored."""

    __tablename__ = "payout_account"

    organizer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizer_profile.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KZT")
    status: Mapped[PayoutAccountStatus] = mapped_column(
        str_enum(PayoutAccountStatus, "payout_account_status"),
        nullable=False,
        default=PayoutAccountStatus.CONNECTED,
    )

    organizer: Mapped[OrganizerProfile] = relationship(back_populates="payout_accounts")


class StaffAssignment(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "staff_assignment"
    __table_args__ = (UniqueConstraint("event_id", "user_id", "role"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[StaffRole] = mapped_column(
        str_enum(StaffRole, "staff_role"), nullable=False, default=StaffRole.EVENT_ADMIN
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))

    user: Mapped[User] = relationship(foreign_keys=[user_id])
