"""Чтение прав и ролей пользователя.

Отдельный модуль, а не методы модели: те же выборки нужны сервисам и DRF,
а тащить туда экземпляр модели ради одного запроса незачем. Модель вызывает
эти функции, а не наоборот.
"""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone


def active_scopes(user, at: datetime | None = None):
    """Действующие на момент `at` записи `user_role_scopes`.

    Роль и её область выдаются на период. Просроченная выдача не даёт
    ни прав, ни видимости — это одно и то же множество строк.
    """
    from humotech.accounts.models import UserRoleScope

    at = at or timezone.now()
    return (
        UserRoleScope.objects.filter(user=user, valid_from__lte=at)
        .filter(models_q_valid_to(at))
        .select_related("role")
    )


def models_q_valid_to(at: datetime):
    from django.db.models import Q

    return Q(valid_to__isnull=True) | Q(valid_to__gte=at)


def active_role_codes(user, at: datetime | None = None) -> set[str]:
    return set(active_scopes(user, at).values_list("role__code", flat=True))


def permission_codes(user, at: datetime | None = None) -> set[str]:
    """Объединение разрешений всех действующих ролей пользователя."""
    from humotech.accounts.models import Permission

    role_ids = set(active_scopes(user, at).values_list("role_id", flat=True))
    if not role_ids:
        return set()
    return set(
        Permission.objects.filter(role_links__role_id__in=role_ids)
        .values_list("code", flat=True)
        .distinct()
    )
