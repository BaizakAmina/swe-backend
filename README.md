# BiletFlow — Backend

FastAPI · PostgreSQL 16 · SQLAlchemy 2.0 + Alembic.

## Local development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Health: `GET /health`, `GET /health/db`

## Checks

```bash
ruff check . && black --check . && mypy app && pytest
```

Tests marked `db` need PostgreSQL and run only with `RUN_DB_TESTS=1`.

Errors are returned as `{"error": {"code": "...", "message": "...", "details": ...}}`.
