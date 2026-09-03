"""Кто действует и что ему можно.

Один слой на весь проект: разрешение отвечает на вопрос «что можно делать»,
область из `user_role_scopes` — «с чьими данными». Параллельной системы ролей
здесь нет и быть не должно — в том числе поэтому модель пользователя не берёт
`PermissionsMixin` из Django.

Три вещи, которые легко перепутать и которые проверяются отдельно:

  * «видит всё» и «не видит ничего» — РАЗНЫЕ состояния. `visible_office_ids`
    возвращает `None` для доступа ко всей организации и пустое множество
    для отсутствия доступа. Проверка на истинность (`if visible:`) склеивает
    их и молча выдаёт доступ ко всей организации тому, у кого прав нет вовсе;
  * разрешения и область — разные вещи. У регионального HR может быть
    `employees.manage`, но чужой офис ему всё равно закрыт;
  * чужая организация отвечает как несуществующая запись, а свой объект вне
    области — отказом. Иначе перебором идентификаторов можно пересчитать
    записи соседней организации.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from humotech.core.errors import NotFound, PermissionDenied


@dataclass(frozen=True)
class Actor:
    """Пользователь CRM, от имени которого выполняется операция."""

    user_id: uuid.UUID
    organization_id: uuid.UUID

    @classmethod
    def from_user(cls, user) -> "Actor":
        return cls(user_id=user.id, organization_id=user.organization_id)


@dataclass(frozen=True)
class Scope:
    """Что пользователь имеет право видеть.

    `all_offices=True` — доступ ко всей организации. В этом случае множества
    офисов и регионов не заполняются и смысла не имеют.
    """

    all_offices: bool
    office_ids: frozenset[uuid.UUID]
    region_ids: frozenset[uuid.UUID]

    @property
    def sees_nothing(self) -> bool:
        return not self.all_offices and not self.office_ids and not self.region_ids


class AccessControl:
    """Проверки прав и области. Единственная точка, где они принимаются."""

    def __init__(self) -> None:
        # Ключ кэша включает момент времени: роли и области выданы на период,
        # и один и тот же пользователь «на вчера» и «на сегодня» — разные
        # ответы. Кэш по одному user_id вернул бы вчерашний ответ на
        # сегодняшний вопрос.
        self._permissions: dict[tuple[uuid.UUID, datetime | None], set[str]] = {}
        self._scopes: dict[tuple[uuid.UUID, datetime | None], Scope] = {}

    # ----------------------------------------------------------- разрешения

    def permissions(self, actor: Actor, at: datetime | None = None) -> set[str]:
        key = (actor.user_id, at)
        if key not in self._permissions:
            from humotech.rbac.models import Permission

            role_ids = set(
                self._active_scopes(actor, at).values_list("role_id", flat=True)
            )
            self._permissions[key] = (
                set(
                    Permission.objects.filter(role_links__role_id__in=role_ids)
                    .values_list("code", flat=True)
                    .distinct()
                )
                if role_ids
                else set()
            )
        return self._permissions[key]

    def require(self, actor: Actor, permission: str) -> None:
        if permission not in self.permissions(actor):
            raise PermissionDenied(
                f"Нужно разрешение {permission}", details={"permission": permission}
            )

    def has(self, actor: Actor, permission: str) -> bool:
        return permission in self.permissions(actor)

    # --------------------------------------------------------------- область

    def _active_scopes(self, actor: Actor, at: datetime | None = None):
        from humotech.accounts.models import UserRoleScope

        moment = at or timezone.now()
        return UserRoleScope.objects.filter(
            user_id=actor.user_id, valid_from__lte=moment
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=moment))

    def scope(self, actor: Actor, at: datetime | None = None) -> Scope:
        key = (actor.user_id, at)
        if key in self._scopes:
            return self._scopes[key]

        from humotech.offices.models import Office

        offices: set[uuid.UUID] = set()
        regions: set[uuid.UUID] = set()
        all_offices = False

        for region_id, office_id in self._active_scopes(actor, at).values_list(
            "region_id", "office_id"
        ):
            if region_id is None and office_id is None:
                all_offices = True
            if office_id is not None:
                offices.add(office_id)
            if region_id is not None:
                regions.add(region_id)

        if regions and not all_offices:
            # регион раскрывается в свои офисы: право на регион означает
            # право на каждый офис внутри него
            offices.update(
                Office.objects.filter(region_id__in=regions).values_list(
                    "id", flat=True
                )
            )

        scope = Scope(
            all_offices=all_offices,
            office_ids=frozenset(offices),
            region_ids=frozenset(regions),
        )
        self._scopes[key] = scope
        return scope

    def invalidate(self) -> None:
        """Сбросить кэш после выдачи или отзыва роли в той же транзакции."""
        self._permissions.clear()
        self._scopes.clear()

    # ------------------------------------------------------- доступ к объекту

    def require_office(self, actor: Actor, office_id: uuid.UUID):
        """Офис существует, принадлежит организации актора и виден ему."""
        from humotech.offices.models import Office

        office = Office.objects.filter(
            id=office_id, organization_id=actor.organization_id
        ).first()
        # чужая организация и несуществующий офис отвечают одинаково:
        # иначе перебором id можно узнать, что есть у соседей
        if office is None:
            raise NotFound("Офис не найден")

        scope = self.scope(actor)
        if not scope.all_offices and office_id not in scope.office_ids:
            raise PermissionDenied("Офис вне вашей области видимости")
        return office

    def require_region(self, actor: Actor, region_id: uuid.UUID):
        from humotech.offices.models import Office
        from humotech.regions.models import Region

        region = Region.objects.filter(
            id=region_id, organization_id=actor.organization_id
        ).first()
        if region is None:
            raise NotFound("Регион не найден")

        scope = self.scope(actor)
        if scope.all_offices or region_id in scope.region_ids:
            return region
        # регион виден и тогда, когда пользователю выдан офис внутри него
        visible_here = (
            Office.objects.filter(
                region_id=region_id, id__in=scope.office_ids
            ).exists()
            if scope.office_ids
            else False
        )
        if not visible_here:
            raise PermissionDenied("Регион вне вашей области видимости")
        return region

    def require_schedule(self, actor: Actor, schedule_id: uuid.UUID):
        """График принадлежит организации актора.

        У графика нет офиса: он общий для организации. Поэтому территориальная
        область здесь не применяется — только принадлежность организации.
        """
        from humotech.schedules.models import WorkSchedule

        schedule = WorkSchedule.objects.filter(
            id=schedule_id, organization_id=actor.organization_id
        ).first()
        if schedule is None:
            # чужой график отвечает так же, как несуществующий: иначе перебором
            # id можно пересчитать графики соседней организации
            raise NotFound("График работы не найден")
        return schedule

    # -------------------------------------------------- условия для списков

    def visible_office_ids(self, actor: Actor) -> set[uuid.UUID] | None:
        """Множество доступных офисов; None — вся организация."""
        scope = self.scope(actor)
        return None if scope.all_offices else set(scope.office_ids)

    def office_filter(self, actor: Actor) -> Q | None:
        """Условие для списка офисов.

        Возвращает None, если ограничивать не нужно (доступ ко всей
        организации). Пустая область даёт заведомо ложное условие — это
        не то же самое, что None.
        """
        scope = self.scope(actor)
        if scope.all_offices:
            return None
        return Q(id__in=scope.office_ids)  # пустое множество = ничего не видит

    def region_filter(self, actor: Actor) -> Q | None:
        """Условие для списка регионов.

        Регион виден, если он выдан напрямую ЛИБО если пользователю выдан
        хотя бы один офис внутри него: администратор офиса обязан видеть
        регион, к которому его офис относится, иначе карточка офиса
        ссылается на невидимую запись.
        """
        from humotech.offices.models import Office

        scope = self.scope(actor)
        if scope.all_offices:
            return None
        regions_of_visible_offices = Office.objects.filter(
            id__in=scope.office_ids
        ).values_list("region_id", flat=True)
        return Q(id__in=scope.region_ids) | Q(id__in=regions_of_visible_offices)


class AuditTrail:
    """Запись действий в существующий `audit_logs`.

    Секреты сюда не попадают: фильтр работает на составе полей, а не на
    добросовестности вызывающего — сервис может передать лишнее по ошибке.
    """

    # Никогда не пишем в журнал, даже если поле попало в diff по ошибке.
    FORBIDDEN_FIELDS = frozenset(
        {
            "password", "password_hash", "token", "access_token", "api_key",
            "secret", "static_token_hash", "device_identifier_hash",
            "display_identifier_hash", "qr_nonce_hash",
        }
    )

    @classmethod
    def sanitize(cls, values: dict | None) -> dict | None:
        if not values:
            return None
        cleaned = {
            key: value
            for key, value in values.items()
            if key.lower() not in cls.FORBIDDEN_FIELDS
        }
        return cleaned or None

    def record(
        self,
        actor: Actor,
        *,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        before: dict | None = None,
        after: dict | None = None,
    ):
        from humotech.audit.models import AuditLog

        return AuditLog.objects.create(
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=self.sanitize(before),
            new_values=self.sanitize(after),
            occurred_at=timezone.now(),
        )


def snapshot(obj, fields: tuple[str, ...]) -> dict:
    """Значения полей объекта в виде, пригодном для журнала."""
    result: dict = {}
    for name in fields:
        value = getattr(obj, name, None)
        if value is None:
            result[name] = None
        elif isinstance(value, (str, int, float, bool)):
            result[name] = value
        else:
            result[name] = str(value)
    return result
