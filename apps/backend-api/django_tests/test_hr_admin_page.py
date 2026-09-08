"""Страница «Администрирование»: то, чем список отличается от карточки.

Здесь проверяется не RBAC как таковой — им заняты `test_hr_rbac.py` и
`test_hr_rbac_admin.py` — а ровно то, что понадобилось экрану и чего до
него не было:

  * сводка считается по ВСЕМУ отобранному набору, а не по странице.
    Курсорная пагинация не знает общего числа, и без отдельного счётчика
    интерфейсу пришлось бы либо врать «25», либо вычитывать всё;
  * строка списка несёт ВСЕ действующие назначения. Одно назначение
    вместо трёх — это не сокращение, а другое утверждение о правах
    человека;
  * пустой список назначений означает разное для того, у кого есть
    `roles.manage`, и для того, у кого его нет. Поэтому рядом идёт
    признак `grants_visible`;
  * две правки подряд из двух вкладок не должны молча затирать друг
    друга — ни у роли, ни у назначения;
  * историю одного человека нельзя собрать отбором чужих записей на
    клиенте: страница журнала конечна, и до старых записей дело просто
    не дойдёт.

Отдельно — пояс организации в «кто я»: без него интерфейс печатает
время в поясе браузера смотрящего, и журнал начинает утверждать не тот
час, в который действие произошло.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from humotech.accounts.models import UserRoleScope
from humotech.employees.models import Employee
from humotech.rbac.models import Permission, Role, RolePermission

pytestmark = pytest.mark.django_db

API = "/api/v1"

ADMIN = ("users.manage", "roles.manage", "audit.read", "employees.read")


def make_role(organization, code: str, permissions: tuple[str, ...]) -> Role:
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
def admin(api_client, make_user, organization):
    api_client.force_authenticate(user=make_user(organization, permissions=ADMIN))
    return api_client


@pytest.fixture()
def bare(organization):
    """Учётная запись вовсе без роли и назначений.

    `make_user` из conftest заводит и то, и другое — ему нужен
    работоспособный пользователь для входа. Здесь проверяется как раз
    противоположное: что показывает список, когда назначений нет.
    """
    from humotech.accounts.models import User

    def _make() -> User:
        user = User(
            organization=organization,
            email=f"bare-{uuid_module.uuid4().hex[:8]}@humotech.tj",
            status="ACTIVE",
        )
        user.set_password(uuid_module.uuid4().hex)
        user.save()
        return user

    return _make


@pytest.fixture()
def people(make_user, organization):
    """Тридцать учётных записей — заведомо больше одной страницы."""
    return [make_user(organization, permissions=()) for _ in range(30)]


# --- сводка ------------------------------------------------------------------


class TestCounts:
    def test_total_counts_the_whole_set_not_the_page(self, admin, people):
        """Ключевое: 25 строк на странице, а в сводке — все.

        Иначе «Всего» на экране означало бы «влезло в один запрос».
        """
        page = admin.get(f"{API}/users/", {"limit": "25"}).json()
        counts = admin.get(f"{API}/users/counts/").json()

        assert len(page["items"]) == 25
        assert page["has_more"] is True
        # 30 заведённых плюс сам администратор.
        assert counts["total"] == 31
        assert counts["active"] + counts["inactive"] == counts["total"]

    def test_search_narrows_the_summary_too(self, admin, people):
        """Сводка обязана описывать тот же набор, что и таблица под ней."""
        found = admin.get(f"{API}/users/", {"search": "actor-"}).json()
        counts = admin.get(
            f"{API}/users/counts/", {"search": "nikto-takogo-net"}
        ).json()

        assert found["items"]
        assert counts["total"] == 0

    def test_status_does_not_narrow_the_summary(self, admin, people):
        """«Сколько неактивных» не должно превращаться в «сколько неактивных
        среди неактивных»: иначе число рядом с вкладкой зависит от того,
        какая вкладка открыта.
        """
        everything = admin.get(f"{API}/users/counts/").json()
        with_status = admin.get(
            f"{API}/users/counts/", {"status": "INACTIVE"}
        ).json()

        assert with_status == everything

    def test_role_filter_applies_to_both(self, admin, organization, make_user):
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())
        UserRoleScope.objects.create(
            organization=organization,
            user=person,
            role=role,
            valid_from=timezone.now() - timedelta(days=1),
        )
        make_user(organization, permissions=())

        page = admin.get(f"{API}/users/", {"role_id": str(role.id)}).json()
        counts = admin.get(
            f"{API}/users/counts/", {"role_id": str(role.id)}
        ).json()

        assert [row["id"] for row in page["items"]] == [str(person.id)]
        assert counts["total"] == 1

    def test_expired_grant_does_not_match_the_role_filter(
        self, admin, organization, make_user
    ):
        """Истёкшее назначение не делает человека обладателем роли."""
        role = make_role(organization, "TEMP", ("employees.read",))
        person = make_user(organization, permissions=())
        moment = timezone.now()
        UserRoleScope.objects.create(
            organization=organization,
            user=person,
            role=role,
            valid_from=moment - timedelta(days=10),
            valid_to=moment - timedelta(days=1),
        )

        counts = admin.get(
            f"{API}/users/counts/", {"role_id": str(role.id)}
        ).json()
        assert counts["total"] == 0

    def test_roles_number_is_null_without_roles_manage(
        self, api_client, make_user, organization
    ):
        """Число ролей — сведение о настройке доступа, а не о людях."""
        api_client.force_authenticate(
            user=make_user(organization, permissions=("users.manage",))
        )
        counts = api_client.get(f"{API}/users/counts/").json()
        assert counts["roles"] is None

    def test_foreign_organization_is_not_counted(
        self, admin, other_organization, make_user
    ):
        make_user(other_organization, permissions=())
        counts = admin.get(f"{API}/users/counts/").json()
        # Только сам администратор: чужая организация не считается.
        assert counts["total"] == 1


# --- строка списка -----------------------------------------------------------


class TestListRow:
    def test_all_active_grants_are_listed_not_the_first(
        self, admin, organization, bare, region, office
    ):
        """Две роли в разных областях — это две строки назначений.

        Показать одну означало бы утверждать, что у человека одна роль.
        """
        person = bare()
        first = make_role(organization, "R1", ("employees.read",))
        second = make_role(organization, "R2", ("schedules.read",))
        moment = timezone.now() - timedelta(days=1)
        UserRoleScope.objects.create(
            organization=organization,
            user=person,
            role=first,
            region=region,
            valid_from=moment,
        )
        UserRoleScope.objects.create(
            organization=organization,
            user=person,
            role=second,
            office=office,
            valid_from=moment,
        )

        row = self._row(admin, person)
        assert len(row["active_grants"]) == 2
        assert {grant["role_code"] for grant in row["active_grants"]} == {
            "R1", "R2",
        }
        scopes = {
            (grant["region_name"], grant["office_name"])
            for grant in row["active_grants"]
        }
        assert scopes == {(region.name, None), (None, office.name)}

    def test_revoked_grant_leaves_the_row(self, admin, organization, bare):
        """Активная запись без действующих назначений — запись без доступа."""
        person = bare()
        role = make_role(organization, "GONE", ("employees.read",))
        moment = timezone.now()
        UserRoleScope.objects.create(
            organization=organization,
            user=person,
            role=role,
            valid_from=moment - timedelta(days=5),
            valid_to=moment - timedelta(seconds=1),
        )

        row = self._row(admin, person)
        assert row["active_grants"] == []
        assert row["grants_visible"] is True

    def test_future_grant_is_not_active_yet(self, admin, organization, bare):
        person = bare()
        role = make_role(organization, "SOON", ("employees.read",))
        UserRoleScope.objects.create(
            organization=organization,
            user=person,
            role=role,
            valid_from=timezone.now() + timedelta(days=3),
        )

        assert self._row(admin, person)["active_grants"] == []

    def test_empty_grants_without_roles_manage_are_marked_hidden(
        self, api_client, make_user, organization, bare
    ):
        """Пустой список без права — «не показано», а не «нет назначений»."""
        person = bare()
        api_client.force_authenticate(
            user=make_user(organization, permissions=("users.manage",))
        )
        row = self._row(api_client, person)
        assert row["active_grants"] == []
        assert row["grants_visible"] is False

    def test_full_name_comes_from_the_employee(
        self, admin, organization, bare, office
    ):
        employee = Employee.objects.create(
            organization=organization,
            last_name="Рахимов",
            first_name="Далер",
            middle_name="Собирович",
            hire_date="2026-01-09",
            employment_status="ACTIVE",
        )
        person = bare()
        person.employee = employee
        person.save(update_fields=["employee"])

        assert self._row(admin, person)["full_name"] == "Рахимов Далер Собирович"

    def test_technical_account_has_no_name(self, admin, organization, bare):
        """Подставлять адрес вместо имени значит выдавать одно за другое."""
        person = bare()
        assert self._row(admin, person)["full_name"] is None

    @staticmethod
    def _row(client, person) -> dict:
        page = client.get(f"{API}/users/", {"search": person.email}).json()
        return next(row for row in page["items"] if row["id"] == str(person.id))


# --- конкурентные изменения --------------------------------------------------


class TestConcurrency:
    def test_stale_role_edition_is_refused(self, admin, organization):
        role = make_role(organization, "CUSTOM", ("employees.read",))
        seen = admin.get(f"{API}/roles/{role.id}").json()["updated_at"]

        first = admin.patch(
            f"{API}/roles/{role.id}",
            {"name": "Первый", "expected_updated_at": seen},
            format="json",
        )
        assert first.status_code == 200

        second = admin.patch(
            f"{API}/roles/{role.id}",
            {"name": "Второй", "expected_updated_at": seen},
            format="json",
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "conflict"
        role.refresh_from_db()
        assert role.name == "Первый"

    def test_fresh_edition_round_trips_exactly(self, admin, organization):
        """Отметка редакции переживает JSON без потери точности.

        Микросекунды у `updated_at` есть, и если бы формат их срезал,
        законная правка отвергалась бы конфликтом на ровном месте.
        """
        role = make_role(organization, "CUSTOM", ("employees.read",))
        for name in ("Раз", "Два", "Три"):
            seen = admin.get(f"{API}/roles/{role.id}").json()["updated_at"]
            response = admin.patch(
                f"{API}/roles/{role.id}",
                {"name": name, "expected_updated_at": seen},
                format="json",
            )
            assert response.status_code == 200, response.json()

    def test_permission_change_alone_bumps_the_edition(self, admin, organization):
        """Иначе правка одних только прав осталась бы незамеченной."""
        role = make_role(organization, "CUSTOM", ("employees.read",))
        before = admin.get(f"{API}/roles/{role.id}").json()["updated_at"]

        changed = admin.patch(
            f"{API}/roles/{role.id}",
            {
                "permissions": ["employees.read", "users.manage"],
                "expected_updated_at": before,
            },
            format="json",
        )
        assert changed.status_code == 200
        assert changed.json()["updated_at"] != before

    def test_without_expected_edition_the_patch_still_works(
        self, admin, organization
    ):
        """Сверка необязательна: прежние вызовы не ломаются."""
        role = make_role(organization, "CUSTOM", ("employees.read",))
        response = admin.patch(
            f"{API}/roles/{role.id}", {"name": "Без сверки"}, format="json",
        )
        assert response.status_code == 200

    def test_stale_grant_validity_is_refused(self, admin, organization, make_user):
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())
        grant = admin.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(role.id)},
            format="json",
        ).json()
        assert grant["valid_to"] is None

        later = (timezone.now() + timedelta(days=30)).isoformat()
        first = admin.patch(
            f"{API}/grants/{grant['id']}",
            {"valid_to": later, "expected_valid_to": None, "check_expected": True},
            format="json",
        )
        assert first.status_code == 200

        # Второй администратор всё ещё видит «бессрочно».
        second = admin.patch(
            f"{API}/grants/{grant['id']}",
            {
                "valid_to": (timezone.now() + timedelta(days=1)).isoformat(),
                "expected_valid_to": None,
                "check_expected": True,
            },
            format="json",
        )
        assert second.status_code == 409

    def test_validity_check_is_opt_in(self, admin, organization, make_user):
        """Без признака сверки правка проходит: `null` — это «бессрочно»,
        а не «не передали».
        """
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())
        grant = admin.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(role.id)},
            format="json",
        ).json()

        response = admin.patch(
            f"{API}/grants/{grant['id']}",
            {"valid_to": (timezone.now() + timedelta(days=5)).isoformat()},
            format="json",
        )
        assert response.status_code == 200


# --- срок назначения по календарной дате -------------------------------------


class TestValidityByDate:
    """«Действует по 31 декабря» — это конец 31 декабря В ПОЯСЕ ОРГАНИЗАЦИИ.

    Считать границу суток в браузере нельзя: у администратора в одном
    городе и у администратора в другом один и тот же выбранный день дал бы
    разные моменты, а на экране стояло бы одно и то же число.
    """

    def test_date_becomes_the_end_of_that_day_in_the_office_zone(
        self, admin, organization, make_user, office
    ):
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())

        created = admin.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(role.id),
             "valid_to_date": "2026-12-31"},
            format="json",
        )
        assert created.status_code == 201, created.json()

        grant = UserRoleScope.objects.get(id=created.json()["id"])
        zone = ZoneInfo(office.timezone)
        assert grant.valid_to.astimezone(zone).date() == date(2026, 12, 31)
        # Последний день входит в срок целиком.
        assert grant.valid_to.astimezone(zone).hour == 23

    def test_moment_and_date_together_are_refused(
        self, admin, organization, make_user
    ):
        """Два способа задать одно и то же — повод спросить, а не угадывать."""
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())

        response = admin.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(role.id),
             "valid_to": timezone.now().isoformat(),
             "valid_to_date": "2026-12-31"},
            format="json",
        )
        assert response.status_code == 400

    def test_validity_patch_accepts_a_date(
        self, admin, organization, make_user, office
    ):
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())
        grant = admin.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(role.id)},
            format="json",
        ).json()

        moved = admin.patch(
            f"{API}/grants/{grant['id']}",
            {"valid_to_date": "2026-11-30"},
            format="json",
        )
        assert moved.status_code == 200, moved.json()
        row = UserRoleScope.objects.get(id=grant["id"])
        assert row.valid_to.astimezone(ZoneInfo(office.timezone)).date() == date(
            2026, 11, 30
        )

    def test_validity_patch_without_any_field_is_refused(
        self, admin, organization, make_user
    ):
        """Пустое тело — не «сделать бессрочным»: это ничего не сказать."""
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())
        grant = admin.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(role.id)},
            format="json",
        ).json()

        assert admin.patch(
            f"{API}/grants/{grant['id']}", {}, format="json",
        ).status_code == 400

    def test_explicit_null_still_means_forever(
        self, admin, organization, make_user
    ):
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())
        grant = admin.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(role.id),
             "valid_to_date": "2026-11-30"},
            format="json",
        ).json()

        response = admin.patch(
            f"{API}/grants/{grant['id']}", {"valid_to": None}, format="json",
        )
        assert response.status_code == 200, response.json()
        assert UserRoleScope.objects.get(id=grant["id"]).valid_to is None


# --- журнал ------------------------------------------------------------------


class TestAuditForOnePerson:
    def test_entity_ids_selects_exactly_those_grants(
        self, admin, organization, make_user
    ):
        """История назначений одного человека берётся сервером, а не
        отбором чужих записей на клиенте.
        """
        role = make_role(organization, "READER", ("employees.read",))
        mine = make_user(organization, permissions=())
        other = make_user(organization, permissions=())
        made = [
            admin.post(
                f"{API}/grants",
                {"user_id": str(person.id), "role_id": str(role.id)},
                format="json",
            ).json()
            for person in (mine, other)
        ]

        response = admin.get(
            f"{API}/audit-logs",
            {"entity_type": "user_role_scopes", "entity_ids": made[0]["id"]},
        )
        assert response.status_code == 200
        found = response.json()["items"]
        assert found
        assert {row["entity_id"] for row in found} == {made[0]["id"]}

    def test_empty_entity_ids_means_nothing_not_everything(
        self, admin, organization, make_user
    ):
        """У человека без назначений история назначений пуста — но это не
        повод показать ему чужие.
        """
        role = make_role(organization, "READER", ("employees.read",))
        person = make_user(organization, permissions=())
        admin.post(
            f"{API}/grants",
            {"user_id": str(person.id), "role_id": str(role.id)},
            format="json",
        )

        response = admin.get(
            f"{API}/audit-logs",
            {"entity_type": "user_role_scopes", "entity_ids": ""},
        )
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_garbage_in_entity_ids_is_a_clear_refusal(self, admin):
        response = admin.get(f"{API}/audit-logs", {"entity_ids": "ne-uuid"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_too_many_ids_are_refused(self, admin):
        response = admin.get(
            f"{API}/audit-logs",
            {"entity_ids": ",".join(str(uuid_module.uuid4()) for _ in range(101))},
        )
        assert response.status_code == 400

    def test_password_never_appears_in_the_history_of_the_account(
        self, admin, organization, make_user
    ):
        """Для установки пароля в журнале остаётся только факт операции."""
        person = make_user(organization, permissions=())
        secret = "korrekt-parol-2026"
        setting = admin.post(
            f"{API}/users/{person.id}/set-password/",
            {"password": secret},
            format="json",
        )
        assert setting.status_code == 200

        entries = admin.get(
            f"{API}/audit-logs",
            {"entity_type": "users", "entity_id": str(person.id)},
        ).json()["items"]
        assert entries
        body = repr(entries)
        assert secret not in body
        assert "password_hash" not in body


# --- защита последнего администратора ----------------------------------------


class TestLastAdmin:
    """Защита есть, но она защищает доступ, а не запрещает работу.

    Проверка сравнивает «до» и «после». Организация без суперадминистратора
    ничего не теряет от отключения постороннего, и отказ там превращал бы
    защиту в замок на всей организации: не отключить вообще никого.
    """

    def test_ordinary_account_is_deactivated_when_there_is_no_super_admin(
        self, admin, organization, make_user
    ):
        person = make_user(organization, permissions=())
        response = admin.post(f"{API}/users/{person.id}/deactivate/")

        assert response.status_code == 200, response.json()
        person.refresh_from_db()
        assert person.status == "INACTIVE"

    def test_last_super_admin_is_still_protected(
        self, admin, organization, make_user
    ):
        role, _ = Role.objects.get_or_create(
            organization=None, code="SUPER_ADMIN",
            defaults={"name": "Суперадминистратор"},
        )
        root = make_user(organization, permissions=())
        UserRoleScope.objects.create(
            organization=organization, user=root, role=role,
            valid_from=timezone.now() - timedelta(days=1),
        )

        response = admin.post(f"{API}/users/{root.id}/deactivate/")
        assert response.status_code == 409
        root.refresh_from_db()
        assert root.status == "ACTIVE"

    def test_bystander_is_not_blocked_by_someone_else_being_the_last_admin(
        self, admin, organization, make_user
    ):
        """Посторонний отключается и тогда, когда админ в организации один."""
        role, _ = Role.objects.get_or_create(
            organization=None, code="SUPER_ADMIN",
            defaults={"name": "Суперадминистратор"},
        )
        root = make_user(organization, permissions=())
        UserRoleScope.objects.create(
            organization=organization, user=root, role=role,
            valid_from=timezone.now() - timedelta(days=1),
        )
        person = make_user(organization, permissions=())

        assert admin.post(
            f"{API}/users/{person.id}/deactivate/"
        ).status_code == 200


# --- пояс организации --------------------------------------------------------


class TestWhoAmI:
    def test_me_carries_the_organization_timezone(
        self, api_client, make_user, organization, office
    ):
        """Без него интерфейс печатает время в поясе браузера смотрящего."""
        api_client.force_authenticate(user=make_user(organization, permissions=()))
        body = api_client.get(f"{API}/auth/me").json()
        assert body["timezone"] == office.timezone

    def test_timezone_falls_back_to_the_organization(
        self, api_client, make_user, organization
    ):
        """Офисов нет — берётся пояс организации, а не UTC наугад."""
        api_client.force_authenticate(user=make_user(organization, permissions=()))
        body = api_client.get(f"{API}/auth/me").json()
        assert body["timezone"] == organization.default_timezone


# --- источник областей для выдачи --------------------------------------------


class TestAssignableScopes:
    """Откуда форма берёт список областей.

    До этого форма собирала регионы из видимых офисов. Источник был
    неверный по трём причинам сразу: у технического администратора есть
    `offices.read` и нет `regions.read`, поэтому справочник регионов ему
    закрыт; регион без офисов из такого источника пропадал совсем;
    а «вправе прочитать» и «вправе выдать» — разные вопросы, и второй
    задавался только сервером, уже после нажатия кнопки.

    Список считается тем же кодом, что и проверка при выдаче
    (`region_filter`/`office_filter` — это `require_region`/
    `require_office` как условие запроса), поэтому здесь проверяется не
    совпадение двух похожих реализаций, а поведение на границах.
    """

    URL = f"{API}/grants/scopes"

    @staticmethod
    def _ids(rows: list[dict]) -> set[str]:
        return {row["id"] for row in rows}

    def test_region_without_offices_is_offered(
        self, admin, region, other_region, office
    ):
        """Главный случай: регион есть, офисов в нём нет.

        Прежний источник — «регионы видимых офисов» — терял такой регион
        молча, и выдать назначение на него из формы было нельзя вовсе.
        """
        body = admin.get(self.URL).json()
        assert body["all_organization"] is True
        assert self._ids(body["regions"]) == {str(region.id), str(other_region.id)}
        # офисов у второго региона нет — и это ему не мешает
        assert self._ids(body["offices"]) == {str(office.id)}

    def test_foreign_organization_is_absent(
        self, admin, region, foreign_region, foreign_office
    ):
        body = admin.get(self.URL).json()
        assert str(foreign_region.id) not in self._ids(body["regions"])
        assert str(foreign_office.id) not in self._ids(body["offices"])

    def test_region_admin_gets_own_region_only(
        self, api_client, make_user, organization, region, other_region,
        office, other_office,
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=ADMIN, region=region)
        )
        body = api_client.get(self.URL).json()
        assert body["all_organization"] is False
        assert self._ids(body["regions"]) == {str(region.id)}
        assert self._ids(body["offices"]) == {str(office.id)}

    def test_office_admin_gets_own_office(
        self, api_client, make_user, organization, region, other_region,
        office, other_office,
    ):
        """Область — один офис. Его регион в списке остаётся.

        Так отвечает и `require_region`: регион виден и тогда, когда
        выдан офис внутри него. Список обязан совпадать с проверкой,
        а не быть строже или мягче неё.
        """
        api_client.force_authenticate(
            user=make_user(organization, permissions=ADMIN, office=office)
        )
        body = api_client.get(self.URL).json()
        assert body["all_organization"] is False
        assert self._ids(body["offices"]) == {str(office.id)}
        assert self._ids(body["regions"]) == {str(region.id)}

    def test_several_limited_regions_do_not_add_up_to_the_organization(
        self, api_client, make_user, organization, region, other_region,
        make_actor,
    ):
        """Две области — это две области, а не «вся организация».

        Проверяется не только признак: следом идёт сама выдача без
        региона и офиса, и она обязана быть отклонена.
        """
        from humotech.accounts.models import UserRoleScope as Grant

        user = make_user(organization, permissions=ADMIN, region=region)
        first = Grant.objects.get(user=user)
        Grant.objects.create(
            organization=organization,
            user=user,
            role=first.role,
            region=other_region,
            valid_from=first.valid_from,
        )
        api_client.force_authenticate(user=user)

        body = api_client.get(self.URL).json()
        assert body["all_organization"] is False
        assert self._ids(body["regions"]) == {str(region.id), str(other_region.id)}

        target = make_user(organization, permissions=())
        modest = make_role(organization, "MODEST_ORG", ("employees.read",))
        refusal = api_client.post(
            f"{API}/grants",
            {"user_id": str(target.id), "role_id": str(modest.id)},
            format="json",
        )
        assert refusal.status_code == 403

    def test_scope_not_offered_is_refused_when_substituted_by_hand(
        self, api_client, make_user, organization, region, other_region,
        other_office,
    ):
        """Идентификатор из формы доверия не даёт.

        Список областей — подсказка, а не разрешение. Подставленный
        вручную чужой регион обязан быть отклонён сервером, иначе
        защита существовала бы только в разметке.
        """
        user = make_user(organization, permissions=ADMIN, region=region)
        api_client.force_authenticate(user=user)

        offered = self._ids(api_client.get(self.URL).json()["regions"])
        assert str(other_region.id) not in offered

        target = make_user(organization, permissions=())
        modest = make_role(organization, "MODEST_SUB", ("employees.read",))
        for payload in (
            {"region_id": str(other_region.id)},
            {"office_id": str(other_office.id)},
        ):
            refusal = api_client.post(
                f"{API}/grants",
                {"user_id": str(target.id), "role_id": str(modest.id), **payload},
                format="json",
            )
            assert refusal.status_code == 403, payload

    def test_roles_manage_is_required(
        self, api_client, make_user, organization, region
    ):
        """`users.manage` этого механизма не открывает: выдача — не заведение."""
        api_client.force_authenticate(
            user=make_user(organization, permissions=("users.manage",))
        )
        assert api_client.get(self.URL).status_code == 403

    def test_regions_read_is_not_required(
        self, api_client, make_user, organization, region, office
    ):
        """Ровно набор технического администратора: офисы есть, регионов нет.

        Справочник регионов ему закрыт, а роли он выдаёт. Если бы форма
        по-прежнему зависела от `regions.read`, здесь был бы отказ.
        """
        tech = make_user(
            organization,
            permissions=("users.manage", "roles.manage", "offices.read"),
        )
        api_client.force_authenticate(user=tech)
        assert api_client.get(f"{API}/regions/").status_code == 403

        body = api_client.get(self.URL).json()
        assert self._ids(body["regions"]) == {str(region.id)}

    def test_closed_office_is_not_offered_but_its_region_stays(
        self, admin, region, office
    ):
        """Сознательное сужение: предлагать закрытый офис незачем.

        Регион при этом остаётся: назначение на него по-прежнему
        законно, и исчезнуть вместе с последним офисом он не должен.
        """
        office.status = "CLOSED"
        office.save(update_fields=["status"])

        body = admin.get(self.URL).json()
        assert body["offices"] == []
        assert self._ids(body["regions"]) == {str(region.id)}
