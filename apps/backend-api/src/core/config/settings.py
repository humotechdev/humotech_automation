from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Настройки backend-api. Секреты только через .env, никогда в коде."""

    model_config = SettingsConfigDict(
        env_file=APP_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # postgresql+psycopg://user:password@host:5432/humotech
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/humotech"

    # отдельная база под тесты: она создаётся и дропается целиком
    test_database_url: str | None = None

    sql_echo: bool = False
    log_level: str = "INFO"


settings = Settings()
