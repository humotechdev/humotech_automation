"""Проверки, которых нет и не может быть на уровне схемы.

CHECK-ограничение видит одну строку и не умеет ходить в другие таблицы, поэтому
«офис принадлежит той же организации» и «часовой пояс существует» проверяются
здесь. Всё, что база проверить может, проверяет база — дублировать её не нужно.
"""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from humotech.core.errors import ValidationFailed

MAX_NAME_LENGTH = 255
MAX_CODE_LENGTH = 50


def clean_text(value: str | None, *, field: str, required: bool = False,
               max_length: int | None = None) -> str | None:
    """Обрезает пробелы; пустая строка после обрезки — это отсутствие значения.

    Иначе в базу попадает `""`, которое не равно NULL, не ищется поиском
    и молча ломает проверки на заполненность.
    """
    if value is None:
        cleaned = None
    else:
        cleaned = value.strip() or None

    if required and cleaned is None:
        raise ValidationFailed(f"Поле «{field}» обязательно",
                               details={"field": field})
    if cleaned is not None and max_length and len(cleaned) > max_length:
        raise ValidationFailed(
            f"Поле «{field}» длиннее {max_length} символов",
            details={"field": field, "max_length": max_length},
        )
    return cleaned


def clean_code(value: str, *, field: str = "code") -> str:
    """Код справочника: верхний регистр, без пробелов внутри.

    Регистр приводится специально: `uq_..._code` регистрозависим, и `MAIN`
    с `main` прошли бы как два разных офиса.
    """
    cleaned = clean_text(value, field=field, required=True,
                         max_length=MAX_CODE_LENGTH)
    assert cleaned is not None  # required=True гарантирует
    if any(ch.isspace() for ch in cleaned):
        raise ValidationFailed(
            f"Поле «{field}» не должно содержать пробелов",
            details={"field": field, "value": cleaned},
        )
    return cleaned.upper()


def validate_timezone(value: str | None, *, field: str = "timezone",
                      required: bool = True) -> str | None:
    """Часовой пояс обязан существовать в базе IANA.

    Опечатка вроде `Asia/Dushambe` иначе сохранится и обнаружится только при
    расчёте опозданий — то есть на живых данных и задним числом.
    """
    cleaned = clean_text(value, field=field, required=required, max_length=100)
    if cleaned is None:
        return None
    try:
        ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValidationFailed(
            f"Неизвестный часовой пояс «{cleaned}»",
            details={"field": field, "value": cleaned},
        ) from exc
    return cleaned


def validate_email(value: str | None, *, field: str) -> str | None:
    """Минимальная проверка формы. Подтверждение адреса — отдельная задача."""
    cleaned = clean_text(value, field=field, max_length=255)
    if cleaned is None:
        return None
    local, _, domain = cleaned.partition("@")
    if not local or not domain or "." not in domain or any(
        ch.isspace() for ch in cleaned
    ):
        raise ValidationFailed("Некорректный адрес электронной почты",
                               details={"field": field, "value": cleaned})
    return cleaned


def validate_phone(value: str | None, *, field: str = "phone") -> str | None:
    """Телефон хранится как есть, но без мусора: только цифры, `+`, скобки, дефис."""
    cleaned = clean_text(value, field=field, max_length=30)
    if cleaned is None:
        return None
    allowed = set("+-() 0123456789")
    if not set(cleaned) <= allowed or sum(ch.isdigit() for ch in cleaned) < 7:
        raise ValidationFailed("Некорректный номер телефона",
                               details={"field": field, "value": cleaned})
    return cleaned


def require_order(earlier: date | None, later: date | None, *,
                  message: str, details: dict | None = None) -> None:
    """`later` не раньше `earlier`, если обе даты заданы."""
    if earlier is not None and later is not None and later < earlier:
        raise ValidationFailed(message, details=details or {})


def validate_day_interval(
    *, start: time | None, end: time | None, crosses_midnight: bool,
    weekday: int, is_working_day: bool,
) -> None:
    """Интервал рабочего дня. В базе такой проверки нет и быть не может:
    ночная смена (22:00–06:00) для CHECK неотличима от опечатки.

    Ночную смену от ошибки отделяет явный флаг `crosses_midnight`.
    """
    if not is_working_day:
        if start is not None or end is not None:
            raise ValidationFailed(
                "У нерабочего дня не должно быть времени начала и окончания",
                details={"weekday": weekday},
            )
        return

    if start is None or end is None:
        raise ValidationFailed(
            "У рабочего дня должны быть указаны время начала и время окончания",
            details={"weekday": weekday},
        )
    if start == end:
        raise ValidationFailed(
            "Рабочий день нулевой длины", details={"weekday": weekday}
        )
    if crosses_midnight and end > start:
        raise ValidationFailed(
            "Ночная смена должна заканчиваться раньше, чем начинается: "
            "окончание приходится на следующие сутки",
            details={"weekday": weekday},
        )
    if not crosses_midnight and end < start:
        raise ValidationFailed(
            "Окончание рабочего дня раньше его начала. "
            "Для ночной смены укажите crosses_midnight",
            details={"weekday": weekday},
        )


def validate_break(
    *, break_start: time, break_end: time, day_start: time, day_end: time,
    crosses_midnight: bool, weekday: int, name: str,
) -> None:
    """Перерыв не короче нуля и укладывается в рабочий день.

    Для ночной смены отрезок разрывается полуночью, поэтому проверка
    «внутри дня» для неё не применяется — только корректность самого перерыва.
    """
    if break_end <= break_start:
        raise ValidationFailed(
            f"Перерыв «{name}» заканчивается не позже, чем начинается",
            details={"weekday": weekday, "break": name},
        )
    if crosses_midnight:
        return
    if break_start < day_start or break_end > day_end:
        raise ValidationFailed(
            f"Перерыв «{name}» выходит за границы рабочего дня",
            details={"weekday": weekday, "break": name},
        )
