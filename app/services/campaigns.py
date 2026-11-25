"""Promotional campaigns, promo codes and Campaign QR codes (SRS §4.14).

A Campaign QR is an HTTPS link to the event page carrying an **opaque** token
(``C1.<random>``). It never encodes a discount and is structurally different from admission
ticket payloads (``T1.…``), so the check-in endpoint can always reject it.

Applying discounts and counting redemptions atomically happens in the orders service (BE-23).
"""

import secrets
import uuid

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import CampaignTicketType, Event, PromoCode, PromotionalCampaign, TicketType, User
from app.models.enums import CampaignStatus, DiscountType, EventStatus, EventVisibility
from app.schemas.campaigns import CampaignCreate, CampaignRead, CampaignResolveRead, CampaignUpdate
from app.services import audit, permissions
from app.services.audit import AuditAction

CAMPAIGN_QR_PREFIX = "C1."
# No 0/O, 1/I/L — codes are typed by hand from posters.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def generate_qr_token() -> str:
    return CAMPAIGN_QR_PREFIX + secrets.token_urlsafe(24)


def is_campaign_qr(value: str) -> bool:
    """True for a Campaign QR token or link — used by check-in to reject it explicitly."""
    return CAMPAIGN_QR_PREFIX in value and ("?c=" in value or value.startswith(CAMPAIGN_QR_PREFIX))


def qr_link(event_id: uuid.UUID, token: str) -> str:
    return f"{get_settings().public_web_url.rstrip('/')}/events/{event_id}?c={token}"


def _load(db: Session, campaign_id: uuid.UUID) -> PromotionalCampaign:
    campaign = db.scalar(
        select(PromotionalCampaign)
        .where(PromotionalCampaign.id == campaign_id)
        .options(selectinload(PromotionalCampaign.promo_code))
    )
    if campaign is None:
        raise NotFoundError("campaign")
    return campaign


def load_managed(db: Session, user: User, campaign_id: uuid.UUID) -> PromotionalCampaign:
    campaign = _load(db, campaign_id)
    event = permissions.get_event(db, campaign.event_id)
    permissions.ensure_can_manage_event(db, user, event)
    return campaign


def to_read(c: PromotionalCampaign) -> CampaignRead:
    code = c.promo_code
    assert code is not None
    return CampaignRead(
        id=c.id,
        event_id=c.event_id,
        name=c.name,
        discount_type=c.discount_type,
        discount_value=c.discount_value,
        starts_at=c.starts_at,
        ends_at=c.ends_at,
        max_redemptions=c.max_redemptions,
        redemption_count=c.redemption_count,
        remaining=None if c.max_redemptions is None else c.max_redemptions - c.redemption_count,
        status=c.status,
        ticket_type_ids=[t.ticket_type_id for t in c.ticket_types],
        code=code.code,
        qr_link=qr_link(c.event_id, code.qr_token),
        created_at=c.created_at,
    )


def _validate_ticket_types(db: Session, event: Event, ids: list[uuid.UUID]) -> None:
    if not ids:
        return
    found = set(
        db.scalars(
            select(TicketType.id).where(TicketType.id.in_(ids), TicketType.event_id == event.id)
        )
    )
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise AppError(
            "ticket_type_not_in_event",
            "Some ticket types do not belong to this event",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=missing,
        )


def _log(
    db: Session,
    user: User,
    action: AuditAction,
    c: PromotionalCampaign,
    text: str,
    metadata: dict[str, object] | None = None,
) -> None:
    audit.log(
        db,
        actor_id=user.id,
        action=action,
        entity_type="campaign",
        entity_id=c.id,
        event_id=c.event_id,
        description=text,
        metadata=metadata,
    )


def create(db: Session, user: User, event: Event, data: CampaignCreate) -> CampaignRead:
    if event.status == EventStatus.CANCELLED:
        raise ConflictError("event_cancelled", "Cancelled events cannot be changed")
    _validate_ticket_types(db, event, data.ticket_type_ids)

    code = data.code
    if code is not None:
        if db.scalar(select(PromoCode.id).where(PromoCode.code == code)) is not None:
            raise ConflictError("promo_code_taken", "This promo code is already in use")
    else:
        code = generate_code()
        while db.scalar(select(PromoCode.id).where(PromoCode.code == code)) is not None:
            code = generate_code()

    campaign = PromotionalCampaign(
        event_id=event.id,
        name=data.name.strip(),
        discount_type=data.discount_type,
        discount_value=data.discount_value,
        starts_at=data.starts_at,
        ends_at=data.ends_at,
        max_redemptions=data.max_redemptions,
        ticket_types=[
            CampaignTicketType(ticket_type_id=i) for i in dict.fromkeys(data.ticket_type_ids)
        ],
    )
    db.add(campaign)
    db.flush()
    db.add(PromoCode(campaign_id=campaign.id, code=code, qr_token=generate_qr_token()))
    _log(db, user, AuditAction.CAMPAIGN_CREATED, campaign, f"Campaign “{campaign.name}” ({code})")
    db.commit()
    return to_read(_load(db, campaign.id))


def list_for_event(db: Session, event: Event) -> list[CampaignRead]:
    rows = db.scalars(
        select(PromotionalCampaign)
        .where(PromotionalCampaign.event_id == event.id)
        .options(selectinload(PromotionalCampaign.promo_code))
        .order_by(PromotionalCampaign.created_at)
    )
    return [to_read(c) for c in rows]


def _lock(db: Session, campaign_id: uuid.UUID) -> PromotionalCampaign:
    """Campaign row FOR UPDATE — the same lock order creation takes in ``promo.quote``, so an
    order sees either the old or the new terms, never a mix, and limits can't be overshot."""
    campaign = db.scalar(
        select(PromotionalCampaign)
        .where(PromotionalCampaign.id == campaign_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if campaign is None:
        raise NotFoundError("campaign")
    return campaign


def update(db: Session, user: User, campaign_id: uuid.UUID, data: CampaignUpdate) -> CampaignRead:
    load_managed(db, user, campaign_id)
    campaign = _lock(db, campaign_id)
    changes = data.model_dump(exclude_unset=True)
    for field in ("name", "discount_type", "discount_value", "code"):
        if field in changes and changes[field] is None:
            raise AppError(
                "invalid_value", f"{field} cannot be empty", status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    discount_type = changes.get("discount_type", campaign.discount_type)
    discount_value = changes.get("discount_value", campaign.discount_value)
    if discount_type == DiscountType.PERCENT and discount_value > 100:
        raise AppError(
            "invalid_discount",
            "Percent discount must be 1–100",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    starts = changes.get("starts_at", campaign.starts_at)
    ends = changes.get("ends_at", campaign.ends_at)
    if starts and ends and ends <= starts:
        raise AppError(
            "invalid_window",
            "ends_at must be after starts_at",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    limit = changes.get("max_redemptions", campaign.max_redemptions)
    if limit is not None and limit < campaign.redemption_count:
        raise ConflictError(
            "limit_below_redemptions",
            f"Code was already redeemed {campaign.redemption_count} times",
        )
    meta: dict[str, object] = {"fields": sorted(changes)}
    if "code" in changes:
        promo_code = db.scalar(select(PromoCode).where(PromoCode.campaign_id == campaign.id))
        assert promo_code is not None
        new_code = changes.pop("code")
        if new_code != promo_code.code:
            taken = db.scalar(select(PromoCode.id).where(PromoCode.code == new_code))
            if taken is not None:
                raise ConflictError("promo_code_taken", "This promo code is already in use")
            meta["code"] = {"old": promo_code.code, "new": new_code}
            promo_code.code = new_code
    if "ticket_type_ids" in changes:
        ids = changes.pop("ticket_type_ids") or []
        event = permissions.get_event(db, campaign.event_id)
        _validate_ticket_types(db, event, ids)
        campaign.ticket_types = [CampaignTicketType(ticket_type_id=i) for i in dict.fromkeys(ids)]
    for field in ("discount_type", "discount_value"):
        if field in changes and changes[field] != getattr(campaign, field):
            meta[field] = {"old": str(getattr(campaign, field)), "new": str(changes[field])}
    for field, value in changes.items():
        setattr(campaign, field, value.strip() if isinstance(value, str) else value)
    try:
        db.flush()
    except IntegrityError as exc:  # code taken by a concurrent create / rename
        db.rollback()
        raise ConflictError("promo_code_taken", "This promo code is already in use") from exc
    _log(
        db,
        user,
        AuditAction.CAMPAIGN_UPDATED,
        campaign,
        f"Campaign “{campaign.name}” updated: {', '.join(sorted(data.model_fields_set))}",
        meta,
    )
    db.commit()
    return to_read(_load(db, campaign.id))


def set_status(
    db: Session, user: User, campaign_id: uuid.UUID, new_status: CampaignStatus
) -> CampaignRead:
    campaign = load_managed(db, user, campaign_id)
    if campaign.status != new_status:
        campaign.status = new_status
        action = (
            AuditAction.CAMPAIGN_DISABLED
            if new_status == CampaignStatus.DISABLED
            else AuditAction.CAMPAIGN_ENABLED
        )
        _log(db, user, action, campaign, f"Campaign “{campaign.name}” {new_status.value}")
        db.commit()
    return to_read(campaign)


def qr_payload(db: Session, user: User, campaign_id: uuid.UUID) -> str:
    campaign = load_managed(db, user, campaign_id)
    assert campaign.promo_code is not None
    return qr_link(campaign.event_id, campaign.promo_code.qr_token)


def resolve(db: Session, token: str) -> CampaignResolveRead:
    """Public: Campaign QR token → event + code to prefill. Never reveals the discount."""
    row = db.execute(
        select(PromoCode, PromotionalCampaign, Event)
        .join(PromotionalCampaign, PromotionalCampaign.id == PromoCode.campaign_id)
        .join(Event, Event.id == PromotionalCampaign.event_id)
        .where(PromoCode.qr_token == token)
    ).first()
    if row is None:
        raise NotFoundError("campaign")
    code, _, event = row
    visible = (
        event.status == EventStatus.PUBLISHED
        and event.suspended_at is None
        and event.visibility != EventVisibility.PRIVATE
    )
    if not visible:
        raise NotFoundError("campaign")
    return CampaignResolveRead(event_id=event.id, code=code.code)
