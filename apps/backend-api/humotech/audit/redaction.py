"""Маскирование секретов в журнале аудита и в логах.

Журнал аудита читают кадровики с правом `audit.read`, логи — все, у кого
есть доступ к серверу или к агрегатору. Ни туда, ни туда не должны попасть
пароль, токен бота, ключ API или содержимое заголовка `Authorization`.

Фильтр `AuditTrail.sanitize` отбрасывает известные имена полей верхнего
уровня. Здесь — вторая линия, которая работает на ЛЮБОЙ записи в
`audit_logs` (её вызывает `AuditLog.save`), независимо от того, каким
путём запись создана:

  * имена полей проверяются по образцу, а не по точному списку:
    `new_password`, `telegram_bot_token`, `Authorization` тоже секреты;
  * проверка рекурсивная — секрет, вложенный в словарь или список,
    находится так же, как на верхнем уровне;
  * значения-строки проверяются по форме: токен бота Telegram
    (`123456:AA…`), `password=…`, `Bearer …` маскируются, где бы ни
    стояли.

Ключ заменяется маской, а не удаляется: «пароль меняли» — полезный факт
для разбора, само значение — нет.
"""

from __future__ import annotations

import logging
import re
from typing import Any

MASK = "[скрыто]"

#: Имена полей, которые являются секретом целиком.
_EXACT = frozenset(
    {
        "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
        "authorization", "cookie", "set-cookie", "x-bot-token", "csrftoken",
        "sessionid", "init_data", "initdata", "private_key", "hash",
    }
)

#: Окончания и начала имён полей, по которым секрет узнаётся в составе
#: имени: `new_password`, `bot_token`, `pairing_secret_hash`. Окончание
#: `_token` не задевает `token_version` и `input_tokens` — это счётчики.
_SUFFIXES = (
    "_password", "_passwd", "_secret", "_token", "_hash", "_api_key",
    "_apikey", "_private_key", "_credential", "_credentials", "_dsn",
)
_PREFIXES = ("password", "secret_", "api_key_value")

#: Строки, опасные по форме, где бы они ни стояли.
_VALUE_PATTERNS = (
    # Токен бота Telegram: числовой id, двоеточие, 30+ символов base64url.
    # Без `\b` в начале: в адресе API токен стоит сразу за `bot`.
    re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_\-]{30,}"),
    # `password=…`, `token: …`, `api_key=…` в тексте или строке запроса.
    re.compile(
        r"(?i)\b(пароль|password|passwd|pwd|secret|token|api[_\-]?key|"
        r"access_token|bot_token)(\s*[:=]\s*)([^\s&,;\"']+)"
    ),
    # Заголовок авторизации.
    re.compile(r"(?i)\b(bearer|basic)(\s+)([A-Za-z0-9._~+/=\-]{6,})"),
    # DSN с паролем: scheme://user:pass@host
    re.compile(r"([a-z][a-z0-9+.\-]*://[^\s:/@]+:)([^\s@/]+)(@)"),
)


def is_secret_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    name = key.strip().lower()
    return (
        name in _EXACT
        or name.endswith(_SUFFIXES)
        or name.startswith(_PREFIXES)
    )


def redact_text(text: str) -> str:
    """Замаскировать опасные по форме фрагменты строки."""
    if not text:
        return text
    out = _VALUE_PATTERNS[0].sub(MASK, text)
    out = _VALUE_PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    out = _VALUE_PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    out = _VALUE_PATTERNS[3].sub(lambda m: f"{m.group(1)}{MASK}{m.group(3)}", out)
    return out


#: Глубже журнал не разбирает: вложенность такой глубины в аудите
#: не бывает, а бесконечная рекурсия на присланном извне JSON — бывает.
_MAX_DEPTH = 20


def redact_values(value: Any, _depth: int = 0) -> Any:
    """Рекурсивно замаскировать секреты в значениях журнала."""
    if value is None:
        return None
    if _depth > _MAX_DEPTH:
        return MASK
    if isinstance(value, dict):
        return {
            key: (MASK if is_secret_key(key) else redact_values(item, _depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_values(item, _depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class SecretRedactingFilter(logging.Filter):
    """Фильтр логов: маскирует секреты в готовом тексте сообщения.

    Сообщение собирается здесь же (`getMessage`) и кладётся обратно без
    аргументов: иначе секрет, переданный аргументом, подставился бы уже
    после фильтра. Текст исключения не трогается — трассировка не
    содержит значений локальных переменных.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — кривой формат не должен ронять лог
            return True
        cleaned = redact_text(message)
        if cleaned != message or record.args:
            record.msg = cleaned
            record.args = None
        return True


__all__ = [
    "MASK",
    "SecretRedactingFilter",
    "is_secret_key",
    "redact_text",
    "redact_values",
]
