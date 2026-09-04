"""Как описываются в схеме два собственных способа входа.

Генератор умеет описывать только знакомые ему способы аутентификации.
Свои он пропускает с предупреждением, и в схеме у половины эндпоинтов
не остаётся ни слова о том, чем к ним обращаться. Клиент, собранный по
такой схеме, шлёт запросы без заголовков и получает 401, а причину
приходится искать в коде backend.

Расширения ничего не меняют в поведении: они только рассказывают
о нём. Заголовки и их смысл описаны ровно те, что проверяет
`humotech/telegram/auth.py`.
"""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension

from humotech.telegram.auth import BOT_EMPLOYEE_HEADER, BOT_SECRET_HEADER


class MiniAppAuthenticationExtension(OpenApiAuthenticationExtension):
    """Вход Mini App: токен, выданный после проверки подписи Telegram."""

    target_class = "humotech.telegram.auth.MiniAppAuthentication"
    name = "miniAppToken"

    def get_security_definition(self, auto_schema) -> dict:
        return {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "Токен из `POST /api/v1/telegram/mini-app/auth`. "
                "Состояние привязки перечитывается на каждом запросе, "
                "поэтому отзыв действует немедленно, а не с истечением "
                "срока токена."
            ),
        }


class BotEmployeeAuthenticationExtension(OpenApiAuthenticationExtension):
    """Вход бота за сотрудника: общий секрет плюс подтверждённый Telegram ID.

    В схеме это два заголовка, и оба обязательны. Порядок проверки в коде
    именно такой: без верного секрета заголовок с Telegram ID — просто
    число, которое написал отправитель запроса.
    """

    target_class = "humotech.telegram.auth.BotEmployeeAuthentication"
    name = "botEmployee"

    def get_security_definition(self, auto_schema) -> dict:
        return {
            "type": "apiKey",
            "in": "header",
            "name": BOT_SECRET_HEADER,
            "description": (
                f"Общий секрет бота. Вместе с ним обязателен заголовок "
                f"`{BOT_EMPLOYEE_HEADER}` — подтверждённый Telegram ID "
                f"сотрудника, от имени которого действует бот."
            ),
        }


__all__ = [
    "BotEmployeeAuthenticationExtension",
    "MiniAppAuthenticationExtension",
]
