from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = Field(default="development", alias="APP_ENV")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, gt=0, alias="API_PORT")
    database_url: str = Field(
        default="postgresql+psycopg://gaijin_market:gaijin_market_dev@localhost:5432/gaijin_market_analytics",
        alias="DATABASE_URL",
    )
    cors_allowed_origins: str = Field(
        default="http://localhost:3000",
        alias="CORS_ALLOWED_ORIGINS",
    )
    analytics_maximum_snapshot_age_hours: int = Field(
        default=24,
        gt=0,
        alias="ANALYTICS_MAXIMUM_SNAPSHOT_AGE_HOURS",
    )
    analytics_minimum_snapshot_count: int = Field(
        default=3,
        gt=0,
        alias="ANALYTICS_MINIMUM_SNAPSHOT_COUNT",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def parse_cors_allowed_origins(value: str) -> list[str]:
    return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
