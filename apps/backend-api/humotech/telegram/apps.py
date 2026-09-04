from django.apps import AppConfig


class TelegramConfig(AppConfig):
    name = "humotech.telegram"
    label = "telegram"
    verbose_name = "Привязка Telegram"

    def ready(self) -> None:
        # Описания собственных способов входа для схемы OpenAPI.
        # Импорт нужен ради регистрации расширений: без него генератор
        # не знает про них и молча оставляет половину эндпоинтов без
        # указания, чем к ним обращаться.
        from humotech.telegram import schema  # noqa: F401
