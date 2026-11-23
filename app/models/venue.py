import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class Venue(UUIDPkMixin, TimestampMixin, Base):
    """Predefined venue layout (one for the MVP, no visual editor)."""

    __tablename__ = "venue"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(100))

    sections: Mapped[list["VenueSection"]] = relationship(back_populates="venue")


class VenueSection(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "venue_section"
    __table_args__ = (UniqueConstraint("venue_id", "name"),)

    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venue.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    venue: Mapped[Venue] = relationship(back_populates="sections")
    rows: Mapped[list["VenueRow"]] = relationship(back_populates="section")


class VenueRow(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "venue_row"
    __table_args__ = (UniqueConstraint("section_id", "label"),)

    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venue_section.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    section: Mapped[VenueSection] = relationship(back_populates="rows")
    seats: Mapped[list["Seat"]] = relationship(back_populates="row")


class Seat(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "seat"
    __table_args__ = (UniqueConstraint("row_id", "seat_number"),)

    row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venue_row.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seat_number: Mapped[str] = mapped_column(String(10), nullable=False)
    price_category: Mapped[str | None] = mapped_column(String(50))
    is_accessible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    row: Mapped[VenueRow] = relationship(back_populates="seats")
