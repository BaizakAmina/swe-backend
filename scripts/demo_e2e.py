"""End-to-end demo scenario (SRS §13.4) against a running stack.

    python scripts/demo_e2e.py                       # http://localhost:8000 + MailHog :8025
    python scripts/demo_e2e.py --base-url URL --mailhog-url URL --strict

Prints ✅ / ❌ / ⏳ / ⏭ per step:
  ✅ passed   ❌ failed   ⏳ endpoint not implemented yet (501)
  ⏭ skipped (depends on a step above)

Exit code: 1 if any step failed (with ``--strict`` also if any step is pending/skipped).
Uses fresh random accounts every run, so it can be re-run against the same database.
"""

from __future__ import annotations

import argparse
import email
import re
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import Message
from typing import Any

import httpx

PASSWORD = "demo-e2e-12345"


class NotImplementedYetError(Exception):
    """Endpoint answered 501 (part of the contract, not implemented yet)."""


class SkippedError(Exception):
    pass


class Demo:
    def __init__(self, base_url: str, mailhog_url: str) -> None:
        self.api = httpx.Client(base_url=base_url.rstrip("/") + "/api/v1", timeout=15)
        self.mailhog = httpx.Client(base_url=mailhog_url.rstrip("/"), timeout=10)
        self.ctx: dict[str, Any] = {}
        self.results: list[tuple[str, str, str]] = []
        self.run_id = uuid.uuid4().hex[:6]

    # ------------------------------------------------------------------ helpers

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        expect: int | tuple[int, ...] = (200, 201, 204),
        **kw: Any,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = self.api.request(method, path, headers=headers, **kw)
        if resp.status_code == 501:
            raise NotImplementedYetError(resp.json()["error"]["message"])
        expected = (expect,) if isinstance(expect, int) else expect
        if resp.status_code not in expected:
            raise AssertionError(f"{method} {path} → {resp.status_code}: {resp.text[:300]}")
        return resp

    def need(self, *keys: str) -> None:
        missing = [k for k in keys if k not in self.ctx]
        if missing:
            raise SkippedError(f"needs {', '.join(missing)}")

    def email_text(self, to: str, contains: str, attempts: int = 20) -> str:
        """Plain-text body of the newest email to ``to`` that contains ``contains``."""
        for _ in range(attempts):
            data = self.mailhog.get("/api/v2/search", params={"kind": "to", "query": to}).json()
            for item in data.get("items", []):
                msg = email.message_from_string(item["Raw"]["Data"])
                text = _plain_text(msg)
                if contains in text:
                    return text
            time.sleep(0.5)
        raise AssertionError(f"no email to {to} containing {contains!r} in MailHog")

    def account(self, label: str, role: str = "attendee") -> str:
        """Register → verify via the emailed link → login. Returns the access token."""
        addr = f"{label}-{self.run_id}@example.com"
        self.call(
            "POST",
            "/auth/register",
            json={"email": addr, "password": PASSWORD, "full_name": label.title(), "role": role},
        )
        body = self.email_text(addr, "verify-email?token=")
        token = re.search(r"verify-email\?token=([A-Za-z0-9_\-]+)", body)
        assert token, "verification link not found"
        self.call("POST", "/auth/verify-email", json={"token": token.group(1)})
        resp = self.call("POST", "/auth/login", json={"email": addr, "password": PASSWORD})
        self.ctx[f"{label}_email"] = addr
        return str(resp.json()["access_token"])

    # ------------------------------------------------------------------ runner

    def step(self, name: str, fn: Callable[[], str | None]) -> None:
        try:
            detail = fn() or ""
            self.results.append(("✅", name, detail))
        except NotImplementedYetError as exc:
            self.results.append(("⏳", name, str(exc)))
        except SkippedError as exc:
            self.results.append(("⏭", name, str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.results.append(("❌", name, f"{type(exc).__name__}: {exc}"))
        icon, _, detail = self.results[-1]
        print(f"{icon} {name}" + (f" — {detail}" if detail else ""), flush=True)

    # ------------------------------------------------------------------ scenario

    def run(self) -> None:
        c = self.ctx

        def health() -> str:
            assert self.api.get("/health/db").json()["database"] == "ok"
            return "api + database up"

        def organizer() -> str:
            c["org"] = self.account("organizer", role="organizer")
            return c["organizer_email"]

        def organizer_profile() -> str:
            self.need("org")
            t = c["org"]
            self.call(
                "POST",
                "/organizers/me",
                token=t,
                json={
                    "display_name": f"Demo Org {self.run_id}",
                    "contact_email": c["organizer_email"],
                },
            )
            self.call("POST", "/organizers/me/verify", token=t)
            self.call(
                "POST",
                "/organizers/me/payout-accounts",
                token=t,
                json={"provider_ref": f"sandbox_{self.run_id}"},
            )
            checklist = self.call("GET", "/organizers/me/activation-checklist", token=t).json()
            assert all(checklist.values()), checklist
            return "verified, payout connected"

        def create_event() -> str:
            self.need("org")
            t = c["org"]
            start = (datetime.now(UTC) + timedelta(days=21)).replace(microsecond=0)
            ev = self.call(
                "POST",
                "/events",
                token=t,
                json={
                    "title": f"Demo Concert {self.run_id}",
                    "description": "End-to-end demo",
                    "category": "music",
                    "location_name": "Kazakh National Philharmonic",
                    "location_address": "Kaldayakov St 35, Almaty",
                    "starts_at": start.isoformat(),
                    "ends_at": (start + timedelta(hours=3)).isoformat(),
                    "capacity": 100,
                },
            ).json()
            c["event_id"] = ev["id"]
            free = self.call(
                "POST",
                f"/events/{ev['id']}/ticket-types",
                token=t,
                json={"name": "Student", "kind": "free", "quantity_total": 20, "max_per_order": 2},
            ).json()
            paid = self.call(
                "POST",
                f"/events/{ev['id']}/ticket-types",
                token=t,
                json={
                    "name": "Standard",
                    "kind": "paid",
                    "price_minor": 1_000_000,
                    "quantity_total": 50,
                    "max_per_order": 4,
                },
            ).json()
            c["free_tt"], c["paid_tt"] = free["id"], paid["id"]
            published = self.call("POST", f"/events/{ev['id']}/publish", token=t).json()
            assert published["status"] == "published"
            listed = self.call("GET", "/events", params={"q": self.run_id}).json()
            assert listed["total"] == 1, "event not in public catalog"
            return f"published, in catalog ({ev['id'][:8]})"

        def activate_paid() -> str:
            self.need("org", "event_id")
            r = self.call(
                "POST",
                f"/events/{c['event_id']}/activate-paid-sales",
                token=c["org"],
                json={"simulate": "success"},
            ).json()
            assert r["paid_sales_active"] is True
            c["paid_active"] = True
            return "activation fee paid (simulated)"

        def campaign() -> str:
            self.need("org", "event_id")
            camp = self.call(
                "POST",
                f"/events/{c['event_id']}/campaigns",
                token=c["org"],
                json={
                    "name": "Launch",
                    "discount_type": "percent",
                    "discount_value": 10,
                    "max_redemptions": 10,
                    "ticket_type_ids": [c["paid_tt"]],
                },
            ).json()
            c["promo_code"], c["campaign_qr"] = camp["code"], camp["qr_link"]
            token = camp["qr_link"].split("?c=")[1]
            resolved = self.call("GET", "/campaigns/resolve", params={"token": token}).json()
            assert resolved["code"] == camp["code"]
            return f"promo {camp['code']}, Campaign QR resolves"

        def buyer() -> str:
            c["buyer"] = self.account("buyer")
            return c["buyer_email"]

        def free_registration() -> str:
            self.need("buyer", "free_tt")
            order = self.call(
                "POST",
                "/orders",
                token=c["buyer"],
                json={
                    "event_id": c["event_id"],
                    "items": [{"ticket_type_id": c["free_tt"], "quantity": 1}],
                    "attendees": [{"full_name": "Buyer", "email": c["buyer_email"]}],
                },
            ).json()
            if order["status"] == "pending":
                order = self.call(
                    "POST", f"/orders/{order['id']}/register-free", token=c["buyer"]
                ).json()
            assert order["status"] == "paid" and order["total_minor"] == 0, order
            c["free_order"] = order["id"]
            return "free ticket issued"

        def paid_purchase() -> str:
            self.need("buyer", "paid_active", "promo_code")
            order = self.call(
                "POST",
                "/orders",
                token=c["buyer"],
                json={
                    "event_id": c["event_id"],
                    "items": [{"ticket_type_id": c["paid_tt"], "quantity": 2}],
                    "attendees": [
                        {"full_name": "Buyer", "email": c["buyer_email"]},
                        {"full_name": "Friend", "email": f"friend-{self.run_id}@example.com"},
                    ],
                    "promo_code": c["promo_code"],
                },
            ).json()
            assert order["discount_minor"] == 200_000, order
            pay = self.call(
                "POST", f"/orders/{order['id']}/pay", token=c["buyer"], json={"simulate": "success"}
            ).json()
            assert pay["status"] == "succeeded" and pay["is_simulated"] is True
            c["paid_order"] = order["id"]
            return "2 × Standard with −10%, paid (simulated)"

        def my_tickets() -> str:
            self.need("buyer")
            if "free_order" not in c and "paid_order" not in c:
                raise SkippedError("needs an order")
            tickets = self.call("GET", "/me/tickets", token=c["buyer"]).json()["items"]
            mine = [t for t in tickets if t["event"]["id"] == c["event_id"]]
            assert mine, "no tickets"
            c["ticket"] = mine[0]
            pdf = self.call("GET", f"/tickets/{mine[0]['id']}/pdf", token=c["buyer"])
            assert pdf.content.startswith(b"%PDF")
            self.email_text(c["buyer_email"], mine[0]["ticket_code"])
            return f"{len(mine)} tickets, PDF ok, ticket email in MailHog"

        def staff() -> str:
            self.need("org", "event_id")
            c["staff"] = self.account("doorstaff")
            self.call(
                "POST",
                f"/events/{c['event_id']}/staff",
                token=c["org"],
                json={"email": c["doorstaff_email"]},
            )
            assigned = self.call("GET", "/me/assigned-events", token=c["staff"]).json()
            assert any(e["id"] == c["event_id"] for e in assigned)
            return "event admin sees the event"

        def check_in() -> str:
            self.need("staff", "ticket")
            body = {"event_id": c["event_id"], "qr_payload": c["ticket"]["qr_payload"]}
            first = self.call("POST", "/checkin", token=c["staff"], json=body).json()
            assert first["result"] == "valid", first
            second = self.call("POST", "/checkin", token=c["staff"], json=body, expect=(200, 409))
            assert second.status_code == 409 or second.json()["result"] == "already_checked_in"
            c["checked_in"] = True
            return "admitted once, second scan rejected"

        def campaign_qr_rejected() -> str:
            self.need("staff", "campaign_qr")
            body = {"event_id": c["event_id"], "qr_payload": c["campaign_qr"]}
            resp = self.call(
                "POST", "/checkin", token=c["staff"], json=body, expect=(200, 400, 409, 422)
            )
            data = resp.json()
            code = data.get("result") or data.get("error", {}).get("code")
            assert code == "campaign_qr_not_admission", data
            return "Campaign QR is not a ticket"

        def analytics() -> str:
            self.need("org", "event_id")
            a = self.call("GET", f"/events/{c['event_id']}/analytics", token=c["org"]).json()
            expected_net = 1_800_000 if "paid_order" in c else 0
            assert a["revenue"]["net_minor"] == expected_net, a["revenue"]
            timeline = self.call("GET", f"/events/{c['event_id']}/timeline", token=c["org"]).json()
            assert timeline["total"] >= 3
            return f"net {a['revenue']['net_minor'] // 100} ₸, timeline {timeline['total']} entries"

        def support() -> str:
            self.need("buyer", "org", "event_id")
            case = self.call(
                "POST",
                "/support/cases",
                token=c["buyer"],
                json={
                    "category": "event_info",
                    "subject": "Parking?",
                    "message": "Is there parking?",
                    "event_id": c["event_id"],
                },
            ).json()
            self.call(
                "POST",
                f"/support/cases/{case['id']}/messages",
                token=c["org"],
                json={"body": "Yes, free parking at the venue."},
            )
            detail = self.call("GET", f"/support/cases/{case['id']}", token=c["buyer"]).json()
            assert detail["status"] == "waiting_customer" and len(detail["messages"]) == 2
            return "case opened and answered"

        def refund() -> str:
            self.need("org", "paid_order")
            r = self.call(
                "POST", f"/orders/{c['paid_order']}/refund", token=c["org"], json={"reason": "demo"}
            ).json()
            assert r["is_simulated"] is True
            order = self.call("GET", f"/orders/{c['paid_order']}", token=c["buyer"]).json()
            assert order["status"] == "refunded"
            return "full refund (simulated)"

        steps: list[tuple[str, Callable[[], str | None]]] = [
            ("API is up", health),
            ("1. Organizer registers, verifies email (MailHog), logs in", organizer),
            ("2. Organizer profile, verification, payout account", organizer_profile),
            ("2. Create event + free & paid ticket types, publish", create_event),
            ("3. Activate paid sales (simulated fee)", activate_paid),
            ("4. Campaign with promo code + Campaign QR", campaign),
            ("5. Buyer registers, verifies, logs in", buyer),
            ("5. Free registration", free_registration),
            ("5. Paid purchase with promo code + simulated payment", paid_purchase),
            ("6. Tickets in /me/tickets, PDF, email", my_tickets),
            ("7. Assign event admin → /me/assigned-events", staff),
            ("7. Check-in; second check-in rejected", check_in),
            ("7. Campaign QR rejected at check-in", campaign_qr_rejected),
            ("8. Analytics + timeline", analytics),
            ("9. Support case + reply", support),
            ("10. Refund an order", refund),
        ]
        for name, fn in steps:
            self.step(name, fn)


def _plain_text(msg: Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode(part.get_content_charset() or "utf-8", "replace")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--mailhog-url", default="http://localhost:8025")
    parser.add_argument("--strict", action="store_true", help="pending/skipped steps also fail")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    demo = Demo(args.base_url, args.mailhog_url)
    print(f"BiletFlow demo — {args.base_url} (run {demo.run_id})\n")
    demo.run()

    counts = {k: sum(1 for r in demo.results if r[0] == k) for k in ("✅", "❌", "⏳", "⏭")}
    print(
        f"\n✅ {counts['✅']} passed · ❌ {counts['❌']} failed · "
        f"⏳ {counts['⏳']} not implemented yet · ⏭ {counts['⏭']} skipped"
    )
    failed = counts["❌"] > 0 or (args.strict and (counts["⏳"] or counts["⏭"]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
