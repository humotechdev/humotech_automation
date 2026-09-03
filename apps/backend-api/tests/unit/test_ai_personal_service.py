"""Логика сервиса личных данных, не требующая базы.

Здесь проверяется то, что можно проверить без PostgreSQL: распознавание
намерений, подсчёт минут по сессиям, границы периодов в часовом поясе офиса
и главное правило — открытая сессия не превращается в выдуманное время ухода.
Запросы к таблицам проверяются интеграционными тестами.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.modules.ai_assistant.services.personal_data import (
    PersonalDataQueryRouter,
    PersonalIntent,
)
from src.modules.ai_assistant.services.personal_data_service import (
    SqlPersonalDataQueryService,
    _fmt_minutes,
)

DUSHANBE = ZoneInfo("Asia/Dushanbe")


@pytest.fixture()
def router() -> PersonalDataQueryRouter:
    return PersonalDataQueryRouter()


@pytest.fixture()
def service(fake_session) -> SqlPersonalDataQueryService:
    return SqlPersonalDataQueryService(fake_session)


# ------------------------------------------------------- распознавание намерений

@pytest.mark.parametrize(
    "question,expected",
    [
        ("Когда я сегодня пришёл?", PersonalIntent.ARRIVAL_TODAY),
        ("Во сколько я сегодня пришел?", PersonalIntent.ARRIVAL_TODAY),
        ("Когда я ушёл?", PersonalIntent.DEPARTURE_TODAY),
        ("Нахожусь ли я сейчас в офисе?", PersonalIntent.IN_OFFICE_NOW),
        ("Сколько времени я провёл в офисе сегодня?", PersonalIntent.DURATION_TODAY),
        ("Сколько часов получилось за неделю?", PersonalIntent.HOURS_WEEK),
        ("Сколько часов получилось за месяц?", PersonalIntent.HOURS_MONTH),
        ("Какие дни я отсутствовал?", PersonalIntent.ABSENCE_DAYS),
        ("Какой статус моего больничного?", PersonalIntent.SICK_LEAVE_STATUS),
        ("Какие даты моего больничного?", PersonalIntent.SICK_LEAVE_DATES),
        ("Какой статус моего отпуска?", PersonalIntent.VACATION_STATUS),
        ("Какие даты моего отпуска?", PersonalIntent.VACATION_DATES),
        ("Сколько дней отпуска осталось?", PersonalIntent.LEAVE_BALANCE),
    ],
)
def test_every_required_question_maps_to_an_intent(router, question, expected):
    decision = router.classify(question)
    assert decision.is_personal
    assert decision.intent is expected, f"{question} -> {decision.intent}"


@pytest.mark.parametrize(
    "question",
    [
        "Сколько дней отпуска положено по закону?",
        "Как оформить больничный?",
        "Во сколько начинается рабочий день в компании?",
        "Какой порядок оформления отпуска?",
    ],
)
def test_policy_questions_get_no_personal_intent(router, question):
    decision = router.classify(question)
    assert decision.intent is None
    assert not decision.is_personal


def test_personal_question_without_clear_intent_asks_to_clarify(router, service):
    """«Когда мне выплатят зарплату» — про себя, но данных такого среза нет."""
    decision = router.classify("Когда мне выплатят зарплату?")
    assert decision.is_personal
    assert decision.intent is None


# ---------------------------------------------------------------- подсчёт времени

def make_session(*, started, ended=None, status="CLOSED", duration=None):
    return SimpleNamespace(
        started_at=started, ended_at=ended, status=status,
        duration_seconds=duration,
        office_id=uuid.uuid4(),
    )


def test_closed_sessions_are_summed_from_stored_duration(service):
    now = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
    sessions = [
        make_session(
            started=now - timedelta(hours=9), ended=now - timedelta(hours=5),
            duration=4 * 3600,
        ),
        make_session(
            started=now - timedelta(hours=4), ended=now - timedelta(hours=1),
            duration=3 * 3600,
        ),
    ]
    minutes, has_open = service._worked_minutes(sessions, now=now)
    assert minutes == 7 * 60
    assert has_open is False


def test_open_session_is_counted_up_to_now_and_flagged(service):
    """Времени ухода у открытой сессии НЕТ — подставлять его нельзя."""
    now = datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc)
    sessions = [
        make_session(started=now - timedelta(hours=2), status="OPEN"),
    ]
    minutes, has_open = service._worked_minutes(sessions, now=now)
    assert minutes == 120
    assert has_open is True, "открытая сессия должна быть помечена"


def test_session_without_stored_duration_falls_back_to_timestamps(service):
    now = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)
    sessions = [
        make_session(
            started=now - timedelta(hours=3), ended=now - timedelta(hours=1),
            duration=None,
        )
    ]
    minutes, _ = service._worked_minutes(sessions, now=now)
    assert minutes == 120


def test_no_sessions_means_zero_not_a_guess(service):
    minutes, has_open = service._worked_minutes(
        [], now=datetime(2026, 9, 3, tzinfo=timezone.utc)
    )
    assert minutes == 0
    assert has_open is False


# -------------------------------------------------------- границы суток по офису

def test_day_boundaries_use_office_timezone_not_server(service):
    """Душанбе — UTC+5: локальные сутки начинаются в 19:00 предыдущего дня UTC."""
    from datetime import date

    period = service._period(DUSHANBE, date(2026, 9, 3), date(2026, 9, 3), "сегодня")

    assert period.utc_from == datetime(2026, 9, 2, 19, 0, tzinfo=timezone.utc)
    assert period.utc_to == datetime(2026, 9, 3, 19, 0, tzinfo=timezone.utc)


def test_period_spans_whole_range(service):
    from datetime import date

    period = service._period(DUSHANBE, date(2026, 9, 1), date(2026, 9, 30), "месяц")
    assert (period.utc_to - period.utc_from) == timedelta(days=30)


# ------------------------------------------------------------------ форматирование

@pytest.mark.parametrize(
    "minutes,expected",
    [(0, "0 мин"), (45, "45 мин"), (60, "1 ч"), (135, "2 ч 15 мин"), (-5, "0 мин")],
)
def test_minutes_are_formatted_for_humans(minutes, expected):
    assert _fmt_minutes(minutes) == expected


# ------------------------------------------------------------------- контейнер

def test_container_wires_real_personal_data_service(fake_session):
    from src.modules.ai_assistant.container import build_personal_data_service

    service = build_personal_data_service(fake_session)
    assert isinstance(service, SqlPersonalDataQueryService)
    assert service.is_available() is True


def test_container_survives_disabled_module(fake_session):
    """Выключенный модуль — штатное состояние, а не сбой."""
    from src.modules.ai_assistant.container import build_container
    from src.modules.ai_assistant.schemas import AnswerRequest, AnswerStatus

    container = build_container(fake_session)
    assert container.enabled is False
    assert container.llm is None

    response = container.answer.execute(
        AnswerRequest(employee_id=uuid.uuid4(), question="как оформить отпуск")
    )
    assert response.status is AnswerStatus.ERROR
    assert "HR" in response.answer


def test_unknown_employee_gets_honest_answer(service):
    """Сотрудника нет — не выдумываем ни данных, ни ошибки 500."""
    result = service.answer(
        employee_id=uuid.uuid4(),
        question="Когда я сегодня пришёл?",
        language="ru",
    )
    assert result.available is False
    assert not any(ch.isdigit() for ch in result.text)
