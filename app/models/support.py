import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TZ, TimestampMixin, UUIDPkMixin, str_enum
from app.models.enums import SupportCaseKind, SupportCategory, SupportStatus


class SupportCase(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "support_case"

    opened_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[SupportCaseKind] = mapped_column(
        str_enum(SupportCaseKind, "support_case_kind"), nullable=False
    )
    category: Mapped[SupportCategory] = mapped_column(
        str_enum(SupportCategory, "support_category"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[SupportStatus] = mapped_column(
        str_enum(SupportStatus, "support_status"),
        nullable=False,
        default=SupportStatus.OPEN,
        server_default="open",
        index=True,
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event.id"), index=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ticket.id"))
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(TZ)

    messages: Mapped[list["SupportMessage"]] = relationship(
        back_populates="case", order_by="SupportMessage.sent_at"
    )


class SupportMessage(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "support_message"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_case.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))

    case: Mapped[SupportCase] = relationship(back_populates="messages")
