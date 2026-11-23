import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession, ManagedEvent, OptionalUser
from app.api.v1._stub import not_implemented
from app.models.enums import CampaignStatus
from app.schemas.campaigns import (
    CampaignCreate,
    CampaignRead,
    CampaignResolveRead,
    CampaignUpdate,
    PromoValidateRequest,
    PromoValidateResponse,
)
from app.services import campaigns as svc
from app.services import qr_image

router = APIRouter(tags=["campaigns & promo"])


@router.get("/events/{event_id}/campaigns", response_model=list[CampaignRead])
def list_campaigns(event: ManagedEvent, db: DbSession) -> list[CampaignRead]:
    return svc.list_for_event(db, event)


@router.post(
    "/events/{event_id}/campaigns", response_model=CampaignRead, status_code=status.HTTP_201_CREATED
)
def create_campaign(
    data: CampaignCreate, event: ManagedEvent, user: CurrentUser, db: DbSession
) -> CampaignRead:
    """Creates the campaign, its promo code (given or generated) and a Campaign QR link."""
    return svc.create(db, user, event, data)


@router.get("/campaigns/resolve", response_model=CampaignResolveRead)
def resolve_campaign_token(token: str, db: DbSession) -> CampaignResolveRead:
    """Public: Campaign QR token → event + promo code to prefill. Never returns the discount."""
    return svc.resolve(db, token)


@router.get("/campaigns/{campaign_id}", response_model=CampaignRead)
def get_campaign(campaign_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CampaignRead:
    return svc.to_read(svc.load_managed(db, user, campaign_id))


@router.patch("/campaigns/{campaign_id}", response_model=CampaignRead)
def update_campaign(
    campaign_id: uuid.UUID, data: CampaignUpdate, user: CurrentUser, db: DbSession
) -> CampaignRead:
    return svc.update(db, user, campaign_id, data)


@router.post("/campaigns/{campaign_id}/disable", response_model=CampaignRead)
def disable_campaign(campaign_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CampaignRead:
    return svc.set_status(db, user, campaign_id, CampaignStatus.DISABLED)


@router.post("/campaigns/{campaign_id}/enable", response_model=CampaignRead)
def enable_campaign(campaign_id: uuid.UUID, user: CurrentUser, db: DbSession) -> CampaignRead:
    return svc.set_status(db, user, campaign_id, CampaignStatus.ACTIVE)


@router.get(
    "/campaigns/{campaign_id}/qr.png",
    response_model=None,
    responses={200: {"content": {"image/png": {}}}},
)
def campaign_qr(campaign_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """Campaign QR for posters: an HTTPS link to the event page — not an admission ticket."""
    return Response(
        qr_image.make_png(svc.qr_payload(db, user, campaign_id)), media_type="image/png"
    )


@router.post("/promo/validate", response_model=PromoValidateResponse)
def validate_promo(
    data: PromoValidateRequest, db: DbSession, user: OptionalUser
) -> PromoValidateResponse:
    """Preview of the discount. Nothing is redeemed."""
    not_implemented("BE-23")
