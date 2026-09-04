from pathlib import Path

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
    # Имя бота нужно только для подсказок в тексте: ссылки привязки
    # собирает backend — он же знает, кому и на какой срок их выдал.
    bot_username: str = ""

    backend_api_url: str = "http://localhost:8000/api/v1"
    api_timeout_seconds: int = 10

    # Общий секрет с backend. Им бот доказывает, что `telegram_user_id`
    # в запросе привязки пришёл от Telegram через него, а не выдуман
    # отправителем: проверить это своими силами backend не может.
    backend_bot_secret: str = ""

    # Адрес Mini App. Без него кнопка личного кабинета не показывается
    # вовсе: кнопка, которая ничего не открывает, хуже её отсутствия.
    mini_app_url: str = ""

    # Как часто спрашивать очередь уведомлений.
    notifications_poll_seconds: int = 5

    bot_internal_token: str = "change-me"
    bot_internal_port: int = 8081

    log_level: str = "INFO"
    default_language: str = "ru"


settings = Settings()
