"""Правка учётных записей, пароли и редактор ролей.

Продолжение `test_hr_rbac.py`: там выдача и отзыв ролей, здесь то, чего
не хватало, чтобы CRM можно было завести людям, — правка карточки,
установка пароля и собственные роли организации.

Главная проверяемая мысль одна и та же во всех трёх частях: повысить
себя нельзя. Выдать роль с чужими правами не давала прежняя проверка;
теперь ту же дыру закрывает редактор ролей — собрать роль из прав,
которых у тебя нет, и выдать её себе, это тот же обход, только в два
шага.

Отдельно про пароль: в журнале не должно оказаться ни его самого, ни
хеша, ни длины. Проверяется не глазами, а поиском значения по всем
записям журнала.
"""

from __future__ import annotations

import pytest

from humotech.accounts.models import User
from humotech.audit.models import AuditLog
from humotech.rbac.models import Permission, Role, RolePermission

pytestmark = pytest.mark.django_db

API = "/api/v1"

#: Пароль, стойкий по правилам проекта (минимум 12 символов, не
#: словарный, не только цифры). Значение ищется в журнале целиком.
GOOD_PASSWORD = "korrekt-parol-2026"


@pytest.fixture()
def admin_permissions() -> tuple[str, ...]:
    """Права администратора доступа плюс два прикладных.

    Прикладные здесь не для чтения данных, а чтобы было что вкладывать
    в роль: вложить можно только то, чем владеешь сам, и администратор
    без единого прикладного права не собрал бы ни одной роли.
    """
    return (
        "users.manage", "roles.manage", "employees.read", "schedules.read",
    )


@pytest.fixture()
def admin_client(api_client, make_user, organization, admin_permissions):
    api_client.force_authenticate(
        user=make_user(organization, permissions=admin_permissions)
    )
    return api_client


@pytest.fixture()
def target(make_user, organization) -> User:
    return make_user(organization, permissions=())


@pytest.fixture()
def fresh_user(admin_client, organization) -> User:
    """Учётная запись, заведённая через API, — то есть без пароля.

    Фикстура `make_user` пароль задаёт: она собирает действующего
    пользователя для входа. Здесь нужен ровно противоположный случай —
    запись сразу после создания, какой её видит кадровик.
    """
    created = admin_client.post(
        f"{API}/users/", {"email": "novyy@humotech.tj"}, format="json"
    ).json()
    return User.objects.get(id=created["id"])


@pytest.fixture()
def foreign_permission(db) -> Permission:
    """Разрешение, которого у администратора из фикстур нет.

    Справочник в тестах наполняется по требованию, а не целиком: так
    «отказано» не может случиться просто потому, что таблица пуста.
    Поэтому строку приходится завести явно — иначе проверка упёрлась бы
    в «нет такого разрешения» вместо проверяемого «оно не ваше».
    """
    row, _ = Permission.objects.get_or_create(
        code="employees.manage",
        defaults={"name": "Управление сотрудниками", "description": ""},
    )
    return row


# --- правка учётной записи ---------------------------------------------------


class TestUserUpdate:
    def test_email_is_changed(self, admin_client, target):
        response = admin_client.patch(
            f"{API}/users/{target.id}/",
            {"email": "new-address@humotech.tj"},
            format="json",
        )

        assert response.status_code == 200
        target.refresh_from_db()
        assert target.email == "new-address@humotech.tj"

    def test_taken_email_is_a_conflict_not_a_crash(
        self, admin_client, target, make_user, organization
    ):
        """Адрес занят — понятное сообщение, а не текст PostgreSQL."""
        other = make_user(organization, permissions=())

        response = admin_client.patch(
            f"{API}/users/{target.id}/",
            {"email": other.email},
            format="json",
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_employee_is_linked(self, admin_client, target, employee):
        response = admin_client.patch(
            f"{API}/users/{target.id}/",
            {"employee_id": str(employee.id)},
            format="json",
        )

        assert response.status_code == 200
        target.refresh_from_db()
        assert target.employee_id == employee.id

    def test_employee_outside_the_scope_is_refused(
        self, api_client, make_user, organization, office, employee
    ):
        """Привязать можно только видимого сотрудника.

        Иначе привязка стала бы способом узнать идентификаторы людей из
        чужих офисов: «привязалось» означало бы «такой человек есть».
        """
        outsider = make_user(organization, permissions=())
        api_client.force_authenticate(
            user=make_user(
                organization,
                permissions=("users.manage", "employees.read"),
                office=office,
            )
        )
        # сотрудник фикстуры относится к `office`, поэтому берём чужой офис
        from humotech.employees.models import EmployeeAssignment

        EmployeeAssignment.objects.filter(employee=employee).delete()

        response = api_client.patch(
            f"{API}/users/{outsider.id}/",
            {"employee_id": str(employee.id)},
            format="json",
        )

        assert response.status_code == 403

    def test_unlinking_needs_an_explicit_flag(
        self, admin_client, target, employee
    ):
        """`employee_id` отсутствует и `employee_id: null` — разные вещи.

        Склеив их, каждая правка адреса отвязывала бы сотрудника.
        """
        admin_client.patch(
            f"{API}/users/{target.id}/",
            {"employee_id": str(employee.id)},
            format="json",
        )

        admin_client.patch(
            f"{API}/users/{target.id}/",
            {"email": "renamed@humotech.tj"},
            format="json",
        )
        target.refresh_from_db()
        assert target.employee_id == employee.id

        admin_client.patch(
            f"{API}/users/{target.id}/",
            {"unlink_employee": True},
            format="json",
        )
        target.refresh_from_db()
        assert target.employee_id is None

    def test_update_is_recorded(self, admin_client, target):
        admin_client.patch(
            f"{API}/users/{target.id}/",
            {"email": "audited@humotech.tj"},
            format="json",
        )

        entry = AuditLog.objects.get(
            entity_type="users", entity_id=target.id, action="user.update"
        )
        assert entry.new_values["email"] == "audited@humotech.tj"

    def test_reading_needs_the_permission(
        self, api_client, make_user, organization, target
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=())
        )

        response = api_client.patch(
            f"{API}/users/{target.id}/", {"email": "x@humotech.tj"},
            format="json",
        )

        assert response.status_code == 403


# --- пароль ------------------------------------------------------------------


class TestSetPassword:
    def test_password_makes_the_account_usable(self, admin_client, fresh_user):
        """До этого учётная запись войти не могла вовсе.

        Она заводится с непригодным паролем, и без этого действия завести
        человека в CRM было нельзя — ни одного.
        """
        assert not fresh_user.has_usable_password()

        response = admin_client.post(
            f"{API}/users/{fresh_user.id}/set-password/",
            {"password": GOOD_PASSWORD},
            format="json",
        )

        assert response.status_code == 200
        fresh_user.refresh_from_db()
        assert fresh_user.check_password(GOOD_PASSWORD)

    def test_weak_password_is_refused(self, admin_client, fresh_user):
        response = admin_client.post(
            f"{API}/users/{fresh_user.id}/set-password/",
            {"password": "12345678"},
            format="json",
        )

        assert response.status_code == 400
        fresh_user.refresh_from_db()
        assert not fresh_user.has_usable_password()

    def test_password_similar_to_the_address_is_refused(
        self, admin_client, target
    ):
        """Проверка сходства работает, только если валидатору дали
        пользователя. Без него этот валидатор молча пропускает всё."""
        local_part = target.email.split("@")[0]

        response = admin_client.post(
            f"{API}/users/{target.id}/set-password/",
            {"password": local_part},
            format="json",
        )

        assert response.status_code == 400

    def test_nothing_of_the_password_reaches_the_journal(
        self, admin_client, target
    ):
        """Ни значение, ни хеш, ни длина.

        Длина сама по себе сужает перебор, поэтому в журнал уходит один
        только факт установки.
        """
        admin_client.post(
            f"{API}/users/{target.id}/set-password/",
            {"password": GOOD_PASSWORD},
            format="json",
        )
        target.refresh_from_db()

        entries = AuditLog.objects.filter(
            entity_type="users", entity_id=target.id,
            action="user.password.set",
        )
        assert entries.count() == 1
        dumped = str(list(entries.values("old_values", "new_values")))
        assert GOOD_PASSWORD not in dumped
        assert target.password not in dumped
        assert str(len(GOOD_PASSWORD)) not in dumped

    def test_the_password_never_comes_back_in_the_response(
        self, admin_client, target
    ):
        response = admin_client.post(
            f"{API}/users/{target.id}/set-password/",
            {"password": GOOD_PASSWORD},
            format="json",
        )

        body = response.content.decode()
        assert GOOD_PASSWORD not in body
        assert "password" not in body

    def test_setting_a_password_ends_the_old_sessions(
        self, api_client, make_user, organization
    ):
        """Смена пароля обязана выбрасывать тех, кто уже вошёл.

        Django сверяет с сессией отпечаток пароля, поэтому свойство
        достаётся даром — но именно поэтому его надо проверить: оно
        держится на настройке аутентификации, а не на нашем коде.
        """
        from django.test import Client

        victim = make_user(
            organization, permissions=("employees.read",),
            raw_password=GOOD_PASSWORD,
        )
        victim.status = "ACTIVE"
        victim.save(update_fields=["status"])

        browser = Client()
        assert browser.post(
            f"{API}/auth/login",
            {
                "organization_code": organization.code,
                "email": victim.email,
                "password": GOOD_PASSWORD,
            },
            content_type="application/json",
        ).status_code == 200
        assert browser.get(f"{API}/auth/me").status_code == 200

        api_client.force_authenticate(
            user=make_user(organization, permissions=("users.manage",))
        )
        api_client.post(
            f"{API}/users/{victim.id}/set-password/",
            {"password": "drugoy-parol-2026"},
            format="json",
        )

        assert browser.get(f"{API}/auth/me").status_code in (401, 403)

    def test_setting_a_password_needs_users_manage(
        self, api_client, make_user, organization, target
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("roles.manage",))
        )

        response = api_client.post(
            f"{API}/users/{target.id}/set-password/",
            {"password": GOOD_PASSWORD},
            format="json",
        )

        assert response.status_code == 403

    def test_foreign_organization_is_not_found(
        self, api_client, make_user, other_organization, target
    ):
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=("users.manage",))
        )

        response = api_client.post(
            f"{API}/users/{target.id}/set-password/",
            {"password": GOOD_PASSWORD},
            format="json",
        )

        assert response.status_code == 404


# --- справочник разрешений ---------------------------------------------------


class TestPermissionCatalog:
    def test_catalog_is_listed(self, admin_client):
        body = admin_client.get(f"{API}/permissions").json()

        codes = {item["code"] for item in body["items"]}
        assert "employees.read" in codes
        assert Permission.objects.count() == len(body["items"])

    def test_catalog_needs_roles_manage(
        self, api_client, make_user, organization
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("users.manage",))
        )

        assert api_client.get(f"{API}/permissions").status_code == 403


# --- роли --------------------------------------------------------------------


class TestRoleEditing:
    def test_role_is_created_with_its_permissions(self, admin_client):
        response = admin_client.post(
            f"{API}/roles",
            {
                "code": "hr_region",
                "name": "Кадровик региона",
                "permissions": ["employees.read", "schedules.read"],
            },
            format="json",
        )

        assert response.status_code == 201
        body = response.json()
        # код приводится к верхнему регистру: уникальность регистрозависима
        assert body["code"] == "HR_REGION"
        assert body["permissions"] == ["employees.read", "schedules.read"]
        assert body["is_system"] is False

    def test_role_cannot_carry_permissions_the_author_lacks(
        self, admin_client, foreign_permission
    ):
        """Тот же обход, что и при выдаче роли, только в два шага."""
        response = admin_client.post(
            f"{API}/roles",
            {"code": "SNEAKY", "name": "Обход",
             "permissions": ["employees.manage"]},
            format="json",
        )

        assert response.status_code == 403
        assert "employees.manage" in (
            response.json()["error"]["details"]["missing_permissions"]
        )
        assert not Role.objects.filter(code="SNEAKY").exists()

    def test_unknown_permission_is_a_validation_error(self, admin_client):
        response = admin_client.post(
            f"{API}/roles",
            {"code": "TYPO", "name": "Опечатка",
             "permissions": ["employees.raed"]},
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["error"]["details"]["unknown"] == [
            "employees.raed"
        ]

    def test_super_admin_code_is_refused(self, admin_client):
        """Своя роль с этим кодом сломала бы счёт администраторов.

        По коду SUPER_ADMIN проверяется, остался ли в организации хоть
        один управляющий. Вторая роль с тем же кодом позволила бы снять
        с неё права и оставить организацию без управления при формально
        непустом счёте.
        """
        response = admin_client.post(
            f"{API}/roles",
            {"code": "SUPER_ADMIN", "name": "Подмена"},
            format="json",
        )

        assert response.status_code == 403

    def test_duplicate_code_is_a_conflict(self, admin_client):
        admin_client.post(
            f"{API}/roles", {"code": "DUPL", "name": "Первая"}, format="json"
        )

        response = admin_client.post(
            f"{API}/roles", {"code": "DUPL", "name": "Вторая"}, format="json"
        )

        assert response.status_code == 409

    def test_role_is_read_back(self, admin_client):
        created = admin_client.post(
            f"{API}/roles",
            {"code": "READBACK", "name": "Чтение",
             "permissions": ["employees.read"]},
            format="json",
        ).json()

        body = admin_client.get(f"{API}/roles/{created['id']}").json()

        assert body["code"] == "READBACK"
        assert body["permissions"] == ["employees.read"]

    def test_permissions_are_replaced_not_merged(self, admin_client):
        created = admin_client.post(
            f"{API}/roles",
            {"code": "REPLACE", "name": "Замена",
             "permissions": ["employees.read", "schedules.read"]},
            format="json",
        ).json()

        body = admin_client.patch(
            f"{API}/roles/{created['id']}",
            {"permissions": ["schedules.read"]},
            format="json",
        ).json()

        assert body["permissions"] == ["schedules.read"]
        assert RolePermission.objects.filter(role_id=created["id"]).count() == 1

    def test_editing_cannot_add_permissions_the_author_lacks(
        self, admin_client, foreign_permission
    ):
        created = admin_client.post(
            f"{API}/roles",
            {"code": "GROW", "name": "Рост",
             "permissions": ["employees.read"]},
            format="json",
        ).json()

        response = admin_client.patch(
            f"{API}/roles/{created['id']}",
            {"permissions": ["employees.read", "employees.manage"]},
            format="json",
        )

        assert response.status_code == 403
        assert RolePermission.objects.filter(role_id=created["id"]).count() == 1

    def test_system_role_is_not_editable(self, admin_client):
        """Системная роль общая для всех организаций.

        Правка из одной поменяла бы права соседям — этого не должно быть
        возможно вовсе.
        """
        system = Role.objects.create(
            organization=None, code="SYSTEM_WIDE", name="Общая",
            is_system=True,
        )

        response = admin_client.patch(
            f"{API}/roles/{system.id}", {"name": "Переименовано"},
            format="json",
        )

        assert response.status_code == 403
        system.refresh_from_db()
        assert system.name != "Переименовано"

    def test_foreign_role_looks_like_nothing(
        self, api_client, make_user, other_organization, admin_client
    ):
        created = admin_client.post(
            f"{API}/roles", {"code": "MINE", "name": "Своя"}, format="json"
        ).json()
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=("roles.manage",))
        )

        assert api_client.get(f"{API}/roles/{created['id']}").status_code == 404

    def test_changes_are_recorded(self, admin_client):
        created = admin_client.post(
            f"{API}/roles",
            {"code": "AUDITED", "name": "Журнал",
             "permissions": ["employees.read"]},
            format="json",
        ).json()
        admin_client.patch(
            f"{API}/roles/{created['id']}",
            {"permissions": ["schedules.read"]},
            format="json",
        )

        entries = list(
            AuditLog.objects.filter(
                entity_type="roles", entity_id=created["id"]
            ).order_by("occurred_at").values_list("action", flat=True)
        )
        assert entries == ["role.create", "role.update"]

    def test_the_diff_names_the_permissions_on_both_sides(self, admin_client):
        """Ради этого журнал и ведётся: что было и что стало."""
        created = admin_client.post(
            f"{API}/roles",
            {"code": "DIFF", "name": "Разница",
             "permissions": ["employees.read"]},
            format="json",
        ).json()
        admin_client.patch(
            f"{API}/roles/{created['id']}",
            {"permissions": ["schedules.read"]},
            format="json",
        )

        entry = AuditLog.objects.get(
            entity_type="roles", entity_id=created["id"],
            action="role.update",
        )
        assert entry.old_values["permissions"] == ["employees.read"]
        assert entry.new_values["permissions"] == ["schedules.read"]

    def test_editing_needs_roles_manage(
        self, api_client, make_user, organization, admin_client
    ):
        created = admin_client.post(
            f"{API}/roles", {"code": "GUARDED", "name": "Под охраной"},
            format="json",
        ).json()
        api_client.force_authenticate(
            user=make_user(organization, permissions=("users.manage",))
        )

        assert api_client.post(
            f"{API}/roles", {"code": "X", "name": "X"}, format="json"
        ).status_code == 403
        assert api_client.patch(
            f"{API}/roles/{created['id']}", {"name": "X"}, format="json"
        ).status_code == 403
