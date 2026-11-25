"""Payment gateway port and its simulated implementation (BACKEND_ARCHITECTURE.md §5.6).

Every payment is simulated: rows carry ``is_simulated = true`` and are never presented as real
money movement. Connecting a real sandbox later means one more ``PaymentGateway``
implementation and a new value of ``settings.payment_gateway`` — no schema change.
"""

import secrets
from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings
from app.models.enums import PaymentProvider

SIMULATED_OUTCOMES = ("success", "fail")


@dataclass(frozen=True)
class ChargeResult:
    succeeded: bool
    provider_ref: str
    failure_reason: str | None = None


@dataclass(frozen=True)
class RefundResult:
    succeeded: bool
    provider_ref: str
    failure_reason: str | None = None


class PaymentGateway(Protocol):
    provider: PaymentProvider
    is_simulated: bool

    def charge(
        self, *, amount_minor: int, currency: str, reference: str, simulate: str
    ) -> ChargeResult:
        """Charge ``amount_minor``. ``reference`` is our order id; ``simulate`` is demo-only."""
        ...

    def refund(self, *, provider_ref: str, amount_minor: int) -> RefundResult: ...


class SimulatedGateway:
    """No money moves. The outcome of a charge is chosen by the caller (``simulate``)."""

    provider = PaymentProvider.SIMULATION
    is_simulated = True

    @staticmethod
    def _ref(kind: str) -> str:
        return f"sim_{kind}_{secrets.token_hex(12)}"

    def charge(
        self, *, amount_minor: int, currency: str, reference: str, simulate: str
    ) -> ChargeResult:
        if amount_minor <= 0:
            raise ValueError("amount_minor must be positive")
        if simulate not in SIMULATED_OUTCOMES:
            raise ValueError(f"simulate must be one of {SIMULATED_OUTCOMES}")
        if simulate == "success":
            return ChargeResult(succeeded=True, provider_ref=self._ref("ch"))
        return ChargeResult(
            succeeded=False,
            provider_ref=self._ref("ch"),
            failure_reason="Payment declined (simulated)",
        )

    def refund(self, *, provider_ref: str, amount_minor: int) -> RefundResult:
        if amount_minor <= 0:
            raise ValueError("amount_minor must be positive")
        return RefundResult(succeeded=True, provider_ref=self._ref("rf"))


def get_gateway() -> PaymentGateway:
    """FastAPI dependency / factory: the gateway named in ``settings.payment_gateway``."""
    name = get_settings().payment_gateway
    if name == "simulated":
        return SimulatedGateway()
    raise RuntimeError(f"Unknown payment gateway: {name!r}")
