"""Проверки данных и постраничный курсор — без базы и без сети.

Перенос `tests/unit/test_hr_validation.py`: сценарии те же, меняется
только способ, которым до кода доходит нарушение целостности.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

import pytest
from django.db import IntegrityError

from humotech.core.errors import (
    Conflict,
    DomainError,
    NotFound,
    PermissionDenied,
    ValidationFailed,
    _CONSTRAINT_MESSAGES,
    translate_integrity_error,
)
from humotech.core.pagination import (
    Cursor,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    normalize_limit,
)
from humotech.core.rbac import AuditTrail, Scope, snapshot
from humotech.core.validation import (
    clean_code,
    clean_text,
    require_order,
    validate_break,
    validate_day_interval,
    validate_email,
    validate_phone,
    validate_timezone,
)


# --- коды ошибок ------------------------------------------------------------

@pytest.mark.parametrize(
    "error, code, status",
    [
        (ValidationFailed("x"), "validation_error", 400),
        (PermissionDenied("x"), "forbidden", 403),
        (NotFound("x"), "not_found", 404),
        (Conflict("x"), "conflict", 409),
    ],
)
def test_error_codes_are_stable(error, code, status):
    """Клиент различает ситуации по коду, а не по тексту сообщения."""
    assert isinstance(error, DomainError)
    assert error.code == code
    assert error.http_status == status
    assert error.as_dict()["error"]["code"] == code


def test_error_payload_carries_message_and_details():
    error = ValidationFailed("Неверный пояс", details={"field": "timezone"})
    payload = error.as_dict()["error"]
    assert payload["message"] == "Неверный пояс"
    assert payload["details"] == {"field": "timezone"}


class _FakeDiag:
    def __init__(self, name):
        self.constraint_name = name


class _FakeDriverError(Exception):
    """Ошибка уровня psycopg: у неё есть diag с именем ограничения."""

    def __init__(self, name):
        super().__init__(name)
        self.diag = _FakeDiag(name)


def _FakeIntegrityError(name):
    """Django оборачивает ошибку драйвера в свою и кладёт исходную в __cause__.

    Имя нарушенного ограничения приходится доставать по этой цепочке —
    поэтому подделка обязана её воспроизводить, иначе тест проверял бы
    не тот путь, по которому идёт настоящая ошибка.
    """
    wrapper = IntegrityError("нарушение ограничения")
    wrapper.__cause__ = _FakeDriverError(name)
    return wrapper


def test_known_constraint_becomes_readable_conflict():
    error = translate_integrity_error(
        _FakeIntegrityError("uq_employees_org_number")
    )
    assert isinstance(error, Conflict)
    assert "табельным номером" in error.message
    assert error.details["constraint"] == "uq_employees_org_number"


def test_unknown_check_constraint_becomes_validation_error():
    error = translate_integrity_error(_FakeIntegrityError("ck_something_new"))
    assert isinstance(error, ValidationFailed)


def test_unknown_constraint_never_leaks_raw_sqlalchemy_error():
    error = translate_integrity_error(_FakeIntegrityError("weird_thing"))
    assert isinstance(error, Conflict)
    assert "weird_thing" in str(error.details)
    assert "IntegrityError" not in error.message


def test_constraint_messages_have_no_empty_entries():
    for name, message in _CONSTRAINT_MESSAGES.items():
        assert message.strip(), f"пустое сообщение для {name}"
        assert name.startswith(("uq_", "ck_", "ex_")), (
            f"имя {name} не похоже на ограничение"
        )


# --- текст и коды -----------------------------------------------------------

def test_blank_string_becomes_none_not_empty_string():
    """Пустая строка в базе не равна NULL и молча ломает проверки."""
    assert clean_text("   ", field="name") is None
    assert clean_text(None, field="name") is None
    assert clean_text("  Офис  ", field="name") == "Офис"


def test_required_blank_value_is_rejected():
    with pytest.raises(ValidationFailed, match="обязательно"):
        clean_text("  ", field="name", required=True)


def test_too_long_value_is_rejected():
    with pytest.raises(ValidationFailed, match="длиннее"):
        clean_text("x" * 300, field="name", max_length=255)


def test_code_is_uppercased_and_space_free():
    assert clean_code(" main ") == "MAIN"
    with pytest.raises(ValidationFailed, match="пробелов"):
        clean_code("main office")


# --- часовой пояс -----------------------------------------------------------

@pytest.mark.parametrize("zone", ["Asia/Dushanbe", "Asia/Tashkent", "UTC",
                                  "Europe/Moscow"])
def test_real_timezones_are_accepted(zone):
    assert validate_timezone(zone) == zone


@pytest.mark.parametrize("zone", ["Asia/Dushambe", "Mars/Olympus", "GMT+5:00",
                                  "не пояс"])
def test_invalid_timezones_are_rejected(zone):
    with pytest.raises(ValidationFailed, match="часовой пояс"):
        validate_timezone(zone)


def test_optional_timezone_may_be_absent():
    assert validate_timezone(None, required=False) is None
    with pytest.raises(ValidationFailed):
        validate_timezone(None, required=True)


# --- контакты ---------------------------------------------------------------

@pytest.mark.parametrize("value", ["hr@humotech.tj", "a.b@sub.domain.com"])
def test_valid_emails_pass(value):
    assert validate_email(value, field="email") == value


@pytest.mark.parametrize("value", ["без-собаки.tj", "@humotech.tj", "hr@humotech",
                                   "hr @humotech.tj"])
def test_invalid_emails_are_rejected(value):
    with pytest.raises(ValidationFailed, match="почты"):
        validate_email(value, field="email")


@pytest.mark.parametrize("value", ["+992 900 11 22 33", "(992)900-112233",
                                   "900112233"])
def test_valid_phones_pass(value):
    assert validate_phone(value) == value


@pytest.mark.parametrize("value", ["позвоните мне", "+992", "911"])
def test_invalid_phones_are_rejected(value):
    with pytest.raises(ValidationFailed, match="телефона"):
        validate_phone(value)


# --- даты -------------------------------------------------------------------

def test_order_of_dates_is_enforced_only_when_both_present():
    require_order(None, date(2020, 1, 1), message="не должно сработать")
    require_order(date(2020, 1, 1), None, message="не должно сработать")
    require_order(date(2020, 1, 1), date(2020, 1, 1), message="равные даты годятся")
    with pytest.raises(ValidationFailed, match="раньше"):
        require_order(date(2025, 5, 1), date(2025, 4, 1),
                      message="Дата увольнения раньше даты приёма")


# --- интервалы рабочего дня -------------------------------------------------

def test_normal_working_day_is_accepted():
    validate_day_interval(start=time(9), end=time(18), crosses_midnight=False,
                          weekday=1, is_working_day=True)


def test_night_shift_needs_the_flag():
    validate_day_interval(start=time(22), end=time(6), crosses_midnight=True,
                          weekday=1, is_working_day=True)
    with pytest.raises(ValidationFailed, match="раньше его начала"):
        validate_day_interval(start=time(22), end=time(6),
                              crosses_midnight=False, weekday=1,
                              is_working_day=True)


def test_flag_without_night_shift_is_rejected():
    with pytest.raises(ValidationFailed, match="Ночная смена"):
        validate_day_interval(start=time(9), end=time(18),
                              crosses_midnight=True, weekday=1,
                              is_working_day=True)


def test_working_day_needs_both_times():
    with pytest.raises(ValidationFailed, match="должны быть указаны"):
        validate_day_interval(start=time(9), end=None, crosses_midnight=False,
                              weekday=1, is_working_day=True)


def test_non_working_day_must_have_no_times():
    validate_day_interval(start=None, end=None, crosses_midnight=False,
                          weekday=6, is_working_day=False)
    with pytest.raises(ValidationFailed, match="нерабочего дня"):
        validate_day_interval(start=time(9), end=time(18),
                              crosses_midnight=False, weekday=6,
                              is_working_day=False)


def test_break_must_fit_inside_the_day():
    validate_break(break_start=time(13), break_end=time(14), day_start=time(9),
                   day_end=time(18), crosses_midnight=False, weekday=1,
                   name="Обед")
    with pytest.raises(ValidationFailed, match="за границы"):
        validate_break(break_start=time(19), break_end=time(20),
                       day_start=time(9), day_end=time(18),
                       crosses_midnight=False, weekday=1, name="Обед")


def test_reversed_break_is_rejected():
    with pytest.raises(ValidationFailed, match="не позже"):
        validate_break(break_start=time(14), break_end=time(13),
                       day_start=time(9), day_end=time(18),
                       crosses_midnight=False, weekday=1, name="Обед")


# --- постраничный курсор ----------------------------------------------------

def test_cursor_round_trip_keeps_both_parts():
    """Ключ — пара (время, id): у записей одной транзакции время совпадает,
    и по одному времени часть строк потерялась бы."""
    original = Cursor(
        created_at=datetime(2025, 6, 1, 12, 30, 45, 123456, tzinfo=timezone.utc),
        id=uuid.uuid4(),
    )
    restored = Cursor.decode(original.encode())
    assert restored.created_at == original.created_at
    assert restored.id == original.id


@pytest.mark.parametrize("value", ["мусор", "", "!!!!", "eyJ1bmZpbmlzaGVk"])
def test_broken_cursor_is_a_validation_error(value):
    with pytest.raises(ValidationFailed, match="курсор"):
        Cursor.decode(value)


def test_page_size_is_clamped():
    assert normalize_limit(None) == DEFAULT_PAGE_SIZE
    assert normalize_limit(10) == 10
    assert normalize_limit(10_000) == MAX_PAGE_SIZE
    with pytest.raises(ValidationFailed, match="положительным"):
        normalize_limit(0)


# --- область видимости ------------------------------------------------------

def test_empty_scope_is_distinguishable_from_full_access():
    everything = Scope(all_offices=True, office_ids=frozenset(),
                       region_ids=frozenset())
    nothing = Scope(all_offices=False, office_ids=frozenset(),
                    region_ids=frozenset())
    assert everything.sees_nothing is False
    assert nothing.sees_nothing is True


# --- журнал -----------------------------------------------------------------

@pytest.mark.parametrize(
    "field",
    ["password", "password_hash", "token", "api_key", "secret",
     "static_token_hash", "qr_nonce_hash", "device_identifier_hash"],
)
def test_secrets_never_reach_the_audit_log(field):
    cleaned = AuditTrail.sanitize({field: "значение", "name": "Офис"})
    assert field not in cleaned
    assert cleaned["name"] == "Офис"


def test_sanitize_returns_none_for_empty_result():
    assert AuditTrail.sanitize({}) is None
    assert AuditTrail.sanitize(None) is None
    assert AuditTrail.sanitize({"password": "x"}) is None


def test_snapshot_makes_values_json_safe():
    class Obj:
        name = "Офис"
        count = 3
        active = True
        when = date(2025, 1, 1)
        who = uuid.uuid4()
        missing = None

    result = snapshot(Obj(), ("name", "count", "active", "when", "who", "missing",
                             "absent"))
    assert result["name"] == "Офис"
    assert result["count"] == 3
    assert result["active"] is True
    assert result["when"] == "2025-01-01"
    assert isinstance(result["who"], str)
    assert result["missing"] is None
    assert result["absent"] is None
