import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip DB tests unless a database is explicitly available (CI sets RUN_DB_TESTS=1)."""
    if os.getenv("RUN_DB_TESTS") == "1":
        return
    skip_db = pytest.mark.skip(reason="needs PostgreSQL: set RUN_DB_TESTS=1")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)
