import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TZ, TimestampMixin, UUIDPkMixin, str_enum
from app.models.enums import CampaignStatus, DiscountType


class PromotionalCampaign(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "promotional_campaign"
    __table_args__ = (
        CheckConstraint(
            "max_redemptions IS NULL OR redemption_count <= max_redemptions",
            name="redemptions_within_limit",
        ),
        CheckConstraint("redemption_count >= 0", name="redemption_count_non_negative"),
        CheckConstraint(
            "(discount_type = 'percent' AND discount_value BETWEEN 1 AND 100)"
            " OR (discount_type = 'fixed' AND discount_value > 0)",
            name="discount_value_valid",
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at", name="window_valid"
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    discount_type: Mapped[DiscountType] = mapped_column(
        str_enum(DiscountType, "discount_type"), nullable=False
    )
    # Percent (1–100) or fixed amount in KZT minor units.
    discount_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(TZ)
    ends_at: Mapped[datetime | None] = mapped_column(TZ)
    max_redemptions: Mapped[int | None] = mapped_column(Integer)
    redemption_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[CampaignStatus] = mapped_column(
        str_enum(CampaignStatus, "campaign_status"),
        nullable=False,
        default=CampaignStatus.ACTIVE,
        server_default="active",
    )

    ticket_types: Mapped[list["CampaignTicketType"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    promo_code: Mapped["PromoCode | None"] = relationship(back_populates="campaign", uselist=False)


class CampaignTicketType(Base):
    """Ticket types a campaign applies to. No rows = applies to all ticket types of the event."""

    __tablename__ = "campaign_ticket_type"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("promotional_campaign.id", ondelete="CASCADE"), primary_key=True
    )
    ticket_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ticket_type.id", ondelete="CASCADE"), primary_key=True
    )


class PromoCode(UUIDPkMixin, TimestampMixin, Base):
    """`qr_token` is an opaque token for the Campaign QR link — never a discount amount and
    structurally different from admission ticket QR payloads."""

    __tablename__ = "promo_code"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("promotional_campaign.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    qr_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    campaign: Mapped[PromotionalCampaign] = relationship(back_populates="promo_code")


class PromoRedemption(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "promo_redemption"
    __table_args__ = (CheckConstraint("discount_applied_minor >= 0", name="discount_non_negative"),)

    promo_code_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("promo_code.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # One campaign attribution per order.
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    discount_applied_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    redeemed_at: Mapped[datetime] = mapped_column(TZ, nullable=False, server_default=text("now()"))
    released_at: Mapped[datetime | None] = mapped_column(TZ)
