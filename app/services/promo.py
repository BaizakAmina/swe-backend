"""Promo codes: preview and atomic redemption (SRS §4.14, §7; BACKEND_ARCHITECTURE.md §5.3).

All discount math is on the server; the client never sends an amount.

**When a code counts as redeemed:** when the order is created, in the same transaction, under
``SELECT … FOR UPDATE`` on the campaign row (``redemption_count += 1`` + a ``promo_redemption``
row). If the order then expires or is cancelled before payment — or is later refunded /
cancelled (BE-24) — the redemption is released (``released_at``, ``redemption_count -= 1``).
So ``max_redemptions`` can never be exceeded, and an abandoned checkout doesn't burn a use.
The ``redemptions_within_limit`` CHECK is the last line of defence.

Lock order (same as in ``orders``): ticket types → campaign. Never the other way round.

Checks, in order, each with its own error code: ``promo_not_found``, ``promo_wrong_event``
(the code belongs to another event — checked before anything else so nothing about that
campaign leaks), ``promo_disabled``, ``promo_not_started``, ``promo_expired``,
``promo_exhausted``, ``promo_not_applicable`` (no item of the order is in the campaign's
ticket types, or they are all free).

Discount: ``percent`` — of the applicable items' total, rounded down to whole minor units;
``fixed`` — at most the applicable items' total. The order total never goes below zero.
"""

import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Order, PromoCode, PromoRedemption, PromotionalCampaign, TicketType
from app.models.enums import CampaignStatus, DiscountType
from app.schemas.campaigns import PromoValidateRequest, PromoValidateResponse
from app.services import audit
from app.services.audit import AuditAction

MESSAGES: dict[str, str] = {
    "promo_not_found": "Promo code not found",
    "promo_wrong_event": "This promo code is for another event",
    "promo_disabled": "This promo code is disabled",
    "promo_not_started": "This promo code is not active yet",
    "promo_expired": "This promo code has expired",
    "promo_exhausted": "This promo code has been used up",
    "promo_not_applicable": "This promo code doesn't apply to the selected tickets",
}


def promo_error(code: str) -> AppError:
    http = status.HTTP_409_CONFLICT if code == "promo_exhausted" else 422
    return AppError(code, MESSAGES[code], http)


@dataclass(frozen=True)
class Line:
    ticket_type_id: uuid.UUID
    unit_price_minor: int
    quantity: int


@dataclass(frozen=True)
class Quote:
    promo_code: PromoCode
    campaign: PromotionalCampaign
    discount_minor: int


def normalize(code: str) -> str:
    return code.strip().upper()


def discount_for(campaign: PromotionalCampaign, lines: Iterable[Line]) -> int | None:
    """Discount in minor units, or ``None`` if the campaign applies to none of the lines."""
    scope = {link.ticket_type_id for link in campaign.ticket_types}  # empty = all types
    applicable = sum(
        line.unit_price_minor * line.quantity
        for line in lines
        if not scope or line.ticket_type_id in scope
    )
    if applicable <= 0:
        return None
    if campaign.discount_type == DiscountType.PERCENT:
        return applicable * campaign.discount_value // 100
    return min(campaign.discount_value, applicable)


def quote(
    db: Session,
    event_id: uuid.UUID,
    code: str,
    lines: Sequence[Line],
    *,
    lock: bool,
    now: datetime | None = None,
) -> Quote:
    """Validate ``code`` for these lines. ``lock`` → campaign row FOR UPDATE (order creation)."""
    now = now or datetime.now(UTC)
    promo = db.scalar(select(PromoCode).where(PromoCode.code == normalize(code)))
    if promo is None:
        raise promo_error("promo_not_found")
    stmt = select(PromotionalCampaign).where(PromotionalCampaign.id == promo.campaign_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    campaign = db.scalar(stmt)
    if campaign is None:  # FK makes this impossible
        raise promo_error("promo_not_found")
    if lock:
        # the organizer may have renamed the code while we waited for the campaign lock
        db.refresh(promo)
        if promo.code != normalize(code):
            raise promo_error("promo_not_found")

    if campaign.event_id != event_id:
        raise promo_error("promo_wrong_event")
    if campaign.status == CampaignStatus.DISABLED:
        raise promo_error("promo_disabled")
    if campaign.starts_at and now < campaign.starts_at:
        raise promo_error("promo_not_started")
    if campaign.ends_at and now >= campaign.ends_at:
        raise promo_error("promo_expired")
    if campaign.max_redemptions is not None and (
        campaign.redemption_count >= campaign.max_redemptions
    ):
        raise promo_error("promo_exhausted")
    discount = discount_for(campaign, lines)
    if discount is None:
        raise promo_error("promo_not_applicable")
    return Quote(promo_code=promo, campaign=campaign, discount_minor=discount)


def redeem(db: Session, order: Order, q: Quote, actor_id: uuid.UUID) -> None:
    """Count the use (campaign row locked by :func:`quote`) and attribute it to ``order``."""
    q.campaign.redemption_count += 1
    db.add(
        PromoRedemption(
            promo_code_id=q.promo_code.id,
            order_id=order.id,
            discount_applied_minor=q.discount_minor,
        )
    )
    audit.log(
        db,
        actor_id=actor_id,
        action=AuditAction.PROMO_REDEEMED,
        entity_type="order",
        entity_id=order.id,
        event_id=order.event_id,
        description=f"Promo code {q.promo_code.code} applied: −{q.discount_minor} minor units",
        metadata={
            "campaign_id": str(q.campaign.id),
            "code": q.promo_code.code,
            "discount_minor": q.discount_minor,
            "redemption_count": q.campaign.redemption_count,
        },
    )


def release(db: Session, orders: Sequence[Order]) -> int:
    """Give back the promo uses of ``orders`` (expired / cancelled / refunded). Idempotent.

    Campaign rows are locked in ``id`` order. Returns the number of released redemptions.
    """
    order_ids = [o.id for o in orders if o.promo_code_id]
    if not order_ids:
        return 0
    redemptions = db.scalars(
        select(PromoRedemption)
        .where(PromoRedemption.order_id.in_(order_ids), PromoRedemption.released_at.is_(None))
        .with_for_update()
    ).all()
    if not redemptions:
        return 0
    per_code = Counter(r.promo_code_id for r in redemptions)
    campaigns = {
        promo_code_id: campaign
        for promo_code_id, campaign in db.execute(
            select(PromoCode.id, PromotionalCampaign)
            .join(PromotionalCampaign, PromotionalCampaign.id == PromoCode.campaign_id)
            .where(PromoCode.id.in_(per_code))
            .order_by(PromotionalCampaign.id)
            .with_for_update(of=PromotionalCampaign)
            .execution_options(populate_existing=True)
        ).tuples()
    }
    now = datetime.now(UTC)
    for redemption in redemptions:
        redemption.released_at = now
    for promo_code_id, n in per_code.items():
        campaigns[promo_code_id].redemption_count -= n
    db.flush()
    return len(redemptions)


def preview(db: Session, data: PromoValidateRequest) -> PromoValidateResponse:
    """``POST /promo/validate``: what the code would do for these items. Nothing is redeemed."""
    wanted = {line.ticket_type_id for line in data.items}
    types = {
        tt.id: tt
        for tt in db.scalars(
            select(TicketType).where(
                TicketType.id.in_(wanted), TicketType.event_id == data.event_id
            )
        )
    }
    if len(types) != len(wanted):
        return PromoValidateResponse(
            valid=False,
            error_code="ticket_type_not_found",
            message="Some ticket types do not belong to this event",
            subtotal_minor=0,
            discount_minor=0,
            total_minor=0,
        )
    lines = [
        Line(line.ticket_type_id, types[line.ticket_type_id].price_minor, line.quantity)
        for line in data.items
    ]
    subtotal = sum(line.unit_price_minor * line.quantity for line in lines)
    currency = next(iter(types.values())).currency
    try:
        q = quote(db, data.event_id, data.code, lines, lock=False)
    except AppError as exc:
        return PromoValidateResponse(
            valid=False,
            error_code=exc.code,
            message=exc.message,
            subtotal_minor=subtotal,
            discount_minor=0,
            total_minor=subtotal,
            currency=currency,
        )
    return PromoValidateResponse(
        valid=True,
        subtotal_minor=subtotal,
        discount_minor=q.discount_minor,
        total_minor=subtotal - q.discount_minor,
        currency=currency,
    )
