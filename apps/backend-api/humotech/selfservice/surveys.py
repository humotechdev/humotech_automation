"""Опросы глазами сотрудника.

Живёт под `/me/` вместе с остальным кабинетом, и это не вопрос вкуса: на
этом префиксе стоят зона CORS и ограничение частоты. Endpoint сотрудника
вне `/me/` тихо остался бы без обоих.

Прав HR здесь нет и `Actor` нет: действует сам человек. Сотрудник
берётся из проверенной привязки Telegram, а не из запроса — иначе чужой
опрос открывался бы подстановкой идентификатора в адрес.

Вопросы отдаются целиком одним ответом. Опрос короткий, а запрос на
каждый экран означал бы белый экран в лифте на каждом «Далее».
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response

from humotech.core.api import validated
from humotech.selfservice.views import EmployeeSelfView
from humotech.surveys import services


class SurveyQuestionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    position = serializers.IntegerField()
    text = serializers.CharField()
    kind = serializers.CharField()
    is_required = serializers.BooleanField()
    options = serializers.ListField(child=serializers.CharField(), allow_null=True)


class SurveyAnswerSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    text = serializers.CharField(allow_null=True)
    number = serializers.IntegerField(allow_null=True)
    options = serializers.ListField(child=serializers.CharField(), allow_null=True)


class SurveySerializer(serializers.Serializer):
    """Опрос, готовый к показу."""

    id = serializers.UUIDField()
    title = serializers.CharField(source="campaign.title")
    description = serializers.CharField(
        source="campaign.template.description", allow_null=True,
    )
    status = serializers.CharField()
    questions = serializers.SerializerMethodField()
    answers = serializers.SerializerMethodField()

    def get_questions(self, row) -> list[dict]:
        rows = sorted(
            row.campaign.template.questions.all(), key=lambda one: one.position,
        )
        return SurveyQuestionSerializer(rows, many=True).data

    def get_answers(self, row) -> list[dict]:
        # Уже сохранённые ответы возвращаются вместе с вопросами: человек,
        # закрывший приложение на середине, не начинает заново.
        return SurveyAnswerSerializer(row.answers.all(), many=True).data


class SurveyBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField(source="campaign.title")
    status = serializers.CharField()
    sent_at = serializers.DateTimeField(allow_null=True)
    questions_total = serializers.SerializerMethodField()

    def get_questions_total(self, row) -> int:
        return row.campaign.template.questions.count()


class SurveyListSerializer(serializers.Serializer):
    items = SurveyBriefSerializer(many=True)


class AnswerInputSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    text = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    number = serializers.IntegerField(required=False, allow_null=True)
    options = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True,
    )


class SubmitSerializer(serializers.Serializer):
    """Ответы целиком: опрос проходят за раз, а не по одному вопросу."""

    answers = AnswerInputSerializer(many=True)


class SubmittedSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    completed_at = serializers.DateTimeField(allow_null=True)


@extend_schema(tags=["Личный кабинет"])
class SurveyListView(EmployeeSelfView):
    """Опросы, которые ещё предстоит пройти."""

    @extend_schema(
        operation_id="self_surveys",
        summary="Мои непройденные опросы",
        responses={200: SurveyListSerializer},
    )
    def get(self, request):
        rows = services.pending_for(self.context.employee.id)
        return Response({"items": SurveyBriefSerializer(rows, many=True).data})


@extend_schema(tags=["Личный кабинет"])
class SurveyView(EmployeeSelfView):
    """Один опрос: открыть и прислать ответы."""

    @extend_schema(
        operation_id="self_survey_open",
        summary="Открыть опрос",
        description=(
            "Открытие — не просто чтение: с него начинается отсчёт "
            "«начал проходить», и HR видит разницу между «не открывал» "
            "и «бросил на середине»."
        ),
        responses={200: SurveySerializer},
    )
    def get(self, request, recipient_id):
        row = services.open_survey(
            employee_id=self.context.employee.id, recipient_id=recipient_id,
        )
        return Response(SurveySerializer(row).data)

    @extend_schema(
        operation_id="self_survey_submit",
        summary="Отправить ответы",
        description=(
            "Ответы принимаются целиком. Повторная отправка пройденного "
            "опроса отклоняется: иначе ответ можно было бы переписать "
            "после разговора с руководителем."
        ),
        request=SubmitSerializer,
        responses={200: SubmittedSerializer},
    )
    def post(self, request, recipient_id):
        payload = validated(SubmitSerializer, request.data)
        row = services.submit(
            employee_id=self.context.employee.id,
            recipient_id=recipient_id,
            answers=payload["answers"],
        )
        return Response(SubmittedSerializer(row).data)
