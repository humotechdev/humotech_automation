"""Вопрос в HR из Telegram.

Бот пересылает сюда текст сотрудника. Кто пишет — решает аутентификация
(общий секрет бота и подтверждённый Telegram ID), а не тело запроса:
ни `employee_id`, ни номера обращения в нём нет. В какое обращение
ляжет сообщение, решает сервис по правилам очереди.
"""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response

from humotech.core.api import validated
from humotech.questions.inbox import InboxService, employee_reply_file
from humotech.selfservice.views import EmployeeSelfView


class QuestionMessageRequestSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=4000)
    # Границы bigint: число шире колонки иначе доходит до PostgreSQL и
    # возвращается 500 «bigint out of range».
    telegram_message_id = serializers.IntegerField(
        required=False, allow_null=True,
        min_value=-(2**63), max_value=2**63 - 1,
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


@extend_schema(tags=["Личный кабинет"])
class ReplyFileView(EmployeeSelfView):
    """Файл из ответа HR — боту, чтобы отправить его сотруднику документом."""

    @extend_schema(
        operation_id="me_question_reply_file",
        summary="Файл из ответа HR",
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    def get(self, request, message_id):
        stream, meta = employee_reply_file(self.context, message_id)
        # Имя по RFC 5987: бот берёт его для подписи файла в чате.
        # scan_status, удаление и безопасные заголовки — одной проверкой.
        from humotech.files.serving import EMPLOYEE, file_response

        return file_response(
            stream, meta, audience=EMPLOYEE,
            filename=meta.original_filename or "file", rfc5987=True,
        )
