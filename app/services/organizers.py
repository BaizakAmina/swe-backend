"""Organizer profile and sandbox payout accounts (SRS §4.1, §4.5). Verification is simulated."""

import re
import uuid
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.models import OrganizerProfile, PayoutAccount, User
from app.models.enums import PayoutAccountStatus, VerificationStatus
from app.schemas.organizers import (
    ActivationChecklist,
    OrganizerProfileCreate,
    OrganizerProfileUpdate,
    PayoutAccountCreate,
    PayoutAccountUpdate,
)
from app.services import audit
from app.services.audit import AuditAction

# Looks like a card number or an IBAN → we never store real bank data.
_BANK_DATA = re.compile(r"^(\d{12,19}|[A-Z]{2}\d{2}[A-Z0-9]{10,30})$", re.IGNORECASE)


def get_profile(db: Session, user: User) -> OrganizerProfile | None:
    return db.scalar(select(OrganizerProfile).where(OrganizerProfile.user_id == user.id))


def require_profile(db: Session, user: User) -> OrganizerProfile:
    profile = get_profile(db, user)
    if profile is None:
        raise NotFoundError("organizer_profile")
    return profile


def _log(db: Session, user: User, action: AuditAction, entity_id: uuid.UUID, text: str) -> None:
    audit.log(
        db,
        actor_id=user.id,
        action=action,
        entity_type="organizer_profile",
        entity_id=entity_id,
        description=text,
    )


def create_profile(db: Session, user: User, data: OrganizerProfileCreate) -> OrganizerProfile:
    if get_profile(db, user) is not None:
        raise ConflictError("organizer_profile_exists", "Organizer profile already exists")
    profile = OrganizerProfile(
        user_id=user.id,
        display_name=data.display_name.strip(),
        contact_email=data.contact_email,
        contact_phone=data.contact_phone,
    )
    db.add(profile)
    db.flush()
    _log(db, user, AuditAction.ORGANIZER_PROFILE_CREATED, profile.id, "Organizer profile created")
    db.commit()
    return profile


def update_profile(db: Session, user: User, data: OrganizerProfileUpdate) -> OrganizerProfile:
    profile = require_profile(db, user)
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(profile, field, value.strip() if isinstance(value, str) else value)
    if changes:
        _log(
            db,
            user,
            AuditAction.ORGANIZER_PROFILE_UPDATED,
            profile.id,
            f"Profile updated: {', '.join(sorted(changes))}",
        )
    db.commit()
    return profile


def _profile_complete(profile: OrganizerProfile) -> bool:
    return bool(profile.display_name and (profile.contact_email or profile.contact_phone))


def verify_profile(db: Session, user: User) -> OrganizerProfile:
    """Simulated verification: completes immediately when the profile is complete."""
    profile = require_profile(db, user)
    if profile.verification_status == VerificationStatus.VERIFIED:
        return profile
    if not _profile_complete(profile):
        raise AppError(
            "profile_incomplete",
            "Add a display name and a contact email or phone before verification",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    profile.verification_status = VerificationStatus.VERIFIED
    _log(db, user, AuditAction.ORGANIZER_VERIFIED, profile.id, "Organizer verified (simulated)")
    db.commit()
    return profile


def list_payout_accounts(db: Session, user: User) -> list[PayoutAccount]:
    profile = require_profile(db, user)
    return list(
        db.scalars(
            select(PayoutAccount)
            .where(PayoutAccount.organizer_id == profile.id)
            .order_by(PayoutAccount.created_at)
        )
    )


def create_payout_account(db: Session, user: User, data: PayoutAccountCreate) -> PayoutAccount:
    profile = require_profile(db, user)
    if _BANK_DATA.match(data.provider_ref):
        raise AppError(
            "bank_data_not_allowed",
            "Use a sandbox reference, never a real card number or IBAN",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    account = PayoutAccount(
        organizer_id=profile.id,
        provider_ref=data.provider_ref,
        status=PayoutAccountStatus.CONNECTED,
    )
    db.add(account)
    db.flush()
    audit.log(
        db,
        actor_id=user.id,
        action=AuditAction.PAYOUT_ACCOUNT_CONNECTED,
        entity_type="payout_account",
        entity_id=account.id,
        description="Sandbox payout account connected",
    )
    db.commit()
    return account


def update_payout_account(
    db: Session, user: User, account_id: uuid.UUID, data: PayoutAccountUpdate
) -> PayoutAccount:
    profile = require_profile(db, user)
    account = db.get(PayoutAccount, account_id)
    if account is None:
        raise NotFoundError("payout_account")
    if account.organizer_id != profile.id:
        raise ForbiddenError("Not your payout account")
    account.status = data.status
    audit.log(
        db,
        actor_id=user.id,
        action=AuditAction.PAYOUT_ACCOUNT_UPDATED,
        entity_type="payout_account",
        entity_id=account.id,
        description=f"Payout account status → {data.status}",
    )
    db.commit()
    return account


def get_activation_checklist(db: Session, profile: OrganizerProfile) -> ActivationChecklist:
    """Paid-sales prerequisites (used by BE-16)."""
    connected = db.scalar(
        select(
            exists().where(
                PayoutAccount.organizer_id == profile.id,
                PayoutAccount.status == PayoutAccountStatus.CONNECTED,
            )
        )
    )
    s = get_settings()
    return ActivationChecklist(
        profile_complete=_profile_complete(profile),
        verified=profile.verification_status == VerificationStatus.VERIFIED,
        payout_account_connected=bool(connected),
        terms_accepted=profile.paid_terms_version == s.paid_terms_version,
        terms_version=s.paid_terms_version,
        terms_url=paid_terms_url(),
    )


def paid_terms_url() -> str:
    s = get_settings()
    return f"{s.public_web_url.rstrip('/')}{s.paid_terms_path}"


def accept_paid_terms(db: Session, user: User, version: str | None) -> ActivationChecklist:
    """Records that the organizer accepted the current paid-event terms (idempotent)."""
    profile = require_profile(db, user)
    current = get_settings().paid_terms_version
    if version is not None and version != current:
        raise AppError(
            "terms_version_outdated",
            "The terms have changed; please review the current version",
            status.HTTP_409_CONFLICT,
            {"current_version": current},
        )
    if profile.paid_terms_version != current:
        profile.paid_terms_version = current
        profile.paid_terms_accepted_at = datetime.now(UTC)
        audit.log(
            db,
            actor_id=user.id,
            action=AuditAction.ORGANIZER_TERMS_ACCEPTED,
            entity_type="organizer_profile",
            entity_id=profile.id,
            description=f"Accepted the paid-event terms (version {current})",
            metadata={"version": current},
        )
        db.commit()
    return get_activation_checklist(db, profile)
