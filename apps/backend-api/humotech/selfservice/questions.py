"""Вопрос в HR из Telegram.

Бот пересылает сюда текст сотрудника. Кто пишет — решает аутентификация
(общий секрет бота и подтверждённый Telegram ID), а не тело запроса:
ни `employee_id`, ни номера обращения в нём нет. В какое обращение
ляжет сообщение, решает сервис по правилам очереди.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response

from humotech.core.api import validated
from humotech.questions.inbox import InboxService
from humotech.selfservice.views import EmployeeSelfView


class QuestionMessageRequestSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=4000)
    telegram_message_id = serializers.IntegerField(
        required=False, allow_null=True,
        help_text="Идентификатор сообщения Telegram: повтор не даёт дубля",
    )


class QuestionMessageResultSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    number = serializers.IntegerField()
    status = serializers.CharField()
    created = serializers.BooleanField(
        help_text="true — заведено новое обращение, false — сообщение "
                  "добавлено в текущее",
    )


@extend_schema(tags=["Личный кабинет"])
class QuestionMessageView(EmployeeSelfView):
    @extend_schema(
        operation_id="me_question_message",
        summary="Написать в HR",
        description=(
            "Сообщение ложится в открытое обращение сотрудника. Если "
            "открытого нет, закрытое в пределах трёх дней переоткрывается, "
            "иначе заводится новое."
        ),
        request=QuestionMessageRequestSerializer,
        responses={201: QuestionMessageResultSerializer},
    )
    def post(self, request):
        data = validated(QuestionMessageRequestSerializer, request.data)
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
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
