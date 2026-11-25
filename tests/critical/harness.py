"""Running N real transactions at the same moment.

Each worker gets its **own Session on its own Postgres connection** (checked by comparing
``pg_backend_pid()``), all of them wait on a barrier, then run the same service call. Results
are normalised so a test can assert on a ``Counter``:

- the function's return value (``"ok"`` by default),
- ``AppError`` → its ``code``,
- anything else → ``"crash: <repr>"`` (which no test expects, so it shows up in the diff).

The data a test works on must be **committed** first (other connections can't see the test's
rolled-back transaction); ``tests.committed_data.cleanup`` removes it afterwards.
"""

import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.errors import AppError

Work = Callable[[Session, int], Any]


@dataclass(frozen=True)
class Outcome:
    results: list[Any]
    backend_pids: set[int]

    @property
    def counts(self) -> Counter[Any]:
        return Counter(self.results)


def run_parallel(engine: Engine, n: int, work: Work, timeout: float = 60) -> Outcome:
    """Run ``work(session, i)`` for ``i in range(n)`` in ``n`` threads released together."""
    barrier = threading.Barrier(n, timeout=timeout)
    pids: list[int] = []
    lock = threading.Lock()

    def worker(i: int) -> Any:
        with Session(engine) as db:
            # checks out this worker's connection and keeps it (READ COMMITTED: every
            # statement still sees the latest committed data, no stale snapshot)
            pid = int(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
            with lock:
                pids.append(pid)
            barrier.wait()
            try:
                result = work(db, i)
                return "ok" if result is None else result
            except AppError as exc:
                db.rollback()
                return exc.code
            except Exception as exc:  # noqa: BLE001 — reported, never expected
                db.rollback()
                return f"crash: {exc!r}"

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(worker, range(n), timeout=timeout))
    outcome = Outcome(results=results, backend_pids=set(pids))
    assert len(outcome.backend_pids) == n, "workers must use separate database connections"
    return outcome
