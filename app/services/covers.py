"""Event cover images: upload, public serving, URL for clients.

Covers live in object storage under ``covers/<event id>/<random>.<ext>``; every upload gets a new
key, so the public URL can be cached forever and an old URL never shows a newer picture.
"""

import contextlib
import logging
import re
import uuid

from fastapi import status
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.urls import public_api_url
from app.models.enums import EventStatus
from app.models.event import Event
from app.models.user import User
from app.services import audit, storage
from app.services.audit import AuditAction

log = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
PREFIX = "covers/"
_CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
_KEY_RE = re.compile(r"^covers/[0-9a-f-]{36}/[0-9a-f]{32}\.(jpg|png|webp)$")


def _sniff(data: bytes) -> str | None:
    """File extension from the first bytes; the client's Content-Type is not trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def url_for(key: str | None) -> str | None:
    """Public URL for ``cover_image_key``: our own uploads are served by ``GET /files/…``;
    a key that already is an absolute http(s) URL is returned as is."""
    if not key:
        return None
    if key.startswith(("https://", "http://")):
        return key
    if _KEY_RE.match(key):
        return public_api_url(f"/files/{key}")
    return None


def upload(db: Session, user: User, event: Event, data: bytes) -> Event:
    _ensure_editable(event)
    if not data:
        raise AppError("empty_file", "The file is empty", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if len(data) > MAX_BYTES:
        raise AppError(
            "file_too_large",
            f"Cover image must be at most {MAX_BYTES // (1024 * 1024)} MB",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    ext = _sniff(data)
    if ext is None:
        raise AppError(
            "unsupported_image",
            "Cover image must be JPEG, PNG or WebP",
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    key = f"{PREFIX}{event.id}/{uuid.uuid4().hex}.{ext}"
    try:
        storage.put(key, data, _CONTENT_TYPES[ext])
    except storage.StorageError as exc:
        raise AppError(
            "storage_unavailable", "Could not store the image, try again later", 503
        ) from exc
    return _replace(db, user, event, key, "Uploaded a cover image")


def remove(db: Session, user: User, event: Event) -> Event:
    _ensure_editable(event)
    if event.cover_image_key is None:
        return event
    return _replace(db, user, event, None, "Removed the cover image")


def _ensure_editable(event: Event) -> None:
    if event.status == EventStatus.CANCELLED:
        raise ConflictError("event_cancelled", "Cancelled events cannot be changed")


def _replace(db: Session, user: User, event: Event, key: str | None, text: str) -> Event:
    old = event.cover_image_key
    event.cover_image_key = key
    event.revision += 1
    audit.log(
        db,
        actor_id=user.id,
        action=AuditAction.EVENT_UPDATED,
        entity_type="event",
        entity_id=event.id,
        event_id=event.id,
        description=text,
        metadata={"fields": ["cover_image_key"]},
    )
    db.commit()
    if old and _KEY_RE.match(old):
        with contextlib.suppress(storage.StorageError):
            storage.delete(old)
    return event


def read(key: str) -> tuple[bytes, str]:
    """Bytes and content type of an uploaded cover (public, no auth)."""
    if not _KEY_RE.match(key):
        raise NotFoundError("file")
    try:
        data = storage.get(key)
    except storage.StorageError as exc:
        log.warning("cover read failed: %s", exc)
        raise AppError("storage_unavailable", "Storage is unavailable", 503) from exc
    if data is None:
        raise NotFoundError("file")
    return data, _CONTENT_TYPES[key.rsplit(".", 1)[1]]
