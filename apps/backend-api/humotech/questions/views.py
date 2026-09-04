"""REST-интерфейс вопросов: чего не знает ассистент и кто ждёт ответа.

Два разных списка под похожими названиями, и путать их дорого.
`unanswered-questions` — кластеры формулировок для пополнения базы
знаний. `escalations` — обращения конкретных людей, которые ждут ответа
в чате.
"""

from __future__ import annotations

import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action

from humotech.attendance.serializers import EmployeeBriefSerializer
from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import QUESTION_STATUSES, UNANSWERED_QUESTION_STATUSES
from humotech.core.errors import ValidationFailed
from humotech.knowledge.views import FaqSerializer
from humotech.questions.service import QuestionService


def _uuid(request, name: str) -> uuid.UUID | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError) as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть UUID", details={"field": name}
        ) from exc


# --- чего не знает ассистент -------------------------------------------------


class UnansweredQuestionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    question_text = serializers.CharField()
    language = serializers.CharField()
    occurrences_count = serializers.IntegerField(
        help_text="Сколько раз спрашивали одно и то же по смыслу",
    )
    best_retrieval_score = serializers.DecimalField(
        max_digits=6, decimal_places=4, allow_null=True,
    )
    status = serializers.ChoiceField(choices=UNANSWERED_QUESTION_STATUSES)
    assigned_to_user_id = serializers.UUIDField(allow_null=True)
    resolved_faq_id = serializers.UUIDField(allow_null=True)
    office_id = serializers.UUIDField(allow_null=True)
    region_id = serializers.UUIDField(allow_null=True)
    first_asked_at = serializers.DateTimeField()
    last_asked_at = serializers.DateTimeField()
    created_at = serializers.DateTimeField()


class UnansweredPageSerializer(serializers.Serializer):
    items = UnansweredQuestionSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class AssignSerializer(serializers.Serializer):
    to_user_id = serializers.UUIDField()


class CloseUnansweredSerializer(serializers.Serializer):
    as_answered = serializers.BooleanField(
        help_text="false — вопрос признан неподходящим и не пополняет базу",
    )
    note = serializers.CharField(required=False, allow_blank=True,
                                 allow_null=True)


class FaqFromQuestionSerializer(serializers.Serializer):
    canonical_question = serializers.CharField()
    approved_answer = serializers.CharField()
    language = serializers.CharField(max_length=10)
    source_id = serializers.UUIDField(required=False, allow_null=True)
    office_id = serializers.UUIDField(required=False, allow_null=True)
    region_id = serializers.UUIDField(required=False, allow_null=True)
    priority = serializers.IntegerField(required=False, default=0)


@extend_schema(tags=["Вопросы"])
class UnansweredQuestionViewSet(ServiceViewSet):
    """Кластеры вопросов, на которые ответа не нашлось.

    Чтение — `questions.read`, работа с ними — `questions.answer`.
    Это список пробелов в базе знаний, а не очередь обращений.
    """

    service_class = QuestionService
    read_serializer_class = UnansweredQuestionSerializer

    @extend_schema(
        summary="Чего не знает ассистент",
        parameters=[
            OpenApiParameter(
                "status", str, enum=list(UNANSWERED_QUESTION_STATUSES),
            ),
            OpenApiParameter("language", str),
            OpenApiParameter("search", str),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
        responses={200: UnansweredPageSerializer},
    )
    def list(self, request):
        return self.page_response(
            self.service.list_unanswered(
                self.actor,
                **self.list_params(),
                language=request.query_params.get("language") or None,
            )
        )

    @extend_schema(summary="Один кластер вопросов")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get_unanswered(self.actor, pk))

    @extend_schema(
        summary="Назначить ответственного",
        request=AssignSerializer,
        responses={200: UnansweredQuestionSerializer},
    )
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        payload = validated(AssignSerializer, request.data)
        return self.item_response(
            self.service.assign_unanswered(self.actor, pk, **payload)
        )

    @extend_schema(
        summary="Закрыть кластер",
        request=CloseUnansweredSerializer,
        responses={200: UnansweredQuestionSerializer},
    )
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        payload = validated(CloseUnansweredSerializer, request.data)
        return self.item_response(
            self.service.close_unanswered(self.actor, pk, **payload)
        )

    @extend_schema(
        summary="Сделать из вопроса FAQ",
        description=(
            "Запись заводится черновиком: эмбеддинг посчитает воркер "
            "индексации, до этого она в поиск не попадает."
        ),
        request=FaqFromQuestionSerializer,
        responses={201: FaqSerializer},
    )
    @action(detail=True, methods=["post"], url_path="make-faq")
    def make_faq(self, request, pk=None):
        payload = validated(FaqFromQuestionSerializer, request.data)
        faq = self.service.create_faq_from_unanswered(self.actor, pk, **payload)
        return self.item_response(
            faq, created=True, serializer_class=FaqSerializer
        )


# --- кто ждёт ответа ---------------------------------------------------------


class EscalationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    employee = EmployeeBriefSerializer()
    question_text = serializers.CharField()
    normalized_topic = serializers.CharField(allow_null=True)
    status = serializers.ChoiceField(choices=QUESTION_STATUSES)
    ai_answer_text = serializers.CharField(allow_null=True)
    ai_confidence = serializers.DecimalField(
        max_digits=5, decimal_places=4, allow_null=True,
    )
    hr_answer_text = serializers.CharField(allow_null=True)
    assigned_to_user_id = serializers.UUIDField(allow_null=True)
    answer_source_id = serializers.UUIDField(allow_null=True)
    answered_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class EscalationPageSerializer(serializers.Serializer):
    items = EscalationSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class AnswerSerializer(serializers.Serializer):
    answer = serializers.CharField(
        help_text="Уйдёт сотруднику в чат как есть",
    )


@extend_schema(tags=["Вопросы"])
class EscalationViewSet(ServiceViewSet):
    """Обращения сотрудников, переданные кадровику.

    Чтение — `questions.read`, ответ — `questions.answer`. Видны
    обращения сотрудников из области видимости пользователя.

    Ответ уходит человеку в чат той же транзакцией: вопрос, отвеченный
    в интерфейсе и не дошедший до сотрудника, для него не отвечен.
    """

    service_class = QuestionService
    read_serializer_class = EscalationSerializer

    @extend_schema(
        summary="Кто ждёт ответа",
        description=(
            "Без параметра `status` показывает только ожидающие "
            "(ESCALATED_TO_HR): список всех вопросов за год не отвечает "
            "ни на один вопрос кадровика."
        ),
        parameters=[
            OpenApiParameter("status", str, enum=list(QUESTION_STATUSES)),
            OpenApiParameter("employee_id", OpenApiTypes.UUID),
            OpenApiParameter(
                "assigned_to_me", OpenApiTypes.BOOL,
                description="true — только назначенные на меня",
            ),
            OpenApiParameter("search", str),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
        responses={200: EscalationPageSerializer},
    )
    def list(self, request):
        return self.page_response(
            self.service.list_escalations(
                self.actor,
                **self.list_params(),
                employee_id=_uuid(request, "employee_id"),
                assigned_to_me=(
                    request.query_params.get("assigned_to_me") == "true"
                ),
            )
        )

    @extend_schema(summary="Одно обращение")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get_escalation(self.actor, pk))

    @extend_schema(
        summary="Назначить ответственного",
        request=AssignSerializer,
        responses={200: EscalationSerializer},
    )
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        payload = validated(AssignSerializer, request.data)
        return self.item_response(
            self.service.assign_escalation(self.actor, pk, **payload)
        )

    @extend_schema(
        summary="Ответить сотруднику",
        description=(
            "Текст уходит человеку в чат. Повторное нажатие второго "
            "сообщения не даёт: у уведомления ключ по идентификатору "
            "вопроса."
        ),
        request=AnswerSerializer,
        responses={200: EscalationSerializer},
    )
    @action(detail=True, methods=["post"])
    def answer(self, request, pk=None):
        payload = validated(AnswerSerializer, request.data)
        return self.item_response(
            self.service.answer_escalation(self.actor, pk, **payload)
        )

    @extend_schema(
        summary="Снять вопрос без ответа",
        request=None,
        responses={200: EscalationSerializer},
    )
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        return self.item_response(
            self.service.close_escalation(self.actor, pk)
        )


__all__ = ["EscalationViewSet", "UnansweredQuestionViewSet"]
