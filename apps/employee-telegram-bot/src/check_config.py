"""Проверка настроек бота. Ничего не отправляет и никуда не ходит.

    python -m src.check_config

Нужна потому, что три из четырёх способов сломать привязку — это опечатка
в `.env`, а замечается она молчанием: человек переходит по ссылке, бот
получает от backend 403 и пишет «сервис недоступен». Здесь то же самое
видно сразу и по имени переменной.

Сеть не трогается намеренно. Проверка конфигурации, которая отправляет
сообщение или дёргает Telegram, перестаёт быть безопасной: её нельзя
запускать на боевом боте между делом.

Секреты не печатаются. Показывается только длина и то, задано ли значение, —
этого достаточно, чтобы отличить «не задано» от «задано неправильно», и
недостаточно, чтобы утечь из журнала CI.
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

from src.config.settings import settings

OK = "  OK    "
WARN = "  ВНИМ. "
FAIL = "  ОШИБКА"


def _mask(value: str) -> str:
    return f"задано, {len(value)} символов" if value else "НЕ ЗАДАНО"


def check() -> list[str]:
    """Возвращает список проблем. Пустой список — конфигурация рабочая."""
    problems: list[str] = []

    print("== Telegram ==")
    if not settings.bot_token:
        problems.append("BOT_TOKEN не задан")
        print(f"{FAIL} BOT_TOKEN: НЕ ЗАДАНО")
    elif ":" not in settings.bot_token:
        # Настоящий токен выглядит как `<цифры>:<буквы и цифры>`.
        problems.append("BOT_TOKEN не похож на токен Telegram")
        print(f"{FAIL} BOT_TOKEN: задано, но не похоже на токен Telegram")
    else:
        print(f"{OK} BOT_TOKEN: {_mask(settings.bot_token)}")

    if settings.bot_username:
        print(f"{OK} BOT_USERNAME: @{settings.bot_username}")
    else:
        # Ссылки собирает backend, боту имя нужно только для подсказок.
        print(f"{WARN} BOT_USERNAME: не задано (ссылки собирает backend)")

    print("== backend-api ==")
    parsed = urlparse(settings.backend_api_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        problems.append("BACKEND_API_URL не похож на адрес")
        print(f"{FAIL} BACKEND_API_URL: {settings.backend_api_url!r}")
    else:
        print(f"{OK} BACKEND_API_URL: {settings.backend_api_url}")

    if not settings.backend_bot_secret:
        # Без секрета backend отклонит ВСЁ: и привязку, и личный кабинет.
        # Токенов сотрудников больше нет, и этот секрет — единственное,
        # чем бот доказывает, что он наш.
        problems.append(
            "BACKEND_BOT_SECRET не задан: backend отклонит все запросы бота"
        )
        print(f"{FAIL} BACKEND_BOT_SECRET: НЕ ЗАДАНО")
    else:
        print(f"{OK} BACKEND_BOT_SECRET: {_mask(settings.backend_bot_secret)}")

    if parsed.scheme != "https" and parsed.hostname not in (
        "localhost", "127.0.0.1"
    ):
        problems.append(
            "BACKEND_API_URL без https: секрет бота пойдёт открытым текстом"
        )
        print(f"{FAIL} BACKEND_API_URL без https на внешнем адресе")

    print("== личный кабинет ==")
    if not settings.mini_app_url:
        # Не ошибка: бот работает и без кабинета, просто без кнопки.
        print(f"{WARN} MINI_APP_URL: не задано — кнопка кабинета не появится")
    elif not settings.mini_app_url.startswith("https://"):
        # Telegram открывает Mini App только по https.
        problems.append("MINI_APP_URL обязан начинаться с https://")
        print(f"{FAIL} MINI_APP_URL: Telegram откроет только https")
    else:
        print(f"{OK} MINI_APP_URL: {settings.mini_app_url}")

    return problems


def main() -> int:
    problems = check()
    print()
    if problems:
        print(f"Проблем: {len(problems)}")
        for item in problems:
            print(f"  * {item}")
        return 1
    print("Конфигурация в порядке. Ни одного обращения наружу не выполнено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
