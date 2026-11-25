"""Background sweep of expired order reservations (BE-14).

Runs inside every API process (FastAPI lifespan) every ``order_sweep_interval_seconds``;
``0`` turns it off (tests). Several processes may sweep at once: the sweep uses SKIP LOCKED.
Single orders are also expired lazily when they are accessed (``services.orders``).
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.services.orders import expire_stale_orders
from app.services.seating import expire_stale_holds

logger = logging.getLogger("biletflow.order_sweeper")


def sweep_once() -> int:
    """Expired order reservations, then expired free seat holds. Returns how many were freed."""
    with SessionLocal() as db:
        return expire_stale_orders(db) + expire_stale_holds(db)


async def run_forever(interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            expired = await asyncio.to_thread(sweep_once)
        except Exception:  # noqa: BLE001 — keep sweeping; the next run retries
            logger.exception("order sweep failed")
            continue
        if expired:
            logger.info("released %d stale reservation(s) / seat hold(s)", expired)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    interval = get_settings().order_sweep_interval_seconds
    task = asyncio.create_task(run_forever(interval)) if interval > 0 else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
