from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables / `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "BiletFlow API"
    environment: str = "local"
    debug: bool = False
    api_prefix: str = "/api/v1"
    public_web_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Database
    database_url: str = "postgresql+psycopg://biletflow:biletflow@localhost:5432/biletflow"

    # Auth
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14

    # QR signing (admission tickets)
    qr_secret: str = "change-me-too"

    # Orders
    reservation_ttl_minutes: int = 10
    # Background sweep of expired reservations (seconds between runs; 0 disables it).
    order_sweep_interval_seconds: float = 30.0
    # Assigned seating: how long a seat stays held before checkout, and how many at once.
    seat_hold_ttl_minutes: int = 10
    max_seat_holds_per_user: int = 10

    # Email (MailHog in dev)
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    mail_from: str = "BiletFlow <no-reply@biletflow.kz>"
    # smtp → real SMTP (MailHog in dev); console → log only; memory → kept in a list (tests)
    email_backend: str = "smtp"
    smtp_timeout_seconds: float = 5.0
    # Public base URL of this API (links to PDFs etc. in emails)
    api_base_url: str = "http://localhost:8000"
    # Address clients use to reach this API, for links returned in API responses (cover images,
    # calendar files). Empty → the address of the incoming request (works behind a proxy that
    # forwards Host / X-Forwarded-Proto); outside a request → api_base_url.
    public_api_url: str = ""

    # Object storage (S3-compatible: SeaweedFS in docker compose, MinIO/AWS S3 also work)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "biletflow"
    # s3 → any S3-compatible service; memory → in-process dict (tests)
    storage_backend: str = "s3"

    # Paid-event terms organizers accept before paid sales; bump the version to ask again.
    paid_terms_version: str = "2026-11"
    paid_terms_path: str = "/terms/paid-events"

    # Payments (simulated)
    activation_fee_minor: int = 500_000  # 5 000 KZT
    # PaymentGateway implementation (app/services/payments/gateway.py); only "simulated" for now.
    payment_gateway: str = "simulated"


@lru_cache
def get_settings() -> Settings:
    return Settings()
