"""Admin-editable platform settings, with defaults from env (``Settings``)."""

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import PlatformSetting

ACTIVATION_FEE = "activation_fee_minor"


def get_value(db: Session, key: str, default: Any) -> Any:
    row = db.get(PlatformSetting, key)
    return default if row is None else row.value


def set_value(db: Session, key: str, value: Any) -> None:
    row = db.get(PlatformSetting, key)
    if row is None:
        db.add(PlatformSetting(key=key, value=value))
    else:
        row.value = value


def activation_fee_minor(db: Session) -> int:
    """Paid-sales activation fee in KZT minor units (used by BE-16)."""
    return int(get_value(db, ACTIVATION_FEE, get_settings().activation_fee_minor))
