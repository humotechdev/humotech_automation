"""Настройки организации: белый список, проверка значений, влияние на правила.

Главная опасность здесь — свободный key/value. `organization_settings` —
это JSONB, и `settings.manage` без белого списка означал бы «пиши любой
JSON под любым ключом»: опечатка создавала бы настройку, которую никто
не читает, а кривое значение всплывало бы через месяц при расчёте.
Поэтому половина тестов ниже — про то, что отвергается.

Вторая половина — про то, что настройка что-то меняет. Настройка, на
которую никто не смотрит, ничем не лучше опечатки.
"""

from __future__ import annotations

import pytest

from humotech.absences.policy import policy_for
from humotech.core.errors import NotFound, PermissionDenied, ValidationFailed
from humotech.organizations.models import Organization
from humotech.absences.policy import SETTING_KEY as POLICY_KEY
from humotech.organizations.settings_service import OrganizationSettingsService

API = "/api/v1"


@pytest.fixture()
def service() -> OrganizationSettingsService:
    return OrganizationSettingsService()


@pytest.fixture()
def settings_actor(make_actor, organization):
    return make_actor(organization, permissions=("settings.manage",))


# --- права -------------------------------------------------------------------


@pytest.mark.django_db
class TestAccess:
    def test_reading_requires_settings_manage(self, service, nobody_actor):
        with pytest.raises(PermissionDenied):
            service.all(nobody_actor)

    def test_hr_admin_without_settings_manage_is_refused(
        self, service, make_actor, organization
    ):
        """Кадровый администратор настройками не управляет.

        Это не упущение каталога, а решение: правила больничного меняют
        то, по чему живёт вся компания, и право на это отдельное.
        """
        hr = make_actor(
            organization,
            permissions=("employees.manage", "absences.approve", "audit.read"),
        )
        with pytest.raises(PermissionDenied):
            service.all(hr)

    def test_writing_requires_the_same_permission(self, service, readonly_actor):
        with pytest.raises(PermissionDenied):
            service.update(readonly_actor, POLICY_KEY, {"document_required": True})


# --- белый список ------------------------------------------------------------


@pytest.mark.django_db
class TestWhitelist:
    def test_unknown_key_is_not_written(self, service, settings_actor):
        with pytest.raises(NotFound):
            service.update(settings_actor, "payroll.rules", {"tax": 13})

    def test_unknown_field_is_refused_not_swallowed(self, service, settings_actor):
        """Опечатка в имени поля — ошибка, а не тихая запись.

        `policy.py` разбирает мягко и намеренно: значение в JSONB могло
        появиться откуда угодно. Но то, что приходит из CRM, — другое:
        молча проглоченная опечатка выглядит записанной и не работает.
        """
        with pytest.raises(ValidationFailed) as exc:
            service.update(
                settings_actor, POLICY_KEY, {"documnet_required": True}
            )
        assert "documnet_required" in str(exc.value.details)

    def test_boolean_field_rejects_a_string(self, service, settings_actor):
        with pytest.raises(ValidationFailed):
            service.update(
                settings_actor, POLICY_KEY, {"document_required": "yes"}
            )

    def test_day_field_rejects_true(self, service, settings_actor):
        """`bool` — подкласс `int`, и без явной проверки `true` стало бы «1»."""
        with pytest.raises(ValidationFailed):
            service.update(
                settings_actor, POLICY_KEY, {"document_required_from_day": True}
            )

    def test_day_field_rejects_absurd_values(self, service, settings_actor):
        for value in (-1, 400):
            with pytest.raises(ValidationFailed):
                service.update(
                    settings_actor, POLICY_KEY,
                    {"backdating_days_allowed": value},
                )

    def test_document_types_must_be_a_non_empty_list(self, service, settings_actor):
        for value in ([], "application/pdf", [""], [1]):
            with pytest.raises(ValidationFailed):
                service.update(
                    settings_actor, POLICY_KEY,
                    {"allowed_document_types": value},
                )

    def test_bad_timezone_is_refused(self, service, settings_actor):
        with pytest.raises(ValidationFailed):
            service.update(
                settings_actor, "organization.defaults",
                {"default_timezone": "Mars/Olympus"},
            )


# --- запись и чтение ---------------------------------------------------------


@pytest.mark.django_db
class TestRoundTrip:
    def test_partial_update_keeps_the_rest(self, service, settings_actor):
        service.update(
            settings_actor, POLICY_KEY, {"document_required": True}
        )
        service.update(
            settings_actor, POLICY_KEY, {"extensions_allowed": False}
        )
        values = service.get(settings_actor, POLICY_KEY)["values"]
        assert values["document_required"] is True
        assert values["extensions_allowed"] is False

    def test_defaults_are_reported_next_to_values(self, service, settings_actor):
        section = service.get(settings_actor, POLICY_KEY)
        assert section["defaults"]["require_hr_approval"] is True
        assert set(section["help"]) >= set(section["values"])

    def test_timezone_goes_to_its_own_column(
        self, service, settings_actor, organization
    ):
        service.update(
            settings_actor, "organization.defaults",
            {"default_timezone": "Asia/Dushanbe"},
        )
        organization.refresh_from_db()
        assert organization.default_timezone == "Asia/Dushanbe"

    def test_settings_owned_elsewhere_are_pointers_not_copies(
        self, service, settings_actor
    ):
        """Допуск опоздания и радиус геозоны здесь не хранятся.

        Копия рядом с оригиналом рано или поздно с ним разъезжается,
        и тогда непонятно, какая из двух работает.
        """
        owners = {row["name"]: row["owner"] for row in service.all(settings_actor)["elsewhere"]}
        assert owners["late_grace_minutes"] == "work_schedules.late_grace_minutes"
        assert owners["geofence_radius_m"] == "offices.geofence_radius_m"
        assert "values" not in owners

    def test_every_change_is_audited(self, service, settings_actor):
        from humotech.audit.models import AuditLog

        service.update(settings_actor, POLICY_KEY, {"document_required": True})
        record = AuditLog.objects.get(action="organization_setting.update")
        assert record.old_values["document_required"] is False
        assert record.new_values["document_required"] is True

    def test_foreign_organization_is_untouched(
        self, service, settings_actor, other_organization
    ):
        service.update(
            settings_actor, "organization.defaults",
            {"default_timezone": "Asia/Dushanbe"},
        )
        other = Organization.objects.get(id=other_organization.id)
        assert other.default_timezone != "Asia/Dushanbe"


# --- настройка обязана что-то менять -----------------------------------------


@pytest.mark.django_db
class TestSettingsChangeBehaviour:
    """Настройка, на которую никто не смотрит, ничем не лучше опечатки."""

    def test_document_from_day_gates_short_absences(
        self, service, settings_actor, organization
    ):
        service.update(
            settings_actor, POLICY_KEY,
            {"document_required": True, "document_required_from_day": 4},
        )
        policy = policy_for(organization.id)
        assert policy.document_required is True
        assert policy.document_required_from_day == 4

    def test_backdating_zero_means_no_limit(
        self, service, settings_actor, organization
    ):
        """Ноль — «без ограничения», а не «ничего задним числом».

        Больничный по своей природе оформляется после болезни: закрытое
        умолчание сломало бы главный сценарий продукта.
        """
        assert policy_for(organization.id).backdating_days_allowed == 0

    def test_stored_value_reaches_the_policy_reader(
        self, service, settings_actor, organization
    ):
        """Записанное значение обязано доехать до того, кто его читает.

        Само правило проверяется поведением в `test_absences.py`: там
        есть контекст сотрудника, и там видно, что заявка отклоняется.
        """
        service.update(
            settings_actor, POLICY_KEY, {"backdating_days_allowed": 3}
        )
        assert policy_for(organization.id).backdating_days_allowed == 3


# --- HTTP --------------------------------------------------------------------


@pytest.fixture()
def settings_client(api_client, make_user, organization):
    user = make_user(organization, permissions=("settings.manage",))
    api_client.force_authenticate(user=user)
    return api_client


@pytest.mark.django_db
class TestHttp:
    def test_list_returns_both_groups(self, settings_client):
        response = settings_client.get(f"{API}/settings")
        assert response.status_code == 200
        keys = {row["key"] for row in response.json()["items"]}
        assert keys == {POLICY_KEY, "organization.defaults"}

    def test_patch_updates_and_returns_the_group(self, settings_client):
        response = settings_client.patch(
            f"{API}/settings/{POLICY_KEY}",
            {"values": {"document_required": True}},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["values"]["document_required"] is True

    def test_unknown_key_is_404(self, settings_client):
        response = settings_client.patch(
            f"{API}/settings/payroll.rules", {"values": {}}, format="json"
        )
        assert response.status_code == 404

    def test_unknown_field_is_400(self, settings_client):
        response = settings_client.patch(
            f"{API}/settings/{POLICY_KEY}",
            {"values": {"nonsense": 1}},
            format="json",
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    def test_without_permission_it_is_403(self, api_client, make_user, organization):
        user = make_user(organization, permissions=("employees.read",))
        api_client.force_authenticate(user=user)
        assert api_client.get(f"{API}/settings").status_code == 403

    def test_anonymous_gets_nothing(self, api_client):
        assert api_client.get(f"{API}/settings").status_code in (401, 403)
