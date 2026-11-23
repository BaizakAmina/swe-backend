"""Domain enums. Stored as VARCHAR + CHECK constraint (no native PG enums → simpler migrations)."""

from enum import StrEnum


class Locale(StrEnum):
    KK = "kk"
    RU = "ru"
    EN = "en"


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Role(StrEnum):
    ATTENDEE = "attendee"
    ORGANIZER = "organizer"
    PLATFORM_ADMIN = "platform_admin"


class TokenPurpose(StrEnum):
    VERIFY_EMAIL = "verify_email"
    PASSWORD_RESET = "password_reset"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"


class PayoutAccountStatus(StrEnum):
    PENDING = "pending"
    CONNECTED = "connected"
    DISABLED = "disabled"


class EventVisibility(StrEnum):
    PUBLIC = "public"
    UNLISTED = "unlisted"
    PRIVATE = "private"


class SeatingMode(StrEnum):
    GENERAL_ADMISSION = "general_admission"
    ASSIGNED = "assigned"


class EventStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CANCELLED = "cancelled"


class EventLifecycle(StrEnum):
    """Derived from status + dates, never stored."""

    UPCOMING = "upcoming"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TicketKind(StrEnum):
    FREE = "free"
    PAID = "paid"


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    EXPIRED = "expired"


class TicketStatus(StrEnum):
    VALID = "valid"
    CHECKED_IN = "checked_in"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentPurpose(StrEnum):
    TICKET_PURCHASE = "ticket_purchase"
    ACTIVATION_FEE = "activation_fee"


class PaymentProvider(StrEnum):
    SIMULATION = "simulation"
    SANDBOX = "sandbox"


class PaymentStatus(StrEnum):
    INITIATED = "initiated"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CheckInAction(StrEnum):
    CHECK_IN = "check_in"
    REVERSE = "reverse"


class StaffRole(StrEnum):
    EVENT_ADMIN = "event_admin"


class NotificationChannel(StrEnum):
    EMAIL = "email"
    IN_APP = "in_app"


class SupportCaseKind(StrEnum):
    ATTENDEE = "attendee"
    ORGANIZER = "organizer"


class SupportCategory(StrEnum):
    TICKET_DELIVERY = "ticket_delivery"
    PAYMENT = "payment"
    REFUND = "refund"
    SEATING = "seating"
    EVENT_INFO = "event_info"
    CHECKIN = "checkin"
    ACCOUNT = "account"
    TECHNICAL = "technical"


class SupportStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_CUSTOMER = "waiting_customer"
    RESOLVED = "resolved"


class DiscountType(StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"


class CampaignStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
