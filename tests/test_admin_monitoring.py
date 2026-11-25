"""Admin search / filters on orders and payments, monitoring lists, settings, report dates."""

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Payment, SupportCase
from app.models.enums import (
    OrderStatus,
    PaymentPurpose,
    PaymentStatus,
    Role,
    SupportCaseKind,
    SupportCategory,
    SupportStatus,
)
from app.services import platform_settings
from tests.factories import (
    auth_headers,
    make_event,
    make_organizer,
    make_paid_order,
    make_ticket_type,
    make_user,
)
from tests.test_orders import order_body, post_order
from tests.test_promo import make_campaign

pytestmark = pytest.mark.db

ADMIN = "/api/v1/admin"
DAY = datetime(2026, 3, 10, 12, tzinfo=UTC)


@pytest.fixture
def admin_h(db: Session) -> dict[str, str]:
    return auth_headers(make_user(db, roles=(Role.PLATFORM_ADMIN,), full_name="Admin"))


def ids(resp: object) -> set[str]:
    return {i["id"] for i in resp.json()["items"]}  # type: ignore[attr-defined]


@pytest.fixture
def market(db: Session) -> dict[str, object]:
    jazz = make_event(db, title="Jazz Night")
    rock = make_event(db, title="Rock Fest")
    alice = make_user(db, full_name="Alice Smith", email="alice@example.com")
    bob = make_user(db, full_name="Bob Stone", email="bob@example.com")
    o1 = make_paid_order(db, alice, make_ticket_type(db, jazz))
    o2 = make_paid_order(db, bob, make_ticket_type(db, rock))
    o3 = make_paid_order(db, bob, make_ticket_type(db, jazz), status=OrderStatus.REFUNDED)
    for order, when in ((o1, DAY), (o2, DAY + timedelta(days=5)), (o3, DAY + timedelta(days=9))):
        order.created_at = when
    payments = []
    for order, ref, st in (
        (o1, "sim_aaa111", PaymentStatus.SUCCEEDED),
        (o2, "sim_bbb222", PaymentStatus.SUCCEEDED),
        (o3, "sim_ccc333", PaymentStatus.FAILED),
    ):
        p = Payment(
            order_id=order.id,
            event_id=order.event_id,
            payer_user_id=order.buyer_user_id,
            purpose=PaymentPurpose.TICKET_PURCHASE,
            amount_minor=order.total_minor,
            provider_ref=ref,
            status=st,
            created_at=order.created_at,
        )
        db.add(p)
        payments.append(p)
    db.flush()
    return {"orders": (o1, o2, o3), "payments": payments, "jazz": jazz, "rock": rock}


# ------------------------------------------------------------------ orders & payments


def test_order_search_and_filters(client: TestClient, admin_h, market) -> None:
    o1, o2, o3 = (str(o.id) for o in market["orders"])  # type: ignore[attr-defined]

    def get(**params: str) -> object:
        return client.get(f"{ADMIN}/orders", params=params, headers=admin_h)

    assert ids(get(q="alice@EXAMPLE")) == {o1}
    assert ids(get(q="stone")) == {o2, o3}
    assert ids(get(q="jazz night")) == {o1, o3}
    assert ids(get(q=o2)) == {o2}
    assert ids(get(q="stone", order_status="refunded")) == {o3}
    assert ids(get(date_from="2026-03-10", date_to="2026-03-15")) == {o1, o2}
    assert ids(get(date_from="2026-03-11")) == {o2, o3}
    assert ids(get(date_to="2026-03-10T12:00:00Z")) == set()  # end is exclusive for a moment
    assert get(date_from="10.03.2026").status_code == 422


def test_payment_search_and_filters(client: TestClient, admin_h, market) -> None:
    p1, p2, p3 = (str(p.id) for p in market["payments"])  # type: ignore[attr-defined]
    o2 = str(market["orders"][1].id)  # type: ignore[index]

    def get(**params: str) -> object:
        return client.get(f"{ADMIN}/payments", params=params, headers=admin_h)

    assert ids(get(q="bbb222")) == {p2}
    assert ids(get(q="bob")) == {p2, p3}
    assert ids(get(q="Rock")) == {p2}
    assert ids(get(q=o2)) == {p2}  # by order id
    assert ids(get(q=p3)) == {p3}
    assert ids(get(payment_status="failed")) == {p3}
    assert ids(get(q="example.com", payment_status="succeeded")) == {p1, p2}
    assert ids(get(date_from="2026-03-15", date_to="2026-03-19")) == {p2, p3}


# ------------------------------------------------------------------ monitoring lists


def _case(
    db: Session, user, event, category: SupportCategory, status: SupportStatus, subject: str = "?"
) -> SupportCase:
    case = SupportCase(
        opened_by_user_id=user.id,
        kind=SupportCaseKind.ATTENDEE,
        category=category,
        subject=subject,
        status=status,
        event_id=event.id if event else None,
    )
    db.add(case)
    db.flush()
    return case


def test_reported_events(client: TestClient, db: Session, admin_h) -> None:
    org = make_organizer(db, display_name="Aru Events")
    loud = make_event(db, org, title="Loud Party")
    calm = make_event(db, org, title="Calm Concert")
    quiet = make_event(db, title="Quiet Evening")
    user = make_user(db)
    for _ in range(2):
        _case(db, user, loud, SupportCategory.EVENT_INFO, SupportStatus.OPEN)
    _case(db, user, loud, SupportCategory.SEATING, SupportStatus.RESOLVED)
    _case(db, user, calm, SupportCategory.TECHNICAL, SupportStatus.IN_PROGRESS)
    _case(db, user, quiet, SupportCategory.CHECKIN, SupportStatus.RESOLVED)
    _case(db, user, None, SupportCategory.ACCOUNT, SupportStatus.OPEN)

    page = client.get(f"{ADMIN}/reported-events", headers=admin_h).json()
    rows = [(r["title"], r["open_cases"], r["total_cases"]) for r in page["items"]]
    assert rows == [("Loud Party", 2, 3), ("Calm Concert", 1, 1)]
    assert page["items"][0]["organizer_name"] == "Aru Events"
    assert page["items"][0]["suspended"] is False

    everything = client.get(
        f"{ADMIN}/reported-events", params={"only_open": False}, headers=admin_h
    ).json()
    assert everything["total"] == 3
    assert (
        client.get(f"{ADMIN}/reported-events", params={"q": "aru"}, headers=admin_h).json()["total"]
        == 2
    )


def test_disputes_queue(client: TestClient, db: Session, admin_h) -> None:
    ev = make_event(db, title="Jazz Night")
    payer = make_user(db, full_name="Dana Payer")
    refund = _case(db, payer, ev, SupportCategory.REFUND, SupportStatus.OPEN, "Money back")
    payment = _case(db, payer, None, SupportCategory.PAYMENT, SupportStatus.RESOLVED, "Charged")
    _case(db, payer, ev, SupportCategory.SEATING, SupportStatus.OPEN, "Wrong seat")

    page = client.get(f"{ADMIN}/disputes", headers=admin_h).json()
    assert ids_of(page) == {str(refund.id), str(payment.id)}
    item = next(i for i in page["items"] if i["id"] == str(refund.id))
    assert (item["requester_name"], item["event_title"]) == ("Dana Payer", "Jazz Night")

    open_only = client.get(f"{ADMIN}/disputes", params={"case_status": "open"}, headers=admin_h)
    assert ids_of(open_only.json()) == {str(refund.id)}
    by_event = client.get(f"{ADMIN}/disputes", params={"q": "jazz"}, headers=admin_h)
    assert ids_of(by_event.json()) == {str(refund.id)}


def ids_of(page: dict[str, object]) -> set[str]:
    return {i["id"] for i in page["items"]}  # type: ignore[attr-defined]


def test_promo_activity(client: TestClient, db: Session, admin_h) -> None:
    ev = make_event(db, title="Promo Gala", paid_sales_active=True)
    tt = make_ticket_type(db, ev, price_minor=1_000_000)
    make_campaign(db, ev, code="GALA10", value=10)
    buyer = make_user(db, email="promo-buyer@example.com")
    resp = post_order(client, buyer, order_body(ev.id, (tt, 2), promo_code="GALA10"))
    assert resp.status_code == 201, resp.text
    order_id = resp.json()["id"]

    page = client.get(f"{ADMIN}/promo-activity", headers=admin_h).json()
    assert page["total"] == 1
    row = page["items"][0]
    assert (row["code"], row["campaign_name"], row["event_title"]) == (
        "GALA10",
        "Campaign GALA10",
        "Promo Gala",
    )
    assert (row["order_id"], row["order_status"], row["buyer_email"]) == (
        order_id,
        "pending",
        "promo-buyer@example.com",
    )
    assert row["discount_minor"] == 200_000 and row["released"] is False

    client.post(f"/api/v1/orders/{order_id}/cancel", headers=auth_headers(buyer))
    row = client.get(f"{ADMIN}/promo-activity", params={"q": "gala"}, headers=admin_h).json()[
        "items"
    ][0]
    assert row["released"] is True and row["order_status"] == "cancelled"
    assert (
        client.get(f"{ADMIN}/promo-activity", params={"q": "nope"}, headers=admin_h).json()["total"]
        == 0
    )


# ------------------------------------------------------------------ settings & reports


def test_all_platform_settings_are_editable_and_used(
    client: TestClient, db: Session, admin_h
) -> None:
    current = client.get(f"{ADMIN}/settings", headers=admin_h).json()
    assert set(current) == {
        "activation_fee_minor",
        "reservation_ttl_minutes",
        "seat_hold_ttl_minutes",
        "max_seat_holds_per_user",
        "paid_terms_version",
        "currency",
    }

    resp = client.patch(
        f"{ADMIN}/settings",
        json={"reservation_ttl_minutes": 25, "max_seat_holds_per_user": 4},
        headers=admin_h,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["reservation_ttl_minutes"], body["max_seat_holds_per_user"]) == (25, 4)
    assert body["activation_fee_minor"] == current["activation_fee_minor"]  # untouched
    changed = db.scalars(
        select(AuditLog.metadata_).where(AuditLog.action_type == "platform.setting_updated")
    ).all()
    assert sorted(m["key"] for m in changed) == [
        "max_seat_holds_per_user",
        "reservation_ttl_minutes",
    ]

    # the new reservation time applies to the next order
    ev = make_event(db, paid_sales_active=True)
    tt = make_ticket_type(db, ev)
    order = post_order(client, make_user(db), order_body(ev.id, (tt, 1))).json()
    left = datetime.fromisoformat(order["reserved_until"]) - datetime.now(UTC)
    assert timedelta(minutes=24) < left <= timedelta(minutes=25)

    # a new terms version un-accepts the terms for organizers
    org_user = make_user(db, roles=(Role.ORGANIZER,))
    make_organizer(db, org_user, paid_terms_version=platform_settings.paid_terms_version(db))
    checklist = "/api/v1/organizers/me/activation-checklist"
    assert client.get(checklist, headers=auth_headers(org_user)).json()["terms_accepted"]
    client.patch(f"{ADMIN}/settings", json={"paid_terms_version": "2027-02"}, headers=admin_h)
    after = client.get(checklist, headers=auth_headers(org_user)).json()
    assert (after["terms_accepted"], after["terms_version"]) == (False, "2027-02")

    for bad in (
        {"reservation_ttl_minutes": 0},
        {"max_seat_holds_per_user": 500},
        {"paid_terms_version": "v 2"},
    ):
        assert client.patch(f"{ADMIN}/settings", json=bad, headers=admin_h).status_code == 422


def test_reports_respect_dates(client: TestClient, admin_h, market) -> None:
    o1, o2, o3 = (str(o.id) for o in market["orders"])  # type: ignore[attr-defined]

    def report(name: str, **params: str) -> set[str]:
        resp = client.get(f"{ADMIN}/reports/{name}.csv", params=params, headers=admin_h)
        assert resp.status_code == 200
        return {row[0] for row in list(csv.reader(io.StringIO(resp.text)))[1:]}

    assert {o1, o2, o3} <= report("orders")
    assert report("orders", date_from="2026-03-12", date_to="2026-03-15") == {o2}
    payments = report("payments", date_to="2026-03-10")
    assert payments == {str(market["payments"][0].id)}  # type: ignore[index]
    soon = (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
    jazz = str(market["jazz"].id)  # type: ignore[attr-defined]
    assert jazz in report("events", date_from=soon, date_to=soon)
    assert jazz not in report("events", date_to="2026-01-01")


def test_new_admin_lists_are_admin_only(client: TestClient, db: Session) -> None:
    h = auth_headers(make_user(db, roles=(Role.ORGANIZER,)))
    for path in ("/reported-events", "/disputes", "/promo-activity"):
        assert client.get(f"{ADMIN}{path}", headers=h).status_code == 403
