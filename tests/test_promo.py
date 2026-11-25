"""BE-23: promo codes — preview + atomic redemption inside order creation."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    CampaignTicketType,
    Event,
    Order,
    Payment,
    PromoCode,
    PromoRedemption,
    PromotionalCampaign,
    TicketType,
)
from app.models.enums import CampaignStatus, DiscountType, OrderStatus, TicketKind
from app.services import campaigns, ticketing
from app.services import orders as orders_svc
from tests.factories import auth_headers, make_event, make_organizer, make_ticket_type, make_user
from tests.test_orders import _expire_now, error_code, order_body, post_order

pytestmark = pytest.mark.db

API = "/api/v1"


def make_campaign(
    db: Session,
    event: Event,
    *,
    code: str,
    discount_type: DiscountType = DiscountType.PERCENT,
    value: int = 10,
    ticket_types: tuple[TicketType, ...] = (),
    max_redemptions: int | None = None,
    **kw: Any,
) -> PromotionalCampaign:
    campaign = PromotionalCampaign(
        event_id=event.id,
        name=f"Campaign {code}",
        discount_type=discount_type,
        discount_value=value,
        max_redemptions=max_redemptions,
        ticket_types=[CampaignTicketType(ticket_type_id=t.id) for t in ticket_types],
        **kw,
    )
    db.add(campaign)
    db.flush()
    db.add(PromoCode(campaign_id=campaign.id, code=code, qr_token=campaigns.generate_qr_token()))
    db.flush()
    return campaign


@pytest.fixture
def shop(db: Session) -> SimpleNamespace:
    ev = make_event(db, make_organizer(db), paid_sales_active=True)
    a = make_ticket_type(db, ev, name="A", price_minor=333_333, quantity_total=50, max_per_order=10)
    b = make_ticket_type(
        db, ev, name="B", price_minor=1_000_000, quantity_total=50, max_per_order=10
    )
    return SimpleNamespace(event=ev, a=a, b=b, buyer=make_user(db))


def buy(
    client: TestClient, shop: SimpleNamespace, *lines: Any, code: str | None, **extra: Any
) -> Any:
    body = order_body(shop.event.id, *lines, **extra)
    if code is not None:
        body["promo_code"] = code
    return post_order(client, shop.buyer, body)


def validate(client: TestClient, event_id: Any, code: str, *lines: tuple[TicketType, int]) -> Any:
    return client.post(
        f"{API}/promo/validate",
        json={
            "event_id": str(event_id),
            "code": code,
            "items": [{"ticket_type_id": str(t.id), "quantity": q} for t, q in lines],
        },
    ).json()


def redemptions(db: Session, campaign: PromotionalCampaign) -> list[PromoRedemption]:
    return list(
        db.scalars(
            select(PromoRedemption)
            .join(PromoCode, PromoCode.id == PromoRedemption.promo_code_id)
            .where(PromoCode.campaign_id == campaign.id)
        )
    )


def order_id_of(order: dict[str, Any]) -> uuid.UUID:
    return uuid.UUID(order["id"])


# ------------------------------------------------------------------ math


def test_percent_applies_to_campaign_items_and_rounds_down(
    client: TestClient, shop, db: Session
) -> None:
    campaign = make_campaign(db, shop.event, code="SAVE15", value=15, ticket_types=(shop.a,))
    resp = buy(client, shop, (shop.a, 1), (shop.b, 1), code="SAVE15")
    assert resp.status_code == 201, resp.text
    order = resp.json()
    # 15 % of 333 333 = 49 999.95 → 49 999; B isn't in the campaign
    assert (order["subtotal_minor"], order["discount_minor"], order["total_minor"]) == (
        1_333_333,
        49_999,
        1_283_334,
    )
    assert order["promo_code"] == "SAVE15" and order["status"] == "pending"

    db.refresh(campaign)
    assert campaign.redemption_count == 1
    [r] = redemptions(db, campaign)
    assert str(r.order_id) == order["id"] and r.discount_applied_minor == 49_999
    assert "promo.redeemed" in set(
        db.scalars(select(AuditLog.action_type).where(AuditLog.event_id == shop.event.id))
    )


def test_fixed_is_capped_at_applicable_items(client: TestClient, shop, db: Session) -> None:
    make_campaign(
        db,
        shop.event,
        code="MINUS5M",
        discount_type=DiscountType.FIXED,
        value=5_000_000,
        ticket_types=(shop.a,),
    )
    order = buy(client, shop, (shop.a, 2), (shop.b, 1), code="MINUS5M").json()
    assert order["discount_minor"] == 666_666  # only the two A tickets
    assert order["total_minor"] == 1_000_000


def test_fixed_on_all_types_never_goes_below_zero(client: TestClient, shop, db: Session) -> None:
    make_campaign(db, shop.event, code="BIG", discount_type=DiscountType.FIXED, value=99_000_000)
    order = buy(client, shop, (shop.a, 1), (shop.b, 1), code="BIG").json()
    assert order["discount_minor"] == 1_333_333 and order["total_minor"] == 0


def test_full_discount_goes_the_free_registration_way(
    client: TestClient, shop, db: Session
) -> None:
    make_campaign(db, shop.event, code="FREE100", value=100)
    order = buy(client, shop, (shop.b, 2), code="free100 ").json()  # case / spaces ignored
    assert order["status"] == "paid" and order["total_minor"] == 0
    assert order["discount_minor"] == 2_000_000
    assert len(ticketing.tickets_of(db, order_id_of(order))) == 2
    assert db.scalars(select(Payment).where(Payment.order_id == order_id_of(order))).all() == []


def test_client_discount_is_ignored(client: TestClient, shop, db: Session) -> None:
    make_campaign(db, shop.event, code="TEN", value=10)
    order = buy(
        client,
        shop,
        (shop.b, 1),
        code="TEN",
        discount_minor=999_999,
        total_minor=1,
        subtotal_minor=1,
    ).json()
    assert (order["discount_minor"], order["total_minor"]) == (100_000, 900_000)

    no_code = buy(client, shop, (shop.b, 1), code=None, discount_minor=500_000).json()
    assert (no_code["discount_minor"], no_code["total_minor"]) == (0, 1_000_000)


# ------------------------------------------------------------------ errors


def test_every_error_code(client: TestClient, shop, db: Session) -> None:
    now = datetime.now(UTC)
    other_event = make_event(db, make_organizer(db))
    make_campaign(db, other_event, code="OTHER")
    make_campaign(db, shop.event, code="OFF", status=CampaignStatus.DISABLED)
    make_campaign(db, shop.event, code="LATER", starts_at=now + timedelta(days=1))
    make_campaign(
        db,
        shop.event,
        code="OLD",
        starts_at=now - timedelta(days=2),
        ends_at=now - timedelta(days=1),
    )
    used = make_campaign(db, shop.event, code="USED", max_redemptions=1)
    used.redemption_count = 1
    make_campaign(db, shop.event, code="ONLYB", ticket_types=(shop.b,))
    free = make_ticket_type(db, shop.event, name="Free", price_minor=0, kind=TicketKind.FREE)
    make_campaign(db, shop.event, code="FREEONLY", ticket_types=(free,))
    db.flush()

    cases = {
        "NOPE": ("promo_not_found", 422),
        "OTHER": ("promo_wrong_event", 422),
        "OFF": ("promo_disabled", 422),
        "LATER": ("promo_not_started", 422),
        "OLD": ("promo_expired", 422),
        "USED": ("promo_exhausted", 409),
        "ONLYB": ("promo_not_applicable", 422),
        "FREEONLY": ("promo_not_applicable", 422),
    }
    for code, (expected, http) in cases.items():
        lines = [(free, 1)] if code == "FREEONLY" else [(shop.a, 1)]
        preview = validate(client, shop.event.id, code, *lines)
        assert preview["valid"] is False and preview["error_code"] == expected, (code, preview)
        assert preview["discount_minor"] == 0 and preview["message"]

        resp = buy(client, shop, *lines, code=code)
        assert resp.status_code == http and error_code(resp) == expected, (code, resp.text)

    db.refresh(shop.a)
    assert shop.a.quantity_reserved == 0  # failed orders reserved nothing
    assert db.scalars(select(Order).where(Order.event_id == shop.event.id)).all() == []
    db.refresh(used)
    assert used.redemption_count == 1


def test_validate_previews_without_redeeming(client: TestClient, shop, db: Session) -> None:
    campaign = make_campaign(db, shop.event, code="TEN", value=10, max_redemptions=1)
    for _ in range(3):
        preview = validate(client, shop.event.id, "ten", (shop.a, 1), (shop.b, 2))
        assert preview == {
            "valid": True,
            "error_code": None,
            "message": None,
            "subtotal_minor": 2_333_333,
            "discount_minor": 233_333,
            "total_minor": 2_100_000,
            "currency": "KZT",
        }
    db.refresh(campaign)
    assert campaign.redemption_count == 0 and redemptions(db, campaign) == []

    other_type = make_ticket_type(db, make_event(db))
    bad = validate(client, shop.event.id, "TEN", (other_type, 1))
    assert bad["valid"] is False and bad["error_code"] == "ticket_type_not_found"


# ------------------------------------------------------------------ release


def test_expired_order_returns_its_redemption(client: TestClient, shop, db: Session) -> None:
    campaign = make_campaign(db, shop.event, code="ONCE", max_redemptions=1)
    first = buy(client, shop, (shop.a, 1), code="ONCE").json()
    resp = buy(client, shop, (shop.a, 1), code="ONCE")
    assert resp.status_code == 409 and error_code(resp) == "promo_exhausted"

    _expire_now(db, first["id"])
    assert orders_svc.expire_stale_orders(db) == 1
    db.refresh(campaign)
    assert campaign.redemption_count == 0
    [r] = redemptions(db, campaign)
    assert r.released_at is not None

    assert buy(client, shop, (shop.a, 1), code="ONCE").status_code == 201
    db.refresh(campaign)
    assert campaign.redemption_count == 1


def test_cancelled_pending_order_returns_its_redemption(
    client: TestClient, shop, db: Session
) -> None:
    campaign = make_campaign(db, shop.event, code="ONCE", max_redemptions=1)
    order = buy(client, shop, (shop.a, 1), code="ONCE").json()
    client.post(f"{API}/orders/{order['id']}/cancel", headers=auth_headers(shop.buyer))
    db.refresh(campaign)
    assert campaign.redemption_count == 0
    assert buy(client, shop, (shop.a, 1), code="ONCE").status_code == 201


def test_paid_order_keeps_its_redemption(client: TestClient, shop, db: Session) -> None:
    campaign = make_campaign(db, shop.event, code="KEEP", max_redemptions=5)
    order = buy(client, shop, (shop.b, 1), code="KEEP").json()
    pay = client.post(
        f"{API}/orders/{order['id']}/pay", json={}, headers=auth_headers(shop.buyer)
    ).json()
    assert pay["amount_minor"] == 900_000  # the discounted total is charged
    assert orders_svc.expire_stale_orders(db) == 0
    db.refresh(campaign)
    assert campaign.redemption_count == 1
    paid = db.get(Order, order_id_of(order))
    assert paid and paid.status == OrderStatus.PAID
