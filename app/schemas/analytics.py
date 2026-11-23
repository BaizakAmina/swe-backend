import uuid
from datetime import datetime

from pydantic import BaseModel


class CapacityBlock(BaseModel):
    capacity: int
    sold: int
    reserved: int
    remaining: int
    sold_percent: float


class RevenueBlock(BaseModel):
    """KZT minor units. All payments are simulated."""

    currency: str = "KZT"
    gross_minor: int
    discounts_minor: int
    refunds_minor: int
    net_minor: int
    is_simulated: bool = True


class SalesPoint(BaseModel):
    bucket_start: datetime
    tickets: int
    revenue_minor: int


class TicketTypeBreakdown(BaseModel):
    ticket_type_id: uuid.UUID
    name: str
    sold: int
    revenue_minor: int
    refunded: int
    checked_in: int


class CampaignBreakdown(BaseModel):
    campaign_id: uuid.UUID
    name: str
    code: str
    redemptions: int
    discount_minor: int
    revenue_minor: int


class AttendanceBlock(BaseModel):
    issued: int
    checked_in: int
    checked_in_percent: float
    no_show_percent: float | None


class EventAnalytics(BaseModel):
    event_id: uuid.UUID
    capacity: CapacityBlock
    revenue: RevenueBlock
    sales_over_time: list[SalesPoint]
    by_ticket_type: list[TicketTypeBreakdown]
    by_campaign: list[CampaignBreakdown]
    attendance: AttendanceBlock


class OrganizerAnalyticsSummary(BaseModel):
    events_total: int
    events_upcoming: int
    tickets_sold: int
    revenue: RevenueBlock
