"""REST-интерфейс ленты событий кадровика.

Четыре действия и ни одного создающего: события в ленте не заводят — они
случаются. Строка появляется тогда, когда появилась заявка, сессия или
обращение, и ручное «добавить уведомление» означало бы сообщение о том,
чего не было.

Единственное, что этот интерфейс пишет, — отметка прочтения, и пишет её
всегда от имени того, кто спрашивает: чужое прочтение проставить нельзя,
даже своему же коллеге.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.core.rbac import Actor
from humotech.notifications.feed import (
    FEED_DAYS,
    FEED_PRIORITIES,
    FEED_TYPES,
    FILTERS,
    FeedService,
)


class FeedCountsSerializer(serializers.Serializer):
    """Счётчики вкладок по всему доступному окну ленты."""

    all = serializers.IntegerField()
    unread = serializers.IntegerField(help_text="Число у колокольчика")
    action = serializers.IntegerField(help_text="Требуют решения кадровика")
    requests = serializers.IntegerField()
    documents = serializers.IntegerField()
    attendance = serializers.IntegerField()
    questions = serializers.IntegerField()
    system = serializers.IntegerField()


class FeedItemSerializer(serializers.Serializer):
    """Строка ленты. Ровно то, что можно показать в списке."""

    id = serializers.CharField(
        help_text=(
            "Составной ключ «вид:запись». Выводится из данных, поэтому "
            "переживает пересборку ленты"
        )
    )
    type = serializers.ChoiceField(choices=[*FEED_TYPES])
    group = serializers.CharField(help_text="Вкладка фильтра")
    title = serializers.CharField()
    short_text = serializers.CharField(
        help_text="Короткая вторичная строка: ни описания целиком, ни документа"
    )
    employee_id = serializers.UUIDField(allow_null=True)
    employee_name = serializers.CharField(allow_blank=True)
    office_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    status_label = serializers.CharField()
    priority = serializers.ChoiceField(choices=[*FEED_PRIORITIES])
    requires_action = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    read_at = serializers.DateTimeField(
        allow_null=True, help_text="Прочтение ЭТОГО пользователя, не общее"
    )
    related_entity_type = serializers.CharField()
    related_entity_id = serializers.UUIDField()
    action_url = serializers.CharField(help_text="Куда ведёт кнопка карточки")
    action_title = serializers.CharField()


class FeedPageSerializer(serializers.Serializer):
    items = FeedItemSerializer(many=True)
    counts = FeedCountsSerializer()
    next_cursor = serializers.CharField(allow_null=True)
    has_more = serializers.BooleanField()
    window_days = serializers.IntegerField(help_text="Глубина ленты в днях")


class FeedDetailSerializer(FeedItemSerializer):
    """Карточка события: общие поля плюс блок своего вида.

    Блоков ровно один на событие, и какой именно — говорит `type`:
    `absence`, `correction`, `session`, `question`, `delivery`,
    `report` или `new_employee`.
    """

    employee = serializers.DictField(allow_null=True)
    author = serializers.DictField(
        allow_null=True, help_text="Кто принял решение или ведёт обращение"
    )
    comment = serializers.CharField(allow_null=True)
    occurred_at = serializers.DateTimeField()
    absence = serializers.DictField(required=False)
    correction = serializers.DictField(required=False)
    session = serializers.DictField(required=False)
    question = serializers.DictField(required=False)
    delivery = serializers.DictField(required=False)
    report = serializers.DictField(required=False)
    new_employee = serializers.DictField(required=False)


class FeedMarkAllSerializer(FeedCountsSerializer):
    marked = serializers.IntegerField(help_text="Сколько событий отмечено сейчас")


@extend_schema(tags=["Уведомления"])
class FeedView(APIView):
    """Лента событий кадровика и её счётчики."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="notification_feed",
        summary="Лента событий",
        description=(
            "События кадрового контура за последние "
            f"{FEED_DAYS} дней: заявки, справки, исправления отметок, "
            "незакрытые выходы, обращения, ошибки доставки, готовые "
            "выгрузки и новые сотрудники.\n\n"
            "Счётчики приходят тем же ответом, а не отдельным запросом: "
            "число у колокольчика и список под ним обязаны описывать "
            "одно состояние, а два запроса дали бы два разных момента.\n\n"
            "Права проверяются по каждому источнику отдельно. Источник, "
            "на который прав нет, не даёт строк и не даёт отказа: "
            "отсутствие права на обращения не должно закрывать заявки."
        ),
        parameters=[
            OpenApiParameter(
                "scope", str,
                enum=[key for key, _ in FILTERS],
                description="Вкладка фильтра, по умолчанию «Все»",
            ),
            OpenApiParameter("limit", int),
            OpenApiParameter("cursor", str),
        ],
        responses={200: FeedPageSerializer},
    )
    def get(self, request):
        limit = request.query_params.get("limit")
        return Response(
            FeedService().page(
                Actor.from_user(request.user),
                scope=request.query_params.get("scope") or None,
                limit=int(limit) if limit and limit.isdigit() else None,
                cursor=request.query_params.get("cursor") or None,
            )
        )


@extend_schema(tags=["Уведомления"])
class FeedCountsView(APIView):
    """Только счётчики — для колокольчика на страницах без ленты."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="notification_feed_counts",
        summary="Счётчики ленты",
        responses={200: FeedCountsSerializer},
    )
    def get(self, request):
        return Response(FeedService().counts(Actor.from_user(request.user)))


@extend_schema(tags=["Уведомления"])
class FeedItemView(APIView):
    """Карточка события и отметка прочтения."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="notification_feed_item",
        summary="Карточка события",
        description=(
            "Доступ проверяется заново, от исходной записи: ключ события "
            "выводится из данных, то есть угадываем, и открывать по нему "
            "чужую заявку нельзя.\n\n"
            "Ни тела документа, ни паспортных данных, ни токенов "
            "провайдера в ответе нет — только безопасные сведения "
            "о событии."
        ),
        responses={200: FeedDetailSerializer},
    )
    def get(self, request, event_id: str):
        return Response(
            FeedService().detail(Actor.from_user(request.user), event_id)
        )

    @extend_schema(
        operation_id="notification_feed_read",
        summary="Отметить событие прочитанным",
        description=(
            "Прочтение хранится для каждого пользователя своё: отметка "
            "одного кадровика не гасит событие у другого. Ответ — новые "
            "счётчики, чтобы число у колокольчика обновилось без "
            "перезагрузки страницы."
        ),
        request=None,
        responses={200: FeedCountsSerializer},
    )
    def post(self, request, event_id: str):
        return Response(
            FeedService().mark_read(Actor.from_user(request.user), event_id)
        )


@extend_schema(tags=["Уведомления"])
class FeedReadAllView(APIView):
    """«Прочитать все» — ровно то, что видно этому пользователю."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="notification_feed_read_all",
        summary="Прочитать все",
        description=(
            "Отметки пишутся по тем событиям, которые вернула лента "
            "ЭТОГО пользователя: его офисы и его права. Общего обновления "
            "здесь нет — оно пометило бы и чужие офисы, и виды, на "
            "которые прав нет."
        ),
        request=None,
        responses={200: FeedMarkAllSerializer},
    )
    def post(self, request):
        return Response(FeedService().mark_all(Actor.from_user(request.user)))


__all__ = ["FeedCountsView", "FeedItemView", "FeedReadAllView", "FeedView"]
