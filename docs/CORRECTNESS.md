# Correctness guarantees

What BiletFlow guarantees, how, and which test proves it (SRS §7, §11;
[`BACKEND_ARCHITECTURE.md`](../BACKEND_ARCHITECTURE.md) §5). The tests live in
[`tests/critical/`](../tests/critical) and run with the `critical` pytest marker:

```bash
RUN_DB_TESTS=1 pytest -m critical          # needs PostgreSQL (DATABASE_URL)
```

CI runs them as a separate step, three times in a row. Locally they pass 10 runs in a row.

## How the concurrency tests work

`tests/critical/harness.py` starts **N threads, each with its own session on its own
PostgreSQL connection** (the harness checks that every `pg_backend_pid()` is different), holds
them on a barrier and releases them together into the same service call. Test data is
committed first and removed afterwards (`tests/committed_data.py`). Every test also checks the
database after the race, not just the responses.

Each test was also run against deliberately broken code — the lock removed, the index dropped,
the payment check disabled — and failed every time. So these tests catch the bug they're
about; they don't just pass.

## Guarantees

| # | Guarantee | Mechanism | Test (`tests/critical/…`) |
|---|---|---|---|
| 1 | The last ticket is never sold twice; `sold + reserved ≤ total` | `SELECT … FOR UPDATE` on `ticket_type` rows, always in `id` order, in the order transaction; CHECK `inventory_not_oversold` as the last line | `test_concurrency.py::test_inventory_is_never_oversold` — 20 buyers, 1 ticket → exactly 1 order; 20 buyers, 5 tickets → exactly 5 |
| 2 | A promo code is never used more than `max_redemptions` times | `SELECT … FOR UPDATE` on the campaign row in the order transaction; `redemption_count` released when an order expires, is cancelled or refunded; CHECK `redemptions_within_limit` | `test_concurrency.py::test_promo_code_never_exceeds_max_redemptions` — limit 3, 20 orders (each on its own ticket type, so only the campaign lock can protect the limit) → exactly 3 discounted |
| 3 | A seat is never held or sold twice | Partial unique index `uq_seat_hold_active (seat_id, event_id) WHERE released_at IS NULL`; a sold seat keeps its active hold (linked to the paid order) for as long as the sale stands | `test_concurrency.py::test_seat_is_held_once` — 20 holds of one seat → 1 |
| 4 | A ticket is admitted at the door once — by QR or picked manually in the attendee search | `SELECT … FOR UPDATE` on the ticket row, `valid → checked_in`; both paths share one admit function | `test_concurrency.py::test_ticket_is_admitted_once` — 6 scanners → 1 `valid`, 5 × 409 `already_checked_in`, one `check_in_record`; `::test_manual_and_qr_check_in_admit_once` — 4 QR + 4 manual at once → 1 |
| 5 | An order is charged once | `SELECT … FOR UPDATE` on the order row for the whole payment transaction | `test_concurrency.py::test_order_is_paid_once` — 5 parallel payments → 1 succeeded payment, 4 × 409 |
| 6 | The activation fee is charged once | `SELECT … FOR UPDATE` on the event row | `test_concurrency.py::test_activation_fee_is_charged_once` |
| 7 | An order is refunded once | Order row lock; status `paid → refunded` checked under the lock | `test_concurrency.py::test_order_is_refunded_once` — 4 parallel refunds → 1 refund, inventory returned once |
| 8 | No ticket without a succeeded payment | `orders.mark_paid` and `ticketing.issue_tickets` both require a succeeded `ticket_purchase` payment for the order's **full** total (free orders: total 0); tickets are created in the same transaction that marks the order paid | `test_paid_tickets_only.py` — failed payments (and a forced `paid` status, and a partial payment) never produce tickets |
| 9 | Only a genuine admission QR of *this* event passes the door | QR payload `T1.<base64(ticket_code\|event_id)>.<HMAC-SHA256(QR_SECRET)>`, verified with `hmac.compare_digest`; the secret comes from the environment | `test_qr_security.py::test_genuine_admission_qr_is_accepted_once` |
| 10 | Campaign / promo QR codes are never accepted as tickets | Structurally different format (`C1.` token / `…?c=` link), detected before verification → `campaign_qr_not_admission` | `test_qr_security.py::test_everything_else_is_rejected[campaign token]`, `[campaign link]` |
| 11 | Forged, foreign and garbage QR codes are rejected | HMAC over prefix + body: a changed signature, ticket code or event id, another secret, or a missing ticket → `invalid_qr`; a real ticket of another event → `wrong_event` (no attendee data shown) | `test_qr_security.py::test_everything_else_is_rejected[…]` — forged signature, foreign event id, other secret, unknown ticket, ticket of another event, garbage, URL, empty string, whitespace |

## Deadlock freedom

All writers take locks in one global order:
**event (share) → order(s) → tickets → ticket types (by `id`) → campaign → seat holds**.
Multi-row locks are taken in one statement ordered by `id`. The expiry sweeps use
`SKIP LOCKED`, so they never wait on a checkout.
