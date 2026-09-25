"""REST-интерфейс вопросов: чего не знает ассистент и кто ждёт ответа.

Два разных списка под похожими названиями, и путать их дорого.
`unanswered-questions` — кластеры формулировок для пополнения базы
знаний. `escalations` — обращения конкретных людей, которые ждут ответа
в чате.
"""

from __future__ import annotations

import uuid
from datetime import date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import (
    QUESTION_CATEGORIES,
    QUESTION_DELIVERY_STATUSES,
    QUESTION_DRAFT_STATUSES,
    QUESTION_EVENTS,
    QUESTION_MESSAGE_KINDS,
    QUESTION_MESSAGE_SOURCES,
    QUESTION_PRIORITIES,
    QUESTION_STATUSES,
    UNANSWERED_QUESTION_STATUSES,
)
from humotech.attendance.hr import PRESENCE_STATES
from humotech.core.errors import ValidationFailed
from humotech.knowledge.views import (
    ANSWER_MAX,
    PRIORITY_LIMITS,
    QUESTION_MAX,
    FaqSerializer,
)
from humotech.questions.inbox import QUICK_FILTERS, REPLY_AFTER, InboxFilters, InboxService
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


#: Идентификатор в адресе — только настоящий UUID. Без этого маршрутизатор
#: DRF пропускает любую строку, `filter(id="abc")` бросает ValidationError
#: Django, и вместо 404 получается 500.
UUID_PATTERN = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: Предел строки поиска. ФИО, табельный номер или кусок фразы — это
#: десятки символов; тысячи слов дали бы запрос из тысяч ILIKE.
SEARCH_MAX_LENGTH = 200


def _text(request, name: str, *, max_length: int) -> str | None:
    """Строковый параметр запроса без NUL и без мегабайтов.

    Сериализаторы DRF NUL отсекают сами, а сырые параметры адреса — нет:
    PostgreSQL на `\\x00` в строке отвечает ошибкой, то есть 500.
    """
    raw = (request.query_params.get(name) or "").strip()
    if not raw:
        return None
    if "\x00" in raw:
        raise ValidationFailed(
            f"Параметр «{name}» содержит недопустимый символ",
            details={"field": name},
        )
    if len(raw) > max_length:
        raise ValidationFailed(
            f"Параметр «{name}» длиннее {max_length} символов",
            details={"field": name, "max_length": max_length},
        )
    return raw


def _date(request, name: str) -> date | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationFailed(
            f"Параметр «{name}» должен быть датой ГГГГ-ММ-ДД",
            details={"field": name},
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
    # Пределы те же, что у FAQ в базе знаний: запись одна и та же, и
    # обходной путь через кластер не должен принимать больше. Приоритет
    # шире int4 иначе доходил до PostgreSQL и возвращал 500.
    canonical_question = serializers.CharField(max_length=QUESTION_MAX)
    approved_answer = serializers.CharField(max_length=ANSWER_MAX)
    language = serializers.CharField(max_length=10)
    source_id = serializers.UUIDField(required=False, allow_null=True)
    office_id = serializers.UUIDField(required=False, allow_null=True)
    region_id = serializers.UUIDField(required=False, allow_null=True)
    priority = serializers.IntegerField(
        required=False, default=0, **PRIORITY_LIMITS,
    )


@extend_schema(tags=["Вопросы"])
class UnansweredQuestionViewSet(ServiceViewSet):
    """Кластеры вопросов, на которые ответа не нашлось.

    Чтение — `questions.read`, работа с ними — `questions.answer`.
    Это список пробелов в базе знаний, а не очередь обращений.
    """

    service_class = QuestionService
    read_serializer_class = UnansweredQuestionSerializer
    lookup_value_regex = UUID_PATTERN

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
        params = self.list_params()
        params["search"] = _text(request, "search", max_length=SEARCH_MAX_LENGTH)
        return self.page_response(
            self.service.list_unanswered(
                self.actor,
                **params,
                language=_text(request, "language", max_length=10),
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


class PersonSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class OfficeRefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class InboxEmployeeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    employee_number = serializers.CharField(allow_null=True)
    has_photo = serializers.BooleanField()


class InboxItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    number = serializers.IntegerField()
    employee = InboxEmployeeSerializer()
    office = OfficeRefSerializer(allow_null=True)
    topic = serializers.CharField()
    snippet = serializers.CharField(help_text="Последнее сообщение, коротко")
    last_message_kind = serializers.ChoiceField(choices=("EMPLOYEE", "HR"))
    category = serializers.ChoiceField(choices=QUESTION_CATEGORIES)
    priority = serializers.ChoiceField(choices=QUESTION_PRIORITIES)
    status = serializers.ChoiceField(choices=QUESTION_STATUSES)
    unread = serializers.BooleanField()
    awaiting_reply = serializers.BooleanField(
        help_text="Последнее слово за сотрудником, ответа ещё не было",
    )
    due_at = serializers.DateTimeField(allow_null=True)
    overdue = serializers.BooleanField()
    last_message_at = serializers.DateTimeField()
    created_at = serializers.DateTimeField()
    assignee = PersonSerializer(allow_null=True)


class InboxPageSerializer(serializers.Serializer):
    items = InboxItemSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class StatusCountsSerializer(serializers.Serializer):
    NEW = serializers.IntegerField()
    IN_PROGRESS = serializers.IntegerField()
    WAITING_EMPLOYEE = serializers.IntegerField()
    CLOSED = serializers.IntegerField()


class QuickCountsSerializer(serializers.Serializer):
    all = serializers.IntegerField()
    unanswered = serializers.IntegerField()
    mine = serializers.IntegerField()
    urgent = serializers.IntegerField()
    unread = serializers.IntegerField()


class InboxCountsSerializer(serializers.Serializer):
    statuses = StatusCountsSerializer()
    total = serializers.IntegerField()
    quick = QuickCountsSerializer()


class AuthorSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=("user", "employee", "system"))
    id = serializers.UUIDField(allow_null=True)
    name = serializers.CharField()


class DeliverySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=QUESTION_DELIVERY_STATUSES)
    sent_at = serializers.DateTimeField(allow_null=True)
    read_at = serializers.DateTimeField(allow_null=True)
    error = serializers.CharField(allow_null=True)


class AttachmentSerializer(serializers.Serializer):
    name = serializers.CharField()
    mime_type = serializers.CharField()
    size_bytes = serializers.IntegerField()


class MessageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=QUESTION_MESSAGE_KINDS)
    source = serializers.ChoiceField(choices=QUESTION_MESSAGE_SOURCES)
    body = serializers.CharField(allow_null=True)
    event = serializers.ChoiceField(choices=QUESTION_EVENTS, allow_null=True)
    details = serializers.JSONField(allow_null=True)
    author = AuthorSerializer()
    created_at = serializers.DateTimeField()
    delivery = DeliverySerializer(allow_null=True)
    attachment = AttachmentSerializer(allow_null=True)


class SourceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    source_type = serializers.CharField()
    version = serializers.IntegerField()
    status = serializers.CharField()
    published_at = serializers.DateTimeField(allow_null=True)
    updated_at = serializers.DateTimeField()


class DraftSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=QUESTION_DRAFT_STATUSES)
    text = serializers.CharField(allow_null=True)
    confidence = serializers.DecimalField(
        max_digits=5, decimal_places=4, allow_null=True,
    )
    generated_at = serializers.DateTimeField(allow_null=True)
    outdated = serializers.BooleanField(
        help_text="Документ, на котором основан черновик, снят с публикации",
    )
    sources = SourceSerializer(many=True)


class TelegramStateSerializer(serializers.Serializer):
    connected = serializers.BooleanField()
    reason = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True)


class ActionsSerializer(serializers.Serializer):
    take = serializers.BooleanField()
    assign = serializers.BooleanField()
    priority = serializers.BooleanField()
    category = serializers.BooleanField()
    wait = serializers.BooleanField()
    start = serializers.BooleanField(
        help_text="Перевести в работу, не меняя ответственного",
    )
    close = serializers.BooleanField()
    reopen = serializers.BooleanField()
    reply = serializers.BooleanField()
    draft = serializers.BooleanField()


class InboxDetailSerializer(InboxItemSerializer):
    question_text = serializers.CharField()
    channel = serializers.CharField()
    first_response_at = serializers.DateTimeField(allow_null=True)
    closed_at = serializers.DateTimeField(allow_null=True)
    closed_by = PersonSerializer(allow_null=True)
    close_reason = serializers.CharField(allow_null=True)
    telegram = TelegramStateSerializer()
    messages = MessageSerializer(many=True)
    draft = DraftSerializer(allow_null=True)
    actions = ActionsSerializer()


class ScheduleRefSerializer(serializers.Serializer):
    name = serializers.CharField()
    flexible = serializers.BooleanField()
    summary = serializers.CharField(allow_null=True)


class ContextTelegramSerializer(TelegramStateSerializer):
    username = serializers.CharField(allow_null=True)


class ContextEmployeeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    employee_number = serializers.CharField(allow_null=True)
    employment_status = serializers.CharField()
    has_photo = serializers.BooleanField()
    position = serializers.CharField(allow_null=True)
    department = serializers.CharField(allow_null=True)
    office = OfficeRefSerializer(allow_null=True)
    schedule = ScheduleRefSerializer(allow_null=True)
    telegram = ContextTelegramSerializer()


class ContextLinksSerializer(serializers.Serializer):
    employee_card = serializers.BooleanField()
    attendance = serializers.BooleanField()
    requests = serializers.BooleanField()


class ContextRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    type = serializers.CharField()
    type_code = serializers.CharField()
    kind = serializers.CharField()
    status = serializers.CharField()
    start = serializers.DateTimeField(allow_null=True)
    end = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class ContextBalanceSerializer(serializers.Serializer):
    type = serializers.CharField()
    year = serializers.IntegerField()
    allocated_days = serializers.FloatField()
    used_days = serializers.FloatField()
    reserved_days = serializers.FloatField()
    available_days = serializers.FloatField()


class ContextCorrectionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    submitted_at = serializers.DateTimeField()
    requested_entry_at = serializers.DateTimeField(allow_null=True)
    requested_exit_at = serializers.DateTimeField(allow_null=True)
    reason = serializers.CharField()


class ContextDocumentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    kind = serializers.CharField()
    status = serializers.CharField()
    has_file = serializers.BooleanField()
    updated_at = serializers.DateTimeField()


class HistoryItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    number = serializers.IntegerField()
    topic = serializers.CharField()
    status = serializers.ChoiceField(choices=QUESTION_STATUSES)
    created_at = serializers.DateTimeField()


class QuestionHistorySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    closed = serializers.IntegerField()
    open = serializers.IntegerField()
    recent = HistoryItemSerializer(many=True)


class TodaySerializer(serializers.Serializer):
    day = serializers.DateField()
    state = serializers.ChoiceField(choices=PRESENCE_STATES, allow_null=True)
    first_entry_at = serializers.DateTimeField(allow_null=True)
    last_exit_at = serializers.DateTimeField(allow_null=True)


class InboxContextSerializer(serializers.Serializer):
    employee = ContextEmployeeSerializer()
    links = ContextLinksSerializer()
    requests = ContextRequestSerializer(many=True, allow_null=True)
    balance = ContextBalanceSerializer(many=True, allow_null=True)
    corrections = ContextCorrectionSerializer(many=True, allow_null=True)
    documents = ContextDocumentSerializer(many=True, allow_null=True)
    history = QuestionHistorySerializer()
    materials = SourceSerializer(many=True, allow_null=True)
    today = TodaySerializer(
        allow_null=True,
        help_text="Где сотрудник сегодня; null — нет права видеть посещаемость",
    )


class ReadResultSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    unread = serializers.BooleanField()


class PrioritySerializer(serializers.Serializer):
    priority = serializers.ChoiceField(choices=QUESTION_PRIORITIES)


class CategorySerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=QUESTION_CATEGORIES)
    topic = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=255,
    )


class CloseSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000)


class ReplySerializer(serializers.Serializer):
    text = serializers.CharField(
        max_length=4000, required=False, allow_blank=True, default="",
        help_text="Уйдёт сотруднику в Telegram как есть; без файла обязателен",
    )
    file = serializers.FileField(
        required=False,
        help_text="PDF, PNG или JPEG до 10 МБ — придёт документом в Telegram",
    )
    after = serializers.ChoiceField(
        choices=REPLY_AFTER, default="KEEP",
        help_text=(
            "KEEP — обращение остаётся в работе; WAIT — ждём ответа "
            "сотрудника; CLOSE — закрыть после отправки"
        ),
    )
    close_reason = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=1000,
    )
    client_request_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=100,
        help_text="Ключ повтора: повторное нажатие не даёт второго сообщения",
    )


FILTER_PARAMETERS = [
    OpenApiParameter("office_id", OpenApiTypes.UUID),
    OpenApiParameter(
        "assignee", str,
        description="`me`, `none` или идентификатор пользователя",
    ),
    OpenApiParameter("category", str, description="Одна или несколько через запятую"),
    OpenApiParameter("priority", str, description="Один или несколько через запятую"),
    OpenApiParameter("date_from", OpenApiTypes.DATE),
    OpenApiParameter("date_to", OpenApiTypes.DATE),
    OpenApiParameter(
        "search", str,
        description="ФИО, табельный номер, номер обращения или текст переписки",
    ),
    OpenApiParameter("status", str, description="Одно или несколько через запятую"),
    OpenApiParameter("quick", str, enum=list(QUICK_FILTERS)),
]


def _filters(request) -> InboxFilters:
    query = request.query_params
    return InboxFilters(
        status=query.get("status") or None,
        office_id=_uuid(request, "office_id"),
        assignee=query.get("assignee") or None,
        category=query.get("category") or None,
        priority=query.get("priority") or None,
        date_from=_date(request, "date_from"),
        date_to=_date(request, "date_to"),
        search=_text(request, "search", max_length=SEARCH_MAX_LENGTH),
        quick=query.get("quick") or None,
    )


@extend_schema(tags=["Вопросы"])
class EscalationViewSet(ServiceViewSet):
    """Обращения сотрудников: очередь, переписка и ответ в Telegram.

    Чтение — `questions.read`, любые изменения — `questions.answer`.
    Видны обращения сотрудников из области видимости пользователя.

    Каждое действие возвращает обращение целиком — с лентой и доступными
    действиями, — чтобы интерфейс не угадывал, что изменилось.
    """

    service_class = InboxService
    read_serializer_class = InboxDetailSerializer
    lookup_value_regex = UUID_PATTERN

    @extend_schema(
        summary="Очередь обращений",
        description=(
            "Срочные и просроченные — сверху, дальше по времени последнего "
            "сообщения. Страницы по курсору."
        ),
        parameters=FILTER_PARAMETERS + [
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
        responses={200: InboxPageSerializer},
    )
    def list(self, request):
        params = self.list_params()
        return self.page_response(
            self.service.list(
                self.actor, _filters(request),
                limit=params.get("limit"), cursor=params.get("cursor"),
            ),
            serializer_class=InboxItemSerializer,
        )

    @extend_schema(
        summary="Счётчики вкладок и быстрых фильтров",
        description=(
            "Вкладки считаются без учёта состояния, быстрые фильтры — "
            "внутри выбранного состояния."
        ),
        parameters=FILTER_PARAMETERS,
        responses={200: InboxCountsSerializer},
    )
    @action(detail=False, methods=["get"])
    def counts(self, request):
        return Response(
            InboxCountsSerializer(
                self.service.counts(self.actor, _filters(request))
            ).data
        )

    @extend_schema(
        summary="Кому можно передать обращение",
        responses={200: PersonSerializer(many=True)},
    )
    @action(detail=False, methods=["get"])
    def assignees(self, request):
        return Response(
            {"items": PersonSerializer(self.service.assignees(self.actor), many=True).data}
        )

    @extend_schema(summary="Обращение с перепиской")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    @extend_schema(
        summary="Сотрудник и связанные данные",
        description=(
            "Раздел без права на него приходит `null`, а не пустым "
            "списком: «нет заявок» и «заявки не показаны» — разные ответы."
        ),
        responses={200: InboxContextSerializer},
    )
    @action(detail=True, methods=["get"])
    def context(self, request, pk=None):
        return Response(
            InboxContextSerializer(self.service.context(self.actor, pk)).data
        )

    @extend_schema(
        summary="Отметить прочитанным", request=None,
        responses={200: ReadResultSerializer},
    )
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        return Response(ReadResultSerializer(self.service.read(self.actor, pk)).data)

    @extend_schema(summary="Взять в работу", request=None)
    @action(detail=True, methods=["post"])
    def take(self, request, pk=None):
        return self.item_response(self.service.take(self.actor, pk))

    @extend_schema(summary="Назначить или передать", request=AssignSerializer)
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        payload = validated(AssignSerializer, request.data)
        return self.item_response(self.service.assign(self.actor, pk, **payload))

    @extend_schema(summary="Сменить приоритет", request=PrioritySerializer)
    @action(detail=True, methods=["post"])
    def priority(self, request, pk=None):
        payload = validated(PrioritySerializer, request.data)
        return self.item_response(
            self.service.set_priority(self.actor, pk, **payload)
        )

    @extend_schema(summary="Сменить категорию и тему", request=CategorySerializer)
    @action(detail=True, methods=["post"])
    def category(self, request, pk=None):
        payload = validated(CategorySerializer, request.data)
        return self.item_response(
            self.service.set_category(self.actor, pk, **payload)
        )

    @extend_schema(summary="В работу, не меняя ответственного", request=None)
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self.item_response(self.service.start(self.actor, pk))

    @extend_schema(
        summary="Файл из переписки",
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    @action(
        detail=True, methods=["get"],
        url_path=rf"messages/(?P<message_id>{UUID_PATTERN})/file",
    )
    def message_file(self, request, pk=None, message_id=None):
        stream, meta = self.service.message_file(
            self.actor, pk, uuid.UUID(message_id),
        )
        # Имя по RFC 5987: кириллица в заголовке как есть не проходит.
        # scan_status, удаление и безопасные заголовки — одной проверкой.
        from humotech.files.serving import STAFF, file_response

        return file_response(
            stream, meta, audience=STAFF,
            filename=meta.original_filename or "file", rfc5987=True,
        )

    @extend_schema(summary="Ждём сотрудника", request=None)
    @action(detail=True, methods=["post"])
    def wait(self, request, pk=None):
        return self.item_response(self.service.wait_employee(self.actor, pk))

    @extend_schema(summary="Закрыть с причиной", request=CloseSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        payload = validated(CloseSerializer, request.data)
        return self.item_response(self.service.close(self.actor, pk, **payload))

    @extend_schema(summary="Переоткрыть", request=None)
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        return self.item_response(self.service.reopen(self.actor, pk))

    @extend_schema(
        summary="Ответить сотруднику в Telegram",
        description=(
            "Перед записью проверяется привязка Telegram: без неё ответ не "
            "сохраняется и отправленным не считается (409 "
            "`telegram_not_connected`)."
        ),
        request=ReplySerializer,
    )
    @action(
        detail=True, methods=["post"],
        parser_classes=[JSONParser, MultiPartParser, FormParser],
    )
    def reply(self, request, pk=None):
        payload = validated(ReplySerializer, request.data)
        upload = payload.pop("file", None)
        return self.item_response(
            self.service.reply(self.actor, pk, upload=upload, **payload)
        )

    @extend_schema(
        summary="Пересобрать черновик по базе знаний",
        description="Черновик никуда не отправляется — это подсказка кадровику.",
        request=None,
    )
    @action(detail=True, methods=["post"])
    def draft(self, request, pk=None):
        return self.item_response(self.service.refresh_draft(self.actor, pk))


__all__ = ["EscalationViewSet", "UnansweredQuestionViewSet"]
