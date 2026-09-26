"""Вопрос сотрудника: сначала AI, потом — по желанию — HR.

Порядок здесь и есть главное решение. Раньше любой вопрос из чата сразу
заводил обращение в CRM: кадровик получал очередь из «во сколько
обед?», а сотрудник — ожидание ответа на то, что написано в правилах.

Теперь так:

1. Вопрос идёт к ассистенту, и тот ищет ответ **только** в утверждённой
   внутренней базе знаний.
2. Нашёл с источником и достаточной уверенностью — отвечает сам.
3. Не нашёл — **не придумывает**. Бот говорит «у меня нет точного
   ответа» и предлагает кнопку «Передать HR».
4. Обращение в CRM создаётся только после нажатия этой кнопки.

**Почему обращение не создаётся сразу.** Вопрос, на который ассистент
ответил, не нужен кадровику; вопрос, который человек передумал
задавать, — тем более. Очередь, забитая тем, что решилось само,
перестаёт быть очередью: в ней не ищут, её просматривают.

**Что уходит в CRM при передаче.** Исходный вопрос дословно, сотрудник,
дата и канал. Не пересказ ассистента и не его неудачный ответ:
кадровик должен прочитать то, что написал человек.
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response

from humotech.ai_assistant.config import ai_settings
from humotech.ai_assistant.container import build_container
from humotech.ai_assistant.schemas import AnswerRequest, AnswerStatus, ScoreBand
from humotech.core.api import validated
from humotech.questions.inbox import InboxService
from humotech.selfservice.views import EmployeeSelfView

logger = logging.getLogger("humotech.selfservice.ask")

#: Статусы, при которых ответ вообще может считаться ответом.
#: `ESCALATED` сюда не входит намеренно: это и есть «я не знаю».
ANSWERED = (AnswerStatus.EXACT_FAQ, AnswerStatus.RAG_ANSWERED)

#: Насколько уверенно должен был сработать поиск. Те же пороги, что
#: у черновика для кадровика (`questions/inbox.py`): два места, решающие
#: «уверен ли ассистент» по-разному, однажды дадут человеку ответ,
#: который кадровику показали бы как сомнительный.
SURE_ENOUGH = (ScoreBand.HIGH, ScoreBand.MEDIUM)


class AskRequestSerializer(serializers.Serializer):
    # Граница та же, что у ассистента (`AI_QUERY_MAX_LENGTH`). Раньше здесь
    # стояло 4000: вопрос длиннее 1000 проходил проверку, отказывался уже
    # внутри конвейера, и человек читал «Ваш вопрос передан HR», хотя
    # обращение не заводилось. Теперь это честный 400, и бот предлагает
    # передать вопрос HR — там предел свой, 4000.
    text = serializers.CharField(max_length=ai_settings.ai_query_max_length)
    client_request_id = serializers.CharField(
        max_length=100, required=False, allow_null=True,
        help_text="Повтор той же отправки не считается новым вопросом",
    )


class AskResultSerializer(serializers.Serializer):
    answered = serializers.BooleanField(
        help_text="true — ассистент ответил; false — ответа нет, "
                  "можно предложить передать HR",
    )
    answer = serializers.CharField(allow_null=True)
    sources = serializers.ListField(child=serializers.CharField())
    status = serializers.CharField()


@extend_schema(tags=["Личный кабинет"])
class AskView(EmployeeSelfView):
    """Спросить ассистента. Обращение в CRM здесь не создаётся."""

    @extend_schema(
        operation_id="me_ask",
        summary="Вопрос ассистенту",
        description=(
            "Ассистент ищет ответ только в утверждённой внутренней базе "
            "знаний. Если точного ответа нет, он не придумывает: "
            "`answered = false`, и бот предлагает передать вопрос HR."
        ),
        request=AskRequestSerializer,
        responses={200: AskResultSerializer},
    )
    def post(self, request):
        data = validated(AskRequestSerializer, request.data)
        employee = self.context.employee

        container = build_container()
        answer = container.answer.execute(
            AnswerRequest(
                employee_id=employee.id,
                question=data["text"],
                language=employee.preferred_language or None,
                client_request_id=data.get("client_request_id"),
            )
        )

        # Личные данные — отдельный случай: это ответ про самого
        # человека, он не из базы знаний и в передаче HR не нуждается.
        confident = bool(answer.answer) and (
            answer.status == AnswerStatus.PERSONAL_DATA
            or (answer.status in ANSWERED and answer.score_band in SURE_ENOUGH)
        )
        return Response({
            "answered": confident,
            # Текст отдаётся и при неудаче: там объяснение, почему ответа
            # нет, и оно человеку полезнее пустоты.
            "answer": answer.answer or None,
            "sources": [one.title for one in answer.sources if one.title],
            "status": str(answer.status),
        })


class EscalateRequestSerializer(serializers.Serializer):
    """Передача вопроса в отдел кадров.

    Текст — исходный вопрос человека, а не ответ ассистента: кадровик
    должен прочитать то, что написал сотрудник.
    """

    text = serializers.CharField(max_length=4000)
    telegram_message_id = serializers.IntegerField(
        required=False, allow_null=True,
        help_text="Повтор той же отправки не даёт второго обращения",
    )


class EscalateResultSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    number = serializers.IntegerField()
    status = serializers.CharField()
    created = serializers.BooleanField()


@extend_schema(tags=["Личный кабинет"])
class EscalateView(EmployeeSelfView):
    """Передать вопрос HR. Только по явному нажатию человека."""

    @extend_schema(
        operation_id="me_ask_escalate",
        summary="Передать вопрос HR",
        description=(
            "Создаёт обращение в CRM с исходным вопросом сотрудника. "
            "Вызывается только после нажатия «Передать HR»: вопрос, на "
            "который ассистент ответил, кадровику не нужен."
        ),
        request=EscalateRequestSerializer,
        responses={201: EscalateResultSerializer},
    )
    def post(self, request):
        data = validated(EscalateRequestSerializer, request.data)
        question, created = InboxService().receive(
            self.context,
            text=data["text"],
            telegram_message_id=data.get("telegram_message_id"),
        )
        return Response(
            {
                "question_id": str(question.id),
                "number": question.number,
                "status": question.status,
                "created": created,
            },
            status=201 if created else 200,
        )


__all__ = ["AskView", "EscalateView"]
