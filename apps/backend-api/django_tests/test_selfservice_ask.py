"""Вопрос сотрудника: сначала ассистент, HR — по кнопке.

Проверяется то, из-за чего такая схема перестаёт работать:

— вопрос, заведший обращение до того, как человек об этом попросил;
— выдуманный ответ вместо честного «не знаю»;
— ответ без источника, которому нечем верить;
— передача HR, унёсшая пересказ ассистента вместо вопроса человека;
— выключенный ассистент, оставивший человека без пути вперёд.
"""

from __future__ import annotations

import uuid

import pytest

from humotech.ai_assistant.schemas import (
    AnswerResponse,
    AnswerStatus,
    ScoreBand,
    SourceRef,
)
from humotech.questions.models import EmployeeQuestion
from humotech.telegram.identity import resolve_by_telegram_user_id

from .conftest import bot_headers, link_telegram

pytestmark = pytest.mark.django_db

TG_ID = 777_000_111
ASK = "/api/v1/me/ask"
ESCALATE = "/api/v1/me/ask/escalate"


@pytest.fixture()
def context(db, employee, telegram_settings):
    link_telegram(employee)
    return resolve_by_telegram_user_id(TG_ID)


class FakeAnswer:
    """Ассистент, отвечающий тем, чем велели."""

    def __init__(self, response: AnswerResponse, enabled: bool = True):
        self.response = response
        self.enabled = enabled
        self.asked: list[str] = []

    def execute(self, request):
        self.asked.append(request.question)
        return self.response


def answering(
    *,
    status: AnswerStatus = AnswerStatus.RAG_ANSWERED,
    band: ScoreBand = ScoreBand.HIGH,
    answer: str = "Отпуск переносится заявлением за две недели.",
    sources: tuple[str, ...] = ("Правила отпусков",),
) -> AnswerResponse:
    return AnswerResponse(
        request_id=uuid.uuid4(),
        status=status,
        answer=answer,
        language="ru",
        sources=[
            SourceRef(id=uuid.uuid4(), title=one, version=1)
            for one in sources
        ],
        score_band=band,
    )


@pytest.fixture()
def assistant(monkeypatch):
    """Подменяет ассистента в контейнере, не трогая настройки."""

    holder: dict = {}

    def _use(response: AnswerResponse):
        fake = FakeAnswer(response)
        holder["fake"] = fake
        monkeypatch.setattr(
            "humotech.selfservice.ask.build_container",
            lambda **kwargs: type("C", (), {"answer": fake})(),
        )
        return fake

    holder["use"] = _use
    return _use


# --- ассистент отвечает ------------------------------------------------------


def test_confident_answer_does_not_create_a_question(
    bot_client, context, assistant, employee
):
    assistant(answering())

    got = bot_client.post(ASK, {"text": "Как перенести отпуск?"},
                          format="json", **bot_headers(TG_ID))

    assert got.status_code == 200, got.content
    body = got.json()
    assert body["answered"] is True
    assert "Отпуск переносится" in body["answer"]
    assert body["sources"] == ["Правила отпусков"]
    # Очередь, забитая тем, что решилось само, перестаёт быть очередью.
    assert not EmployeeQuestion.objects.filter(employee_id=employee.id).exists()


def test_a_shaky_answer_is_not_passed_off_as_an_answer(
    bot_client, context, assistant
):
    # Поиск сработал, но неуверенно. Отдать такой ответ как точный —
    # значит подставить человека: он поступит по нему и узнает от
    # кадровика, что было иначе.
    assistant(answering(band=ScoreBand.LOW))

    body = bot_client.post(ASK, {"text": "Сколько дней отпуска?"},
                           format="json", **bot_headers(TG_ID)).json()

    assert body["answered"] is False


def test_escalated_status_means_i_do_not_know(bot_client, context, assistant):
    assistant(answering(status=AnswerStatus.ESCALATED, band=ScoreBand.NONE,
                        answer="Не нашёл ответа", sources=()))

    body = bot_client.post(ASK, {"text": "Где взять справку 2-НДФЛ?"},
                           format="json", **bot_headers(TG_ID)).json()

    assert body["answered"] is False
    # Текст всё равно отдаётся: там объяснение, и оно полезнее пустоты.
    assert body["answer"]


def test_personal_data_answer_is_not_escalated(bot_client, context, assistant):
    # Ответ про самого человека не из базы знаний, и передавать его
    # кадровику незачем — он уже отвечен.
    assistant(answering(status=AnswerStatus.PERSONAL_DATA, band=ScoreBand.NONE,
                        answer="Вы сейчас в офисе с 09:02.", sources=()))

    body = bot_client.post(ASK, {"text": "Я сейчас в офисе?"},
                           format="json", **bot_headers(TG_ID)).json()

    assert body["answered"] is True


def test_disabled_assistant_answers_without_falling(
    bot_client, context, monkeypatch
):
    class Off:
        enabled = False

        def execute(self, request):
            return answering(status=AnswerStatus.ERROR, band=ScoreBand.NONE,
                             answer="Ассистент сейчас недоступен", sources=())

    monkeypatch.setattr(
        "humotech.selfservice.ask.build_container",
        lambda **kwargs: type("C", (), {"answer": Off()})(),
    )

    got = bot_client.post(ASK, {"text": "Вопрос"}, format="json",
                          **bot_headers(TG_ID))

    # Не 500: выключенный модуль — штатное состояние, и человек получает
    # понятный ответ, после которого бот предложит HR.
    assert got.status_code == 200, got.content
    assert got.json()["answered"] is False


# --- передача HR -------------------------------------------------------------


def test_escalation_creates_the_question_with_the_original_text(
    bot_client, context, employee
):
    got = bot_client.post(
        ESCALATE, {"text": "Где взять справку 2-НДФЛ?", "telegram_message_id": 314},
        format="json", **bot_headers(TG_ID),
    )

    assert got.status_code == 201, got.content
    question = EmployeeQuestion.objects.get(employee_id=employee.id)
    # Кадровик должен прочитать то, что написал человек, а не пересказ
    # ассистента и не его неудачный ответ.
    assert "2-НДФЛ" in question.question_text
    assert got.json()["number"] == question.number


def test_repeat_of_the_same_press_does_not_double_the_question(
    bot_client, context, employee
):
    for _ in range(2):
        bot_client.post(
            ESCALATE, {"text": "Один вопрос", "telegram_message_id": 777},
            format="json", **bot_headers(TG_ID),
        )

    assert EmployeeQuestion.objects.filter(employee_id=employee.id).count() == 1


def test_escalation_keeps_the_channel(bot_client, context, employee):
    bot_client.post(ESCALATE, {"text": "Вопрос из чата"}, format="json",
                    **bot_headers(TG_ID))

    question = EmployeeQuestion.objects.get(employee_id=employee.id)
    message = question.messages.filter(kind="EMPLOYEE").first()
    assert message is not None
    # Канал, дата и сотрудник — то, по чему кадровик понимает, откуда
    # пришёл вопрос и когда.
    assert message.source == "TELEGRAM"
    assert question.employee_id == employee.id
    assert question.created_at is not None
