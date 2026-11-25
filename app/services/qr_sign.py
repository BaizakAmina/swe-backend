"""Admission-ticket QR payload: signed token binding ``ticket_code`` + ``event_id`` (SRS §4.7).

Format (BACKEND_ARCHITECTURE.md §5.4)::

    T1.<base64url(ticket_code|event_id)>.<base64url(HMAC-SHA256(QR_SECRET, "T1.<body>"))>

It is structurally different from a Campaign QR (``C1.`` token / ``…?c=`` link): only the
check-in endpoint accepts this one, and it rejects every Campaign QR. The secret comes from the
environment (``QR_SECRET``) and is never part of the code.

This module only signs and verifies strings; turning them into an image is ``qr_image``.
"""

import base64
import binascii
import hashlib
import hmac
import uuid

from app.core.config import get_settings
from app.services.campaigns import is_campaign_qr

PREFIX = "T1"
MAX_PAYLOAD_LENGTH = 512  # ticket.qr_payload column
_DEFAULT_SECRET = "change-me-too"  # placeholder in Settings; refused in production


class InvalidAdmissionQRError(ValueError):
    """Not a (valid) admission-ticket QR: wrong format, bad signature or garbage."""


class CampaignQRError(InvalidAdmissionQRError):
    """A Campaign / promo QR was scanned where an admission ticket is expected."""


InvalidAdmissionQR = InvalidAdmissionQRError  # name used in the BE-17 spec


def _secret() -> bytes:
    settings = get_settings()
    secret = settings.qr_secret
    if not secret:
        raise RuntimeError("QR_SECRET is not set")
    if secret == _DEFAULT_SECRET and settings.environment.lower() in ("production", "prod"):
        raise RuntimeError("QR_SECRET must be set to a real secret in production")
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(signed_part: str) -> str:
    return _b64(hmac.new(_secret(), signed_part.encode("ascii"), hashlib.sha256).digest())


def sign_ticket_payload(ticket_code: str, event_id: uuid.UUID) -> str:
    if not ticket_code or "|" in ticket_code:
        raise ValueError("ticket_code must be non-empty and must not contain '|'")
    body = _b64(f"{ticket_code}|{event_id}".encode())
    signed_part = f"{PREFIX}.{body}"
    return f"{signed_part}.{_signature(signed_part)}"


def verify_ticket_payload(payload: str) -> tuple[str, uuid.UUID]:
    """Return ``(ticket_code, event_id)`` of a genuine admission QR.

    Raises :class:`CampaignQRError` for a Campaign QR, :class:`InvalidAdmissionQRError` for
    anything else that isn't a correctly signed ``T1`` token. Checking that the event matches
    the scanner's event is the caller's job (check-in → ``wrong_event``).
    """
    if not isinstance(payload, str):
        raise InvalidAdmissionQRError("QR payload must be a string")
    value = payload.strip()
    if is_campaign_qr(value):
        raise CampaignQRError("This is a campaign QR code, not a ticket")
    if not value or len(value) > MAX_PAYLOAD_LENGTH:
        raise InvalidAdmissionQRError("Not a ticket QR code")

    parts = value.split(".")
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        raise InvalidAdmissionQRError("Not a ticket QR code")
    signed_part = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(_signature(signed_part), parts[2]):
        raise InvalidAdmissionQRError("QR signature is not valid")

    try:
        code, _, raw_event_id = _unb64(parts[1]).decode("utf-8").partition("|")
        event_id = uuid.UUID(raw_event_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidAdmissionQRError("Not a ticket QR code") from exc
    if not code:
        raise InvalidAdmissionQRError("Not a ticket QR code")
    return code, event_id
