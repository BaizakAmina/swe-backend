# BiletFlow — Backend Architecture

**Project:** BiletFlow — Self-Service Event Ticketing Platform for Kazakhstan
**Document status:** v1.0 (Architecture phase deliverable, Weeks 1–2)
**Scope reference:** SRS v0.3, sections 6 (Data Entities), 9 (Tech Stack), 13.3 (Schedule)
**Backend stack:** Python 3.12 · FastAPI · PostgreSQL 16 · SQLAlchemy 2.0 + Alembic
**Deployment:** Docker Compose

---

## 1. Purpose

This document defines the backend architecture for the BiletFlow academic MVP. It records the
technology decision and its rationale, the data model, the service decomposition, the API surface,
and the critical design decisions (atomic seat/inventory reservation, QR-ticket security, RBAC,
payment simulation). It is the contract that lets the backend team work in parallel and lets the
web and mobile teams integrate against a stable API.

It intentionally stays inside the MVP scope boundaries in SRS §8/§13.4. Anything outside those
boundaries (real payouts, production KYC, offline scanner sync, native attendee apps) is **out of
scope** and is not designed here.

---

## 2. Technology Selection & Rationale

The SRS requires a short rationale covering **team familiarity, development speed, data-model
requirements, and deployment complexity**.

| Decision | Choice | Rationale |
|---|---|---|
| Language / framework | **Python 3.12 + FastAPI** | Team familiarity; fast to build; first-class async I/O; automatic OpenAPI generation gives the web/mobile teams a live API contract for free. |
| Data validation | **Pydantic v2** | Request/response schemas double as validation and as the published contract. |
| Database | **PostgreSQL 16** | The domain is strongly relational (orders ↔ tickets ↔ seats ↔ payments) and needs **ACID transactions** and **row-level locking** for atomic seat/inventory reservation. Postgres delivers this natively; a document store would push that correctness into application code. |
| ORM / migrations | **SQLAlchemy 2.0 + Alembic** | Mature, typed ORM; Alembic gives versioned, reviewable schema migrations. |
| Auth | **JWT access + refresh, `bcrypt`/`argon2` password hashing** | Stateless, works identically for the web app and the React Native scanner. |
| Background jobs | **FastAPI `BackgroundTasks` (MVP), upgrade path to Celery + Redis** | Email sending, seat-hold expiry sweeps, QR/PDF generation. |
| Deployment | **Docker Compose** (api + postgres + mailhog + minio) | Matches SRS §9; one-command local/demo bring-up. |

**Why not MongoDB?** Seat reservation and promo-code redemption must be *atomic and correct under
concurrency* (SRS §4.3.1, §4.14, §7). PostgreSQL gives us `SELECT ... FOR UPDATE`, unique
constraints, and transactional multi-row writes out of the box. This is the single most important
correctness property of the whole system, so we optimize for it.

**One backend, one language** (SRS §9): a single FastAPI service — no microservices — organized as
a **modular monolith**.

---

## 3. High-Level Architecture

A **modular monolith**: one deployable FastAPI application, internally split into domain modules
with clear boundaries. Each module owns its routes, service layer, and data access.

```
                 ┌─────────────────────────────────────────────┐
  Web (React) ──▶│                 FastAPI app                  │
  RN Scanner  ──▶│  ┌───────────────────────────────────────┐  │
  Admin web   ──▶│  │ API layer (routers, auth, RBAC deps)  │  │
                 │  ├───────────────────────────────────────┤  │
                 │  │ Service layer (business rules, tx)    │  │
                 │  ├───────────────────────────────────────┤  │
                 │  │ Repository / ORM (SQLAlchemy)         │  │
                 │  └───────────────────────────────────────┘  │
                 └───────┬───────────────┬───────────┬─────────┘
                         │               │           │
                   PostgreSQL      Object store   Email (SMTP
                   (canonical)     (S3/MinIO:     sandbox /
                                   PDFs, QR)      MailHog)
                                        │
                              Payment sandbox / simulation
```

**Layering rule:** routers never touch the ORM directly; all business logic and transaction
boundaries live in the service layer. This keeps the atomic-reservation logic in one place and
testable.

### 3.1 Domain modules

| Module | Responsibility (SRS refs) |
|---|---|
| `auth` | Registration, email verification, login/logout, password reset, JWT, RBAC (§4.1) |
| `organizers` | Organizer profile, payout account, staff assignments (§4.1, §4.5) |
| `events` | Event CRUD, publish/unpublish/cancel, capacity, visibility, history/timeline (§4.2, §4.16) |
| `venues` | Predefined venue layout, sections, rows, seats (§4.3.1) |
| `tickets` | Ticket types, inventory counters (§4.3) |
| `seating` | Seat maps, seat holds, atomic seat reservation (§4.3.1) |
| `orders` | Cart, checkout, order + order items, free registration (§4.4, §4.6) |
| `payments` | Activation-fee + ticket payment simulation, refunds (§3.2, §4.5, §4.6, §4.9) |
| `ticketing` | Ticket issuance, QR generation, PDF generation, status (§4.7) |
| `checkin` | Mobile verification: validate QR, record/undo check-in (§4.8) |
| `notifications` | Email + in-app notifications (§4.10) |
| `calendar` | `.ics` export (§4.11) |
| `campaigns` | Promo codes, Campaign QR codes, atomic redemption (§4.14) |
| `analytics` | Organizer dashboard aggregations (§4.15) |
| `support` | Support cases + async message threads (§4.13) |
| `admin` | Platform-admin moderation, suspension, reports (§4.12) |
| `audit` | Append-only audit log (§4.16, §7) |

---

## 4. Core Data Model

All 24 entities from SRS §6 mapped to PostgreSQL. Types abbreviated; every table has `id` (UUID
PK), `created_at`, `updated_at` unless noted. Money is stored in **KZT minor units as `BIGINT`**
(no floats for currency).

### 4.1 Entity overview & relationships

```
User ──1:1── OrganizerProfile ──1:N── PayoutAccount
User ──1:N── Order ──1:N── OrderItem ──1:1── Ticket
Event ──1:N── TicketType ──1:N── OrderItem
Event ──1:N── StaffAssignment ──N:1── User      (Event Admin role)
Event ──N:1── Venue ──1:N── VenueSection ──1:N── Row ──1:N── Seat
Seat ──1:N── SeatHold        (temporary, expiring)
OrderItem ──0:1── Seat       (assigned seating)
Order ──1:N── Payment ──1:N── Refund
Ticket ──1:N── CheckInRecord
Event ──1:N── PromotionalCampaign ──1:1── PromoCode ──1:N── PromoRedemption ──N:1── Order
SupportCase ──1:N── SupportMessage
User ──1:N── Notification
* ──writes──▶ AuditLog
```

### 4.2 Tables (key columns & constraints)

**user**
`email` (unique, citext), `password_hash`, `full_name`, `locale` (kk/ru/en), `email_verified_at`,
`status` (active/suspended). Global role via `user_role` (attendee/organizer/platform_admin);
event-scoped roles come from `staff_assignment`.

**organizer_profile**
`user_id` (unique FK), `display_name`, `contact_email`, `contact_phone`, `verification_status`
(unverified/pending/verified — **simulated** for MVP).

**payout_account**
`organizer_id` FK, `provider_ref` (sandbox), `currency` (KZT), `status`. No real bank data stored.

**event**
`organizer_id` FK, `title`, `description`, `category`, `cover_image_key`, `venue_id` FK (nullable
for GA-only), `starts_at`, `ends_at`, `timezone` (IANA, default `Asia/Almaty`), `visibility`
(public/unlisted/private), `capacity`, `seating_mode` (general_admission/assigned),
`status` (draft/published/cancelled), `paid_sales_active` (bool), `registration_opens_at`,
`registration_closes_at`.
Lifecycle classification (Upcoming/Active/Completed/Cancelled) is **derived** from
`status` + `starts_at`/`ends_at`, not stored.

**venue / venue_section / row / seat**
Predefined layout (one, per SRS §4.3.1 — no visual editor).
`seat`: `row_id` FK, `seat_number`, `price_category`, `is_accessible` (bool),
UNIQUE(`row_id`, `seat_number`).

**seat_hold** — *critical for concurrency*
`seat_id` FK, `event_id` FK, `order_id` (nullable), `held_by_user_id`, `expires_at`.
**Partial unique index** guarantees at most one *active* hold per seat:
`UNIQUE (seat_id) WHERE expires_at > now() AND released_at IS NULL`.

**ticket_type**
`event_id` FK, `name`, `description`, `kind` (free/paid), `price_minor` (BIGINT KZT),
`quantity_total`, `quantity_sold`, `quantity_reserved`, `max_per_order`, `sales_start_at`,
`sales_end_at`, `is_hidden` (bool). CHECK(`quantity_sold + quantity_reserved <= quantity_total`).

**order**
`buyer_user_id` FK, `event_id` FK, `status` (pending/paid/cancelled/refunded/expired),
`subtotal_minor`, `discount_minor`, `fees_minor`, `total_minor`, `promo_code_id` (nullable),
`reserved_until` (checkout hold expiry). Free order ⇒ `total_minor = 0`.

**order_item**
`order_id` FK, `ticket_type_id` FK, `seat_id` (nullable — assigned seating), `unit_price_minor`,
`section`, `row_label`, `seat_number` (denormalized snapshot per SRS §4.3.1 "stored on the ticket
and order item").

**ticket**
`order_item_id` (unique FK), `event_id` FK, `attendee_id` FK, `ticket_code` (unique, opaque),
`qr_payload` (signed token — see §5.4), `status` (valid/checked_in/cancelled/refunded),
`issued_at`. Digital and printed copies **share one `ticket_code`** (SRS §4.7).

**attendee**
`order_item_id`/`ticket` link, `full_name`, `email`. Attendee details captured at checkout.

**payment**
`order_id` FK, `purpose` (ticket_purchase/activation_fee), `amount_minor`, `currency` (KZT),
`provider` (sandbox/simulation), `provider_ref`, `status` (initiated/succeeded/failed),
`is_simulated` (bool, always true in MVP — SRS §4.6 "never presented as real transactions").

**refund**
`payment_id` FK, `amount_minor`, `reason`, `status`, `processed_at`.

**check_in_record**
`ticket_id` FK, `event_admin_user_id` FK, `action` (check_in/reverse), `scanned_at`,
`device_info`. Uniqueness enforced in service layer so a valid ticket cannot be checked in twice
(SRS §4.8).

**staff_assignment**
`event_id` FK, `user_id` FK, `role` (event_admin), `assigned_by`. Drives "view only assigned
events" (SRS §4.8).

**notification**
`user_id` FK, `type`, `channel` (email/in_app), `payload` (JSONB), `read_at`, `sent_at`.

**support_case**
`opened_by_user_id` FK, `kind` (attendee/organizer), `category` (ticket_delivery/payment/refund/
seating/event_info/checkin/account/technical), `status` (open/in_progress/waiting_customer/
resolved), context FKs `event_id`/`order_id`/`ticket_id` (nullable), `assigned_to_user_id`.

**support_message**
`case_id` FK, `sender_user_id` FK, `body`, `sent_at`. Async thread; polling, no WebSocket
required (SRS §4.13).

**promotional_campaign**
`event_id` FK, `name`, `discount_type` (percent/fixed), `discount_value`, `starts_at`, `ends_at`,
`max_redemptions`, `redemption_count`, `status` (active/disabled), applicable ticket types via
`campaign_ticket_type` join.

**promo_code**
`campaign_id` (unique FK), `code` (unique), `qr_token` (opaque, signed — encodes campaign link,
**never a discount amount**, SRS §4.14).

**promo_redemption**
`promo_code_id` FK, `order_id` FK, `discount_applied_minor`, `redeemed_at`.
UNIQUE(`order_id`) — one campaign attribution per order.

**audit_log** — append-only
`actor_user_id`, `action_type`, `entity_type`, `entity_id`, `description`, `occurred_at`,
`metadata` (JSONB). Not editable/deletable via app (SRS §4.16). Feeds the event activity timeline.

---

## 5. Critical Design Decisions

### 5.1 Atomic seat reservation (SRS §4.3.1, §7 — must prevent double-selling)

On "select seat":
1. `BEGIN` transaction.
2. Insert `seat_hold` with `expires_at = now() + N min`. The **partial unique index** on active
   holds makes a concurrent second insert for the same seat fail → serialized without app locks.
3. On success the seat is held; on conflict return `409 Seat unavailable`.
4. `COMMIT`.

On checkout, holds are converted to `order_item` + `ticket` inside one transaction; expired holds
are swept by a periodic background task and released. GA (non-seated) inventory uses the same
principle on `ticket_type` counters with `SELECT ... FOR UPDATE` and the CHECK constraint.

### 5.2 Inventory reservation & checkout

`quantity_reserved` is incremented under row lock at checkout start and either promoted to
`quantity_sold` on payment success or rolled back on failure/abandonment/expiry. Tickets are
issued **only after `payment.status = succeeded`** (SRS §4.6).

### 5.3 Promo-code redemption (SRS §4.14, §7 — atomic, server-validated)

All discount math is **server-side**. Redemption increments `redemption_count` under row lock with
`CHECK (redemption_count <= max_redemptions)`; expired/disabled/exhausted/inapplicable codes are
rejected with a clear message. The client never sends a trusted discount amount.

### 5.4 QR security & the Campaign-vs-Admission distinction (SRS §4.7, §4.14, §7)

Two **structurally different** QR token types, and they are never interchangeable:

- **Admission ticket QR** → signed token binding `ticket_code` + `event_id` (HMAC / short-lived
  JWT). Verified only by the check-in endpoint.
- **Campaign QR** → opaque, signed HTTPS event link with a campaign/promo token. Verified only by
  the public event/promo endpoint.

The check-in/admission endpoint **rejects any Campaign QR** and vice-versa. This is a hard rule and
a required success criterion (SRS §11).

### 5.5 Authentication & RBAC

JWT access + refresh. Global roles: `attendee`, `organizer`, `platform_admin`. Event-scoped role
`event_admin` resolved from `staff_assignment`. FastAPI dependencies enforce:
- organizer/staff see only their own events, orders, analytics, history (SRS §4.15, §4.16, §7);
- event admins see only assigned events (SRS §4.8);
- support-case access enforced by role **and** relationship to the referenced event/order/ticket
  (SRS §7).

### 5.6 Payments — simulated (SRS §3.2, §4.6)

A `PaymentGateway` interface with a `SimulatedGateway` implementation. Every payment row carries
`is_simulated = true` and demonstration records are never presented as real money movement.
Swapping in a real sandbox later means adding one implementation — no schema change.

---

## 6. API Surface (overview)

Base: `/api/v1`. Full contract is the auto-generated OpenAPI at `/docs` (this is why FastAPI was
chosen). Representative endpoints:

| Group | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/verify-email`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/password-reset` |
| Events | `GET /events`, `GET /events/{id}`, `POST /events`, `PATCH /events/{id}`, `POST /events/{id}/publish`, `POST /events/{id}/cancel`, `POST /events/{id}/duplicate`, `GET /events/{id}/timeline` |
| Ticket types | `GET/POST /events/{id}/ticket-types`, `PATCH /ticket-types/{id}` |
| Seating | `GET /events/{id}/seatmap`, `POST /events/{id}/seat-holds`, `DELETE /seat-holds/{id}` |
| Orders/Checkout | `POST /orders` (cart), `POST /orders/{id}/checkout`, `POST /orders/{id}/register-free`, `GET /orders/{id}`, `GET /me/orders` |
| Payments/Refunds | `POST /orders/{id}/pay`, `POST /events/{id}/activate-paid-sales`, `POST /orders/{id}/refund` |
| Tickets | `GET /tickets/{id}`, `GET /tickets/{id}/pdf`, `GET /me/tickets` |
| Check-in (mobile) | `GET /me/assigned-events`, `POST /checkin/validate`, `POST /checkin`, `POST /checkin/{id}/reverse`, `GET /events/{id}/checkin-stats` |
| Campaigns | `POST /events/{id}/campaigns`, `GET /campaigns/{id}`, `POST /promo/validate` |
| Calendar | `GET /events/{id}/calendar.ics` |
| Analytics | `GET /events/{id}/analytics?range=&ticket_type=` |
| Support | `POST /support/cases`, `GET /support/cases`, `GET /support/cases/{id}`, `POST /support/cases/{id}/messages`, `PATCH /support/cases/{id}` |
| Admin | `GET /admin/users`, `GET /admin/events`, `POST /admin/events/{id}/suspend`, `GET /admin/reports/*` |

**Contract-first:** Pydantic request/response schemas are defined in Week 1 and frozen as the
integration contract before parallel work begins (SRS §13.2).

---

## 7. Non-Functional Requirements (SRS §7)

- **Security:** TLS in transit; secrets & sensitive fields encrypted at rest; passwords hashed
  (argon2/bcrypt); **no payment-card data stored**; audit trail on organizer/admin actions.
- **Concurrency/perf:** atomic reservations (§5.1); QR validation target < 2 s; analytics runs as
  read-only aggregation that **never blocks** checkout/issuance.
- **i18n & currency:** UI locales kk/ru/en; all money in **KZT minor units**; event times store an
  IANA timezone so `.ics` export preserves it.
- **Availability:** regular Postgres backups (`pg_dump` cron in compose) + documented restore.
- **Extensibility:** currency and locale are columns, not hard-coded → future multi-currency.

---

## 8. Deployment (SRS §9)

`docker-compose.yml` services:
- `api` — FastAPI (uvicorn/gunicorn)
- `db` — PostgreSQL 16 (named volume)
- `mail` — MailHog (transactional email sandbox)
- `storage` — MinIO (S3-compatible; PDFs, QR images, covers)
- `migrate` — one-shot Alembic upgrade on startup

Config via env vars (`.env`): DB URL, JWT secret, mail/S3 endpoints. One command (`docker compose
up`) brings up the whole backend for local dev and the demo.

---

## 9. Testing & Automated Checks (SRS §9, §13.4)

- **CI (GitHub Actions):** lint (`ruff`), format (`black`), type-check (`mypy`), tests (`pytest`)
  on every PR; spins up a Postgres service container.
- **Test layers:** unit (service rules), API/integration (FastAPI `TestClient` against a real test
  DB), and targeted tests for the correctness-critical paths:
  - concurrent seat holds cannot double-sell a seat;
  - promo redemption never exceeds `max_redemptions`;
  - QR validation accepts admission tickets and **rejects Campaign QR codes**;
  - tickets are issued only after successful payment.
- Migrations reviewed as code (Alembic).

---

## 10. Repository Layout (proposed)

```
biletflow-backend/
├── app/
│   ├── main.py                 # FastAPI app factory, router registration
│   ├── core/                   # config, security (JWT, hashing), db session
│   ├── models/                 # SQLAlchemy models (the 24 entities)
│   ├── schemas/                # Pydantic request/response = the API contract
│   ├── api/v1/                 # routers per module
│   ├── services/               # business logic + transaction boundaries
│   ├── repositories/           # data access
│   └── workers/                # seat-hold expiry sweep, email, PDF/QR
├── alembic/                    # migrations
├── tests/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .github/workflows/ci.yml
```

---

## 11. Open Decisions to Confirm with the Team (SRS §12)

These block nothing in this document but should be pinned before implementation:
- Activation-fee amount & refundability.
- Whether attendees must create accounts (design assumes account required at checkout).
- Processing-fee bearer (organizer vs attendee).
- The single predefined seating layout to ship for the demo.
- Support-case file attachments (currently **out** of MVP).
- Seat-hold / checkout expiry duration (proposed: 10 minutes).
- Event-history/audit retention period for the academic term.

---

## 12. Out of Scope (SRS §8)

Real payouts, production KYC/KYB, visual venue-map designer, offline scanner sync, app-store
publication, resale/transfer, native attendee/organizer apps, multi-split payouts, tax automation,
full field-level version history/rollback. Not designed here.
