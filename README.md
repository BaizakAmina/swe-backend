# BiletFlow — Backend

[![CI](https://github.com/BaizakAmina/swe-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/BaizakAmina/swe-backend/actions/workflows/ci.yml)

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

Tests marked `db` need PostgreSQL and run only with `RUN_DB_TESTS=1`:

```bash
DB_PORT=5433 docker compose up -d db   # use 5433 if a local Postgres already takes 5432
export DATABASE_URL=postgresql+psycopg://biletflow:biletflow@localhost:5433/biletflow
RUN_DB_TESTS=1 pytest
```

## Data model notes

- Money is `BIGINT` in KZT minor units (`*_minor`), never float.
- Enums are `VARCHAR` + `CHECK` (no native PG enums).
- Inventory, promo limits, one redemption per order, one active seat hold — enforced by DB
  constraints, not only in code.
- Active seat hold = `released_at IS NULL` (partial unique index). `now()` can't be used in an
  index predicate, so expired holds must be released by the sweep / before inserting a new hold.
- `audit_log` is append-only (trigger blocks UPDATE/DELETE).
- Tables `user_account` and `orders` avoid the reserved words `user` / `order`.

Errors are returned as `{"error": {"code": "...", "message": "...", "details": ...}}`.

## Docker (full stack)

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| API / Swagger | http://localhost:8000/docs |
| MailHog (emails) | http://localhost:8025 |
| MinIO console | http://localhost:9001 (minioadmin / minioadmin) |
| PostgreSQL | localhost:5432 (biletflow / biletflow) |

Migrations run automatically (`migrate` service) before the API starts. `.env` is optional for compose.

Backup / restore:

```bash
docker compose --profile tools run --rm backup                       # -> backups/*.dump
docker compose exec -T db pg_restore -U biletflow -d biletflow --clean < backups/<file>.dump
```
