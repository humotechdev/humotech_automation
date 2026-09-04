"""Правка через аварийную админку тоже попадает в журнал.

Админка — вход последней надежды, и именно поэтому её след важнее
обычного: сюда приходят, когда обычный путь не сработал, и разбираться
потом будут по журналу. Аварийный вход, не оставляющий записей, — это
не удобство, а способ поменять чужие права незаметно.

Проверяется настоящий путь: HTTP-запрос в админку, её собственная форма,
её `save_model`. Прямой вызов метода доказал бы только то, что метод
существует.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

import pytest
from django.contrib import admin
from django.core.management import call_command
from django.test import Client, RequestFactory
from django.urls import reverse

from humotech.accounts.models import User, UserRoleScope
from humotech.audit.models import AuditLog
from humotech.core.admin import OperationalAdmin
from humotech.organizations.models import Organization
from humotech.rbac.models import Role

pytestmark = pytest.mark.django_db

ADMIN_PASSWORD = "Очень-Длинный-Пароль-2026"


@pytest.fixture()
def seeded(db) -> Organization:
    call_command("seed", create_organization="ADM", verbosity=0)
    return Organization.objects.get(code="ADM")


@pytest.fixture()
def super_admin(seeded) -> User:
    role = Role.objects.get(code="SUPER_ADMIN", organization__isnull=True)
    user = User(
        organization=seeded, email="root@humotech.tj", status="ACTIVE"
    )
    user.set_password(ADMIN_PASSWORD)
    user.save()
    UserRoleScope.objects.create(organization=seeded, user=user, role=role)
    return user


@pytest.fixture()
def admin_browser(super_admin) -> Client:
    browser = Client()
    browser.force_login(super_admin)
    return browser


@pytest.fixture()
def admin_request(super_admin):
    """Запрос от суперадминистратора.

    Нужен именно настоящий: и форма раздела, и проверка прав спрашивают
    `request.user`, и с `None` админка просто падает.
    """
    request = RequestFactory().get("/admin/")
    request.user = super_admin
    return request


def change_url(obj) -> str:
    meta = obj._meta
    return reverse(
        f"admin:{meta.app_label}_{meta.model_name}_change", args=[obj.pk]
    )


def add_url(model) -> str:
    meta = model._meta
    return reverse(f"admin:{meta.app_label}_{meta.model_name}_add")


def _as_form_data(name: str, field, value) -> dict:
    """Одно поле формы в том виде, в каком его прислал бы браузер.

    Через сами виджеты, а не «как получится»: админка показывает дату и
    время двумя полями (`valid_from_0` и `valid_from_1`), и плоское
    значение она просто не примет. Собирать эти имена руками — значит
    переписывать тест при каждой смене виджета.
    """
    from django.forms.widgets import MultiWidget

    if hasattr(value, "pk"):
        value = value.pk
    widget = field.widget

    if isinstance(widget, MultiWidget):
        parts = widget.decompress(value)
        return {
            f"{name}_{index}": (
                sub.format_value(part) if part is not None else ""
            )
            for index, (sub, part) in enumerate(zip(widget.widgets, parts))
        }

    if isinstance(value, bool):
        return {name: value}
    formatted = widget.format_value(value)
    return {name: "" if formatted is None else formatted}


def form_payload(model_admin, request, obj, **overrides) -> dict:
    """Готовые данные формы админки: всё как есть плюс правка.

    Собирается из САМОЙ формы админки, а не из списка полей руками:
    иначе тест начал бы падать от каждой новой колонки, ничего при
    этом не проверяя.
    """
    form_class = model_admin.get_form(request, obj, change=obj is not None)
    form = form_class(instance=obj)

    data: dict = {}
    for name, field in form.fields.items():
        if name in overrides:
            value = overrides.pop(name)
        else:
            value = form.initial.get(name, field.initial)
        data.update(_as_form_data(name, field, value))
    data.update(overrides)
    return data


def entries_for(obj) -> list[AuditLog]:
    return list(
        AuditLog.objects.filter(
            entity_type=obj._meta.db_table, entity_id=obj.pk
        ).order_by("occurred_at")
    )


# --- запись изменений --------------------------------------------------------


class TestChangesAreRecorded:
    def test_editing_a_user_leaves_a_trace(
        self, admin_browser, admin_request, super_admin, seeded
    ):
        victim = User(
            organization=seeded, email="edit-me@humotech.tj", status="INACTIVE"
        )
        victim.set_password(ADMIN_PASSWORD)
        victim.save()
        model_admin = admin.site._registry[User]

        response = admin_browser.post(
            change_url(victim),
            form_payload(model_admin, admin_request, victim, status="ACTIVE"),
        )

        assert response.status_code == 302, "форма не принята админкой"
        victim.refresh_from_db()
        assert victim.status == "ACTIVE"

        entries = entries_for(victim)
        assert [entry.action for entry in entries] == ["admin.users.update"]
        assert entries[0].actor_user_id == super_admin.id
        assert entries[0].old_values == {"status": "INACTIVE"}
        assert entries[0].new_values == {"status": "ACTIVE"}

    def test_the_source_is_visible_in_the_action(
        self, admin_browser, admin_request, seeded
    ):
        """Префикс `admin.` отличает аварийную правку от обычной.

        Без него роль, выданную через админку, нельзя было бы отличить
        от роли, выданной по правилам через API.
        """
        role = Role.objects.create(
            organization=seeded, code="TO_RENAME", name="Старое"
        )
        model_admin = admin.site._registry[Role]

        admin_browser.post(
            change_url(role),
            form_payload(model_admin, admin_request, role, name="Новое"),
        )

        actions = [entry.action for entry in entries_for(role)]
        assert actions == ["admin.roles.update"]
        assert all(action.startswith("admin.") for action in actions)

    def test_creating_is_recorded_too(
        self, admin_browser, admin_request, seeded
    ):
        model_admin = admin.site._registry[Role]

        admin_browser.post(
            add_url(Role),
            form_payload(
                model_admin, admin_request, None,
                organization=seeded.pk,
                code="SOZDANA", name="Созданная в админке",
            ),
        )

        role = Role.objects.get(code="SOZDANA")
        assert [entry.action for entry in entries_for(role)] == [
            "admin.roles.create"
        ]

    def test_role_scope_changes_are_recorded(
        self, admin_browser, admin_request, seeded, super_admin
    ):
        """Область роли — это и есть «кто что видит».

        Расширить её через админку и не оставить следа значило бы дать
        человеку чужие данные так, что заметить это было бы нечем.
        """
        grant = UserRoleScope.objects.get(user=super_admin)
        model_admin = admin.site._registry[UserRoleScope]

        admin_browser.post(
            change_url(grant),
            form_payload(
                model_admin, admin_request, grant,
                valid_to=datetime(2030, 1, 1, tzinfo=dt_timezone.utc),
            ),
        )

        entries = entries_for(grant)
        assert [entry.action for entry in entries] == [
            "admin.user_role_scopes.update"
        ]
        assert "valid_to" in entries[0].new_values

    def test_saving_without_changes_writes_nothing(
        self, admin_browser, admin_request, seeded
    ):
        """Журнал изменений, а не журнал нажатий.

        Записи о сохранении без правок сделали бы журнал нечитаемым:
        настоящие изменения утонули бы среди открытых и закрытых форм.
        """
        role = Role.objects.create(
            organization=seeded, code="UNTOUCHED", name="Без правок"
        )
        model_admin = admin.site._registry[Role]

        admin_browser.post(
            change_url(role), form_payload(model_admin, admin_request, role)
        )

        assert entries_for(role) == []


# --- что в записи, а чего в ней быть не должно -------------------------------


class TestWhatIsRecorded:
    def test_address_and_client_are_kept(
        self, admin_browser, admin_request, seeded
    ):
        """Для аварийного входа «откуда» — часть ответа на «кто».

        Колонки под это в журнале есть с самого начала и до сих пор
        пустовали.
        """
        role = Role.objects.create(
            organization=seeded, code="WITH_IP", name="С адресом"
        )
        model_admin = admin.site._registry[Role]

        admin_browser.post(
            change_url(role),
            form_payload(model_admin, admin_request, role, name="Изменено"),
            HTTP_USER_AGENT="Mozilla/5.0 (проверка)",
        )

        entry = entries_for(role)[0]
        assert entry.ip_address == "127.0.0.1"
        assert entry.user_agent == "Mozilla/5.0 (проверка)"

    def test_password_cannot_reach_the_journal(
        self, admin_browser, admin_request, seeded
    ):
        """Двойная защита: поля нет в форме, и фильтр вырезал бы его.

        Первое можно случайно отменить одной строкой в `exclude`, второе
        работает на составе полей, а не на аккуратности.
        """
        victim = User(
            organization=seeded, email="secret@humotech.tj", status="INACTIVE"
        )
        victim.set_password(ADMIN_PASSWORD)
        victim.save()
        model_admin = admin.site._registry[User]

        admin_browser.post(
            change_url(victim),
            form_payload(model_admin, admin_request, victim, status="ACTIVE"),
        )
        victim.refresh_from_db()

        dumped = str(
            list(
                AuditLog.objects.filter(entity_id=victim.pk).values(
                    "old_values", "new_values"
                )
            )
        )
        assert victim.password not in dumped
        assert ADMIN_PASSWORD not in dumped
        assert "password" not in dumped

    def test_a_relation_is_written_as_an_identifier(
        self, admin_browser, admin_request, seeded, super_admin
    ):
        """В журнале должен остаться `role_id`, а не название роли.

        По названию нельзя найти строку, а названия ещё и меняются.
        """
        grant = UserRoleScope.objects.get(user=super_admin)
        other_role = Role.objects.create(
            organization=seeded, code="OTHER", name="Другая"
        )
        model_admin = admin.site._registry[UserRoleScope]

        admin_browser.post(
            change_url(grant),
            form_payload(model_admin, admin_request, grant, role=other_role.pk),
        )

        entry = entries_for(grant)[0]
        assert entry.new_values["role_id"] == str(other_role.pk)

    def test_the_record_goes_to_the_organization_of_the_object(
        self, admin_browser, admin_request, seeded, super_admin, organization
    ):
        """В админке нет фильтра по организации.

        Суперадминистратор одной организации может править запись другой,
        и положить такое изменение в СВОЙ журнал значило бы спрятать его
        от тех, кого оно касается.
        """
        foreign_role = Role.objects.create(
            organization=organization, code="FOREIGN", name="Чужая"
        )
        assert organization.id != seeded.id
        model_admin = admin.site._registry[Role]

        admin_browser.post(
            change_url(foreign_role),
            form_payload(model_admin, admin_request, foreign_role, name="Правка соседа"),
        )

        entry = entries_for(foreign_role)[0]
        assert entry.organization_id == organization.id
        assert entry.actor_user_id == super_admin.id


# --- устройство --------------------------------------------------------------


def test_every_writable_admin_records_changes(admin_request):
    """Новый редактируемый раздел не должен появиться без журнала.

    Проверка идёт по всем зарегистрированным разделам, а не по списку
    в тесте: список пришлось бы помнить и дополнять руками.
    """
    for model, model_admin in admin.site._registry.items():
        if model._meta.app_label.startswith(("auth", "contenttypes")):
            continue
        if not model_admin.has_change_permission(admin_request):
            continue
        assert isinstance(model_admin, OperationalAdmin), (
            f"{model.__name__}: редактируемый раздел админки обязан "
            "наследоваться от OperationalAdmin, иначе правка не попадёт "
            "в журнал"
        )
