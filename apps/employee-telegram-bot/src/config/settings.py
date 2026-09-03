from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Все настройки бота. Читаются из .env, ничего не хардкодится в коде."""

    model_config = SettingsConfigDict(
        env_file=APP_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str

    # stub — фейковые данные вместо backend-api; live — реальные HTTP-запросы
    api_mode: Literal["stub", "live"] = "stub"
    backend_api_url: str = "http://localhost:8000/api/v1"
    api_timeout_seconds: int = 10

    bot_internal_token: str = "change-me"
    bot_internal_port: int = 8081

    log_level: str = "INFO"
    default_language: str = "ru"


settings = Settings()
