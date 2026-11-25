"""Assigned seating: seat map, seat holds, hold → order (SRS §4.3.1, BACKEND_ARCHITECTURE.md §5.1).

**A seat can't be sold twice** — guaranteed by the database: the partial unique index
``uq_seat_hold_active (seat_id, event_id) WHERE released_at IS NULL`` allows one *active* hold
per seat and event. The hold is the seat's occupancy record for its whole life:

    held (order_id NULL, expires_at = now + seat_hold_ttl)
      → in a pending order (order_id set, expires_at = order.reserved_until)
      → paid: stays active — the seat stays taken for as long as the sale stands
    released (released_at set) when: the buyer drops it, the free hold expires (sweep / before
    a new hold of the same seat), the pending order expires or is cancelled, the order is
    refunded or the registration cancelled (BE-24).

Holds that belong to an order are released only through the order's lifecycle
(``orders`` / ``refunds`` call :func:`release_for_orders`); the hold sweep only touches free
holds. Lock order: … ticket types → campaign → seat holds (after everything else).
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.models import Event, Order, Seat, SeatHold, User, Venue, VenueRow, VenueSection
from app.models.enums import EventStatus, OrderStatus, SeatingMode
from app.schemas.seating import RowRead, SeatMapRead, SeatRead, SectionRead


def _now() -> datetime:
    return datetime.now(UTC)


def _seat_rows(
    db: Session, venue_id: uuid.UUID, seat_ids: Sequence[uuid.UUID] | None = None
) -> list[tuple[Seat, VenueRow, VenueSection]]:
    stmt = (
        select(Seat, VenueRow, VenueSection)
        .join(VenueRow, VenueRow.id == Seat.row_id)
        .join(VenueSection, VenueSection.id == VenueRow.section_id)
        .where(VenueSection.venue_id == venue_id)
    )
    if seat_ids is not None:
        stmt = stmt.where(Seat.id.in_(seat_ids))
    stmt = stmt.order_by(VenueSection.sort_order, VenueRow.sort_order, Seat.created_at)
    return list(db.execute(stmt).tuples().all())


def _assigned_event(db: Session, event_id: uuid.UUID) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise NotFoundError("event")
    if event.seating_mode != SeatingMode.ASSIGNED or event.venue_id is None:
        raise ConflictError("not_assigned_seating", "This event has no seat map")
    return event


# ------------------------------------------------------------------ seat map


def seatmap(db: Session, event_id: uuid.UUID, user: User | None) -> SeatMapRead:
    """Every seat with ``available`` / ``held`` (someone else's) / ``mine`` / ``sold``."""
    # imported here: events → refunds → orders → seating would be circular at import time
    from app.services.events import get_for_viewer

    event = get_for_viewer(db, event_id, user)
    event = _assigned_event(db, event.id)
    assert event.venue_id is not None
    venue = db.get(Venue, event.venue_id)
    assert venue is not None

    now = _now()
    taken: dict[uuid.UUID, str] = {}
    for hold, order_status in db.execute(
        select(SeatHold, Order.status)
        .outerjoin(Order, Order.id == SeatHold.order_id)
        .where(SeatHold.event_id == event.id, SeatHold.released_at.is_(None))
    ).tuples():
        if hold.order_id is None and hold.expires_at <= now:
            continue  # expired, not swept yet → free
        if order_status == OrderStatus.PAID:
            taken[hold.seat_id] = "sold"
        elif user is not None and hold.held_by_user_id == user.id:
            taken[hold.seat_id] = "mine"
        else:
            taken[hold.seat_id] = "held"

    sections: dict[uuid.UUID, SectionRead] = {}
    rows: dict[uuid.UUID, RowRead] = {}
    for seat, row, section in _seat_rows(db, venue.id):
        if section.id not in sections:
            sections[section.id] = SectionRead(id=section.id, name=section.name, rows=[])
        if row.id not in rows:
            rows[row.id] = RowRead(id=row.id, label=row.label, seats=[])
            sections[section.id].rows.append(rows[row.id])
        rows[row.id].seats.append(
            SeatRead(
                id=seat.id,
                seat_number=seat.seat_number,
                price_category=seat.price_category,
                is_accessible=seat.is_accessible,
                status=taken.get(seat.id, "available"),
            )
        )
    return SeatMapRead(
        event_id=event.id,
        venue_id=venue.id,
        venue_name=venue.name,
        sections=list(sections.values()),
    )


# ------------------------------------------------------------------ holds


def _release_expired_free_holds(
    db: Session, event_id: uuid.UUID, seat_ids: Sequence[uuid.UUID], now: datetime
) -> None:
    db.execute(
        update(SeatHold)
        .where(
            SeatHold.event_id == event_id,
            SeatHold.seat_id.in_(seat_ids),
            SeatHold.released_at.is_(None),
            SeatHold.order_id.is_(None),
            SeatHold.expires_at <= now,
        )
        .values(released_at=now)
        .execution_options(synchronize_session=False)
    )


def _active_seat_ids(
    db: Session, event_id: uuid.UUID, seat_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(SeatHold.seat_id).where(
                SeatHold.event_id == event_id,
                SeatHold.seat_id.in_(seat_ids),
                SeatHold.released_at.is_(None),
            )
        )
    )


def hold(
    db: Session, user: User, event_id: uuid.UUID, seat_ids: Sequence[uuid.UUID]
) -> list[SeatHold]:
    """Hold seats for ``seat_hold_ttl_minutes``. 409 ``seat_unavailable`` lists taken seats."""
    settings = get_settings()
    event = _assigned_event(db, event_id)
    now = _now()
    if (
        event.status != EventStatus.PUBLISHED
        or event.suspended_at is not None
        or now >= event.ends_at
    ):
        raise ConflictError("event_not_on_sale", "Tickets for this event are not on sale")

    wanted = list(dict.fromkeys(seat_ids))
    assert event.venue_id is not None
    found = {seat.id for seat, _, _ in _seat_rows(db, event.venue_id, wanted)}
    unknown = [str(s) for s in wanted if s not in found]
    if unknown:
        raise AppError(
            "seat_not_found",
            "Some seats are not in this event's venue",
            status.HTTP_404_NOT_FOUND,
            {"seat_ids": unknown},
        )

    mine = db.scalars(
        select(SeatHold.id).where(
            SeatHold.event_id == event.id,
            SeatHold.held_by_user_id == user.id,
            SeatHold.order_id.is_(None),
            SeatHold.released_at.is_(None),
            SeatHold.expires_at > now,
        )
    ).all()
    if len(mine) + len(wanted) > settings.max_seat_holds_per_user:
        raise AppError(
            "seat_hold_limit",
            f"At most {settings.max_seat_holds_per_user} seats can be held at once",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    _release_expired_free_holds(db, event.id, wanted, now)
    expires_at = now + timedelta(minutes=settings.seat_hold_ttl_minutes)
    holds = [
        SeatHold(
            seat_id=seat_id,
            event_id=event.id,
            held_by_user_id=user.id,
            expires_at=expires_at,
        )
        for seat_id in wanted
    ]
    try:
        with db.begin_nested():  # a conflict rolls back only the inserts
            db.add_all(holds)
            db.flush()
    except IntegrityError as exc:
        if "uq_seat_hold_active" not in str(exc.orig):
            raise
        taken = _active_seat_ids(db, event.id, wanted)
        raise AppError(
            "seat_unavailable",
            "Some seats are already taken",
            status.HTTP_409_CONFLICT,
            {"seat_ids": sorted(str(s) for s in taken) or [str(s) for s in wanted]},
        ) from exc
    db.commit()
    return holds


def drop_hold(db: Session, user: User, hold_id: uuid.UUID) -> None:
    """The holder gives a seat back. Idempotent; holds inside an order go with the order."""
    held = db.scalar(select(SeatHold).where(SeatHold.id == hold_id).with_for_update())
    if held is None:
        raise NotFoundError("seat_hold")
    if held.held_by_user_id != user.id:
        raise ForbiddenError("Not your seat hold")
    if held.released_at is not None:
        return
    if held.order_id is not None:
        raise ConflictError(
            "seat_hold_in_order", "This seat is part of an order — cancel the order instead"
        )
    held.released_at = _now()
    db.commit()


# ------------------------------------------------------------------ orders (BE-14 / BE-24)


def lock_for_checkout(
    db: Session, user: User, event_id: uuid.UUID, hold_ids: Sequence[uuid.UUID], now: datetime
) -> list[SeatHold]:
    """Lock and check the buyer's holds before the order is written. In ``hold_ids`` order."""
    if len(set(hold_ids)) != len(hold_ids):
        raise AppError("duplicate_seat_hold", "Each seat hold may be used once", 422)
    rows = {
        h.id: h
        for h in db.scalars(
            select(SeatHold)
            .where(SeatHold.id.in_(hold_ids))
            .order_by(SeatHold.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    holds: list[SeatHold] = []
    for hold_id in hold_ids:
        held = rows.get(hold_id)
        if (
            held is None
            or held.event_id != event_id
            or held.held_by_user_id != user.id
            or held.order_id is not None
        ):
            raise AppError(
                "seat_hold_invalid",
                "A seat hold doesn't belong to you or to this event",
                status.HTTP_409_CONFLICT,
                {"seat_hold_id": str(hold_id)},
            )
        if held.released_at is not None or held.expires_at <= now:
            raise AppError(
                "seat_hold_expired",
                "A seat hold has expired — pick the seat again",
                status.HTTP_409_CONFLICT,
                {"seat_hold_id": str(hold_id)},
            )
        holds.append(held)
    return holds


def attach_to_order(db: Session, order: Order, holds: Sequence[SeatHold]) -> None:
    """The holds now belong to ``order`` and live as long as its reservation / sale."""
    for held in holds:
        held.order_id = order.id
        if order.reserved_until is not None:
            held.expires_at = order.reserved_until
    db.flush()


def seat_labels(
    db: Session, seat_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str, str]]:
    """seat id → (section, row, seat number) — the snapshot copied into ``order_item``."""
    return {
        seat.id: (section.name, row.label, seat.seat_number)
        for seat, row, section in db.execute(
            select(Seat, VenueRow, VenueSection)
            .join(VenueRow, VenueRow.id == Seat.row_id)
            .join(VenueSection, VenueSection.id == VenueRow.section_id)
            .where(Seat.id.in_(seat_ids))
        ).tuples()
    }


def release_for_orders(db: Session, orders: Sequence[Order]) -> None:
    """Free the seats of orders that expired, were cancelled or refunded."""
    order_ids = [o.id for o in orders]
    if not order_ids:
        return
    db.execute(
        update(SeatHold)
        .where(SeatHold.order_id.in_(order_ids), SeatHold.released_at.is_(None))
        .values(released_at=_now())
        .execution_options(synchronize_session=False)
    )


def expire_stale_holds(db: Session, *, now: datetime | None = None, batch_size: int = 500) -> int:
    """Sweep: release free holds past ``expires_at`` (holds in orders follow their order)."""
    now = now or _now()
    released = 0
    while True:
        ids = db.scalars(
            select(SeatHold.id)
            .where(
                SeatHold.released_at.is_(None),
                SeatHold.order_id.is_(None),
                SeatHold.expires_at <= now,
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        if not ids:
            break
        db.execute(
            update(SeatHold)
            .where(SeatHold.id.in_(ids))
            .values(released_at=now)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        released += len(ids)
        if len(ids) < batch_size:
            break
    return released
