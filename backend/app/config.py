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
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    platform_fee_pct: float = 0.20  # 20%
    default_price_per_kwh: float = 0.25  # EUR

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    frontend_url: str = "https://chargedev.io"
    resend_api_key: str = ""
    email_from: str = "ChargedEV <noreply@chargedev.io>"

    allowed_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://chargedev.io",
        "https://www.chargedev.io",
    ]


settings = Settings()
