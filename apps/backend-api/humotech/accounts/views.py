"""Вход, выход и сведения о текущем пользователе.

Организация передаётся при входе явно: почта уникальна внутри организации,
а не глобально, поэтому пары «почта + пароль» для опознания недостаточно.
"""

from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import exceptions, serializers, status
from rest_framework.authentication import CSRFCheck
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.accounts.security import LoginGuard, audit_logout
from humotech.accounts.selectors import active_role_codes, permission_codes
from humotech.core.timeframes import organization_zone


class LoginSerializer(serializers.Serializer):
    # Пределы длины — не только про базу: без них стокилобайтный «пароль»
    # честно хешировался бы argon2. Одиночные суррогаты и NUL-байты
    # отсекают встроенные проверки CharField, JSON-объект вместо строки —
    # он же: всё это 400, а не 500.
    organization_code = serializers.CharField(max_length=50)
    email = serializers.CharField(max_length=255)
    password = serializers.CharField(max_length=256, write_only=True)


def _enforce_csrf(request) -> None:
    """CSRF и для входа.

    DRF снимает проверку CSRF со всех своих view и возвращает её только
    вошедшему по сессии. Вход — запрос анонимный, и без явной проверки
    чужая страница могла бы тихо войти в браузере кадровика под учёткой
    злоумышленника («login CSRF»): дальше всё, что человек введёт,
    окажется у того. Cookie `csrftoken` CRM получает раньше — её ставит
    `GET /auth/me`, который CRM вызывает при каждой загрузке.
    """
    check = CSRFCheck(lambda _request: None)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise exceptions.PermissionDenied(f"CSRF Failed: {reason}")


def _too_many(retry_after: int) -> Response:
    minutes = max(1, (retry_after + 59) // 60)
    response = Response(
        {"error": {"code": "too_many_attempts",
                   "message": (
                       "Слишком много неудачных попыток входа. "
                       f"Повторите через {minutes} мин."
                   ),
                   "details": {"retry_after": retry_after}}},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
    )
    response["Retry-After"] = str(retry_after)
    return response


class CurrentUserSerializer(serializers.Serializer):
    """Кто вошёл и что ему можно.

    Тот же состав отдают и вход, и «кто я»: интерфейсу после входа
    нужно ровно то же, что и при обновлении страницы, и второй формой
    ответа они бы только разошлись.
    """

    id = serializers.UUIDField()
    email = serializers.CharField()
    organization_id = serializers.UUIDField()
    organization_code = serializers.CharField()
    employee_id = serializers.UUIDField(
        allow_null=True,
        help_text="null у технической учётной записи без сотрудника",
    )
    status = serializers.CharField()
    timezone = serializers.CharField(
        help_text=(
            "Пояс организации. В нём интерфейс печатает время там, где "
            "у строки нет своего офиса: журнал действий, карточка "
            "учётной записи, сроки назначений"
        ),
    )
    roles = serializers.ListField(child=serializers.CharField())
    permissions = serializers.ListField(
        child=serializers.CharField(),
        help_text=(
            "Коды разрешений — чтобы интерфейс мог скрыть недоступные "
            "кнопки. Скрытая кнопка не защита: решение всё равно "
            "принимает сервер при каждом вызове."
        ),
    )


class ErrorBodySerializer(serializers.Serializer):
    """Тело ошибки, единое для всего API.

    Клиент различает ситуации по `code`, а не по тексту: текст
    переводится и переписывается, код — нет.
    """

    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField(allow_null=True)


class ErrorSerializer(serializers.Serializer):
    error = ErrorBodySerializer()


@extend_schema(tags=["Вход"])
class LoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Вход по организации, почте и паролю",
        description=(
            "Организация передаётся явно: почта уникальна внутри "
            "организации, а не глобально, и пары «почта + пароль» для "
            "опознания недостаточно.\n\n"
            "В ответе устанавливается сессионная cookie. Дальнейшие "
            "изменяющие запросы требуют заголовка CSRF."
        ),
        request=LoginSerializer,
        responses={
            200: CurrentUserSerializer,
            401: OpenApiResponse(
                response=ErrorSerializer,
                description=(
                    "Одно сообщение на все причины: иначе по разнице "
                    "ответов можно перебором узнать, какие учётные "
                    "записи существуют."
                ),
            ),
            429: OpenApiResponse(
                response=ErrorSerializer,
                description=(
                    "Слишком много неудачных попыток: по этой учётной записи "
                    "с этого адреса, по учётной записи вообще или с адреса. "
                    "Одинаково для существующих и несуществующих учётных "
                    "записей. Срок — в заголовке Retry-After."
                ),
            ),
        },
        examples=[
            OpenApiExample(
                "Кадровик HUMOTECH",
                value={"organization_code": "HUMO",
                       "email": "hr@humotech.tj", "password": "…"},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        _enforce_csrf(request)
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Попытка засчитывается ДО проверки пароля: иначе параллельные
        # запросы успевают перебрать пароли, пока первый дописывает неудачу.
        guard = LoginGuard(request, data["organization_code"], data["email"])
        retry_after = guard.admit()
        if retry_after:
            return _too_many(retry_after)

        user = authenticate(
            request,
            username=data["email"],
            password=data["password"],
            organization_code=data["organization_code"],
        )
        if user is None:
            guard.failed()
            # Одно сообщение на все причины: иначе по разнице ответов можно
            # перебором узнать, какие учётные записи существуют.
            return Response(
                {"error": {"code": "invalid_credentials",
                           "message": "Неверная организация, почта или пароль",
                           "details": None}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # `login` сам меняет ключ сессии и CSRF-токен: ключ, подсунутый
        # до входа (session fixation), после входа ничего не открывает.
        login(request, user)
        guard.succeeded(user)
        return Response(_describe(user))


@extend_schema(tags=["Вход"])
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Выход",
        description="Сессия удаляется на сервере, cookie перестаёт работать.",
        request=None,
        responses={204: None},
    )
    def post(self, request):
        user = request.user
        logout(request)
        audit_logout(request, user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Вход"])
class CurrentUserView(APIView):
    """Кто я и что мне можно.

    Права отдаются списком, чтобы CRM могла скрыть недоступные кнопки.
    Скрытая кнопка — удобство, а не защита: решение всё равно принимает
    backend при каждом вызове.
    """

    permission_classes = [IsAuthenticated]

    # Cookie `csrftoken` нужна CRM ещё до входа: вход тоже проверяет CSRF.
    # Этот запрос CRM делает при каждой загрузке, в том числе анонимно.
    @method_decorator(ensure_csrf_cookie)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    @extend_schema(
        summary="Текущий пользователь",
        responses={200: CurrentUserSerializer},
    )
    def get(self, request):
        return Response(_describe(request.user))


def _describe(user) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "organization_id": str(user.organization_id),
        "organization_code": user.organization.code,
        "employee_id": str(user.employee_id) if user.employee_id else None,
        "status": user.status,
        # Пояс организации, а не сервера и не браузера: иначе журнал
        # действий у двух администраторов из разных городов утверждает
        # разное время про одно и то же событие.
        "timezone": str(organization_zone(user.organization_id)),
        "roles": sorted(active_role_codes(user)),
        "permissions": sorted(permission_codes(user)),
    }
