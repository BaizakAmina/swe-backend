"""Append-only audit trail (SRS §4.16, §7).

Call ``audit.log(...)`` from every service that changes state on behalf of an organizer,
event admin, platform admin or the system. The entry is added to the *caller's* session and is
committed or rolled back together with the business change — this function never commits.

Always pass ``event_id`` when the change belongs to an event (ticket types, orders, check-ins,
campaigns, refunds, staff …): the event activity timeline is built from it.

Example::

    audit.log(
        db,
        actor_id=current_user.id,
        action=AuditAction.EVENT_PUBLISHED,
        entity_type="event",
        entity_id=event.id,
        event_id=event.id,
        description=f"Published event “{event.title}”",
    )
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


class AuditAction(StrEnum):
    # auth / users
    USER_REGISTERED = "user.registered"
    USER_EMAIL_VERIFIED = "user.email_verified"
    USER_PASSWORD_CHANGED = "user.password_changed"
    USER_SUSPENDED = "user.suspended"
    USER_UNSUSPENDED = "user.unsuspended"
    # organizers
    ORGANIZER_PROFILE_CREATED = "organizer.profile_created"
    ORGANIZER_PROFILE_UPDATED = "organizer.profile_updated"
    ORGANIZER_VERIFIED = "organizer.verified"
    PAYOUT_ACCOUNT_CONNECTED = "payout_account.connected"
    PAYOUT_ACCOUNT_UPDATED = "payout_account.updated"
    # events
    EVENT_CREATED = "event.created"
    EVENT_UPDATED = "event.updated"
    EVENT_PUBLISHED = "event.published"
    EVENT_UNPUBLISHED = "event.unpublished"
    EVENT_CANCELLED = "event.cancelled"
    EVENT_DUPLICATED = "event.duplicated"
    EVENT_SUSPENDED = "event.suspended"
    EVENT_UNSUSPENDED = "event.unsuspended"
    EVENT_PAID_SALES_ACTIVATED = "event.paid_sales_activated"
    # ticket types
    TICKET_TYPE_CREATED = "ticket_type.created"
    TICKET_TYPE_UPDATED = "ticket_type.updated"
    TICKET_TYPE_HIDDEN = "ticket_type.hidden"
    TICKET_TYPE_UNHIDDEN = "ticket_type.unhidden"
    TICKET_TYPE_DELETED = "ticket_type.deleted"
    # staff
    STAFF_ASSIGNED = "staff.assigned"
    STAFF_REMOVED = "staff.removed"
    # orders / payments / tickets
    ORDER_CREATED = "order.created"
    ORDER_PAID = "order.paid"
    ORDER_EXPIRED = "order.expired"
    ORDER_CANCELLED = "order.cancelled"
    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"
    TICKETS_ISSUED = "ticket.issued"
    TICKET_CANCELLED = "ticket.cancelled"
    REFUND_PROCESSED = "refund.processed"
    # check-in
    TICKET_CHECKED_IN = "ticket.checked_in"
    CHECK_IN_REVERSED = "ticket.check_in_reversed"
    # campaigns
    CAMPAIGN_CREATED = "campaign.created"
    CAMPAIGN_UPDATED = "campaign.updated"
    CAMPAIGN_DISABLED = "campaign.disabled"
    CAMPAIGN_ENABLED = "campaign.enabled"
    PROMO_REDEEMED = "promo.redeemed"
    # support
    SUPPORT_CASE_OPENED = "support.case_opened"
    SUPPORT_CASE_UPDATED = "support.case_updated"
    # admin
    PLATFORM_SETTING_UPDATED = "platform.setting_updated"


def log(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    action: AuditAction | str,
    entity_type: str,
    entity_id: uuid.UUID,
    description: str,
    event_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an audit entry to the current transaction (no commit)."""
    entry = AuditLog(
        actor_user_id=actor_id,
        action_type=str(action),
        entity_type=entity_type,
        entity_id=entity_id,
        event_id=event_id,
        description=description,
        metadata_=metadata or {},
        # Set in code (not now() in SQL) so entries within one transaction keep their order.
        occurred_at=datetime.now(UTC),
    )
    db.add(entry)
    return entry
