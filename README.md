# BiletFlow — Backend

[![CI](https://github.com/BaizakAmina/swe-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/BaizakAmina/swe-backend/actions/workflows/ci.yml)

Self-service event ticketing for Kazakhstan — backend API.
FastAPI · PostgreSQL 16 · SQLAlchemy 2.0 + Alembic · Docker Compose.

- **API contract (live):** http://localhost:8000/docs — every endpoint, schema and auth requirement
- Architecture: [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) · Plan: [`BACKEND_PLAN.md`](BACKEND_PLAN.md)

---

## Quick start (Docker, ~5 minutes)

```bash
git clone https://github.com/BaizakAmina/swe-backend.git && cd swe-backend
docker compose up --build -d                 # db, migrations, api, mail, storage
docker compose exec api python -m app.seed   # demo accounts + events
```

Open http://localhost:8000/docs → **Authorize** → `organizer@biletflow.kz` / `demo12345`.

> Port 5432 already taken by a local PostgreSQL? Start with `DB_PORT=5433 docker compose up --build -d`.

| Service | URL |
|---|---|
| API / Swagger | http://localhost:8000/docs (OpenAPI JSON: `/openapi.json`) |
| MailHog — all outgoing emails | http://localhost:8025 |
| S3 storage (SeaweedFS) — PDF tickets | http://localhost:9000 (bucket `biletflow`, created automatically) |
| PostgreSQL | `localhost:5432` (`DB_PORT`), user/password/db `biletflow` |

Migrations run automatically (`migrate` service) before the API starts. `.env` is optional for compose.

### Demo accounts

Password for all: `demo12345`

| Email | Role |
|---|---|
| admin@biletflow.kz | platform admin |
| organizer@biletflow.kz | organizer — verified, payout connected, paid sales active |
| organizer2@biletflow.kz | another organizer (for "not your event" checks) |
| eventadmin@biletflow.kz | event admin (door scanner) of "Almaty Jazz Night" |
| attendee@biletflow.kz | attendee |

Events: **Almaty Jazz Night** (published; Student free / Standard 10 000 ₸ / VIP 25 000 ₸; promo `DEMO10` −10% on Standard) and a draft **Tech Meetup Astana**.
`python -m app.seed --reset` wipes everything and re-seeds (dev only).

---

## For web & mobile developers

### Auth

```
POST /api/v1/auth/register        {email, password, full_name, locale, role: attendee|organizer}
POST /api/v1/auth/verify-email    {token}            ← link from the email (see MailHog)
POST /api/v1/auth/login           {email, password}  → {access_token, refresh_token, expires_in}
POST /api/v1/auth/refresh         {refresh_token}    → new pair (old refresh is revoked)
POST /api/v1/auth/logout          {refresh_token}
GET  /api/v1/me
```

Send `Authorization: Bearer <access_token>`. Access tokens live 15 min, refresh tokens 14 days.
Organizers can create events only after verifying their email and creating an organizer profile.

### Conventions

| Topic | Rule |
|---|---|
| Errors | `{"error": {"code": "machine_code", "message": "human text", "details": …}}` — switch on `code` |
| Status codes | 401 not logged in · 403 logged in but not allowed · 404 not found / not visible · 409 conflict with current state · 422 validation |
| Lists | `?limit=&offset=` → `{items, total, limit, offset}` |
| Money | integer **minor units** + `currency` (`KZT`): `1000000` = 10 000 ₸. Never floats. Prices/discounts are always computed by the server |
| Time | ISO 8601 with offset, stored in UTC; events also have an IANA `timezone` (default `Asia/Almaty`) for display |
| Payments | simulated — payment/refund objects always have `is_simulated: true`; pay with `{"simulate": "success" \| "fail"}` |
| Event lifecycle | `upcoming / active / completed / cancelled` — derived, read-only |
| Languages | `locale`: `kk` / `ru` / `en` (emails are localized) |

### QR codes — two different things

- **Admission ticket QR** — `ticket.qr_payload`, starts with `T1.`; scanned only by the check-in endpoint. Image: `GET /tickets/{id}/qr.png`, printable ticket: `GET /tickets/{id}/pdf`.
- **Campaign QR** — an HTTPS link `…/events/{id}?c=C1.…` for posters. The web app calls `GET /campaigns/resolve?token=…` to prefill the promo code. **Check-in always rejects it.**

### Main flows (see `/docs` for all endpoints)

- **Catalog:** `GET /events?q=&category=&date_from=&date_to=` · `GET /events/{id}` · `GET /events/{id}/ticket-types` · `GET /events/{id}/calendar.ics`
- **Organizer:** organizer profile & payout (`/organizers/me…`) → `POST /events` → ticket types → `POST /events/{id}/publish`; staff (`/events/{id}/staff`), campaigns, attendees (+ CSV), orders, analytics, timeline
- **Buyer:** `POST /orders` → `POST /orders/{id}/pay` → `GET /me/tickets` · `GET /me/orders` · in-app notifications `GET /me/notifications`
- **Scanner app:** `GET /me/assigned-events` → `POST /checkin/validate` / `POST /checkin` → `GET /events/{id}/checkin-stats`
- **Support:** `/support/cases` (+ messages, poll with `?after=`)
- **Admin:** `/admin/...` — users, events, suspension, orders & payments (`q`, status, `date_from`/`date_to`), reported events, disputes, promo activity, platform settings, CSV reports (with dates)

---

## Local development without Docker

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                  # point DATABASE_URL at your PostgreSQL
docker compose up -d db mail storage  # or run them yourself
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://biletflow:biletflow@localhost:5432/biletflow` | database |
| `JWT_SECRET` | `change-me` | signs access/refresh tokens — **set a long random value outside dev** |
| `QR_SECRET` | `change-me-too` | signs admission QR payloads — **same** |
| `ACCESS_TOKEN_TTL_MINUTES` / `REFRESH_TOKEN_TTL_DAYS` | 15 / 14 | token lifetimes |
| `RESERVATION_TTL_MINUTES` | 10 | checkout hold |
| `PUBLIC_WEB_URL` | `http://localhost:3000` | links in emails / Campaign QR |
| `API_BASE_URL` | `http://localhost:8000` | links to PDFs in emails |
| `PAID_TERMS_VERSION` | `2026-11` | current paid-event terms; organizers re-accept after a change |
| `PUBLIC_API_URL` | *(empty)* | API address in links returned to clients (cover images, calendar); empty → address of the request |
| `CORS_ORIGINS` | `["http://localhost:3000","http://localhost:5173"]` | allowed web origins (JSON list) |
| `EMAIL_BACKEND` | `smtp` | `smtp` / `console` / `memory` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_FROM` | MailHog | outgoing email |
| `STORAGE_BACKEND` | `s3` | `s3` / `memory` |
| `S3_ENDPOINT_URL` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` / `S3_BUCKET` | local storage | any S3-compatible service |
| `ACTIVATION_FEE_MINOR` | `500000` | default paid-sales activation fee (admins can change it) |

### Checks & tests

```bash
ruff check . && black --check . && mypy app && pytest
```

Tests marked `db` need PostgreSQL and run only with `RUN_DB_TESTS=1` (CI does this on every PR):

```bash
DB_PORT=5433 docker compose up -d db
docker compose exec db psql -U biletflow -c "CREATE DATABASE biletflow_test"   # once
export DATABASE_URL=postgresql+psycopg://biletflow:biletflow@localhost:5433/biletflow_test
RUN_DB_TESTS=1 pytest
```

Each test runs inside a transaction that is rolled back; emails and file storage use in-memory backends.

### Demo scenario script

`scripts/demo_e2e.py` walks the whole demo against a running stack and prints ✅/❌ per step:

```bash
python scripts/demo_e2e.py --base-url http://localhost:8000
```

---

## Code layout

```
app/
  main.py            app factory, routers under /api/v1
  core/              config (env), db session, security (JWT, argon2), error format
  models/            SQLAlchemy models (all entities, see BACKEND_ARCHITECTURE.md §4)
  schemas/           Pydantic request/response models = the API contract
  api/deps.py        auth & RBAC dependencies (CurrentUser, ManagedEvent, StaffedEvent, …)
  api/v1/            routers — thin, no ORM queries
  services/          business rules and transaction boundaries
  templates/email/   email templates; texts in services/email_texts.py (ru/en/kk)
  assets/fonts/      DejaVu Sans for PDF tickets (Cyrillic + Kazakh)
alembic/             migrations
tests/               pytest; tests/factories.py builds test data
```

Rule: routers never touch the ORM directly — all logic lives in `services/`. Every state change by an organizer/admin calls `audit.log(...)` (append-only, feeds the event timeline).

### Data model notes

- Money is `BIGINT` in KZT minor units (`*_minor`).
- Enums are `VARCHAR` + `CHECK` (no native PG enums).
- Inventory limits, promo limits, one redemption per order and one active seat hold are enforced by DB constraints, not only in code.
- Active seat hold = `released_at IS NULL` (partial unique index); expired holds are released by the sweep.
- `audit_log` is append-only (a trigger blocks UPDATE/DELETE).
- Tables are `user_account` and `orders` (`user` / `order` are reserved words).

### Backup / restore

```bash
docker compose --profile tools run --rm backup                       # -> backups/*.dump
docker compose exec -T db pg_restore -U biletflow -d biletflow --clean < backups/<file>.dump
```
