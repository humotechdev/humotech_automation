"""Учётные записи CRM, роли и области видимости.

Второй системы авторизации здесь нет: используются существующие `User`,
`Role`, `RolePermission` и `UserRoleScope`, а проверки идут через тот же
`AccessControl`, что и везде. Параллельная система прав — самый дорогой
способ получить два разных ответа на вопрос «можно ли».

Пять решений, каждое против конкретной ошибки.

**Повысить себя нельзя, и проверок для этого две, а не одна.**
`AccessControl.permissions()` объединяет права по ВСЕМ действующим
областям и территорию не учитывает вовсе: администратор одного офиса
с ролью SUPER_ADMIN получит оттуда полный список кодов. Поэтому одной
проверки «выдаёшь только то, что имеешь сам» мало — она разрешила бы
такому администратору выдать SUPER_ADMIN на всю организацию. Проверяются
обе стороны: набор прав роли обязан входить в набор выдающего, И
территория назначения обязана лежать внутри его области.

**Роль отзывается сроком, а не удалением.** `DELETE` стёр бы историю
назначений, а именно она отвечает на вопрос «кто и когда дал человеку
этот доступ». Отзыв ставит `valid_to` в прошлое — строго в прошлое:
`_active_scopes` сравнивает через `valid_to >= now`, и роль, отозванная
«ровно сейчас», осталась бы действующей ещё на это мгновение.

**Повторная выдача проверяется кодом, потому что база её не ловит.**
`uq_user_role_scopes_grant` включает `valid_from`, а он приходит из
`TransactionNow()` — в новой транзакции значение другое, и вторая
такая же строка вставится без возражений.

**Последний SUPER_ADMIN защищён блокировкой строки роли, а не подсчётом.**
`SELECT ... FOR UPDATE` нельзя применить к `count()`, поэтому точкой
сериализации служит сама строка роли: два одновременных отзыва встанут
в очередь на ней, и второй увидит уже уменьшенное число.

**Кэш прав сбрасывается после каждой записи.** `AccessControl` помнит
права и области в пределах экземпляра; без сброса проверка, идущая
после выдачи, ответила бы по состоянию до неё.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from humotech.accounts.models import User, UserRoleScope
from humotech.core.enums import USER_STATUSES
from humotech.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import validate_email
from humotech.rbac.models import Permission, Role

USER_FIELDS = ("email", "status", "mfa_enabled")

#: Код роли, которая не должна исчезнуть из организации.
SUPER_ADMIN = "SUPER_ADMIN"

#: На сколько отодвигается `valid_to` при отзыве. Секунда в прошлое,
#: потому что `_active_scopes` сравнивает через `>=`: ровно `now()`
#: оставил бы роль действующей ещё на это мгновение.
REVOKE_BACKDATE = timedelta(seconds=1)


class UserAdminService(BaseService):
    """Учётные записи CRM. Требует `users.manage`.

    Пароли здесь не задаются и не меняются — ни при создании, ни потом.
    Новая запись заводится без пригодного пароля и в статусе INACTIVE:
    вход открывается, когда человек установит пароль сам. Учётная
    запись, чей пароль знает кто-то ещё, не отвечает на вопрос «кто это
    сделал», а именно на него отвечает весь журнал.
    """

    def list(
        self,
        actor: Actor,
        *,
        search: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "users.manage")
        queryset = User.objects.filter(
            organization_id=actor.organization_id
        ).select_related("employee")
        if status:
            queryset = queryset.filter(status=status)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(email__icontains=pattern)
        return paginate(queryset, limit=limit, cursor=cursor)

    def get(self, actor: Actor, user_id: uuid.UUID) -> User:
        self.access.require(actor, "users.manage")
        return self._require(actor, user_id)

    def create(
        self,
        actor: Actor,
        *,
        email: str,
        employee_id: uuid.UUID | None = None,
    ) -> User:
        self.access.require(actor, "users.manage")
        address = validate_email(email, field="email")
        if address is None:
            raise ValidationFailed(
                "Адрес электронной почты обязателен", details={"field": "email"}
            )
        if User.objects.filter(
            organization_id=actor.organization_id, email__iexact=address
        ).exists():
            raise Conflict(
                "Учётная запись с таким адресом уже есть",
                details={"email": address},
            )

        with self.atomic():
            user = User(
                organization_id=actor.organization_id,
                email=address,
                employee_id=employee_id,
                # INACTIVE, а не ACTIVE: запись без пароля, которую уже
                # можно использовать, — это приглашение к перебору.
                status="INACTIVE",
            )
            user.set_unusable_password()
            user.save()
            self.audit.record(
                actor,
                action="user.create",
                entity_type="users",
                entity_id=user.id,
                before=None,
                after=snapshot(user, USER_FIELDS),
            )
        return user

    def set_status(
        self, actor: Actor, user_id: uuid.UUID, *, status: str
    ) -> User:
        self.access.require(actor, "users.manage")
        if status not in USER_STATUSES:
            raise ValidationFailed(
                "Неизвестный статус учётной записи",
                details={"status": status, "allowed": list(USER_STATUSES)},
            )
        user = self._require(actor, user_id)
        if user.status == status:
            return user

        before = snapshot(user, USER_FIELDS)
        with self.atomic():
            if status != "ACTIVE":
                # Блокировка строки роли — точка сериализации: без неё два
                # одновременных отключения оба увидят «админов ещё двое».
                _lock_super_admin_role(actor)
                _refuse_if_last_super_admin(actor, without_user=user.id)
            user.status = status
            user.save(update_fields=["status", "updated_at"])
            self.audit.record(
                actor,
                action="user.status",
                entity_type="users",
                entity_id=user.id,
                before=before,
                after=snapshot(user, USER_FIELDS),
            )
        self.access.invalidate()
        return user

    def _require(self, actor: Actor, user_id: uuid.UUID) -> User:
        user = (
            User.objects.select_related("employee")
            .filter(id=user_id, organization_id=actor.organization_id)
            .first()
        )
        if user is None:
            # Чужая организация отвечает как отсутствие записи.
            raise NotFound("Учётная запись не найдена")
        return user


class RoleAdminService(BaseService):
    """Роли и назначения. Требует `roles.manage`."""

    # ------------------------------------------------------------------- роли

    def roles(self, actor: Actor) -> list[dict]:
        """Роли организации плюс системные, с их разрешениями.

        Рядом с каждой ролью — признак `grantable`: может ли ЭТОТ
        пользователь её выдать. Считает сервер, а не интерфейс; кнопка,
        скрытая на клиенте, защитой не является.
        """
        self.access.require(actor, "roles.manage")
        mine = self.access.permissions(actor)

        rows = Role.objects.filter(
            Q(organization_id=actor.organization_id) | Q(organization__isnull=True)
        ).order_by("code")

        codes = _permissions_by_role([row.id for row in rows])
        result = []
        for row in rows:
            granted = codes.get(row.id, set())
            missing = sorted(granted - mine)
            result.append(
                {
                    "id": row.id,
                    "code": row.code,
                    "name": row.name,
                    "is_system": row.organization_id is None,
                    "permissions": sorted(granted),
                    "grantable": not missing,
                    # Прямо называется, чего не хватает: «нельзя» без
                    # причины выглядит как поломка.
                    "missing_permissions": missing,
                }
            )
        return result

    # -------------------------------------------------------------- назначения

    def assignments(
        self, actor: Actor, user_id: uuid.UUID, *, include_expired: bool = False
    ) -> list[UserRoleScope]:
        """Действующие назначения или вся история.

        История — не украшение: она отвечает на вопрос «кто и когда дал
        человеку этот доступ», и ради неё отзыв не удаляет строку.
        """
        self.access.require(actor, "roles.manage")
        self._require_user(actor, user_id)

        queryset = UserRoleScope.objects.filter(
            organization_id=actor.organization_id, user_id=user_id
        ).select_related("role", "region", "office")
        if not include_expired:
            moment = timezone.now()
            queryset = queryset.filter(valid_from__lte=moment).filter(
                Q(valid_to__isnull=True) | Q(valid_to__gte=moment)
            )
        return list(queryset.order_by("-valid_from", "-id"))

    def assign(
        self,
        actor: Actor,
        *,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        region_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> UserRoleScope:
        self.access.require(actor, "roles.manage")
        user = self._require_user(actor, user_id)
        role = self._require_role(actor, role_id)

        if region_id and office_id:
            raise ValidationFailed(
                "Область — это либо регион, либо офис, но не оба",
                details={"region_id": str(region_id), "office_id": str(office_id)},
            )
        self._require_grantable(actor, role)
        self._require_within_own_scope(actor, region_id=region_id, office_id=office_id)
        self._require_period(valid_from, valid_to)
        self._refuse_duplicate(
            user_id=user.id, role_id=role.id,
            region_id=region_id, office_id=office_id,
        )

        with self.atomic():
            grant = UserRoleScope.objects.create(
                organization_id=actor.organization_id,
                user=user,
                role=role,
                region_id=region_id,
                office_id=office_id,
                **({"valid_from": valid_from} if valid_from else {}),
                valid_to=valid_to,
            )
            self.audit.record(
                actor,
                action="user_role_scope.assign",
                entity_type="user_role_scopes",
                entity_id=grant.id,
                before=None,
                after=_grant_snapshot(grant),
            )
        self.access.invalidate()
        return grant

    def revoke(self, actor: Actor, grant_id: uuid.UUID) -> UserRoleScope:
        """Отозвать назначение. Строка остаётся, срок закрывается."""
        self.access.require(actor, "roles.manage")
        grant = self._require_grant(actor, grant_id)
        if grant.valid_to and grant.valid_to <= timezone.now():
            raise Conflict(
                "Это назначение уже отозвано",
                details={"valid_to": grant.valid_to.isoformat()},
            )

        before = _grant_snapshot(grant)
        with self.atomic():
            _lock_super_admin_role(actor)
            if _is_super_admin(grant):
                _refuse_if_last_super_admin(actor, without_grant=grant.id)
            # Секунда в прошлое, а не `now()`: сравнение в `_active_scopes`
            # нестрогое, и роль осталась бы действующей ещё мгновение.
            #
            # Но не раньше начала срока: база держит `valid_to >= valid_from`,
            # и роль, выданную секунду назад, иначе отозвать было бы нельзя
            # вовсе — отказ приходил бы из констрейнта, а не по существу.
            # Совпадение границ означает «действовала одно мгновение»,
            # и уже в следующее `_active_scopes` её не видит.
            grant.valid_to = max(timezone.now() - REVOKE_BACKDATE, grant.valid_from)
            grant.save(update_fields=["valid_to"])
            self.audit.record(
                actor,
                action="user_role_scope.revoke",
                entity_type="user_role_scopes",
                entity_id=grant.id,
                before=before,
                after=_grant_snapshot(grant),
            )
        self.access.invalidate()
        return grant

    def set_validity(
        self,
        actor: Actor,
        grant_id: uuid.UUID,
        *,
        valid_to: datetime | None,
    ) -> UserRoleScope:
        """Сдвинуть срок назначения — например, продлить временный доступ."""
        self.access.require(actor, "roles.manage")
        grant = self._require_grant(actor, grant_id)
        self._require_period(grant.valid_from, valid_to)

        before = _grant_snapshot(grant)
        with self.atomic():
            _lock_super_admin_role(actor)
            closes = valid_to is not None and valid_to <= timezone.now()
            if closes and _is_super_admin(grant):
                _refuse_if_last_super_admin(actor, without_grant=grant.id)
            grant.valid_to = valid_to
            grant.save(update_fields=["valid_to"])
            self.audit.record(
                actor,
                action="user_role_scope.validity",
                entity_type="user_role_scopes",
                entity_id=grant.id,
                before=before,
                after=_grant_snapshot(grant),
            )
        self.access.invalidate()
        return grant

    # ---------------------------------------------------------------- частное

    def _require_user(self, actor: Actor, user_id: uuid.UUID) -> User:
        user = User.objects.filter(
            id=user_id, organization_id=actor.organization_id
        ).first()
        if user is None:
            raise NotFound("Учётная запись не найдена")
        return user

    def _require_role(self, actor: Actor, role_id: uuid.UUID) -> Role:
        role = Role.objects.filter(id=role_id).first()
        if role is None or (
            role.organization_id is not None
            and role.organization_id != actor.organization_id
        ):
            # Чужая роль отвечает как отсутствие: существование ролей
            # другой организации — тоже сведение о ней.
            raise NotFound("Роль не найдена")
        return role

    def _require_grant(self, actor: Actor, grant_id: uuid.UUID) -> UserRoleScope:
        grant = (
            UserRoleScope.objects.select_related("role", "user")
            .filter(id=grant_id, organization_id=actor.organization_id)
            .first()
        )
        if grant is None:
            raise NotFound("Назначение не найдено")
        return grant

    def _require_grantable(self, actor: Actor, role: Role) -> None:
        """Выдать можно только то, что имеешь сам.

        Первая из двух проверок. Одной её мало: набор прав в
        `permissions()` собран по всем областям сразу, и территорию
        не ограничивает — этим занимается вторая.
        """
        granted = _permissions_by_role([role.id]).get(role.id, set())
        missing = sorted(granted - self.access.permissions(actor))
        if missing:
            raise PermissionDenied(
                "Нельзя выдать роль с правами, которых нет у вас самих",
                details={"role": role.code, "missing_permissions": missing},
            )

    def _require_within_own_scope(
        self,
        actor: Actor,
        *,
        region_id: uuid.UUID | None,
        office_id: uuid.UUID | None,
    ) -> None:
        """Вторая проверка: территория назначения внутри своей области.

        Назначение без региона и офиса означает «вся организация», и
        выдать его может только тот, чья область — вся организация.
        """
        scope = self.access.scope(actor)
        if region_id is None and office_id is None:
            if not scope.all_offices:
                raise PermissionDenied(
                    "Доступ на всю организацию выдаёт только тот, "
                    "чья область — вся организация",
                    details={"region_id": None, "office_id": None},
                )
            return
        if office_id is not None:
            self.access.require_office(actor, office_id)
            return
        self.access.require_region(actor, region_id)

    @staticmethod
    def _require_period(
        valid_from: datetime | None, valid_to: datetime | None
    ) -> None:
        if valid_from and valid_to and valid_to <= valid_from:
            raise ValidationFailed(
                "Конец срока не может быть раньше начала",
                details={
                    "valid_from": valid_from.isoformat(),
                    "valid_to": valid_to.isoformat(),
                },
            )

    @staticmethod
    def _refuse_duplicate(
        *,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        region_id: uuid.UUID | None,
        office_id: uuid.UUID | None,
    ) -> None:
        """Та же роль на ту же территорию второй раз — отказ.

        База это не ловит: `uq_user_role_scopes_grant` включает
        `valid_from`, который приходит из `TransactionNow()`, и в новой
        транзакции значение другое.
        """
        moment = timezone.now()
        exists = (
            UserRoleScope.objects.filter(
                user_id=user_id,
                role_id=role_id,
                region_id=region_id,
                office_id=office_id,
                valid_from__lte=moment,
            )
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=moment))
            .exists()
        )
        if exists:
            raise Conflict(
                "Такая роль на эту область у пользователя уже есть",
                details={"role_id": str(role_id)},
            )


# --- общие вспомогательные ---------------------------------------------------


def _permissions_by_role(role_ids: list[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
    """Коды разрешений по ролям — одним запросом на все роли."""
    result: dict[uuid.UUID, set[str]] = {}
    rows = Permission.objects.filter(role_links__role_id__in=role_ids).values_list(
        "role_links__role_id", "code"
    )
    for role_id, code in rows:
        result.setdefault(role_id, set()).add(code)
    return result


def _grant_snapshot(grant: UserRoleScope) -> dict:
    return {
        "user_id": str(grant.user_id),
        "role_id": str(grant.role_id),
        "region_id": str(grant.region_id) if grant.region_id else None,
        "office_id": str(grant.office_id) if grant.office_id else None,
        "valid_from": grant.valid_from.isoformat() if grant.valid_from else None,
        "valid_to": grant.valid_to.isoformat() if grant.valid_to else None,
    }


def _is_super_admin(grant: UserRoleScope) -> bool:
    return grant.role.code == SUPER_ADMIN


def _lock_super_admin_role(actor: Actor) -> None:
    """Заблокировать строку роли SUPER_ADMIN до конца транзакции.

    Точкой сериализации служит именно строка роли: `SELECT ... FOR
    UPDATE` нельзя применить к `count()`, а без общей точки два
    одновременных отзыва оба увидели бы «админов ещё двое».
    """
    list(
        Role.objects.filter(
            Q(organization_id=actor.organization_id) | Q(organization__isnull=True),
            code=SUPER_ADMIN,
        ).select_for_update()
    )


def _refuse_if_last_super_admin(
    actor: Actor,
    *,
    without_user: uuid.UUID | None = None,
    without_grant: uuid.UUID | None = None,
) -> None:
    """Не дать организации остаться без суперадминистратора.

    Исключается ровно то, что снимают, и не больше.

    При отключении учётной записи выпадает весь пользователь: он перестаёт
    быть админом целиком. При отзыве одного назначения выпадает только
    оно — у человека может быть вторая такая же роль на другой регион, и
    считать его выбывшим было бы неверным отказом.

    Считаются АКТИВНЫЕ записи: заблокированный админ доступа не даёт, и
    записывать его в живые значило бы разрешить закрыть организацию.
    """
    if without_user is None and without_grant is None:
        raise ValueError("нечего исключать из подсчёта")

    moment = timezone.now()
    queryset = UserRoleScope.objects.filter(
        organization_id=actor.organization_id,
        role__code=SUPER_ADMIN,
        user__status="ACTIVE",
        valid_from__lte=moment,
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=moment))

    if without_user is not None:
        queryset = queryset.exclude(user_id=without_user)
    if without_grant is not None:
        queryset = queryset.exclude(id=without_grant)

    if not queryset.exists():
        raise Conflict(
            "Это последний суперадминистратор организации. "
            "Назначьте другого, прежде чем снимать этого.",
            details={"role": SUPER_ADMIN},
        )


__all__ = ["REVOKE_BACKDATE", "RoleAdminService", "UserAdminService"]
