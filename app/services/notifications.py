"""Notifications facade. Other services call these functions; delivery is implemented in BE-19.

Until then messages are only logged, so the rest of the system can already depend on the
interface.
"""

import logging

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Event, User

logger = logging.getLogger("biletflow.notifications")


def send_verify_email(user: User, token: str) -> None:
    link = f"{get_settings().public_web_url}/verify-email?token={token}"
    logger.info("verify_email to=%s link=%s", user.email, link)


def send_password_reset(user: User, token: str) -> None:
    link = f"{get_settings().public_web_url}/reset-password?token={token}"
    logger.info("password_reset to=%s link=%s", user.email, link)


def send_event_cancelled(db: Session, event: Event) -> None:
    """Notify holders of valid tickets that the event was cancelled (delivery in BE-19)."""
    logger.info("event_cancelled event=%s", event.id)


def send_staff_assigned(user: User, event: Event) -> None:
    logger.info("staff_assigned to=%s event=%s", user.email, event.id)
