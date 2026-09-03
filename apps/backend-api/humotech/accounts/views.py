"""Вход, выход и сведения о текущем пользователе.

Организация передаётся при входе явно: почта уникальна внутри организации,
а не глобально, поэтому пары «почта + пароль» для опознания недостаточно.
"""

from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from humotech.accounts.selectors import active_role_codes, permission_codes


class LoginSerializer(serializers.Serializer):
    organization_code = serializers.CharField(max_length=50)
    email = serializers.CharField(max_length=255)
    password = serializers.CharField(max_length=256, write_only=True)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = authenticate(
            request,
            username=data["email"],
            password=data["password"],
            organization_code=data["organization_code"],
        )
        if user is None:
            # Одно сообщение на все причины: иначе по разнице ответов можно
            # перебором узнать, какие учётные записи существуют.
            return Response(
                {"error": {"code": "invalid_credentials",
                           "message": "Неверная организация, почта или пароль",
                           "details": None}},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        login(request, user)
        return Response(_describe(user))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentUserView(APIView):
    """Кто я и что мне можно.

    Права отдаются списком, чтобы CRM могла скрыть недоступные кнопки.
    Скрытая кнопка — удобство, а не защита: решение всё равно принимает
    backend при каждом вызове.
    """

    permission_classes = [IsAuthenticated]

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
        "roles": sorted(active_role_codes(user)),
        "permissions": sorted(permission_codes(user)),
    }
