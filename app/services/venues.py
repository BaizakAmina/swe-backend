"""Predefined venue layouts (read-only for organizers; created by the seed / admins)."""

import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.venue import Seat, Venue, VenueRow, VenueSection
from app.schemas.common import Page, Pagination
from app.schemas.venues import (
    PriceCategoryCount,
    VenueRead,
    VenueRowRead,
    VenueSeatRead,
    VenueSectionRead,
    VenueSummary,
)

_NO_CATEGORY = "standard"
# Seats created in one transaction share created_at: order numerically by seat number ("2" < "10").
SEAT_ORDER = (func.length(Seat.seat_number), Seat.seat_number, Seat.id)


def _summaries(db: Session, venues: list[Venue]) -> dict[uuid.UUID, VenueSummary]:
    """Section count, seat totals and seats per price category for each venue — two queries."""
    ids = [v.id for v in venues]
    sections: dict[uuid.UUID, int] = defaultdict(int)
    seats_total: dict[uuid.UUID, int] = defaultdict(int)
    accessible: dict[uuid.UUID, int] = defaultdict(int)
    categories: dict[uuid.UUID, list[PriceCategoryCount]] = defaultdict(list)
    if ids:
        for vid, count in db.execute(
            select(VenueSection.venue_id, func.count())
            .where(VenueSection.venue_id.in_(ids))
            .group_by(VenueSection.venue_id)
        ).tuples():
            sections[vid] = count
        category = func.coalesce(Seat.price_category, _NO_CATEGORY)
        for vid, name, seats, acc in db.execute(
            select(
                VenueSection.venue_id,
                category,
                func.count(),
                func.count().filter(Seat.is_accessible.is_(True)),
            )
            .join(VenueRow, VenueRow.id == Seat.row_id)
            .join(VenueSection, VenueSection.id == VenueRow.section_id)
            .where(VenueSection.venue_id.in_(ids))
            .group_by(VenueSection.venue_id, category)
            .order_by(category)
        ).tuples():
            categories[vid].append(PriceCategoryCount(name=name, seats=seats))
            seats_total[vid] += seats
            accessible[vid] += acc
    return {
        v.id: VenueSummary(
            id=v.id,
            name=v.name,
            address=v.address,
            city=v.city,
            sections=sections[v.id],
            seats_total=seats_total[v.id],
            accessible_seats=accessible[v.id],
            price_categories=categories[v.id],
        )
        for v in venues
    }


def list_venues(db: Session, page: Pagination, q: str | None = None) -> Page[VenueSummary]:
    stmt = select(Venue)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(Venue.name.ilike(pattern) | Venue.city.ilike(pattern))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    venues = list(
        db.scalars(stmt.order_by(Venue.name, Venue.id).limit(page.limit).offset(page.offset))
    )
    summaries = _summaries(db, venues)
    items = [summaries[v.id] for v in venues]
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


def get_venue(db: Session, venue_id: uuid.UUID) -> VenueRead:
    venue = db.get(Venue, venue_id)
    if venue is None:
        raise NotFoundError("venue")

    sections: dict[uuid.UUID, VenueSectionRead] = {}
    rows: dict[uuid.UUID, VenueRowRead] = {}
    for section in db.scalars(
        select(VenueSection)
        .where(VenueSection.venue_id == venue.id)
        .order_by(VenueSection.sort_order, VenueSection.name)
    ):
        sections[section.id] = VenueSectionRead(id=section.id, name=section.name, rows=[])
    for row in db.scalars(
        select(VenueRow)
        .where(VenueRow.section_id.in_(sections))
        .order_by(VenueRow.sort_order, VenueRow.label)
    ):
        rows[row.id] = VenueRowRead(id=row.id, label=row.label, seats=[])
        sections[row.section_id].rows.append(rows[row.id])
    for seat in db.scalars(select(Seat).where(Seat.row_id.in_(rows)).order_by(*SEAT_ORDER)):
        rows[seat.row_id].seats.append(
            VenueSeatRead(
                id=seat.id,
                seat_number=seat.seat_number,
                price_category=seat.price_category,
                is_accessible=seat.is_accessible,
            )
        )
    summary = _summaries(db, [venue])[venue.id]
    return VenueRead(**summary.model_dump(), layout=list(sections.values()))
