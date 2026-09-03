"""Личные вопросы не должны попадать в общий RAG."""

from __future__ import annotations

import uuid

import pytest

from humotech.ai_assistant.services.personal_data import (
    NotImplementedPersonalDataQueryService,
    PersonalDataQueryRouter,
)


@pytest.fixture()
def router() -> PersonalDataQueryRouter:
    return PersonalDataQueryRouter()


@pytest.mark.parametrize(
    "question",
    [
        "Сколько часов я отработал?",
        "Во сколько я сегодня пришёл?",
        "Одобрен ли мой больничный?",
        "Сколько дней отпуска осталось?",
        "Какая у меня статистика за месяц?",
        "Когда мне выплатят зарплату?",
        "Мой график на завтра какой?",
        "Сколько у меня опозданий?",
    ],
)
def test_personal_questions_are_routed_away_from_rag(router, question):
    decision = router.classify(question)
    assert decision.is_personal, f"вопрос не распознан как личный: {question}"


@pytest.mark.parametrize(
    "question",
    [
        "Как оформить отпуск?",
        "Сколько дней отпуска положено по закону?",
        "Какой порядок оформления больничного в компании?",
        "Что делать при опоздании?",
        "Как считаются рабочие часы?",
        "Какие документы нужны для командировки?",
    ],
)
def test_policy_questions_stay_in_rag(router, question):
    """Тема личная, но вопрос о правиле компании — это работа базы знаний."""
    decision = router.classify(question)
    assert not decision.is_personal, f"вопрос ошибочно признан личным: {question}"


def test_unrelated_question_is_not_personal(router):
    assert not router.classify("Где находится офис в Худжанде?").is_personal


def test_stub_service_returns_no_invented_data():
    """Заглушка честно говорит, что сервиса нет, а не придумывает цифры."""
    service = NotImplementedPersonalDataQueryService("Сервис пока не подключён.")
    result = service.answer(
        employee_id=uuid.uuid4(), question="Сколько я отработал?", language="ru"
    )
    assert result.available is False
    assert not service.is_available()
    # в ответе нет ни одной цифры — придуманных данных быть не должно
    assert not any(ch.isdigit() for ch in result.text)
