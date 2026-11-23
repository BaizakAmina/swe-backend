"""Shared test fixtures.

- Tests that need PostgreSQL are marked ``@pytest.mark.db`` and run only with ``RUN_DB_TESTS=1``
  (CI sets it). Locally: start ``docker compose up db`` and export ``RUN_DB_TESTS=1``.
- ``db`` gives a Session wrapped in an outer transaction that is rolled back after each test,
  so tests never see each other's data. Service code may call ``commit()`` freely: commits go
  to a SAVEPOINT inside the outer transaction.
- ``client`` is a TestClient whose ``get_db`` dependency uses that same session.
"""

import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.main import create_app

RUN_DB_TESTS = os.getenv("RUN_DB_TESTS") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if RUN_DB_TESTS:
        return
    skip_db = pytest.mark.skip(reason="needs PostgreSQL: set RUN_DB_TESTS=1")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")
    eng = create_engine(get_settings().database_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()


@pytest.fixture
def db(connection: Connection) -> Iterator[Session]:
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    yield session
    session.close()


@pytest.fixture(autouse=True)
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Emails go to an in-memory list: [{"kind", "to", "subject", "text", **context}]."""
    from app.services import notifications

    monkeypatch.setattr(get_settings(), "email_backend", "memory")
    notifications.MEMORY_OUTBOX.clear()
    return notifications.MEMORY_OUTBOX


@pytest.fixture
def app_client() -> TestClient:
    """Client without a database (health checks, error formatting)."""
    return TestClient(create_app())


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """Client whose requests share the per-test rolled-back session."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
