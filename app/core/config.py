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

    # Object storage (MinIO in dev)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "biletflow"
    # s3 → MinIO / S3; memory → in-process dict (tests)
    storage_backend: str = "s3"

    # Payments (simulated)
    activation_fee_minor: int = 500_000  # 5 000 KZT


@lru_cache
def get_settings() -> Settings:
    return Settings()
