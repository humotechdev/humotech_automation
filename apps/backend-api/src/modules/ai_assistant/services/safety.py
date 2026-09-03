"""Проверка вопроса до обращения к модели и очистка того, что пишем в журнал.

Три отдельные задачи, которые часто путают:

  1. ВАЛИДАЦИЯ  — вопрос слишком длинный или пустой: отказ до любых расходов.
  2. РЕДАКТИРОВАНИЕ СЕКРЕТОВ — если сотрудник вставил в чат токен или пароль,
     он не должен попасть ни в журнал, ни в запрос к модели.
  3. ЗАЩИТА ОТ PROMPT INJECTION — попытки перехватить управление помечаются
     и логируются, но НЕ вырезаются молча: вырезание создаёт ложное чувство
     безопасности. Настоящая защита — разделение ролей (инструкции только
     в system, документы только в user) плюс явное правило в промпте.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from src.modules.ai_assistant.errors import QuestionRejectedError

# Шаблоны секретов. Ищем осознанно узко: лучше пропустить экзотику,
# чем изуродовать обычный вопрос сотрудника.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    # Важно съесть сам токен, а не только заголовок: в строке
    # "Authorization: Bearer <token>" значение идёт после ВТОРОГО слова,
    # поэтому наивный \S+ вырезал бы слово Bearer и оставил секрет в тексте.
    (
        "bearer",
        re.compile(
            r"(?i)\b(?:authorization|bearer)\b\s*[:=]?\s*"
            r"(?:bearer\s+)?[A-Za-z0-9._\-/+=]{8,}"
        ),
    ),
    ("telegram_bot_token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b")),
    ("password_assignment", re.compile(r"(?i)\b(пароль|password|passwd)\s*[:=]\s*\S+")),
    ("api_key_assignment", re.compile(r"(?i)\b(api[_\- ]?key|secret|token)\s*[:=]\s*\S+")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
)

REDACTED = "[УДАЛЕНО]"

# Типовые формулировки перехвата управления. Список не претендует
# на полноту — это сигнал для журнала, а не единственная линия обороны.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)игнорируй\s+(все\s+)?(предыдущие|прошлые|上)?\s*инструкц"),
    re.compile(r"(?i)забудь\s+(все\s+)?(предыдущие|прошлые)?\s*(инструкц|правил)"),
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+instructions"),
    re.compile(r"(?i)disregard\s+(all\s+)?(previous|prior)\s+"),
    re.compile(r"(?i)(покажи|выведи|напечатай|раскрой)\s+(мне\s+)?(свой\s+)?(системн\w+\s+)?промпт"),
    re.compile(r"(?i)(show|print|reveal|repeat)\s+(me\s+)?(your\s+)?system\s+prompt"),
    re.compile(r"(?i)ты\s+(теперь|больше не)\s+"),
    re.compile(r"(?i)you\s+are\s+now\s+(a|an)\s+"),
    re.compile(r"(?i)\bdeveloper\s+mode\b|\bjailbreak\b|\bDAN\s+mode\b"),
    re.compile(r"(?i)ответь\s+без\s+(ограничен|правил)"),
    re.compile(r"(?i)(?:^|\n)\s*(system|assistant)\s*:", re.MULTILINE),
)


@dataclass(frozen=True)
class QuestionCheck:
    text: str
    normalized: str
    normalized_hash: str
    redacted: str
    secrets_found: tuple[str, ...] = ()
    injection_suspected: bool = False
    injection_markers: tuple[str, ...] = field(default=())


def normalize_question(text: str) -> str:
    """Нормализация для дедупликации и ключа кэша.

    Приводит к нижнему регистру, схлопывает пробелы, убирает пунктуацию
    и невидимые символы. Похожие формулировки одного вопроса должны давать
    одинаковый хеш, иначе кластеризация неизвестных вопросов не работает.
    """
    text = unicodedata.normalize("NFKC", text)
    # невидимые символы — частый приём обхода фильтров
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = text.lower()
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(text.split())


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redact_secrets(text: str) -> tuple[str, tuple[str, ...]]:
    """Заменяет найденные секреты заглушкой. Возвращает текст и список меток."""
    found: list[str] = []
    result = text
    for label, pattern in _SECRET_PATTERNS:
        result, count = pattern.subn(REDACTED, result)
        if count:
            found.append(label)
    return result, tuple(found)


def detect_injection(text: str) -> tuple[str, ...]:
    """Возвращает сработавшие шаблоны перехвата управления."""
    return tuple(
        pattern.pattern for pattern in _INJECTION_PATTERNS if pattern.search(text)
    )


def check_question(text: str, *, max_length: int) -> QuestionCheck:
    """Полная проверка вопроса до любых расходов на модель."""
    if text is None or not text.strip():
        raise QuestionRejectedError("Вопрос пустой")

    stripped = text.strip()
    if len(stripped) > max_length:
        raise QuestionRejectedError(
            f"Вопрос длиннее допустимых {max_length} символов"
        )

    redacted, secrets = redact_secrets(stripped)
    markers = detect_injection(stripped)
    normalized = normalize_question(redacted)

    return QuestionCheck(
        text=stripped,
        normalized=normalized,
        normalized_hash=hash_text(normalized),
        redacted=redacted,
        secrets_found=secrets,
        injection_suspected=bool(markers),
        injection_markers=markers,
    )


def sanitize_for_log(value: str | None) -> str | None:
    """То, что уходит в журнал, всегда проходит через эту функцию."""
    if value is None:
        return None
    cleaned, _ = redact_secrets(value)
    return cleaned
