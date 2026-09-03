"""Кто действует и что ему можно.

Один слой на весь проект: разрешение отвечает на вопрос «что можно делать»,
область из `user_role_scopes` — «с чьими данными». Параллельной системы ролей
здесь нет и быть не должно.

Три вещи, которые легко перепутать и которые проверяются отдельно:

  * `visible_office_ids` возвращает **None** для «вся организация» и **пустое
    множество** для «не видит ничего». Проверка на истинность (`if visible:`)
    склеивает эти случаи и молча выдаёт доступ ко всей организации тому,
    у кого прав нет вовсе;
  * разрешения и область — разные вещи. У регионального HR может быть
    `employees.manage`, но чужой офис ему всё равно закрыт;
  * область сотрудника (`ai_assistant/services/scoping.py`) и область
    пользователя CRM — разные резолверы. Здесь речь только о пользователях.

`Actor` продублирован в `ai_assistant/use_cases/crm.py`: AI-модуль по условию
задачи менять нельзя, поэтому он пока живёт со своей копией этого же dataclass.
Объединение — отдельная работа, когда модуль снова будет открыт для правок.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.errors import NotFound, PermissionDenied
from src.core.permissions.scopes import active_scopes, permission_codes
from src.modules.audit.models import AuditLog
from src.modules.offices.models import Office


@dataclass(frozen=True)
class Actor:
    """Пользователь CRM, от имени которого выполняется операция."""

    user_id: uuid.UUID
    organization_id: uuid.UUID


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

    def __init__(self, session: Session) -> None:
        self.session = session
        # Ключ кэша включает момент времени: роли и области выданы на период,
        # и один и тот же пользователь «на вчера» и «на сегодня» — разные ответы.
        # Кэш по одному user_id вернул бы вчерашний ответ на сегодняшний вопрос.
        self._permissions: dict[tuple[uuid.UUID, datetime | None], set[str]] = {}
        self._scopes: dict[tuple[uuid.UUID, datetime | None], Scope] = {}

    # ----------------------------------------------------------- разрешения

    def permissions(self, actor: Actor, at: datetime | None = None) -> set[str]:
        key = (actor.user_id, at)
        if key not in self._permissions:
            self._permissions[key] = permission_codes(
                self.session, user_id=actor.user_id, at=at
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

    def scope(self, actor: Actor, at: datetime | None = None) -> Scope:
        key = (actor.user_id, at)
        if key in self._scopes:
            return self._scopes[key]

        at = at or datetime.now(tz=timezone.utc)
        offices: set[uuid.UUID] = set()
        regions: set[uuid.UUID] = set()
        all_offices = False

        for row in active_scopes(self.session, user_id=actor.user_id, at=at):
            if row.region_id is None and row.office_id is None:
                all_offices = True
            if row.office_id is not None:
                offices.add(row.office_id)
            if row.region_id is not None:
                regions.add(row.region_id)

        if regions and not all_offices:
            # регион раскрывается в свои офисы: право на регион означает
            # право на каждый офис внутри него
            offices.update(
                self.session.scalars(
                    select(Office.id).where(Office.region_id.in_(regions))
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
        """Сбросить кэш после выдачи или отзыва роли в той же сессии."""
        self._permissions.clear()
        self._scopes.clear()

    def require_office(self, actor: Actor, office_id: uuid.UUID) -> Office:
        """Офис существует, принадлежит организации актора и виден ему."""
        office = self.session.get(Office, office_id)
        # чужая организация и несуществующий офис отвечают одинаково:
        # иначе перебором id можно узнать, что есть у соседей
        if office is None or office.organization_id != actor.organization_id:
            raise NotFound("Офис не найден")

        scope = self.scope(actor)
        if not scope.all_offices and office_id not in scope.office_ids:
            raise PermissionDenied("Офис вне вашей области видимости")
        return office

    def require_region(self, actor: Actor, region_id: uuid.UUID):
        from src.modules.regions.models import Region

        region = self.session.get(Region, region_id)
        if region is None or region.organization_id != actor.organization_id:
            raise NotFound("Регион не найден")

        scope = self.scope(actor)
        if scope.all_offices or region_id in scope.region_ids:
            return region
        # регион виден и тогда, когда пользователю выдан офис внутри него
        visible_here = self.session.scalar(
            select(Office.id).where(
                Office.region_id == region_id, Office.id.in_(scope.office_ids)
            ).limit(1)
        ) if scope.office_ids else None
        if visible_here is None:
            raise PermissionDenied("Регион вне вашей области видимости")
        return region

    def require_schedule(self, actor: Actor, schedule_id: uuid.UUID):
        """График принадлежит организации актора.

        У графика нет офиса: он общий для организации. Поэтому территориальная
        область здесь не применяется — только принадлежность организации.
        """
        from src.modules.schedules.models import WorkSchedule

        schedule = self.session.get(WorkSchedule, schedule_id)
        if schedule is None or schedule.organization_id != actor.organization_id:
            # чужой график отвечает так же, как несуществующий: иначе перебором
            # id можно пересчитать графики соседней организации
            raise NotFound("График работы не найден")
        return schedule

    def office_filter(self, actor: Actor):
        """Условие для списков: какие офисы пользователь имеет право видеть.

        Возвращает None, если ограничивать не нужно (доступ ко всей
        организации). Пустая область даёт заведомо ложное условие — это
        не то же самое, что None.
        """
        scope = self.scope(actor)
        if scope.all_offices:
            return None
        if not scope.office_ids:
            return Office.id.in_([])  # видит ничего, а не всё
        return Office.id.in_(scope.office_ids)

    def visible_office_ids(self, actor: Actor) -> set[uuid.UUID] | None:
        """Множество доступных офисов; None — вся организация."""
        scope = self.scope(actor)
        return None if scope.all_offices else set(scope.office_ids)

    def region_filter(self, actor: Actor):
        """Условие для списка регионов.

        Регион виден, если он выдан напрямую ЛИБО если пользователю выдан
        хотя бы один офис внутри него: администратор офиса обязан видеть
        регион, к которому его офис относится, иначе карточка офиса
        ссылается на невидимую запись.
        """
        from src.modules.regions.models import Region

        scope = self.scope(actor)
        if scope.all_offices:
            return None
        if not scope.office_ids and not scope.region_ids:
            return Region.id.in_([])
        regions_of_visible_offices = select(Office.region_id).where(
            Office.id.in_(scope.office_ids)
        )
        return Region.id.in_(scope.region_ids) | Region.id.in_(
            regions_of_visible_offices
        )


class AuditTrail:
    """Запись действий в существующий `audit_logs`.

    Секреты сюда не попадают: писать разрешено только те поля, которые
    сервис передал явно, а сервисы передают деловые значения, не пароли.
    """

    # Никогда не пишем в журнал, даже если поле попало в diff по ошибке.
    FORBIDDEN_FIELDS = frozenset(
        {
            "password", "password_hash", "token", "access_token", "api_key",
            "secret", "static_token_hash", "device_identifier_hash",
            "display_identifier_hash", "qr_nonce_hash",
        }
    )

    def __init__(self, session: Session) -> None:
        self.session = session

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
    ) -> AuditLog:
        entry = AuditLog(
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=self.sanitize(before),
            new_values=self.sanitize(after),
            occurred_at=datetime.now(tz=timezone.utc),
        )
        self.session.add(entry)
        self.session.flush()
        return entry


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
