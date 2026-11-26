"""Admin-editable platform settings, with defaults from env (``Settings``).

Each key below can be changed at runtime from ``PATCH /admin/settings``; until then the env value
applies. Services read them through the typed helpers so a change takes effect immediately.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import PlatformSetting

ACTIVATION_FEE = "activation_fee_minor"
RESERVATION_TTL = "reservation_ttl_minutes"
SEAT_HOLD_TTL = "seat_hold_ttl_minutes"
MAX_SEAT_HOLDS = "max_seat_holds_per_user"
PAID_TERMS_VERSION = "paid_terms_version"

# every editable key; the default is the env setting of the same name
KEYS = (ACTIVATION_FEE, RESERVATION_TTL, SEAT_HOLD_TTL, MAX_SEAT_HOLDS, PAID_TERMS_VERSION)


def get_value(db: Session, key: str, default: Any) -> Any:
    row = db.get(PlatformSetting, key)
    return default if row is None else row.value


def set_value(db: Session, key: str, value: Any) -> None:
    row = db.get(PlatformSetting, key)
    if row is None:
        db.add(PlatformSetting(key=key, value=value))
    else:
        row.value = value


def current(db: Session, key: str) -> Any:
    """Stored value of ``key`` or its env default."""
    return get_value(db, key, getattr(get_settings(), key))


def activation_fee_minor(db: Session) -> int:
    """Paid-sales activation fee in KZT minor units (used by BE-16)."""
    return int(current(db, ACTIVATION_FEE))


def reservation_ttl_minutes(db: Session) -> int:
    """How long a pending order keeps its tickets."""
    return int(current(db, RESERVATION_TTL))


def seat_hold_ttl_minutes(db: Session) -> int:
    return int(current(db, SEAT_HOLD_TTL))


def max_seat_holds_per_user(db: Session) -> int:
    return int(current(db, MAX_SEAT_HOLDS))


def paid_terms_version(db: Session) -> str:
    """Current paid-event terms; organizers accepted an older one must accept again."""
    return str(current(db, PAID_TERMS_VERSION))
