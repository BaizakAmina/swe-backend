from typing import NoReturn

from fastapi import status

from app.core.errors import AppError


def not_implemented(issue: str) -> NoReturn:
    """Placeholder for contract-first endpoints; replaced by the real handler in ``issue``."""
    raise AppError(
        "not_implemented",
        f"Endpoint is part of the API contract but not implemented yet ({issue})",
        status.HTTP_501_NOT_IMPLEMENTED,
    )
