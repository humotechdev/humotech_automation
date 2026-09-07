"""REST-интерфейс базы знаний: документы, FAQ и очередь индексации.

Работает и с выключенным ассистентом. Всё, что не требует обращения к
провайдеру, — ведение документов и FAQ, чтение очереди — доступно
всегда. Индексация и включение FAQ в поиск при выключенном ассистенте
отказывают отдельным кодом (`ai_disabled`), а не делают вид, что
получилось.
"""

from __future__ import annotations

import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from humotech.ai_assistant.availability import embeddings_available
from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import (
    FAQ_ENTRY_STATUSES,
    INDEX_JOB_STATUSES,
    KNOWLEDGE_SOURCE_STATUSES,
    KNOWLEDGE_SOURCE_TYPES,
)
from humotech.core.errors import ValidationFailed
from humotech.knowledge.service import KnowledgeService


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


# --- документы ---------------------------------------------------------------


class KnowledgeSourceRowSerializer(serializers.Serializer):
    """Документ в списке — без текста.

    `content` здесь нет намеренно: полный текст двадцати регламентов на
    каждое обновление списка — это мегабайты ради строки заголовка.
    Текст приходит вместе с карточкой, когда документ открыли.
    """

    id = serializers.UUIDField()
    title = serializers.CharField()
    source_type = serializers.ChoiceField(choices=KNOWLEDGE_SOURCE_TYPES)
    language = serializers.CharField()
    status = serializers.ChoiceField(choices=KNOWLEDGE_SOURCE_STATUSES)
    version = serializers.IntegerField()
    priority = serializers.IntegerField()
    office_id = serializers.UUIDField(allow_null=True)
    region_id = serializers.UUIDField(allow_null=True)
    department_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(source="office.name", allow_null=True,
                                        default=None)
    region_name = serializers.CharField(source="region.name", allow_null=True,
                                        default=None)
    created_by = serializers.SerializerMethodField(
        help_text="Кто завёл эту версию. Приходит со списком: запрос "
                  "на каждую строку здесь недопустим",
    )
    effective_from = serializers.DateField(allow_null=True)
    effective_to = serializers.DateField(allow_null=True)
    parent_source_id = serializers.UUIDField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_created_by(self, source) -> str | None:
        user = getattr(source, "created_by_user", None)
        return getattr(user, "email", None) if user else None


class KnowledgeSourceSerializer(KnowledgeSourceRowSerializer):
    """Документ целиком — с текстом. Отдаётся карточкой, не списком."""

    content = serializers.CharField()


class KnowledgeSourcePageSerializer(serializers.Serializer):
    items = KnowledgeSourceRowSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class KnowledgeCountsSerializer(serializers.Serializer):
    """Сколько документов в каждом статусе.

    Явные поля, а не словарь: по схеме генерируются клиенты, и словарь
    без описания оставляет в ней дыру.
    """

    total = serializers.IntegerField()
    DRAFT = serializers.IntegerField()
    INDEXING = serializers.IntegerField()
    ACTIVE = serializers.IntegerField()
    ARCHIVED = serializers.IntegerField()
    ERROR = serializers.IntegerField()


class KnowledgeCapabilitySerializer(serializers.Serializer):
    """Что сейчас можно делать с базой знаний.

    Ни ключа, ни имени модели, ни других настроек провайдера здесь нет:
    интерфейсу нужно знать «нельзя и почему», а не чем именно
    не настроено.
    """

    embeddings_available = serializers.BooleanField()
    reason = serializers.ChoiceField(
        choices=["ai_disabled", "provider_not_configured"],
        allow_null=True,
        help_text="Почему индексация недоступна. null — доступна",
    )


class KnowledgeSourceCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    source_type = serializers.ChoiceField(choices=KNOWLEDGE_SOURCE_TYPES)
    language = serializers.CharField(max_length=10)
    content = serializers.CharField()
    office_id = serializers.UUIDField(required=False, allow_null=True)
    region_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Офис и регион одновременно задать нельзя: уровень "
                  "области действия должен быть однозначным",
    )
    department_id = serializers.UUIDField(required=False, allow_null=True)
    effective_from = serializers.DateField(required=False, allow_null=True)
    effective_to = serializers.DateField(required=False, allow_null=True)
    priority = serializers.IntegerField(required=False, default=0)
    parent_source_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Новая версия существующего документа",
    )


class KnowledgeSourceUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    content = serializers.CharField(required=False)
    priority = serializers.IntegerField(required=False)
    effective_from = serializers.DateField(required=False, allow_null=True)
    effective_to = serializers.DateField(required=False, allow_null=True)


class IndexStatusSerializer(serializers.Serializer):
    source_id = serializers.UUIDField()
    source_status = serializers.CharField()
    job_status = serializers.CharField(allow_null=True)
    attempts = serializers.IntegerField()
    error_summary = serializers.CharField(allow_null=True)
    chunks_indexed = serializers.IntegerField()
    embeddings_available = serializers.BooleanField(
        help_text="false — ассистент выключен или провайдер не настроен; "
                  "индексация сейчас невозможна",
    )


@extend_schema(tags=["База знаний"])
class KnowledgeSourceViewSet(ServiceViewSet):
    """Документы базы знаний: политики, инструкции, регламенты.

    Чтение — `knowledge.read`, правка — `knowledge.write`, индексация —
    `knowledge.index`, публикация и архивация — `knowledge.publish`.
    """

    service_class = KnowledgeService
    read_serializer_class = KnowledgeSourceSerializer

    @extend_schema(
        summary="Список документов",
        description=(
            "По одной строке на ДОКУМЕНТ, а не на версию: показывается "
            "самая новая версия каждой линейки «заголовок + язык». "
            "История версий — отдельным действием `versions`.\n\n"
            "Текста документа в списке нет: он приходит с карточкой."
        ),
        parameters=[
            OpenApiParameter(
                "status", str,
                description="Одно состояние или несколько через запятую",
            ),
            OpenApiParameter("language", str),
            OpenApiParameter("office_id", OpenApiTypes.UUID),
            OpenApiParameter("region_id", OpenApiTypes.UUID),
            OpenApiParameter(
                "search", str, description="Подстрока в заголовке или тексте",
            ),
            OpenApiParameter(
                "all_versions", bool,
                description="true — каждая версия отдельной строкой",
            ),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
        responses={200: KnowledgeSourcePageSerializer},
    )
    def list(self, request):
        params = self.list_params()
        return self.page_response(
            self.service.list_sources(
                self.actor,
                **params,
                **self._scope(request),
                all_versions=request.query_params.get("all_versions") == "true",
            ),
            serializer_class=KnowledgeSourceRowSerializer,
        )

    @extend_schema(
        summary="Сколько документов в каждом статусе",
        description=(
            "Считается по всему доступному набору и по тому же правилу, "
            "что и список: одна строка на документ. Фильтр статуса сюда "
            "не передаётся — число рядом с вкладкой не должно зависеть "
            "от открытой вкладки."
        ),
        parameters=[
            OpenApiParameter("language", str),
            OpenApiParameter("office_id", OpenApiTypes.UUID),
            OpenApiParameter("region_id", OpenApiTypes.UUID),
            OpenApiParameter("search", str),
        ],
        responses={200: KnowledgeCountsSerializer},
    )
    @action(detail=False)
    def counts(self, request):
        return Response(
            self.service.count_sources(
                self.actor,
                search=request.query_params.get("search") or None,
                **self._scope(request),
            )
        )

    @extend_schema(
        summary="Доступна ли сейчас индексация",
        description=(
            "Признак для интерфейса: можно ли публиковать документы и "
            "включать FAQ в поиск. Настройки провайдера наружу не идут."
        ),
        responses={200: KnowledgeCapabilitySerializer},
    )
    @action(detail=False)
    def capability(self, request):
        return Response(self.service.capability(self.actor))

    @extend_schema(summary="Один документ", responses={200: KnowledgeSourceSerializer})
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get_source(self.actor, pk))

    @extend_schema(
        summary="История версий документа",
        description=(
            "Линейка — это «организация + заголовок + язык»: та же тройка, "
            "на которой стоит уникальность действующей версии. Чужой "
            "документ отвечает как отсутствующий."
        ),
        responses={200: KnowledgeSourceRowSerializer(many=True)},
    )
    @action(detail=True)
    def versions(self, request, pk=None):
        rows = self.service.versions(self.actor, pk)
        return Response(KnowledgeSourceRowSerializer(rows, many=True).data)

    def _scope(self, request) -> dict:
        return {
            "language": request.query_params.get("language") or None,
            "office_id": _uuid(request, "office_id"),
            "region_id": _uuid(request, "region_id"),
        }

    @extend_schema(
        summary="Завести черновик документа",
        request=KnowledgeSourceCreateSerializer,
        responses={201: KnowledgeSourceSerializer},
        examples=[
            OpenApiExample(
                "Регламент по отпускам",
                value={"title": "Порядок оформления отпуска",
                       "source_type": "POLICY", "language": "ru",
                       "content": "Заявление подаётся за две недели…"},
                request_only=True,
            ),
        ],
    )
    def create(self, request):
        payload = validated(KnowledgeSourceCreateSerializer, request.data)
        return self.item_response(
            self.service.create_source(self.actor, **payload), created=True
        )

    @extend_schema(
        summary="Изменить черновик",
        request=KnowledgeSourceUpdateSerializer,
        responses={200: KnowledgeSourceSerializer},
    )
    def partial_update(self, request, pk=None):
        payload = validated(KnowledgeSourceUpdateSerializer, request.data)
        return self.item_response(
            self.service.update_source(self.actor, pk, **payload)
        )

    @extend_schema(
        summary="Поставить на индексацию",
        description=(
            "Отказ с кодом `ai_disabled`, если ассистент выключен, и "
            "`provider_not_configured`, если не задан ключ или модель. "
            "Задание не ставится в очередь вовсе: висящее в QUEUED "
            "выглядит принятым, а причина не показана нигде."
        ),
        request=None,
        responses={200: IndexStatusSerializer},
    )
    @action(detail=True, methods=["post"])
    def index(self, request, pk=None):
        self.service.start_indexing(self.actor, pk)
        return self._index_status(pk)

    @extend_schema(
        summary="Состояние индексации",
        responses={200: IndexStatusSerializer},
    )
    @action(detail=True, methods=["get"], url_path="index-status")
    def index_status(self, request, pk=None):
        return self._index_status(pk)

    @extend_schema(
        summary="Опубликовать документ",
        description=(
            "Непроиндексированный документ опубликовать нельзя: в ответах "
            "он всё равно не появится, а в списке выглядел бы работающим."
        ),
        request=None,
        responses={200: KnowledgeSourceSerializer},
    )
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        return self.item_response(self.service.publish(self.actor, pk))

    @extend_schema(
        summary="Убрать документ из ответов",
        request=None,
        responses={200: KnowledgeSourceSerializer},
    )
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self.item_response(self.service.archive(self.actor, pk))

    def _index_status(self, source_id) -> Response:
        status = self.service.index_status(self.actor, source_id)
        return Response(
            {
                "source_id": str(status.source_id),
                "source_status": status.source_status,
                "job_status": status.job_status,
                "attempts": status.attempts,
                "error_summary": status.error_summary,
                "chunks_indexed": status.chunks_indexed,
                "embeddings_available": embeddings_available(),
            }
        )


# --- FAQ ---------------------------------------------------------------------


class FaqSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    canonical_question = serializers.CharField()
    approved_answer = serializers.CharField()
    language = serializers.CharField()
    status = serializers.ChoiceField(choices=FAQ_ENTRY_STATUSES)
    priority = serializers.IntegerField()
    office_id = serializers.UUIDField(allow_null=True)
    region_id = serializers.UUIDField(allow_null=True)
    source_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(source="office.name", allow_null=True,
                                        default=None)
    region_name = serializers.CharField(source="region.name", allow_null=True,
                                        default=None)
    source_title = serializers.CharField(source="source.title", allow_null=True,
                                         default=None)
    created_by = serializers.SerializerMethodField()
    indexed = serializers.SerializerMethodField(
        help_text="Посчитан ли эмбеддинг. Без него запись в поиск не попадает",
    )
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_indexed(self, faq) -> bool:
        return faq.question_embedding is not None

    def get_created_by(self, faq) -> str | None:
        user = getattr(faq, "created_by_user", None)
        return getattr(user, "email", None) if user else None


class FaqPageSerializer(serializers.Serializer):
    items = FaqSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


class FaqCountsSerializer(serializers.Serializer):
    """Сколько FAQ в каждом статусе. Состояния свои, не как у документов."""

    total = serializers.IntegerField()
    DRAFT = serializers.IntegerField()
    ACTIVE = serializers.IntegerField()
    ARCHIVED = serializers.IntegerField()


class FaqCreateSerializer(serializers.Serializer):
    canonical_question = serializers.CharField()
    approved_answer = serializers.CharField()
    language = serializers.CharField(max_length=10)
    source_id = serializers.UUIDField(required=False, allow_null=True)
    office_id = serializers.UUIDField(required=False, allow_null=True)
    region_id = serializers.UUIDField(required=False, allow_null=True)
    priority = serializers.IntegerField(required=False, default=0)


class FaqUpdateSerializer(serializers.Serializer):
    canonical_question = serializers.CharField(required=False)
    approved_answer = serializers.CharField(required=False)
    priority = serializers.IntegerField(required=False)


@extend_schema(tags=["База знаний"])
class FaqViewSet(ServiceViewSet):
    """Готовые пары «вопрос — утверждённый ответ».

    Заводятся черновиком всегда: в поиск попадает только запись
    с посчитанным эмбеддингом, а считает его воркер индексации.
    """

    service_class = KnowledgeService
    read_serializer_class = FaqSerializer

    @extend_schema(
        summary="Список FAQ",
        parameters=[
            OpenApiParameter(
                "status", str,
                description="Одно состояние или несколько через запятую",
            ),
            OpenApiParameter("language", str),
            OpenApiParameter("office_id", OpenApiTypes.UUID),
            OpenApiParameter("region_id", OpenApiTypes.UUID),
            OpenApiParameter(
                "source_id", OpenApiTypes.UUID,
                description="Только записи, привязанные к этому документу",
            ),
            OpenApiParameter("search", str),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
        responses={200: FaqPageSerializer},
    )
    def list(self, request):
        params = self.list_params()
        return self.page_response(
            self.service.list_faq(self.actor, **params, **self._filters(request))
        )

    @extend_schema(
        summary="Сколько FAQ в каждом статусе",
        description=(
            "По всему доступному набору. Фильтр статуса сюда не "
            "передаётся: число рядом с вкладкой не должно зависеть от "
            "того, какая вкладка открыта."
        ),
        parameters=[
            OpenApiParameter("language", str),
            OpenApiParameter("office_id", OpenApiTypes.UUID),
            OpenApiParameter("region_id", OpenApiTypes.UUID),
            OpenApiParameter("source_id", OpenApiTypes.UUID),
            OpenApiParameter("search", str),
        ],
        responses={200: FaqCountsSerializer},
    )
    @action(detail=False)
    def counts(self, request):
        return Response(
            self.service.count_faq(
                self.actor,
                search=request.query_params.get("search") or None,
                **self._filters(request),
            )
        )

    def _filters(self, request) -> dict:
        return {
            "language": request.query_params.get("language") or None,
            "office_id": _uuid(request, "office_id"),
            "region_id": _uuid(request, "region_id"),
            "source_id": _uuid(request, "source_id"),
        }

    @extend_schema(summary="Один FAQ")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get_faq(self.actor, pk))

    @extend_schema(
        summary="Завести FAQ",
        request=FaqCreateSerializer,
        responses={201: FaqSerializer},
    )
    def create(self, request):
        payload = validated(FaqCreateSerializer, request.data)
        return self.item_response(
            self.service.create_faq(self.actor, **payload), created=True
        )

    @extend_schema(
        summary="Изменить FAQ",
        description=(
            "Правка текста сбрасывает запись в черновик и снимает "
            "эмбеддинг: иначе в поиске остался бы старый вопрос при "
            "новом ответе."
        ),
        request=FaqUpdateSerializer,
        responses={200: FaqSerializer},
    )
    def partial_update(self, request, pk=None):
        payload = validated(FaqUpdateSerializer, request.data)
        return self.item_response(
            self.service.update_faq(self.actor, pk, **payload)
        )

    @extend_schema(
        summary="Включить FAQ в поиск",
        description=(
            "Требует посчитанного эмбеддинга. Без него запись всё равно "
            "не отбирается поиском, и «включено» означало бы «включено, "
            "но не работает»."
        ),
        request=None,
        responses={200: FaqSerializer},
    )
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self.item_response(
            self.service.set_faq_status(self.actor, pk, status="ACTIVE")
        )

    @extend_schema(
        summary="Убрать FAQ из поиска",
        request=None,
        responses={200: FaqSerializer},
    )
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self.item_response(
            self.service.set_faq_status(self.actor, pk, status="ARCHIVED")
        )


# --- очередь индексации ------------------------------------------------------


class IndexJobSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    source_id = serializers.UUIDField()
    source_title = serializers.CharField(source="source.title")
    status = serializers.ChoiceField(choices=INDEX_JOB_STATUSES)
    attempts = serializers.IntegerField()
    next_attempt_at = serializers.DateTimeField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    error_summary = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class IndexJobPageSerializer(serializers.Serializer):
    items = IndexJobSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()


@extend_schema(tags=["База знаний"])
class KnowledgeIndexJobViewSet(ServiceViewSet):
    """Очередь индексации. Только чтение.

    Задания ставятся действием над документом, а не сюда: задание без
    документа индексировать нечего.
    """

    service_class = KnowledgeService
    read_serializer_class = IndexJobSerializer

    @extend_schema(
        summary="Список заданий индексации",
        parameters=[
            OpenApiParameter("status", str, enum=list(INDEX_JOB_STATUSES)),
            OpenApiParameter("source_id", OpenApiTypes.UUID),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
        responses={200: IndexJobPageSerializer},
    )
    def list(self, request):
        params = self.list_params()
        params.pop("search", None)  # у задания нет текста для поиска
        return self.page_response(
            self.service.list_index_jobs(
                self.actor, **params, source_id=_uuid(request, "source_id")
            )
        )

    @extend_schema(summary="Одно задание индексации")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get_index_job(self.actor, pk))


__all__ = [
    "FaqViewSet",
    "KnowledgeIndexJobViewSet",
    "KnowledgeSourceViewSet",
]
