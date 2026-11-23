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

Tests marked `db` need PostgreSQL and run only with `RUN_DB_TESTS=1`.

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
