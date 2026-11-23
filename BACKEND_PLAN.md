# BiletFlow — Backend Implementation Plan

**Основано на:** SRS v0.3 (§4, §8, §11, §13) и `BACKEND_ARCHITECTURE.md`
**Стек:** Python 3.12 · FastAPI · PostgreSQL 16 · SQLAlchemy 2.0 + Alembic · Docker Compose

---

## 1. Фазы

| Фаза | Что | Результат |
|---|---|---|
| **0. Фундамент** | скелет проекта, Docker, БД, модели, миграции, auth, CI | `docker compose up` поднимает API, `/docs` открывается, можно зарегаться и залогиниться |
| **1. Core flow** | события → типы билетов → заказ → симуляция оплаты → билет с QR → check-in | Success criteria SRS §11, пункты 1–5 |
| **2. Остальной MVP** | промокоды + Campaign QR, возвраты, support, аналитика, история, админка, PDF | Все обязательные пункты SRS §8 |
| **3. Бонусы** | assigned seating, `.ics` | Только если фазы 1–2 готовы (SRS §13.4) |
| **4. Стабилизация** | интеграция с фронтом/мобилкой, тесты, seed-данные для демо | Демо проходит end-to-end |

> Порядок резки при нехватке времени (SRS §13.4): сначала выкидываем seating → `.ics` →
> refund simulation. Core flow не трогаем.

---

## 2. Фаза 0 — Фундамент

- **Скелет проекта** по §10 архитектуры: `app/main.py`, `core/config.py` (pydantic-settings, `.env`),
  `core/db.py` (session), `pyproject.toml`, ruff/black/mypy. `/health` → 200.
- **Docker:** `Dockerfile` + `docker-compose.yml` (api, db, mailhog, minio, migrate) — всё
  поднимается одной командой.
- **Модели и миграции:** SQLAlchemy-модели всех 24 сущностей + первая Alembic-миграция.
  Деньги — `BIGINT` minor units. CHECK на `ticket_type`, partial unique index на `seat_hold`,
  UNIQUE на `promo_redemption.order_id`. `alembic upgrade head` на чистой БД проходит.
- **Auth:** register, verify-email (токен), login, refresh, logout, password-reset; argon2/bcrypt; JWT.
- **RBAC-зависимости:** `get_current_user`, `require_role(...)`, `require_event_organizer(event_id)`,
  `require_event_admin(event_id)` — используются всеми роутерами.
- **Audit-хелпер:** `audit.log(actor, action, entity, description, metadata)` — append-only,
  вызывается из любого сервиса.
- **CI:** GitHub Actions — ruff, black --check, mypy, pytest с Postgres service.
- **Seed-скрипт:** юзеры всех ролей + одно тестовое событие (`python -m app.seed`).

**Порядок:** скелет → Docker и модели → auth / RBAC / audit → CI и seed.
Pydantic-схемы и модели фиксируем до начала фазы 1.

---

## 3. Фаза 1 — Core flow

- **Organizer profile** + payout account (simulated) CRUD — §4.1, §4.5.
- **Events:** create / edit / publish / unpublish / cancel / duplicate / preview; visibility;
  derived lifecycle (Upcoming / Active / Completed / Cancelled); публичный список и карточка — §4.2, §4.16.
- **Ticket types:** CRUD, hide, sale windows, `max_per_order`, счётчики
  available / reserved / sold / refunded / checked-in (только чтение) — §4.3.
- **Staff assignment:** организатор назначает event admin; `GET /me/assigned-events` — §4.8.
- **Orders:** создание заказа, **атомарное резервирование** `quantity_reserved` под
  `SELECT … FOR UPDATE`, free registration (total = 0), `reserved_until`, фоновый sweep
  просроченных резервов — §4.4, §4.6.
- **Payments:** `PaymentGateway` + `SimulatedGateway`; оплата заказа (succeeded / failed);
  `is_simulated = true` везде — §4.6.
- **Paid sales activation:** чек-лист, оплата activation fee через симуляцию, флаг
  `paid_sales_active`; платные билеты заблокированы до активации — §3.2, §4.5.
- **Ticket issuance:** `ticket` + `attendee` создаются **только после** успешной оплаты;
  подписанный QR-payload (HMAC: `ticket_code` + `event_id`) — §4.7.
- **Check-in API:** validate, check-in, reverse, stats; повторный check-in запрещён;
  **Campaign QR отклоняется** — §4.8, §11.
- **Email-уведомления** через MailHog: verify email, подтверждение заказа, доставка билета,
  отмена события (шаблоны + записи `notification`) — §4.10.
- **Личный кабинет:** `GET /me/orders`, `GET /me/tickets`, `GET /tickets/{id}`; attendee list
  для организатора — §4.4, §4.9.

---

## 4. Фаза 2 — Остальной MVP

- **QR и PDF-билет:** картинки через `qrcode`, PDF (A4, grayscale-friendly, крупный QR, без
  платёжных данных) из канонической записи билета; хранение в MinIO — §4.7.
- **Campaigns:** создание кампании, генерация promo code и Campaign QR (opaque token,
  HTTPS-ссылка), disable — §4.14.
- **Промокоды:** `POST /promo/validate` (только превью) + применение внутри заказа: серверный
  расчёт скидки, **атомарный** инкремент `redemption_count`, понятные ошибки
  (expired / disabled / exhausted / inapplicable) — §4.14, §7.
- **Отмены и возвраты:** отмена free-регистраций, full refund через симуляцию, инвалидация
  билетов, возврат инвентаря, audit — §4.9.
- **Support:** cases + messages, категории, статусы, assign, авто-контекст event/order/ticket,
  доступ по роли **и** связи, уведомления о новых ответах — §4.13.
- **Organizer analytics:** capacity / sold / remaining / %, gross / discounts / refunds / net,
  продажи по времени, по типам билетов, по кампаниям, attendance; фильтры — §4.15.
- **Event history:** фильтр по lifecycle, timeline из `audit_log` с фильтрами; `audit.log()`
  проставлен во всех сервисах — §4.16.
- **Admin:** поиск users / events / orders / payments, suspend user / event (**останавливает
  продажи**), просмотр activation records, настройка activation fee, CSV-экспорт — §4.12.

---

## 5. Фаза 3 — Бонусы (только если фазы 1–2 готовы)

- **`.ics` экспорт** + ссылки Google / Outlook, стабильный UID, cancellation-файл.
- **Assigned seating:** seed одного venue layout, `GET /seatmap`, seat holds через partial
  unique index, конвертация hold → order_item, sweep.

---

## 6. Фаза 4 — Тесты и стабилизация

- **Конкурентные тесты:** N параллельных покупок последнего билета → продан ровно 1;
  промокод не превышает лимит; (бонус) место не продаётся дважды.
- **QR-тесты:** admission QR принимается, Campaign QR отклоняется, подделанный QR отклоняется.
- **API-тесты** на auth, events, orders, check-in, support, analytics, admin.
- **End-to-end сценарий демо** (SRS §13.4) скриптом против поднятого compose.
- **README:** как поднять, `.env.example`, ссылка на `/docs` для фронта/мобилки.

---

## 7. Связи между модулями

- `audit.log(...)` вызывается каждым сервисом, меняющим состояние.
- `require_event_organizer` / `require_event_admin` защищают события, заказы, check-in,
  кампании и возвраты.
- `ticketing.issue_tickets(order)` → `notifications.send_ticket(...)` и `pdf.render_ticket(ticket)`.
- `qr.sign_ticket_payload` / `qr.verify_ticket_payload` (подпись) отдельно от `qr.make_png(data)` (картинка).
- Промокод применяется внутри `orders`; `promo/validate` возвращает только превью.

## 8. Работа с AI-инструментами

1. Давай AI контекст: `BACKEND_ARCHITECTURE.md` + нужную модель из `app/models/` + список эндпоинтов из плана.
    2. Проси сразу роутер + сервис + Pydantic-схемы + pytest-тесты. Правило архитектуры: роутер не трогает ORM напрямую.
3. Всегда прогоняй `pytest` и `ruff` перед PR, открывай `/docs` и руками дёргай эндпоинт.
4. Для RBAC проверяй, что чужой организатор получает 403 — это AI чаще всего забывает.
5. В коде с транзакциями и локами (резервирование, промокоды, check-in) обязательно читать сгенерированный код и покрывать конкурентными тестами.
