"""Base address for links that API responses hand to clients.

``API_BASE_URL`` is what the server calls itself (often ``http://localhost:8000`` inside docker),
which is useless on a phone. Links in responses use ``PUBLIC_API_URL`` when it is set, otherwise
the address the client actually used for the current request.
"""

from contextvars import ContextVar

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings

_request_base: ContextVar[str | None] = ContextVar("request_base", default=None)


def public_api_base() -> str:
    """Scheme + host (+ root path) of this API, without a trailing slash."""
    configured = get_settings().public_api_url.strip()
    if configured:
        return configured.rstrip("/")
    return _request_base.get() or get_settings().api_base_url.rstrip("/")


def public_api_url(path: str) -> str:
    """Absolute URL of ``path`` under the API prefix, e.g. ``/files/covers/…``."""
    return f"{public_api_base()}{get_settings().api_prefix}{path}"


class RequestBaseMiddleware:
    """Remembers the base URL of the request being served (for :func:`public_api_base`)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _request_base.set(str(Request(scope).base_url).rstrip("/"))
        try:
            await self.app(scope, receive, send)
        finally:
            _request_base.reset(token)
