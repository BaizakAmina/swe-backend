from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.core.config import get_settings

MAX_WORKERS = 20


@pytest.fixture
def parallel_engine(engine: Engine) -> Iterator[Engine]:
    """A pool with a connection for every worker (``engine`` runs the migrations first)."""
    eng = create_engine(
        get_settings().database_url, pool_size=MAX_WORKERS + 2, max_overflow=0, pool_timeout=30
    )
    yield eng
    eng.dispose()
