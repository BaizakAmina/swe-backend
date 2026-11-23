import uuid
from datetime import datetime
from typing import Any

from app.models.enums import NotificationChannel
from app.schemas.common import ORMModel


class NotificationRead(ORMModel):
    id: uuid.UUID
    type: str
    channel: NotificationChannel
    payload: dict[str, Any]
    read_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
