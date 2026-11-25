"""API conventions shared by every module.

- JSON is snake_case; ids are UUID strings.
- Datetimes are ISO 8601 with offset; stored in UTC. Events also carry an IANA ``timezone``.
- Money is always an integer amount in **minor units** + ``currency`` (KZT). Never floats.
- Lists: ``?limit=&offset=`` → ``Page[T]``.
- Errors: ``{"error": {"code": "...", "message": "...", "details": ...}}``.
- Payments are simulated: payment/refund objects always expose ``is_simulated: true``.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Generic, TypeVar

from fastapi import Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


MAX_PAGE = 100
# long lists a client keeps in full (door list, activity log): bigger pages allowed
MAX_WIDE_PAGE = 200


class Pagination(BaseModel):
    limit: int = Field(default=20, ge=1, le=MAX_WIDE_PAGE)
    offset: int = Field(default=0, ge=0)


def pagination(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


def wide_pagination(
    limit: Annotated[
        int, Query(ge=1, description=f"Up to {MAX_WIDE_PAGE}; larger values are reduced to it")
    ] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    """For lists clients fetch in full; ``Page.limit`` shows the limit actually applied."""
    return Pagination(limit=min(limit, MAX_WIDE_PAGE), offset=offset)


class Money(BaseModel):
    amount_minor: int
    currency: str = "KZT"


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Period(BaseModel):
    """``[start, end)`` in UTC; either side open when ``None``."""

    start: datetime | None = None
    end: datetime | None = None


def _parse_bound(name: str, raw: str | None, *, end: bool) -> datetime | None:
    if raw is None or not raw.strip():
        return None
    raw = raw.strip()
    try:
        if len(raw) == 10:  # a calendar day: from its start / through its end
            day = date.fromisoformat(raw)
            return datetime.combine(day + timedelta(days=1) if end else day, time(), UTC)
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", name),
                    "msg": "expected YYYY-MM-DD or an ISO 8601 datetime",
                    "input": raw,
                }
            ]
        ) from exc
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def period(
    date_from: Annotated[
        str | None, Query(description="From this day (YYYY-MM-DD) or moment (ISO 8601)")
    ] = None,
    date_to: Annotated[
        str | None, Query(description="Through this day (YYYY-MM-DD) or until this moment")
    ] = None,
) -> Period:
    return Period(
        start=_parse_bound("date_from", date_from, end=False),
        end=_parse_bound("date_to", date_to, end=True),
    )
