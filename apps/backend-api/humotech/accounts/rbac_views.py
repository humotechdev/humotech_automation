"""REST-интерфейс учётных записей, ролей и областей видимости.

Отдельно от `accounts/views.py`: там вход и «кто я», здесь управление
доступом других людей. Разрешения тоже разные — `users.manage` и
`roles.manage` против «быть авторизованным».
"""

from __future__ import annotations

import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.accounts.rbac_service import RoleAdminService, UserAdminService
from humotech.core.api import ServiceViewSet, validated
from humotech.core.enums import USER_STATUSES
from humotech.core.errors import ValidationFailed
from humotech.core.rbac import Actor


class ShortGrantSerializer(serializers.Serializer):
    """Назначение в строке списка: сколько их и какие, без подробностей."""

    id = serializers.UUIDField()
    role_id = serializers.UUIDField()
    role_name = serializers.CharField(source="role.name")
    role_code = serializers.CharField(source="role.code")
    region_id = serializers.UUIDField(allow_null=True)
    region_name = serializers.CharField(
        source="region.name", allow_null=True, default=None
    )
    office_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(
        source="office.name", allow_null=True, default=None
    )


class CrmUserSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.CharField(help_text="Логин для входа")
    status = serializers.ChoiceField(choices=USER_STATUSES)
    mfa_enabled = serializers.BooleanField()
    employee_id = serializers.UUIDField(allow_null=True)
    # Имени у учётной записи нет: она опознаётся адресом. Человеческое
    # имя есть у СОТРУДНИКА, и появляется здесь только если запись к нему
    # привязана. У технической учётки его нет вовсе — и подставлять туда
    # адрес вместо имени значит выдавать одно за другое.
    full_name = serializers.SerializerMethodField()
    last_login = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    active_grants = ShortGrantSerializer(many=True, required=False)
    grants_visible = serializers.BooleanField(
        required=False,
        help_text="false — назначения не показаны, потому что у "
                  "смотрящего нет roles.manage. Пустой список при этом "
                  "не означает отсутствие назначений",
    )

    def get_full_name(self, user) -> str | None:
        # У привязанной записи имя берут из карточки сотрудника: там оно
        # ведётся кадровиком и меняется вместе с человеком. Собственное
        # имя записи — для тех, у кого карточки нет.
        employee = getattr(user, "employee", None)
        if employee is not None:
            parts = [employee.last_name, employee.first_name, employee.middle_name]
            name = " ".join(part for part in parts if part)
            if name:
                return name
        return user.full_name or None


class CrmUserCountsSerializer(serializers.Serializer):
    active = serializers.IntegerField()
    inactive = serializers.IntegerField()
    total = serializers.IntegerField()
    roles = serializers.IntegerField(
        allow_null=True,
        help_text="Число ролей в каталоге. null — у смотрящего нет "
                  "roles.manage, и каталог ему не показывают",
    )


class CrmUserCreateSerializer(serializers.Serializer):
    """Заведение администратора.

    `email` — это логин. Имя поля историческое: оно совпадает с колонкой
    в базе. Адресом почты оно быть не обязано — `malika.hr` ничем не
    хуже, и требовать почту там, где её нет, значит требовать выдумать её.
    """

    email = serializers.CharField(max_length=255)
    full_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    password = serializers.CharField(
        max_length=256, required=False, allow_blank=True, write_only=True,
        help_text="Без пароля запись создаётся отключённой",
    )
    employee_id = serializers.UUIDField(required=False, allow_null=True)


class CrmUserUpdateSerializer(serializers.Serializer):
    email = serializers.CharField(max_length=255, required=False)
    full_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    employee_id = serializers.UUIDField(required=False, allow_null=True)
    unlink_employee = serializers.BooleanField(
        required=False,
        help_text="true — отвязать сотрудника от учётной записи",
    )


class SetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(
        max_length=256,
        write_only=True,
        help_text="Проверяется правилами Django: длина, распространённость, "
                  "сходство с адресом",
    )


class PermissionSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)


class PermissionListSerializer(serializers.Serializer):
    items = PermissionSerializer(many=True)


class RoleWriteSerializer(serializers.Serializer):
    code = serializers.CharField(
        max_length=50, help_text="Приводится к верхнему регистру",
    )
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True,
    )
    permissions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Коды из /api/v1/permissions. Вложить можно только те, "
                  "которыми владеет сам выдающий",
    )


class RoleUpdateSerializer(serializers.Serializer):
    expected_updated_at = serializers.DateTimeField(
        required=False, allow_null=True,
        help_text="Редакция, которую вы правите. Не совпала с текущей — "
                  "409: кто-то изменил роль, пока форма была открыта",
    )
    name = serializers.CharField(max_length=100, required=False)
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True,
    )
    permissions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Полная замена набора, а не добавление",
    )


class RoleSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    is_system = serializers.BooleanField()
    permissions = serializers.ListField(child=serializers.CharField())
    offered = serializers.BooleanField(
        required=False,
        help_text="Предлагается ли роль в форме выдачи доступа",
    )
    grantable = serializers.BooleanField(
        help_text="Может ли ЭТОТ пользователь выдать эту роль",
    )
    missing_permissions = serializers.ListField(
        child=serializers.CharField(),
        help_text="Чего не хватает выдающему, если роль недоступна",
    )
    updated_at = serializers.DateTimeField(
        help_text="Редакция роли. Отправьте её обратно в PATCH, чтобы "
                  "правка не затёрла чужую, сделанную тем временем",
    )


class RoleListSerializer(serializers.Serializer):
    items = RoleSerializer(many=True)


class GrantSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    role_id = serializers.UUIDField()
    role_code = serializers.CharField(source="role.code")
    role_name = serializers.CharField(source="role.name")
    region_id = serializers.UUIDField(allow_null=True)
    region_name = serializers.CharField(
        source="region.name", allow_null=True, default=None
    )
    office_id = serializers.UUIDField(allow_null=True)
    office_name = serializers.CharField(
        source="office.name", allow_null=True, default=None
    )
    valid_from = serializers.DateTimeField()
    valid_to = serializers.DateTimeField(allow_null=True)


class GrantListSerializer(serializers.Serializer):
    items = GrantSerializer(many=True)


class GrantCreateSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    role_id = serializers.UUIDField()
    region_id = serializers.UUIDField(required=False, allow_null=True)
    office_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Без региона и офиса — доступ на всю организацию",
    )
    valid_from = serializers.DateTimeField(required=False, allow_null=True)
    valid_to = serializers.DateTimeField(required=False, allow_null=True)
    valid_to_date = serializers.DateField(
        required=False, allow_null=True,
        help_text="Календарная дата «действует по», включительно. Границу суток ставит сервер в поясе организации: у даты, посчитанной браузером, конец дня зависел бы от того, кто нажимал кнопку",
    )


class GrantValiditySerializer(serializers.Serializer):
    valid_to = serializers.DateTimeField(
        required=False, allow_null=True, help_text="null — бессрочно"
    )
    valid_to_date = serializers.DateField(
        required=False, allow_null=True,
        help_text="Календарная дата «действует по», включительно. Границу суток ставит сервер в поясе организации: у даты, посчитанной браузером, конец дня зависел бы от того, кто нажимал кнопку",
    )
    expected_valid_to = serializers.DateTimeField(
        required=False, allow_null=True,
        help_text="Срок, который вы видели на экране. Передан вместе с "
                  "check_expected — не совпал с текущим, 409",
    )
    check_expected = serializers.BooleanField(
        required=False, default=False,
        help_text="true — сверять expected_valid_to. Отдельный признак "
                  "нужен потому, что null — законное «бессрочно», а не "
                  "«не передали»",
    )


class ScopeRegionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class ScopeOfficeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    region_id = serializers.UUIDField(allow_null=True)


class AssignableScopesSerializer(serializers.Serializer):
    all_organization = serializers.BooleanField(
        help_text="Вправе ли выдать назначение без региона и офиса, то "
                  "есть доступ на всю организацию",
    )
    regions = ScopeRegionSerializer(many=True)
    offices = ScopeOfficeSerializer(many=True)


@extend_schema(tags=["Пользователи"])
class CrmUserViewSet(ServiceViewSet):
    """Учётные записи CRM. Требует `users.manage`.

    Пароль здесь не задаётся и не меняется. Новая запись заводится без
    пригодного пароля и в статусе INACTIVE: вход открывается, когда
    человек установит пароль сам.
    """

    service_class = UserAdminService
    read_serializer_class = CrmUserSerializer

    @extend_schema(
        summary="Список учётных записей",
        description=(
            "Строка несёт ДЕЙСТВУЮЩИЕ назначения (`active_grants`) — все, "
            "а не первое: у человека их может быть несколько и в разных "
            "областях. Истёкшие и отозванные сюда не попадают: активная "
            "запись без действующих назначений — это запись без доступа, "
            "и выглядеть она должна именно так."
        ),
        parameters=[
            OpenApiParameter(
                "search", str,
                description="Подстрока адреса или имени сотрудника",
            ),
            OpenApiParameter("status", str, enum=list(USER_STATUSES)),
            OpenApiParameter(
                "role_id", OpenApiTypes.UUID,
                description="Только с ДЕЙСТВУЮЩИМ назначением этой роли",
            ),
            OpenApiParameter("cursor", str),
            OpenApiParameter("limit", int),
        ],
    )
    def list(self, request):
        return self.page_response(
            self.service.list(
                self.actor, **self.list_params(), **self._filters(request)
            )
        )

    @extend_schema(
        summary="Сводка по учётным записям",
        description=(
            "Считается по всему отобранному набору, а не по показанной "
            "странице. Фильтр статуса сюда не передаётся: он и есть то, "
            "что считают."
        ),
        parameters=[
            OpenApiParameter("search", str),
            OpenApiParameter("role_id", OpenApiTypes.UUID),
        ],
        responses={200: CrmUserCountsSerializer},
    )
    @action(detail=False)
    def counts(self, request):
        # Поиск сюда идёт, статус — нет. Сводка обязана описывать ТОТ ЖЕ
        # набор, что и таблица под ней: иначе «найдено 3» соседствует
        # с «всего 47». Статус — единственное исключение, и по той же
        # причине: он и есть то, что считают.
        return Response(
            self.service.counts(
                self.actor,
                search=request.query_params.get("search") or None,
                **self._filters(request),
            )
        )

    @staticmethod
    def _filters(request) -> dict:
        raw = request.query_params.get("role_id")
        role_id = None
        if raw:
            try:
                role_id = uuid.UUID(raw)
            except ValueError as exc:
                raise ValidationFailed(
                    "role_id должен быть UUID", details={"role_id": raw}
                ) from exc
        return {"role_id": role_id}

    @extend_schema(summary="Одна учётная запись")
    def retrieve(self, request, pk=None):
        return self.item_response(self.service.get(self.actor, pk))

    @extend_schema(
        summary="Завести учётную запись",
        description=(
            "Создаётся без пароля и в статусе INACTIVE. Пароль "
            "устанавливает сам человек: запись, чей пароль знает кто-то "
            "ещё, не отвечает на вопрос «кто это сделал»."
        ),
        request=CrmUserCreateSerializer,
        responses={201: CrmUserSerializer},
    )
    def create(self, request):
        payload = validated(CrmUserCreateSerializer, request.data)
        return self.item_response(
            self.service.create(self.actor, **payload), created=True
        )

    @extend_schema(
        summary="Изменить адрес или привязку к сотруднику",
        description=(
            "Статус сюда не входит: у него свои действия, и они делают "
            "больше правки поля."
        ),
        request=CrmUserUpdateSerializer,
        responses={200: CrmUserSerializer},
    )
    def partial_update(self, request, pk=None):
        payload = validated(CrmUserUpdateSerializer, request.data)
        return self.item_response(self.service.update(self.actor, pk, **payload))

    @extend_schema(
        summary="Установить пароль",
        description=(
            "Пароль проверяется теми же правилами Django, что и везде. "
            "В журнал попадает только факт установки: ни значение, ни хеш, "
            "ни длина — длина сама по себе сужает перебор.\n\n"
            "Действующие сессии этой учётной записи после смены перестают "
            "работать: Django сверяет с сессией отпечаток пароля."
        ),
        request=SetPasswordSerializer,
        responses={200: CrmUserSerializer},
    )
    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        payload = validated(SetPasswordSerializer, request.data)
        return self.item_response(
            self.service.set_password(self.actor, pk, **payload)
        )

    @extend_schema(summary="Включить учётную запись", responses={200: CrmUserSerializer})
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self.item_response(
            self.service.set_status(self.actor, pk, status="ACTIVE")
        )

    @extend_schema(
        summary="Отключить учётную запись",
        description=(
            "Отказ, если это последний действующий суперадминистратор "
            "организации: остаться без него значит потерять управление."
        ),
        responses={200: CrmUserSerializer},
    )
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self.item_response(
            self.service.set_status(self.actor, pk, status="INACTIVE")
        )


@extend_schema(tags=["Роли"])
class RoleListView(APIView):
    """Роли с их разрешениями. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="roles_list",
        summary="Роли и их разрешения",
        description=(
            "Рядом с каждой ролью — может ли ЭТОТ пользователь её выдать "
            "и чего ему для этого не хватает. Считает сервер: кнопка, "
            "скрытая на клиенте, защитой не является."
        ),
        responses={200: RoleListSerializer},
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        return Response({"items": RoleAdminService().roles(actor)})

    @extend_schema(
        operation_id="roles_create",
        summary="Завести роль организации",
        description=(
            "Системная роль отсюда не появится: роль всегда заводится "
            "внутри своей организации. Вложить в неё можно только те "
            "права, которыми владеет сам заводящий, — иначе роль стала бы "
            "обходом собственных ограничений в два шага."
        ),
        request=RoleWriteSerializer,
        responses={201: RoleSerializer},
        examples=[
            OpenApiExample(
                "Кадровик региона",
                value={"code": "HR_REGION", "name": "Кадровик региона",
                       "permissions": ["employees.read", "schedules.read"]},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        actor = Actor.from_user(request.user)
        role = RoleAdminService().create_role(
            actor, **validated(RoleWriteSerializer, request.data)
        )
        return Response(RoleSerializer(role).data, status=201)


@extend_schema(tags=["Роли"])
class RoleDetailView(APIView):
    """Одна роль. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="roles_retrieve",
        summary="Одна роль",
        responses={200: RoleSerializer},
    )
    def get(self, request, role_id):
        actor = Actor.from_user(request.user)
        return Response(
            RoleSerializer(RoleAdminService().role(actor, role_id)).data
        )

    @extend_schema(
        operation_id="roles_partial_update",
        summary="Изменить название, описание или права роли",
        description=(
            "Код не меняется: по нему роль опознают проверки, и другой код "
            "означает другую роль. Набор прав заменяется целиком, а не "
            "дополняется. Системные роли не правятся — они общие для всех "
            "организаций."
        ),
        request=RoleUpdateSerializer,
        responses={200: RoleSerializer},
    )
    def patch(self, request, role_id):
        actor = Actor.from_user(request.user)
        role = RoleAdminService().update_role(
            actor, role_id, **validated(RoleUpdateSerializer, request.data)
        )
        return Response(RoleSerializer(role).data)


@extend_schema(tags=["Роли"])
class PermissionListView(APIView):
    """Справочник разрешений. Требует `roles.manage`.

    Общий для всех организаций и меняется миграциями: это словарь
    операций системы, а не данные организации.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="permissions_list",
        summary="Все разрешения системы",
        responses={200: PermissionListSerializer},
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        return Response(
            {
                "items": PermissionSerializer(
                    RoleAdminService().permissions_catalog(actor), many=True
                ).data
            }
        )


@extend_schema(tags=["Роли"])
class UserGrantsView(APIView):
    """Назначения одного пользователя. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Назначения пользователя",
        parameters=[
            OpenApiParameter(
                "history", OpenApiTypes.BOOL,
                description=(
                    "true — вся история, включая отозванные. Отзыв не "
                    "удаляет строку, поэтому история есть всегда."
                ),
            ),
        ],
        responses={200: GrantListSerializer},
    )
    def get(self, request, user_id: uuid.UUID):
        actor = Actor.from_user(request.user)
        history = str(request.query_params.get("history", "")).lower() in (
            "1", "true", "yes",
        )
        rows = RoleAdminService().assignments(
            actor, user_id, include_expired=history
        )
        return Response({"items": GrantSerializer(rows, many=True).data})


@extend_schema(tags=["Роли"])
class AssignableScopesView(APIView):
    """Области, доступные для выдачи. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="grants_scopes",
        summary="Области, которые вы вправе назначить",
        description=(
            "Не справочник регионов и офисов: тот читается по "
            "`regions.read` и `offices.read`, а роли выдаёт тот, у кого "
            "`roles.manage`. Права это разные, и у технического "
            "администратора `regions.read` нет.\n\n"
            "Список считается тем же кодом, что и проверка при выдаче, "
            "поэтому разойтись с ней не может. Регион попадает сюда и "
            "тогда, когда офисов в нём нет вовсе.\n\n"
            "`all_organization` отвечает на отдельный вопрос: вправе ли "
            "этот пользователь выдать доступ на всю организацию "
            "(назначение без региона и офиса). Несколько ограниченных "
            "областей такого права не дают.\n\n"
            "Параметра роли здесь нет намеренно: выдача проверяет роль "
            "и территорию независимо, и допустимая область от выбранной "
            "роли не зависит.\n\n"
            "Сознательное сужение: отдаются только действующие регионы "
            "и офисы. Сама проверка статуса не смотрит, но предлагать "
            "закрытый офис в форме выдачи незачем."
        ),
        responses={200: AssignableScopesSerializer},
    )
    def get(self, request):
        actor = Actor.from_user(request.user)
        return Response(
            AssignableScopesSerializer(
                RoleAdminService().assignable_scopes(actor)
            ).data
        )


@extend_schema(tags=["Роли"])
class GrantCreateView(APIView):
    """Выдача роли. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Выдать роль",
        description=(
            "Проверок две: набор прав роли обязан входить в набор "
            "выдающего, И территория назначения обязана лежать внутри "
            "его области. Одной первой мало — список прав собирается по "
            "всем областям сразу и территорию не ограничивает."
        ),
        request=GrantCreateSerializer,
        responses={201: GrantSerializer},
        examples=[
            OpenApiExample(
                "Кадровик одного региона",
                value={"user_id": "…", "role_id": "…", "region_id": "…"},
                request_only=True,
            ),
            OpenApiExample(
                "Временный доступ до конца квартала",
                value={"user_id": "…", "role_id": "…", "office_id": "…",
                       "valid_to": "2026-06-30T18:00:00+05:00"},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        actor = Actor.from_user(request.user)
        payload = validated(GrantCreateSerializer, request.data)
        grant = RoleAdminService().assign(actor, **payload)
        return Response(GrantSerializer(grant).data, status=201)


@extend_schema(tags=["Роли"])
class GrantDetailView(APIView):
    """Отзыв и продление одного назначения. Требует `roles.manage`."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Изменить срок назначения",
        description=(
            "Сверка редакции необязательна и включается признаком "
            "`check_expected`: у назначения нет `updated_at`, редакцией "
            "служит прежний срок, а `null` — законное «бессрочно», а не "
            "«не передали»."
        ),
        request=GrantValiditySerializer,
        responses={200: GrantSerializer},
    )
    def patch(self, request, grant_id: uuid.UUID):
        actor = Actor.from_user(request.user)
        payload = validated(GrantValiditySerializer, request.data)
        if "valid_to" not in payload and "valid_to_date" not in payload:
            raise ValidationFailed(
                "Нужен либо valid_to, либо valid_to_date",
                details={"fields": ["valid_to", "valid_to_date"]},
            )
        grant = RoleAdminService().set_validity(
            actor,
            grant_id,
            valid_to=payload.get("valid_to"),
            valid_to_date=payload.get("valid_to_date"),
            expected_valid_to=payload.get("expected_valid_to"),
            check_expected=payload.get("check_expected", False),
        )
        return Response(GrantSerializer(grant).data)

    @extend_schema(
        summary="Отозвать роль",
        description=(
            "Строка остаётся, закрывается срок: удаление стёрло бы "
            "ответ на вопрос «кто и когда дал человеку этот доступ». "
            "Отказ, если это роль последнего суперадминистратора."
        ),
        responses={200: GrantSerializer},
    )
    def delete(self, request, grant_id: uuid.UUID):
        actor = Actor.from_user(request.user)
        grant = RoleAdminService().revoke(actor, grant_id)
        return Response(GrantSerializer(grant).data)


__all__ = [
    "CrmUserViewSet",
    "GrantCreateView",
    "GrantDetailView",
    "PermissionListView",
    "RoleDetailView",
    "RoleListView",
    "UserGrantsView",
]
