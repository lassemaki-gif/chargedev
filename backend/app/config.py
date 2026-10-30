from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./chargedev.db"

    @property
    def async_database_url(self) -> str:
        """Ensure the URL uses the correct async driver scheme."""
        url = self.database_url
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    secret_key: str = ""
    access_token_expire_minutes: int = 60  # 1 hour

    platform_fee_pct: float = 0.20  # 20%
    default_price_per_kwh: float = 0.30  # EUR

    # Fernet key for at-rest encryption (host IBANs, Shelly auth keys).
    # Previously named MOLLIE_TOKEN_KEY — kept as a fallback env name so an
    # existing deploy doesn't break when it hasn't rotated the var yet.
    encryption_key: str = Field(
        default="",
        validation_alias=AliasChoices("ENCRYPTION_KEY", "MOLLIE_TOKEN_KEY"),
    )

    # SumUp Online Payments API (single-merchant acquiring; no sub-merchant OAuth).
    # All checkouts settle to ChargedEV's own merchant account; hosts are paid
    # weekly via SEPA from the backend/app/payouts module.
    sumup_secret_key: str = ""   # sup_sk_… — backend-only, keep out of frontend
    sumup_merchant_code: str = ""  # M-XXXXXXXX from the SumUp dashboard
    sumup_webhook_secret: str = ""  # shared secret for webhook signature verification
    backend_url: str = "https://chargedev-production.up.railway.app"
    frontend_url: str = "https://chargedev.io"
    resend_api_key: str = ""
    email_from: str = "ChargedEV <noreply@chargedev.io>"

    # Production origins only. Local development must override via the
    # ALLOWED_ORIGINS env var (comma-separated) so localhost cannot sneak into
    # a prod CORS allowlist with allow_credentials=True.
    allowed_origins: list[str] = [
        "https://chargedev.io",
        "https://www.chargedev.io",
    ]


settings = Settings()

JWT_ALGORITHM: str = "HS256"
