from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./chargedev.db"
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    platform_fee_pct: float = 0.20  # 20%
    default_price_per_kwh: float = 0.20  # EUR

    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]


settings = Settings()
