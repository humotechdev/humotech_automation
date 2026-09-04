"""Пользователи, роли и области: защита от повышения себя и от потери доступа.

Этот набор почти целиком про то, что НЕ должно получиться. Управление
доступом — единственное место, где ошибка не портит данные, а отдаёт их;
поэтому здесь проверяются не сценарии, а границы.

Три ловушки, каждая проверяется отдельно и каждая неочевидна:

**Одной проверки прав мало.** `AccessControl.permissions()` объединяет
права по всем областям сразу и территорию не учитывает. Проверка «выдаёшь
только то, что имеешь» пропустила бы администратора одного офиса, который
выдаёт себе SUPER_ADMIN на всю организацию.

**Повторную выдачу база не ловит.** `uq_user_role_scopes_grant` включает
`valid_from` из `TransactionNow()`, и в новой транзакции значение другое.

**Отзыв «ровно сейчас» оставляет роль действующей.** `_active_scopes`
сравнивает через `>=`, поэтому `valid_to = now()` — не отзыв.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from humotech.accounts.models import User, UserRoleScope
from humotech.accounts.rbac_service import RoleAdminService, UserAdminService
from humotech.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from humotech.core.rbac import AccessControl, Actor
from humotech.rbac.models import Permission, Role, RolePermission

API = "/api/v1"


def make_role(organization, code: str, permissions: tuple[str, ...]) -> Role:
    """Роль организации с набором разрешений из каталога."""
    role = Role.objects.create(
        organization=organization, code=code, name=code.title()
    )
    for name in permissions:
        permission, _ = Permission.objects.get_or_create(
            code=name, defaults={"name": name, "description": name}
        )
        RolePermission.objects.create(role=role, permission=permission)
    return role


@pytest.fixture()
def users() -> UserAdminService:
    return UserAdminService()


@pytest.fixture()
def roles() -> RoleAdminService:
    return RoleAdminService()


@pytest.fixture()
def admin_actor(make_actor, organization):
    """Полномочный администратор: область — вся организация."""
    return make_actor(
        organization,
        permissions=(
            "users.manage", "roles.manage", "employees.read",
            "employees.manage", "attendance.read",
        ),
    )


@pytest.fixture()
def target(make_user, organization) -> User:
    return make_user(organization, permissions=())


# --- права -------------------------------------------------------------------


@pytest.mark.django_db
class TestAccess:
    def test_listing_users_requires_users_manage(self, users, nobody_actor):
        with pytest.raises(PermissionDenied):
            users.list(nobody_actor)

    def test_hr_admin_cannot_manage_roles(self, roles, make_actor, organization):
        """Кадровый администратор ролями не управляет.

        Это решение каталога, а не упущение: право исправить отчество и
        право открыть человеку доступ — разные решения.
        """
        hr = make_actor(
            organization,
            permissions=(
                "employees.manage", "attendance.read", "absences.approve",
                "audit.read",
            ),
        )
        with pytest.raises(PermissionDenied):
            roles.roles(hr)

    def test_tech_admin_can_manage_roles(self, roles, make_actor, organization):
        tech = make_actor(organization, permissions=("roles.manage",))
        assert isinstance(roles.roles(tech), list)

    def test_foreign_user_is_invisible(
        self, users, admin_actor, other_organization, make_user
    ):
        stranger = make_user(other_organization, permissions=())
        with pytest.raises(NotFound):
            users.get(admin_actor, stranger.id)


# --- повышение прав ----------------------------------------------------------


@pytest.mark.django_db
class TestEscalation:
    def test_cannot_grant_permissions_one_does_not_hold(
        self, roles, admin_actor, organization, target
    ):
        powerful = make_role(
            organization, "POWERFUL", ("users.manage", "settings.manage")
        )
        with pytest.raises(PermissionDenied) as exc:
            roles.assign(
                admin_actor, user_id=target.id, role_id=powerful.id
            )
        assert "settings.manage" in exc.value.details["missing_permissions"]

    def test_office_admin_cannot_grant_organization_wide_access(
        self, roles, make_actor, organization, office, target
    ):
        """Главная ловушка: прав хватает, области — нет.

        Список прав собирается по всем областям сразу, поэтому проверка
        «выдаёшь только то, что имеешь» здесь проходит. Не проходит
        вторая: назначение без региона и офиса означает всю организацию.
        """
        local_role = make_role(organization, "LOCAL", ("roles.manage",))
        local = make_actor(
            organization, permissions=("roles.manage",), office=office
        )
        with pytest.raises(PermissionDenied) as exc:
            roles.assign(local, user_id=target.id, role_id=local_role.id)
        assert "вся организация" in str(exc.value)

    def test_office_admin_cannot_grant_outside_own_office(
        self, roles, make_actor, organization, office, other_office, target
    ):
        local_role = make_role(organization, "LOCAL", ("roles.manage",))
        local = make_actor(
            organization, permissions=("roles.manage",), office=office
        )
        with pytest.raises(PermissionDenied):
            roles.assign(
                local, user_id=target.id, role_id=local_role.id,
                office_id=other_office.id,
            )

    def test_office_admin_may_grant_within_own_office(
        self, roles, make_actor, organization, office, target
    ):
        local_role = make_role(organization, "LOCAL", ("roles.manage",))
        local = make_actor(
            organization, permissions=("roles.manage",), office=office
        )
        grant = roles.assign(
            local, user_id=target.id, role_id=local_role.id, office_id=office.id
        )
        assert grant.office_id == office.id

    def test_region_scope_is_checked_too(
        self, roles, make_actor, organization, region, other_region, target
    ):
        local_role = make_role(organization, "LOCAL", ("roles.manage",))
        regional = make_actor(
            organization, permissions=("roles.manage",), region=region
        )
        assert roles.assign(
            regional, user_id=target.id, role_id=local_role.id,
            region_id=region.id,
        ).region_id == region.id

        with pytest.raises(PermissionDenied):
            roles.assign(
                regional, user_id=target.id, role_id=local_role.id,
                region_id=other_region.id,
            )

    def test_role_list_says_which_roles_are_grantable(
        self, roles, admin_actor, organization
    ):
        make_role(organization, "POWERFUL", ("settings.manage",))
        make_role(organization, "MODEST", ("employees.read",))

        by_code = {row["code"]: row for row in roles.roles(admin_actor)}
        assert by_code["MODEST"]["grantable"] is True
        assert by_code["POWERFUL"]["grantable"] is False
        assert by_code["POWERFUL"]["missing_permissions"] == ["settings.manage"]

    def test_foreign_role_is_not_found(
        self, roles, admin_actor, other_organization, target
    ):
        alien = make_role(other_organization, "ALIEN", ("employees.read",))
        with pytest.raises(NotFound):
            roles.assign(admin_actor, user_id=target.id, role_id=alien.id)


# --- выдача и отзыв ----------------------------------------------------------


@pytest.mark.django_db
class TestGrantAndRevoke:
    def test_same_role_twice_is_refused(
        self, roles, admin_actor, organization, target
    ):
        """База это не ловит: в `uq_...grant` входит `valid_from`."""
        role = make_role(organization, "READER", ("employees.read",))
        roles.assign(admin_actor, user_id=target.id, role_id=role.id)
        with pytest.raises(Conflict):
            roles.assign(admin_actor, user_id=target.id, role_id=role.id)

    def test_same_role_on_a_different_office_is_allowed(
        self, roles, admin_actor, organization, office, other_office, target
    ):
        role = make_role(organization, "READER", ("employees.read",))
        roles.assign(
            admin_actor, user_id=target.id, role_id=role.id, office_id=office.id
        )
        second = roles.assign(
            admin_actor, user_id=target.id, role_id=role.id,
            office_id=other_office.id,
        )
        assert second.office_id == other_office.id

    def test_revoke_keeps_the_row(
        self, roles, admin_actor, organization, target
    ):
        role = make_role(organization, "READER", ("employees.read",))
        grant = roles.assign(admin_actor, user_id=target.id, role_id=role.id)
        roles.revoke(admin_actor, grant.id)

        # История назначений отвечает на вопрос «кто и когда дал доступ».
        assert UserRoleScope.objects.filter(id=grant.id).exists()

    def test_revoked_role_stops_working_immediately(
        self, roles, admin_actor, organization, target
    ):
        """`valid_to = now()` не отзыв: сравнение в области нестрогое.

        Роль, которая действует ещё мгновение после отзыва, — то, чего
        никто не замечает до разбора инцидента.
        """
        role = make_role(organization, "READER", ("employees.read",))
        grant = roles.assign(admin_actor, user_id=target.id, role_id=role.id)

        victim = Actor(user_id=target.id, organization_id=grant.organization_id)
        assert AccessControl().has(victim, "employees.read") is True

        roles.revoke(admin_actor, grant.id)
        assert AccessControl().has(victim, "employees.read") is False

    def test_revoking_twice_is_a_conflict(
        self, roles, admin_actor, organization, target
    ):
        role = make_role(organization, "READER", ("employees.read",))
        grant = roles.assign(admin_actor, user_id=target.id, role_id=role.id)
        roles.revoke(admin_actor, grant.id)
        with pytest.raises(Conflict):
            roles.revoke(admin_actor, grant.id)

    def test_validity_can_be_extended(
        self, roles, admin_actor, organization, target
    ):
        role = make_role(organization, "READER", ("employees.read",))
        until = timezone.now() + timedelta(days=30)
        grant = roles.assign(
            admin_actor, user_id=target.id, role_id=role.id, valid_to=until
        )
        longer = timezone.now() + timedelta(days=90)
        assert roles.set_validity(
            admin_actor, grant.id, valid_to=longer
        ).valid_to == longer

    def test_reversed_period_is_refused(
        self, roles, admin_actor, organization, target
    ):
        role = make_role(organization, "READER", ("employees.read",))
        now = timezone.now()
        with pytest.raises(ValidationFailed):
            roles.assign(
                admin_actor, user_id=target.id, role_id=role.id,
                valid_from=now, valid_to=now - timedelta(days=1),
            )

    def test_history_shows_revoked_grants(
        self, roles, admin_actor, organization, target
    ):
        role = make_role(organization, "READER", ("employees.read",))
        grant = roles.assign(admin_actor, user_id=target.id, role_id=role.id)
        roles.revoke(admin_actor, grant.id)

        # Список действующих не пуст: у любой учётной записи есть роль,
        # с которой её завели. Проверяется не пустота, а то, что
        # отозванного назначения в нём больше нет.
        active = [row.id for row in roles.assignments(admin_actor, target.id)]
        assert grant.id not in active

        history = [
            row.id
            for row in roles.assignments(
                admin_actor, target.id, include_expired=True
            )
        ]
        assert grant.id in history


# --- последний суперадминистратор --------------------------------------------


@pytest.mark.django_db
class TestLastSuperAdmin:
    @pytest.fixture()
    def super_role(self, organization) -> Role:
        return make_role(
            organization, "SUPER_ADMIN", ("users.manage", "roles.manage")
        )

    def test_last_super_admin_cannot_be_deactivated(
        self, users, roles, admin_actor, organization, super_role, make_user
    ):
        only = make_user(organization, permissions=())
        roles.assign(admin_actor, user_id=only.id, role_id=super_role.id)

        with pytest.raises(Conflict) as exc:
            users.set_status(admin_actor, only.id, status="INACTIVE")
        assert "суперадминистратор" in str(exc.value)

    def test_last_super_admin_role_cannot_be_revoked(
        self, roles, admin_actor, organization, super_role, make_user
    ):
        only = make_user(organization, permissions=())
        grant = roles.assign(
            admin_actor, user_id=only.id, role_id=super_role.id
        )
        with pytest.raises(Conflict):
            roles.revoke(admin_actor, grant.id)

    def test_closing_the_validity_is_refused_too(
        self, roles, admin_actor, organization, super_role, make_user
    ):
        """Срок в прошлом — тот же отзыв, только другим путём."""
        only = make_user(organization, permissions=())
        grant = roles.assign(
            admin_actor, user_id=only.id, role_id=super_role.id
        )
        # Закрывают срок сегодняшним моментом, а не вчерашним: база
        # держит `valid_to >= valid_from`, и дата раньше начала — это
        # другая ошибка, не та, которую проверяет этот тест.
        with pytest.raises(Conflict):
            roles.set_validity(admin_actor, grant.id, valid_to=timezone.now())

    def test_one_of_two_super_admins_may_go(
        self, users, roles, admin_actor, organization, super_role, make_user
    ):
        first = make_user(organization, permissions=())
        second = make_user(organization, permissions=())
        roles.assign(admin_actor, user_id=first.id, role_id=super_role.id)
        roles.assign(admin_actor, user_id=second.id, role_id=super_role.id)

        assert users.set_status(
            admin_actor, first.id, status="INACTIVE"
        ).status == "INACTIVE"

    def test_second_grant_of_the_same_admin_may_be_revoked(
        self, roles, admin_actor, organization, super_role, office, make_user
    ):
        """Исключается снимаемое назначение, а не весь человек.

        У одного администратора может быть две такие роли на разные
        территории; считать его выбывшим из-за снятия одной было бы
        неверным отказом.
        """
        only = make_user(organization, permissions=())
        roles.assign(admin_actor, user_id=only.id, role_id=super_role.id)
        second = roles.assign(
            admin_actor, user_id=only.id, role_id=super_role.id,
            office_id=office.id,
        )
        assert roles.revoke(admin_actor, second.id).valid_to is not None

    def test_inactive_super_admin_does_not_count_as_one(
        self, users, roles, admin_actor, organization, super_role, make_user
    ):
        """Заблокированный админ доступа не даёт.

        Считать его живым значило бы разрешить закрыть организацию
        наглухо: оба админа отключены, а система думает, что один есть.
        """
        blocked = make_user(organization, permissions=())
        active = make_user(organization, permissions=())
        roles.assign(admin_actor, user_id=blocked.id, role_id=super_role.id)
        roles.assign(admin_actor, user_id=active.id, role_id=super_role.id)
        users.set_status(admin_actor, blocked.id, status="INACTIVE")

        with pytest.raises(Conflict):
            users.set_status(admin_actor, active.id, status="INACTIVE")


# --- учётные записи ----------------------------------------------------------


@pytest.mark.django_db
class TestUsers:
    def test_created_account_has_no_usable_password(
        self, users, admin_actor, organization
    ):
        """Пароль здесь не задаётся ни при каких условиях.

        Учётная запись, чей пароль знает кто-то ещё, не отвечает на
        вопрос «кто это сделал» — а на него отвечает весь журнал.
        """
        user = users.create(admin_actor, email="new@humotech.tj")
        assert user.has_usable_password() is False
        assert user.status == "INACTIVE"

    def test_duplicate_email_is_a_conflict(self, users, admin_actor):
        users.create(admin_actor, email="dup@humotech.tj")
        with pytest.raises(Conflict):
            users.create(admin_actor, email="DUP@humotech.tj")

    def test_password_hash_never_reaches_the_audit(self, users, admin_actor):
        from humotech.audit.models import AuditLog

        user = users.create(admin_actor, email="audited@humotech.tj")
        record = AuditLog.objects.get(entity_id=user.id, action="user.create")
        assert "password" not in (record.new_values or {})


# --- аудит -------------------------------------------------------------------


@pytest.mark.django_db
class TestAudit:
    def test_every_grant_and_revoke_is_recorded(
        self, roles, admin_actor, organization, target
    ):
        from humotech.audit.models import AuditLog

        role = make_role(organization, "READER", ("employees.read",))
        grant = roles.assign(admin_actor, user_id=target.id, role_id=role.id)
        roles.revoke(admin_actor, grant.id)

        actions = list(
            AuditLog.objects.filter(entity_id=grant.id)
            .order_by("occurred_at")
            .values_list("action", flat=True)
        )
        assert actions == [
            "user_role_scope.assign", "user_role_scope.revoke",
        ]

    def test_revoke_records_both_sides_of_the_change(
        self, roles, admin_actor, organization, target
    ):
        from humotech.audit.models import AuditLog

        role = make_role(organization, "READER", ("employees.read",))
        grant = roles.assign(admin_actor, user_id=target.id, role_id=role.id)
        roles.revoke(admin_actor, grant.id)

        record = AuditLog.objects.get(action="user_role_scope.revoke")
        assert record.old_values["valid_to"] is None
        assert record.new_values["valid_to"] is not None


# --- HTTP --------------------------------------------------------------------


@pytest.fixture()
def rbac_client(api_client, make_user, organization):
    # `employees.read` здесь не для чтения сотрудников, а чтобы было что
    # выдавать: выдать можно только те права, которыми владеешь сам, и
    # администратор без единого прикладного права не выдал бы ни одной роли.
    user = make_user(
        organization,
        permissions=("users.manage", "roles.manage", "employees.read"),
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.mark.django_db
class TestHttp:
    def test_users_are_listed(self, rbac_client):
        response = rbac_client.get(f"{API}/users/")
        assert response.status_code == 200
        assert "items" in response.json()

    def test_created_user_is_returned_without_password(self, rbac_client):
        response = rbac_client.post(
            f"{API}/users/", {"email": "http@humotech.tj"}, format="json"
        )
        assert response.status_code == 201
        body = response.json()
        assert "password" not in body
        assert body["status"] == "INACTIVE"

    def test_roles_expose_grantability(self, rbac_client, organization):
        make_role(organization, "POWERFUL", ("settings.manage",))
        response = rbac_client.get(f"{API}/roles")
        assert response.status_code == 200
        row = next(
            r for r in response.json()["items"] if r["code"] == "POWERFUL"
        )
        assert row["grantable"] is False

    def test_grant_and_revoke_over_http(
        self, rbac_client, organization, make_user
    ):
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())

        created = rbac_client.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(role.id)},
            format="json",
        )
        assert created.status_code == 201
        grant_id = created.json()["id"]

        # Проверяется присутствие конкретного назначения, а не размер
        # списка: у учётной записи есть ещё роль, с которой её завели,
        # и счёт сломался бы от неё, ничего не сказав по существу.
        def ids(**params):
            response = rbac_client.get(
                f"{API}/users/{person.id}/grants", params
            )
            assert response.status_code == 200
            return [row["id"] for row in response.json()["items"]]

        assert grant_id in ids()

        revoked = rbac_client.delete(f"{API}/grants/{grant_id}")
        assert revoked.status_code == 200
        assert revoked.json()["valid_to"] is not None

        assert grant_id not in ids()
        assert grant_id in ids(history="true")

    def test_escalation_over_http_is_403(
        self, rbac_client, organization, make_user
    ):
        powerful = make_role(organization, "POWERFUL", ("settings.manage",))
        person = make_user(organization, permissions=())
        response = rbac_client.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(powerful.id)},
            format="json",
        )
        assert response.status_code == 403

    def test_without_permission_it_is_403(
        self, api_client, make_user, organization
    ):
        user = make_user(organization, permissions=("employees.read",))
        api_client.force_authenticate(user=user)
        assert api_client.get(f"{API}/users/").status_code == 403
        assert api_client.get(f"{API}/roles").status_code == 403

    def test_anonymous_gets_nothing(self, api_client):
        assert api_client.get(f"{API}/users/").status_code in (401, 403)
        assert api_client.get(f"{API}/roles").status_code in (401, 403)
