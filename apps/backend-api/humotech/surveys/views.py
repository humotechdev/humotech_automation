"""REST-интерфейс опросов для HR CRM.

Опрос именной, и ответы отдаются вместе с именем, офисом и отделом. Это
не недосмотр приватности: HR идёт по этим ответам разговаривать с
человеком, а не считает настроение в среднем. Анонимный опрос — другой
продукт с другими гарантиями, и подмешивать его сюда нельзя.

Сторона сотрудника живёт отдельно (`miniapp_views.py`): там нет ни
`Actor`, ни прав HR — действует сам человек, и видит он только свой
опрос.
"""

from __future__ import annotations

from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import (
    SURVEY_AUDIENCE_KINDS,
    SURVEY_CAMPAIGN_STATUSES,
    SURVEY_QUESTION_KINDS,
)
from humotech.surveys.services import (
    SurveyCampaignService,
    SurveyTemplateService,
    places_of,
)


class QuestionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    position = serializers.IntegerField()
    text = serializers.CharField()
    kind = serializers.ChoiceField(choices=SURVEY_QUESTION_KINDS)
    is_required = serializers.BooleanField()
    options = serializers.ListField(child=serializers.CharField(), allow_null=True)


class TemplateSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    questions = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_questions(self, template) -> list[dict]:
        rows = sorted(template.questions.all(), key=lambda one: one.position)
        return QuestionSerializer(rows, many=True).data


class QuestionWriteSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=500)
    kind = serializers.ChoiceField(choices=SURVEY_QUESTION_KINDS)
    is_required = serializers.BooleanField(required=False, default=True)
    options = serializers.ListField(
        child=serializers.CharField(max_length=200), required=False, allow_null=True,
    )


class TemplateCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    questions = QuestionWriteSerializer(many=True)


class TemplateUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    # Список вопросов заменяет прежний ЦЕЛИКОМ. Частичная правка ввела бы
    # неочевидное слияние: непонятно, что означает отсутствие вопроса.
    questions = QuestionWriteSerializer(many=True, required=False)


class CampaignSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    template_id = serializers.UUIDField()
    template_title = serializers.CharField(source="template.title")
    title = serializers.CharField()
    status = serializers.ChoiceField(choices=SURVEY_CAMPAIGN_STATUSES)
    audience_kind = serializers.ChoiceField(choices=SURVEY_AUDIENCE_KINDS)
    audience_ids = serializers.ListField(
        child=serializers.CharField(), allow_null=True,
    )
    scheduled_at = serializers.DateTimeField(allow_null=True)
    repeat_months = serializers.IntegerField(allow_null=True)
    next_send_at = serializers.DateTimeField(allow_null=True)
    sent_at = serializers.DateTimeField(allow_null=True)
    #: Сколько получателей и сколько из них дошли до конца. Есть только
    #: в списке: в карточке рядом стоит подробный разбор.
    total = serializers.IntegerField(required=False)
    done = serializers.IntegerField(required=False)
    created_at = serializers.DateTimeField()


class CampaignCreateSerializer(serializers.Serializer):
    template_id = serializers.UUIDField()
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    audience_kind = serializers.ChoiceField(choices=SURVEY_AUDIENCE_KINDS)
    audience_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False,
    )
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True)
    repeat_months = serializers.IntegerField(required=False, allow_null=True)
    send_now = serializers.BooleanField(required=False, default=False)


class RecipientSerializer(serializers.Serializer):
    """Получатель с именем. Опрос именной — фамилия здесь по замыслу."""

    id = serializers.UUIDField()
    employee_id = serializers.UUIDField()
    full_name = serializers.SerializerMethodField()
    status = serializers.CharField()
    sent_at = serializers.DateTimeField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)

    def get_full_name(self, row) -> str:
        person = row.employee
        parts = [person.last_name, person.first_name, person.middle_name]
        return " ".join(part for part in parts if part)


class AnswerSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    question_text = serializers.CharField(source="question.text")
    kind = serializers.CharField(source="question.kind")
    text = serializers.CharField(allow_null=True)
    number = serializers.IntegerField(allow_null=True)
    options = serializers.ListField(child=serializers.CharField(), allow_null=True)


class FilledSerializer(RecipientSerializer):
    """Пройденный опрос: кто, когда и что ответил."""

    answers = serializers.SerializerMethodField()
    office_name = serializers.SerializerMethodField()
    department_name = serializers.SerializerMethodField()

    def get_answers(self, row) -> list[dict]:
        rows = sorted(
            row.answers.all(), key=lambda one: one.question.position,
        )
        return AnswerSerializer(rows, many=True).data

    def get_office_name(self, row) -> str | None:
        return (self.context.get("places", {}).get(row.employee_id) or {}).get("office")

    def get_department_name(self, row) -> str | None:
        return (
            self.context.get("places", {}).get(row.employee_id) or {}
        ).get("department")


class SurveyTemplateViewSet(ServiceViewSet):
    """Шаблоны опросов: набор вопросов, который переиспользуют."""

    service_class = SurveyTemplateService
    read_serializer_class = TemplateSerializer

    def list(self, request):
        # Состояния у шаблона нет: он либо в работе, либо в архиве, и
        # архивные не показываются вовсе. Общий параметр `status` сюда
        # не передаётся — принимать его значило бы обещать отбор,
        # которого нет.
        params = self.list_params()
        return self.page_response(
            self.service.list(
                self.actor,
                search=params.get("search"),
                limit=params.get("limit"),
                cursor=params.get("cursor"),
            )
        )

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    def create(self, request):
        payload = validated(TemplateCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True,
        )

    def partial_update(self, request, pk=None):
        payload = validated(TemplateUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))

    @action(detail=True, methods=["post"])
    def copy(self, request, pk=None):
        """Копия со всеми вопросами: основа для правки уже отвеченного."""
        return self.item_response(self.service.copy(self.actor, pk), created=True)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self.item_response(self.service.archive(self.actor, pk))


class SurveyCampaignViewSet(ServiceViewSet):
    """Рассылки: кому отправили, кто прошёл и что ответил."""

    service_class = SurveyCampaignService
    read_serializer_class = CampaignSerializer

    def list(self, request):
        return self.page_response(
            self.service.list(
                self.actor,
                status=request.query_params.get("status") or None,
                limit=self.list_params().get("limit"),
                cursor=self.list_params().get("cursor"),
            )
        )

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    def create(self, request):
        payload = validated(CampaignCreateSerializer, request.data)
        ids = payload.pop("audience_ids", None)
        return self.item_response(
            self.service.create(
                self.actor,
                audience_ids=[str(one) for one in (ids or [])],
                **payload,
            ),
            created=True,
        )

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        """Отправить сейчас, в том числе запланированное раньше срока."""
        return self.item_response(self.service.send(self.actor, pk))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self.item_response(self.service.cancel(self.actor, pk))

    @action(detail=True, methods=["get"])
    def recipients(self, request, pk=None):
        rows = self.service.recipients(
            self.actor, pk, status=request.query_params.get("status") or None,
        )
        return Response({"items": RecipientSerializer(rows, many=True).data})

    @action(detail=True, methods=["get"])
    def answers(self, request, pk=None):
        """Ответы с именами. Сводка рядом их не заменяет."""
        rows = self.service.answers(self.actor, pk)
        places = places_of([row.employee_id for row in rows])
        return Response(
            {
                "items": FilledSerializer(
                    rows, many=True, context={"places": places},
                ).data
            }
        )

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        return Response(self.service.summary(self.actor, pk))
