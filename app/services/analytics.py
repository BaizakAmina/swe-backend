"""Organizer analytics (SRS §4.15). Read-only aggregations: no row locks, never blocks checkout.

Definitions (all money in KZT minor units, all simulated):

- **gross**     — list price of tickets in orders that were paid (``paid_at`` set)
- **discounts** — promo discounts on those orders
- **refunds**   — succeeded refunds for those orders
- **net**       — gross − discounts − refunds
- **sold**      — ``ticket_type.quantity_sold`` (authoritative counter kept by orders)

With a ``ticket_type_id`` filter an order's discount and refunds are split across its items in
proportion to their price.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Order,
    OrderItem,
    Payment,
    PromoCode,
    PromoRedemption,
    PromotionalCampaign,
    Refund,
    Ticket,
    TicketType,
    User,
)
from app.models.enums import EventLifecycle, PaymentPurpose, RefundStatus, TicketStatus
from app.schemas.analytics import (
    AttendanceBlock,
    CampaignBreakdown,
    CapacityBlock,
    EventAnalytics,
    OrganizerAnalyticsSummary,
    RevenueBlock,
    SalesPoint,
    TicketTypeBreakdown,
)
from app.services import events as events_svc
from app.services.organizers import get_profile


@dataclass
class _Line:
    order_id: uuid.UUID
    ticket_type_id: uuid.UUID
    paid_at: datetime
    gross: int
    discount: int
    refund: int
    promo_code_id: uuid.UUID | None

    @property
    def net(self) -> int:
        return self.gross - self.discount - self.refund


def _pct(part: int, whole: int) -> float:
    return round(part * 100 / whole, 1) if whole else 0.0


def _lines(
    db: Session,
    event_ids: list[uuid.UUID],
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[_Line]:
    """One line per order item of paid orders, with discount/refund shares allocated."""
    stmt = (
        select(
            Order.id,
            Order.paid_at,
            Order.subtotal_minor,
            Order.discount_minor,
            Order.promo_code_id,
            OrderItem.ticket_type_id,
            OrderItem.unit_price_minor,
        )
        .join(OrderItem, OrderItem.order_id == Order.id)
        .where(Order.event_id.in_(event_ids), Order.paid_at.is_not(None))
    )
    if date_from:
        stmt = stmt.where(Order.paid_at >= date_from)
    if date_to:
        stmt = stmt.where(Order.paid_at <= date_to)
    rows = db.execute(stmt).all()
    if not rows:
        return []

    order_ids = {r[0] for r in rows}
    refunds = dict(
        db.execute(
            select(Payment.order_id, func.sum(Refund.amount_minor))
            .join(Refund, Refund.payment_id == Payment.id)
            .where(
                Payment.order_id.in_(order_ids),
                Payment.purpose == PaymentPurpose.TICKET_PURCHASE,
                Refund.status == RefundStatus.SUCCEEDED,
            )
            .group_by(Payment.order_id)
        )
        .tuples()
        .all()
    )

    lines = []
    for order_id, paid_at, subtotal, discount, promo_id, tt_id, price in rows:
        share = price / subtotal if subtotal else 0
        order_net = subtotal - discount
        line_discount = round(discount * share)
        line_refund = round(int(refunds.get(order_id) or 0) * share) if order_net else 0
        lines.append(_Line(order_id, tt_id, paid_at, price, line_discount, line_refund, promo_id))
    return lines


def _revenue(lines: list[_Line]) -> RevenueBlock:
    gross = sum(x.gross for x in lines)
    discounts = sum(x.discount for x in lines)
    refunds = sum(x.refund for x in lines)
    return RevenueBlock(
        gross_minor=gross,
        discounts_minor=discounts,
        refunds_minor=refunds,
        net_minor=gross - discounts - refunds,
    )


def _sales_over_time(lines: list[_Line], tz: str, bucket: str) -> list[SalesPoint]:
    zone = ZoneInfo(tz)
    buckets: dict[datetime, list[int]] = defaultdict(lambda: [0, 0])
    for x in lines:
        local = x.paid_at.astimezone(zone)
        start = local.replace(minute=0, second=0, microsecond=0)
        if bucket == "day":
            start = start.replace(hour=0)
        buckets[start][0] += 1
        buckets[start][1] += x.gross - x.discount
    return [
        SalesPoint(bucket_start=k, tickets=v[0], revenue_minor=v[1])
        for k, v in sorted(buckets.items())
    ]


def event_analytics(
    db: Session,
    event: Event,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    ticket_type_id: uuid.UUID | None = None,
    bucket: str = "day",
) -> EventAnalytics:
    types = list(
        db.scalars(
            select(TicketType)
            .where(TicketType.event_id == event.id)
            .order_by(TicketType.created_at)
        )
    )
    if ticket_type_id is not None:
        types = [t for t in types if t.id == ticket_type_id]
    type_ids = {t.id for t in types}

    lines = [x for x in _lines(db, [event.id], date_from, date_to) if x.ticket_type_id in type_ids]

    sold = sum(t.quantity_sold for t in types)
    reserved = sum(t.quantity_reserved for t in types)
    capacity = event.capacity if ticket_type_id is None else sum(t.quantity_total for t in types)

    status_counts: dict[tuple[uuid.UUID, TicketStatus], int] = {
        (tt, st): n
        for tt, st, n in db.execute(
            select(OrderItem.ticket_type_id, Ticket.status, func.count())
            .join(OrderItem, OrderItem.id == Ticket.order_item_id)
            .where(Ticket.event_id == event.id)
            .group_by(OrderItem.ticket_type_id, Ticket.status)
        )
        .tuples()
        .all()
    }

    def count(tt_id: uuid.UUID, *statuses: TicketStatus) -> int:
        return sum(status_counts.get((tt_id, s), 0) for s in statuses)

    by_type = []
    for t in types:
        t_lines = [x for x in lines if x.ticket_type_id == t.id]
        by_type.append(
            TicketTypeBreakdown(
                ticket_type_id=t.id,
                name=t.name,
                sold=t.quantity_sold,
                revenue_minor=sum(x.net for x in t_lines),
                refunded=count(t.id, TicketStatus.REFUNDED),
                checked_in=count(t.id, TicketStatus.CHECKED_IN),
            )
        )

    by_campaign = _campaigns(db, event, lines)

    issued = sum(count(t.id, TicketStatus.VALID, TicketStatus.CHECKED_IN) for t in types)
    checked = sum(count(t.id, TicketStatus.CHECKED_IN) for t in types)
    finished = events_svc.compute_lifecycle(event) == EventLifecycle.COMPLETED
    attendance = AttendanceBlock(
        issued=issued,
        checked_in=checked,
        checked_in_percent=_pct(checked, issued),
        no_show_percent=_pct(issued - checked, issued) if finished and issued else None,
    )

    return EventAnalytics(
        event_id=event.id,
        capacity=CapacityBlock(
            capacity=capacity,
            sold=sold,
            reserved=reserved,
            remaining=max(capacity - sold - reserved, 0),
            sold_percent=_pct(sold, capacity),
        ),
        revenue=_revenue(lines),
        sales_over_time=_sales_over_time(lines, event.timezone, bucket),
        by_ticket_type=by_type,
        by_campaign=by_campaign,
        attendance=attendance,
    )


def _campaigns(db: Session, event: Event, lines: list[_Line]) -> list[CampaignBreakdown]:
    rows = db.execute(
        select(PromotionalCampaign.id, PromotionalCampaign.name, PromoCode.id, PromoCode.code)
        .join(PromoCode, PromoCode.campaign_id == PromotionalCampaign.id)
        .where(PromotionalCampaign.event_id == event.id)
        .order_by(PromotionalCampaign.created_at)
    ).all()
    if not rows:
        return []
    redemptions = dict(
        db.execute(
            select(PromoRedemption.promo_code_id, func.count())
            .where(
                PromoRedemption.promo_code_id.in_([r[2] for r in rows]),
                PromoRedemption.released_at.is_(None),
            )
            .group_by(PromoRedemption.promo_code_id)
        )
        .tuples()
        .all()
    )
    result = []
    for campaign_id, name, code_id, code in rows:
        c_lines = [x for x in lines if x.promo_code_id == code_id]
        result.append(
            CampaignBreakdown(
                campaign_id=campaign_id,
                name=name,
                code=code,
                redemptions=int(redemptions.get(code_id, 0)),
                discount_minor=sum(x.discount for x in c_lines),
                revenue_minor=sum(x.net for x in c_lines),
            )
        )
    return result


def organizer_summary(db: Session, user: User) -> OrganizerAnalyticsSummary:
    profile = get_profile(db, user)
    if profile is None:
        return OrganizerAnalyticsSummary(
            events_total=0,
            events_upcoming=0,
            tickets_sold=0,
            revenue=RevenueBlock(gross_minor=0, discounts_minor=0, refunds_minor=0, net_minor=0),
        )
    event_ids = list(db.scalars(select(Event.id).where(Event.organizer_id == profile.id)))
    upcoming = db.scalar(
        select(func.count()).where(
            Event.organizer_id == profile.id,
            events_svc.lifecycle_clause(EventLifecycle.UPCOMING),
        )
    )
    sold = db.scalar(
        select(func.coalesce(func.sum(TicketType.quantity_sold), 0)).where(
            TicketType.event_id.in_(event_ids)
        )
    )
    return OrganizerAnalyticsSummary(
        events_total=len(event_ids),
        events_upcoming=int(upcoming or 0),
        tickets_sold=int(sold or 0),
        revenue=_revenue(_lines(db, event_ids, None, None) if event_ids else []),
    )
