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

import csv
import io

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import (
    SURVEY_AUDIENCE_KINDS,
    SURVEY_CAMPAIGN_STATUSES,
    SURVEY_QUESTION_KINDS,
    SURVEY_TEMPLATE_STATUSES,
    SURVEY_TRIGGER_KINDS,
)
from humotech.surveys.automations import (
    MAX_OFFSET_DAYS,
    SurveyAutomationService,
    next_run,
)

#: Идентификатор в адресе — только UUID. Иначе `abc` доходил до запроса
#: в базу и возвращался как 500.
UUID_PATTERN = (
    "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
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
    status = serializers.ChoiceField(choices=SURVEY_TEMPLATE_STATUSES)
    version = serializers.IntegerField()
    published_at = serializers.DateTimeField(allow_null=True)
    questions = serializers.SerializerMethodField()
    archived_at = serializers.DateTimeField(allow_null=True)
    campaigns_count = serializers.SerializerMethodField()
    #: Кто завёл шаблон: сотрудник, если учётка к нему привязана, иначе
    #: имя учётной записи.
    author_name = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_questions(self, template) -> list[dict]:
        rows = sorted(template.questions.all(), key=lambda one: one.position)
        return QuestionSerializer(rows, many=True).data

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_author_name(self, template) -> str | None:
        user = template.created_by_user
        if user is None:
            return None
        person = getattr(user, "employee", None)
        if person is not None:
            return " ".join(p for p in (person.first_name, person.last_name) if p)
        return user.full_name or user.email

    @extend_schema_field(serializers.IntegerField())
    def get_campaigns_count(self, template) -> int:
        # В списке число уже посчитано одним запросом на всю страницу;
        # у одного шаблона его считаем на месте.
        counted = getattr(template, "campaigns_count", None)
        return counted if counted is not None else template.campaigns.count()


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
    #: Описание шаблона — подпись под названием рассылки: своего
    #: описания у рассылки нет, а «о чём спрашиваем» сказано там.
    template_description = serializers.CharField(
        source="template.description", allow_null=True,
    )
    template_question_count = serializers.SerializerMethodField()
    title = serializers.CharField()
    status = serializers.ChoiceField(choices=SURVEY_CAMPAIGN_STATUSES)
    audience_kind = serializers.ChoiceField(choices=SURVEY_AUDIENCE_KINDS)
    audience_ids = serializers.ListField(
        child=serializers.CharField(), allow_null=True,
    )
    scheduled_at = serializers.DateTimeField(allow_null=True)
    repeat_months = serializers.IntegerField(allow_null=True)
    remind_at = serializers.DateTimeField(allow_null=True)
    #: Когда напоминание уже ушло. Пусто — ещё впереди или не задано.
    reminded_at = serializers.DateTimeField(allow_null=True)
    due_at = serializers.DateTimeField(allow_null=True)
    next_send_at = serializers.DateTimeField(allow_null=True)
    sent_at = serializers.DateTimeField(allow_null=True)
    #: Редакция шаблона на момент отправки и правило, которое
    #: рассылку завело. Пусто у той, что создал человек руками.
    template_version = serializers.IntegerField(allow_null=True)
    automation_id = serializers.UUIDField(allow_null=True)
    #: Анонимный: ответы по людям не показываются никому.
    is_anonymous = serializers.BooleanField()
    #: Сколько получателей и сколько из них дошли до конца. Есть только
    #: в списке: в карточке рядом стоит подробный разбор.
    total = serializers.IntegerField(required=False)
    done = serializers.IntegerField(required=False)
    #: Сколько человек в утверждённом круге запланированной рассылки.
    planned = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    @extend_schema_field(serializers.IntegerField())
    def get_template_question_count(self, row) -> int:
        return row.template.questions.count()

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_planned(self, row) -> int | None:
        return len(row.audience_snapshot) if row.audience_snapshot is not None else None


class CampaignCreateSerializer(serializers.Serializer):
    template_id = serializers.UUIDField()
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    audience_kind = serializers.ChoiceField(choices=SURVEY_AUDIENCE_KINDS)
    audience_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False,
    )
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True)
    repeat_months = serializers.IntegerField(required=False, allow_null=True)
    remind_at = serializers.DateTimeField(required=False, allow_null=True)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    send_now = serializers.BooleanField(required=False, default=False)
    is_anonymous = serializers.BooleanField(required=False, default=False)


class CampaignUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    audience_kind = serializers.ChoiceField(
        choices=SURVEY_AUDIENCE_KINDS, required=False,
    )
    audience_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False,
    )
    scheduled_at = serializers.DateTimeField(required=False)
    remind_at = serializers.DateTimeField(required=False, allow_null=True)
    due_at = serializers.DateTimeField(required=False, allow_null=True)


class AudienceSerializer(serializers.Serializer):
    audience_kind = serializers.ChoiceField(choices=SURVEY_AUDIENCE_KINDS)
    audience_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False,
    )


class RemindSerializer(serializers.Serializer):
    #: Пусто — всем, кто ещё не ответил.
    recipient_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False,
    )


class RecipientSerializer(serializers.Serializer):
    """Получатель с именем. Опрос именной — фамилия здесь по замыслу."""

    id = serializers.UUIDField()
    employee_id = serializers.UUIDField()
    full_name = serializers.SerializerMethodField()
    status = serializers.CharField()
    sent_at = serializers.DateTimeField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    #: Почему пропустили. Без причины строка «Пропущен»
    #: оставляет кадровика гадать.
    skip_reason = serializers.CharField(allow_null=True)
    office_name = serializers.SerializerMethodField()
    department_name = serializers.SerializerMethodField()

    def get_full_name(self, row) -> str:
        person = row.employee
        parts = [person.last_name, person.first_name, person.middle_name]
        return " ".join(part for part in parts if part)

    def get_office_name(self, row) -> str | None:
        return (self.context.get("places", {}).get(row.employee_id) or {}).get("office")

    def get_department_name(self, row) -> str | None:
        return (
            self.context.get("places", {}).get(row.employee_id) or {}
        ).get("department")

    def to_representation(self, row):
        data = super().to_representation(row)
        if self.context.get("anonymous"):
            # В анонимном опросе время открытия и завершения по людям
            # не отдаётся: сверив его со сводкой, ответ сопоставляют с
            # человеком так же легко, как по фамилии.
            data["started_at"] = None
            data["completed_at"] = None
        return data


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

    def get_answers(self, row) -> list[dict]:
        rows = sorted(
            row.answers.all(), key=lambda one: one.question.position,
        )
        return AnswerSerializer(rows, many=True).data


class SurveyTemplateViewSet(ServiceViewSet):
    """Шаблоны опросов: набор вопросов, который переиспользуют."""

    service_class = SurveyTemplateService
    lookup_value_regex = UUID_PATTERN
    read_serializer_class = TemplateSerializer

    def list(self, request):
        # Архивные не показываются вовсе; `status` отбирает среди
        # остальных черновики или опубликованные.
        params = self.list_params()
        return self.page_response(
            self.service.list(
                self.actor,
                search=params.get("search"),
                status=request.query_params.get("status") or None,
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
    def publish(self, request, pk=None):
        """Опубликовать: по шаблону можно рассылать и автоматически."""
        return self.item_response(self.service.publish(self.actor, pk))

    @action(detail=True, methods=["post"], url_path="new-version")
    def new_version(self, request, pk=None):
        """Новая редакция опубликованного: правка на месте закрыта."""
        return self.item_response(
            self.service.new_version(self.actor, pk), created=True,
        )

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self.item_response(self.service.archive(self.actor, pk))

    def destroy(self, request, pk=None):
        """Только неиспользованный черновик; остальное — архивом."""
        self.service.delete(self.actor, pk)
        return Response(status=204)


class AutomationSerializer(serializers.Serializer):
    """Правило автоматической рассылки."""

    id = serializers.UUIDField()
    title = serializers.CharField()
    template_id = serializers.UUIDField()
    template_title = serializers.CharField(source="template.title")
    trigger_kind = serializers.ChoiceField(choices=SURVEY_TRIGGER_KINDS)
    offset_days = serializers.IntegerField()
    send_hour = serializers.IntegerField()
    send_minute = serializers.IntegerField()
    repeat_months = serializers.IntegerField(allow_null=True)
    scope = serializers.JSONField(allow_null=True)
    is_active = serializers.BooleanField()
    last_run_at = serializers.DateTimeField(allow_null=True)
    #: Когда сработает в следующий раз. У правила по событию
    #: пусто: дата зависит от того, кого наймут завтра, и
    #: выдуманная здесь хуже пустоты.
    next_run_at = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    @extend_schema_field(serializers.DateField(allow_null=True))
    def get_next_run_at(self, row) -> str | None:
        day = next_run(row)
        return day.isoformat() if day else None


class AutomationWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    template_id = serializers.UUIDField()
    trigger_kind = serializers.ChoiceField(choices=SURVEY_TRIGGER_KINDS)
    offset_days = serializers.IntegerField(
        required=False, min_value=0, max_value=MAX_OFFSET_DAYS,
    )
    send_hour = serializers.IntegerField(required=False, min_value=0, max_value=23)
    send_minute = serializers.IntegerField(required=False, min_value=0, max_value=59)
    repeat_months = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=12,
    )
    #: Строение условия проверяет сервис: ключи, списки, своя организация.
    scope = serializers.JSONField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False, default=True)


class AutomationPatchSerializer(AutomationWriteSerializer):
    """Правка правила: всё необязательно, но всё проверено.

    Раньше PATCH уходил в сервис сырым `request.data`, и строка вместо
    числа давала 500, а кривое условие сохранялось и роняло очередь.
    """

    template_id = serializers.UUIDField(required=False)
    trigger_kind = serializers.ChoiceField(choices=SURVEY_TRIGGER_KINDS, required=False)
    is_active = serializers.BooleanField(required=False)


class SurveyAutomationViewSet(ServiceViewSet):
    """Автоматизации: по какому событию опрос уходит сам.

    Третья вкладка модуля и третья сущность: у правила нет
    получателей и не будет до самого события.
    """

    service_class = SurveyAutomationService
    lookup_value_regex = UUID_PATTERN
    read_serializer_class = AutomationSerializer

    def list(self, request):
        rows = self.service.list(self.actor)
        return Response({"items": AutomationSerializer(rows, many=True).data})

    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    def create(self, request):
        payload = validated(AutomationWriteSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, payload), created=True,
        )

    def partial_update(self, request, pk=None):
        payload = validated(AutomationPatchSerializer, request.data)
        return self.item_response(
            self.service.update(self.actor, pk, payload)
        )

    def destroy(self, request, pk=None):
        self.service.delete(self.actor, pk)
        return Response(status=204)

    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        return self.item_response(
            self.service.toggle(self.actor, pk, active=True)
        )

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        """Пауза, а не удаление: у правила остаётся история отправок."""
        return self.item_response(
            self.service.toggle(self.actor, pk, active=False)
        )

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        """Срабатывания по людям и счёт за последние 30 дней."""
        return Response(self.service.history(self.actor, pk))


class SurveyCampaignViewSet(ServiceViewSet):
    """Рассылки: кому отправили, кто прошёл и что ответил."""

    service_class = SurveyCampaignService
    lookup_value_regex = UUID_PATTERN
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

    def partial_update(self, request, pk=None):
        """Только запланированная и заведённая руками."""
        payload = validated(CampaignUpdateSerializer, request.data)
        if "audience_ids" in payload:
            payload["audience_ids"] = [str(one) for one in payload["audience_ids"]]
        return self.item_response(self.service.update(self.actor, pk, payload))

    @action(detail=False, methods=["post"])
    def preview(self, request):
        """Кого спросят и до кого опрос не дойдёт — до отправки."""
        payload = validated(AudienceSerializer, request.data)
        return Response(
            self.service.preview(
                self.actor,
                audience_kind=payload["audience_kind"],
                audience_ids=[str(one) for one in payload.get("audience_ids") or []],
            )
        )

    @action(detail=True, methods=["post"])
    def remind(self, request, pk=None):
        payload = validated(RemindSerializer, request.data)
        return Response(
            self.service.remind(
                self.actor, pk,
                recipient_ids=[str(one) for one in payload.get("recipient_ids") or []]
                or None,
            )
        )

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        """Ответы таблицей: CSV, который открывается в Excel как есть."""
        header, rows = self.service.export(self.actor, pk)
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)
        # BOM — чтобы Excel узнал UTF-8 и не показал кириллицу кракозябрами.
        answer = HttpResponse(
            "\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8",
        )
        answer["Content-Disposition"] = f'attachment; filename="survey-{pk}.csv"'
        return answer

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
        campaign = self.service.get(self.actor, pk)
        places = places_of([row.employee_id for row in rows])
        return Response({
            "items": RecipientSerializer(
                rows, many=True,
                context={"places": places, "anonymous": campaign.is_anonymous},
            ).data
        })

    @action(detail=True, methods=["get"])
    def answers(self, request, pk=None):
        """Ответы с именами. Сводка рядом их не заменяет."""
        rows = self.service.answers(self.actor, pk)
        places = places_of([row.employee_id for row in rows])
        campaign = self.service.get(self.actor, pk)
        return Response(
            {
                "items": FilledSerializer(
                    rows, many=True, context={"places": places},
                ).data,
                "anonymous": campaign.is_anonymous,
            }
        )

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        return Response(self.service.summary(self.actor, pk))
