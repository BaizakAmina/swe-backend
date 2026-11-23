"""API conventions shared by every module.

- JSON is snake_case; ids are UUID strings.
- Datetimes are ISO 8601 with offset; stored in UTC. Events also carry an IANA ``timezone``.
- Money is always an integer amount in **minor units** + ``currency`` (KZT). Never floats.
- Lists: ``?limit=&offset=`` → ``Page[T]``.
- Errors: ``{"error": {"code": "...", "message": "...", "details": ...}}``.
- Payments are simulated: payment/refund objects always expose ``is_simulated: true``.
"""

from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class Pagination(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


def pagination(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


class Money(BaseModel):
    amount_minor: int
    currency: str = "KZT"


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
