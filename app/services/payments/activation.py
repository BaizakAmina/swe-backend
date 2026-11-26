"""Paid-sales activation (SRS §3.2, §4.5).

Until an event's organizer has passed the checklist (profile complete, verified, payout account
connected) and paid the activation fee (simulated), only free tickets can be sold for it —
``ticket_types.is_sellable`` / ``orders.create_order`` enforce that.

``activate`` runs in one transaction under ``SELECT … FOR UPDATE`` on the event row, so two
parallel activations can't both charge the fee: the second waits and then gets 409.
"""

import uuid
from datetime import UTC, datetime

from fastapi import status as http
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import Event, OrganizerProfile, Payment, User
from app.models.enums import EventStatus, PaymentPurpose, PaymentStatus
from app.schemas.orders import ActivationStatusRead, PaymentRead
from app.schemas.organizers import ActivationChecklist
from app.services import audit, notifications, organizers, platform_settings
from app.services.audit import AuditAction
from app.services.payments.gateway import PaymentGateway

# Checklist field → what the organizer has to do (shown in the 422 error).
CHECKLIST_STEPS = {
    "profile_complete": "Complete the organizer profile",
    "verified": "Get the organizer profile verified",
    "payout_account_connected": "Connect a payout account",
    "terms_accepted": "Accept the paid-event terms",
}


def _checklist(db: Session, event: Event) -> ActivationChecklist:
    profile = db.get(OrganizerProfile, event.organizer_id)
    if profile is None:  # FK guarantees it; keeps mypy and odd data honest
        return ActivationChecklist(
            profile_complete=False,
            verified=False,
            payout_account_connected=False,
            terms_url=organizers.paid_terms_url(),
        )
    return organizers.get_activation_checklist(db, profile)


def _last_activation_payment(db: Session, event: Event) -> Payment | None:
    """The succeeded fee payment if there is one, else the latest attempt."""
    stmt = select(Payment).where(
        Payment.event_id == event.id, Payment.purpose == PaymentPurpose.ACTIVATION_FEE
    )
    succeeded = db.scalar(stmt.where(Payment.status == PaymentStatus.SUCCEEDED).limit(1))
    return succeeded or db.scalar(stmt.order_by(Payment.created_at.desc()).limit(1))


def get_status(db: Session, event: Event) -> ActivationStatusRead:
    checklist = _checklist(db, event)
    payment = _last_activation_payment(db, event)
    return ActivationStatusRead(
        event_id=event.id,
        paid_sales_active=event.paid_sales_active,
        fee_minor=platform_settings.activation_fee_minor(db),
        profile_complete=checklist.profile_complete,
        verified=checklist.verified,
        payout_account_connected=checklist.payout_account_connected,
        terms_accepted=checklist.terms_accepted,
        terms_version=checklist.terms_version,
        terms_url=checklist.terms_url,
        activation_payment=PaymentRead.model_validate(payment) if payment else None,
    )


def _log_payment(db: Session, payment: Payment, event: Event, user: User) -> None:
    amount = notifications.format_money(payment.amount_minor, payment.currency)
    ok = payment.status == PaymentStatus.SUCCEEDED
    audit.log(
        db,
        actor_id=user.id,
        action=AuditAction.PAYMENT_SUCCEEDED if ok else AuditAction.PAYMENT_FAILED,
        entity_type="payment",
        entity_id=payment.id,
        event_id=event.id,
        description=(
            f"Activation fee of {amount} paid (simulated)"
            if ok
            else f"Activation fee of {amount} failed (simulated): {payment.failure_reason}"
        ),
        metadata={
            "purpose": PaymentPurpose.ACTIVATION_FEE.value,
            "amount_minor": payment.amount_minor,
            "provider_ref": payment.provider_ref,
            "is_simulated": payment.is_simulated,
        },
    )


def activate(
    db: Session,
    user: User,
    event_id: uuid.UUID,
    *,
    simulate: str,
    gateway: PaymentGateway,
) -> ActivationStatusRead:
    """Checklist → activation fee (simulated) → ``event.paid_sales_active = true``.

    A failed charge changes nothing but is recorded; the organizer can try again.
    The caller has already checked that ``user`` may manage the event.
    """
    event = db.get(Event, event_id, with_for_update=True, populate_existing=True)
    if event is None:
        raise NotFoundError("event")
    if event.paid_sales_active:
        raise ConflictError("paid_sales_already_active", "Paid sales are already active")
    if event.status == EventStatus.CANCELLED:
        raise ConflictError("event_cancelled", "The event is cancelled")

    checklist = _checklist(db, event)
    missing = [step for step in CHECKLIST_STEPS if not getattr(checklist, step)]
    if missing:
        raise AppError(
            "activation_checklist_incomplete",
            "Before paid sales: " + "; ".join(CHECKLIST_STEPS[s] for s in missing),
            http.HTTP_422_UNPROCESSABLE_ENTITY,
            {"missing": missing},
        )

    fee = platform_settings.activation_fee_minor(db)
    succeeded = True
    if fee > 0:
        payment = Payment(
            event_id=event.id,
            payer_user_id=user.id,
            purpose=PaymentPurpose.ACTIVATION_FEE,
            amount_minor=fee,
            currency="KZT",
            provider=gateway.provider,
            is_simulated=gateway.is_simulated,
            status=PaymentStatus.INITIATED,
        )
        db.add(payment)
        db.flush()
        result = gateway.charge(
            amount_minor=fee,
            currency="KZT",
            reference=f"activation:{event.id}",
            simulate=simulate,
        )
        payment.provider_ref = result.provider_ref
        payment.completed_at = datetime.now(UTC)
        payment.status = PaymentStatus.SUCCEEDED if result.succeeded else PaymentStatus.FAILED
        payment.failure_reason = None if result.succeeded else result.failure_reason
        db.flush()
        _log_payment(db, payment, event, user)
        succeeded = result.succeeded

    if succeeded:
        event.paid_sales_active = True
        audit.log(
            db,
            actor_id=user.id,
            action=AuditAction.EVENT_PAID_SALES_ACTIVATED,
            entity_type="event",
            entity_id=event.id,
            event_id=event.id,
            description="Paid ticket sales activated",
            metadata={"fee_minor": fee},
        )
    db.commit()
    return get_status(db, event)
