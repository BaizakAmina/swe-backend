from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Payment,
    PromoCode,
    PromoRedemption,
    PromotionalCampaign,
    Refund,
    Ticket,
    User,
)
from app.models.enums import (
    DiscountType,
    OrderStatus,
    PaymentPurpose,
    PaymentStatus,
    RefundStatus,
    TicketStatus,
)
from tests.factories import (
    auth_headers,
    make_event,
    make_organizer,
    make_paid_order,
    make_ticket_type,
    make_user,
)

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture
def world(db: Session):
    """
    Event capacity 100. Standard 10 000 ₸ (qty 60), VIP 25 000 ₸ (qty 20).
      order A: 2 × Standard, promo −10% → gross 20 000, discount 2 000
      order B: 1 × VIP                 → gross 25 000, later fully refunded
      order C: 1 × Standard, paid yesterday
    One Standard ticket from A is checked in.
    """
    org = make_organizer(db)
    event = make_event(db, org, capacity=100)
    std = make_ticket_type(db, event, name="Standard", price_minor=1_000_000, quantity_total=60)
    vip = make_ticket_type(db, event, name="VIP", price_minor=2_500_000, quantity_total=20)
    buyer = make_user(db)

    campaign = PromotionalCampaign(
        event_id=event.id, name="Launch", discount_type=DiscountType.PERCENT, discount_value=10
    )
    db.add(campaign)
    db.flush()
    code = PromoCode(campaign_id=campaign.id, code="DEMO10", qr_token="C1.t")
    db.add(code)
    db.flush()

    now = datetime.now(UTC)
    a = make_paid_order(
        db,
        buyer,
        std,
        [("A1", "a1@example.com"), ("A2", "a2@example.com")],
        discount_minor=200_000,
        promo_code_id=code.id,
        paid_at=now,
    )
    db.add(PromoRedemption(promo_code_id=code.id, order_id=a.id, discount_applied_minor=200_000))
    b = make_paid_order(db, buyer, vip, paid_at=now)
    make_paid_order(db, buyer, std, paid_at=now - timedelta(days=1))

    pay = Payment(
        order_id=b.id,
        payer_user_id=buyer.id,
        purpose=PaymentPurpose.TICKET_PURCHASE,
        amount_minor=2_500_000,
        status=PaymentStatus.SUCCEEDED,
    )
    db.add(pay)
    db.flush()
    db.add(Refund(payment_id=pay.id, amount_minor=2_500_000, status=RefundStatus.SUCCEEDED))
    b.status = OrderStatus.REFUNDED
    for t in db.scalars(select(Ticket).join(Ticket.order_item).where(Ticket.event_id == event.id)):
        if t.order_item.order_id == b.id:
            t.status = TicketStatus.REFUNDED
    vip.quantity_sold -= 1
    first = next(t for t in db.scalars(select(Ticket)) if t.order_item.order_id == a.id)
    first.status = TicketStatus.CHECKED_IN
    db.flush()

    owner = db.get(User, org.user_id)
    assert owner
    return {"event": event, "std": std, "vip": vip, "h": auth_headers(owner)}


def test_event_analytics_numbers(client: TestClient, world) -> None:
    resp = client.get(f"{API}/events/{world['event'].id}/analytics", headers=world["h"])
    assert resp.status_code == 200
    a = resp.json()

    assert a["capacity"] == {
        "capacity": 100,
        "sold": 3,
        "reserved": 0,
        "remaining": 97,
        "sold_percent": 3.0,
    }
    assert a["revenue"] == {
        "currency": "KZT",
        "gross_minor": 5_500_000,  # 20 000 + 25 000 + 10 000
        "discounts_minor": 200_000,
        "refunds_minor": 2_500_000,
        "net_minor": 2_800_000,
        "is_simulated": True,
    }
    by_type = {t["name"]: t for t in a["by_ticket_type"]}
    assert by_type["Standard"]["sold"] == 3 and by_type["Standard"]["revenue_minor"] == 2_800_000
    assert by_type["Standard"]["checked_in"] == 1
    assert by_type["VIP"]["sold"] == 0 and by_type["VIP"]["refunded"] == 1
    assert by_type["VIP"]["revenue_minor"] == 0

    assert a["by_campaign"] == [
        {
            "campaign_id": a["by_campaign"][0]["campaign_id"],
            "name": "Launch",
            "code": "DEMO10",
            "redemptions": 1,
            "discount_minor": 200_000,
            "revenue_minor": 1_800_000,
        }
    ]
    assert a["attendance"]["issued"] == 3 and a["attendance"]["checked_in"] == 1
    assert a["attendance"]["no_show_percent"] is None  # event not finished yet
    assert sum(p["tickets"] for p in a["sales_over_time"]) == 4
    assert len(a["sales_over_time"]) == 2  # yesterday + today


def test_filters_apply_to_all_blocks(client: TestClient, world) -> None:
    url = f"{API}/events/{world['event'].id}/analytics"
    vip_only = client.get(
        url, params={"ticket_type_id": str(world["vip"].id)}, headers=world["h"]
    ).json()
    assert vip_only["revenue"]["gross_minor"] == 2_500_000
    assert vip_only["revenue"]["net_minor"] == 0
    assert vip_only["capacity"]["capacity"] == 20
    assert [t["name"] for t in vip_only["by_ticket_type"]] == ["VIP"]

    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    today = client.get(url, params={"date_from": since}, headers=world["h"]).json()
    assert today["revenue"]["gross_minor"] == 4_500_000

    hourly = client.get(url, params={"bucket": "hour"}, headers=world["h"]).json()
    assert len(hourly["sales_over_time"]) >= 2


def test_only_own_events(client: TestClient, world, db: Session) -> None:
    other = auth_headers(make_organizer(db).user)
    assert (
        client.get(f"{API}/events/{world['event'].id}/analytics", headers=other).status_code == 403
    )


def test_organizer_summary(client: TestClient, world) -> None:
    s = client.get(f"{API}/organizers/me/analytics/summary", headers=world["h"]).json()
    assert s["events_total"] == 1 and s["events_upcoming"] == 1
    assert s["tickets_sold"] == 3 and s["revenue"]["net_minor"] == 2_800_000


def test_empty_event(client: TestClient, db: Session) -> None:
    org = make_organizer(db)
    event = make_event(db, org)
    owner = db.get(User, org.user_id)
    assert owner
    a = client.get(f"{API}/events/{event.id}/analytics", headers=auth_headers(owner)).json()
    assert a["revenue"]["gross_minor"] == 0 and a["sales_over_time"] == []
    assert a["attendance"]["checked_in_percent"] == 0.0
