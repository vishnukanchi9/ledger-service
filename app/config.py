from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # pg8000 rather than psycopg: pure Python, so it installs without a
    # compiler or libpq on both Linux CI runners and Windows/ARM laptops.
    database_url: str = "postgresql+pg8000://ledger:ledger@localhost:5432/ledger"

    api_key: str = "dev-key-change-me"

    # Token bucket, per API key.
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 20

    default_page_size: int = 50
    max_page_size: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
