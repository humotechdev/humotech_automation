"""Вход в систему по паре «организация + почта».

Почта уникальна ВНУТРИ организации, а не глобально: один и тот же адрес может
принадлежать разным людям в разных организациях. Стандартный `ModelBackend`
ищет по одному полю и на двух совпадениях упал бы с MultipleObjectsReturned,
поэтому вход выполняет свой backend.

Организация определяется кодом, который приходит вместе с учётными данными.
Без него по одной почте пользователя не найти — и это не неудобство, а
следствие того, что данные организаций изолированы.
"""

from __future__ import annotations

from django.contrib.auth.backends import BaseBackend

from humotech.accounts.models import User


class OrganizationEmailBackend(BaseBackend):
    """Проверка пароля по (код организации, почта)."""

    def authenticate(self, request, username=None, password=None,
                     organization_code=None, **kwargs):
        email = username or kwargs.get("email")
        if not email or not password or not organization_code:
            return None

        user = (
            User.objects.filter(
                email__iexact=email,
                organization__code__iexact=organization_code,
            )
            .select_related("organization")
            .first()
        )
        if user is None:
            # Пароль всё равно проверяем по несуществующему хешу: иначе время
            # ответа выдаёт, существует ли такая учётная запись.
            User().set_password(password)
            return None
        if not user.check_password(password):
            return None
        if not user.is_active:
            return None
        return user

    def get_user(self, user_id):
        return (
            User.objects.filter(pk=user_id).select_related("organization").first()
        )
