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


# --- группа «Организация» ----------------------------------------------------


@pytest.mark.django_db
class TestOrganizationGroup:
    """Название, описание и пояс показа — одна группа и одна транзакция.

    Разными запросами их сохранять нельзя: половина применённой группы —
    это состояние, которого человек не выбирал.
    """

    KEY = "organization.defaults"

    def section(self, service, actor) -> dict:
        return next(
            item for item in service.all(actor)["items"] if item["key"] == self.KEY
        )

    def test_group_carries_name_description_and_both_zones(
        self, service, settings_actor, organization
    ):
        section = self.section(service, settings_actor)
        assert set(section["values"]) == {
            "name", "description", "crm_timezone", "default_timezone",
        }
        assert section["values"]["name"] == organization.name
        # Что действует НА САМОМ ДЕЛЕ — отдельно от того, что записано:
        # пустой пояс CRM означает не «UTC», а «как у первого офиса».
        assert section["effective"]["timezone"]
        assert section["updated_at"] is not None

    def test_whole_group_is_saved_at_once_and_audited_once(
        self, service, settings_actor, organization
    ):
        from humotech.audit.models import AuditLog

        service.update(
            settings_actor,
            self.KEY,
            {
                "name": "  Новое имя  ",
                "description": "Одна строка",
                "crm_timezone": "Europe/Moscow",
            },
        )
        organization.refresh_from_db()
        assert organization.name == "Новое имя"  # пробелы обрезаны сервером

        values = self.section(service, settings_actor)["values"]
        assert values["description"] == "Одна строка"
        assert values["crm_timezone"] == "Europe/Moscow"

        # Три поля — одно нажатие кнопки — одна запись в журнале.
        rows = AuditLog.objects.filter(
            organization_id=settings_actor.organization_id,
            action="organization.defaults",
        )
        assert rows.count() == 1
        row = rows.first()
        assert row.old_values["description"] is None
        assert row.new_values["crm_timezone"] == "Europe/Moscow"

    def test_crm_timezone_changes_what_the_crm_shows(
        self, service, settings_actor, organization, office
    ):
        """Настройка, на которую никто не смотрит, ничем не лучше опечатки.

        Здесь проверяется именно применение: пояс показа читает `/auth/me`
        и через него — весь интерфейс.
        """
        from humotech.core.timeframes import organization_zone

        assert str(organization_zone(organization.id)) == office.timezone

        service.update(
            settings_actor, self.KEY, {"crm_timezone": "Europe/Moscow"}
        )
        assert str(organization_zone(organization.id)) == "Europe/Moscow"

        # Пусто — законный выбор: он возвращает прежнее правило, а не UTC.
        service.update(settings_actor, self.KEY, {"crm_timezone": None})
        assert str(organization_zone(organization.id)) == office.timezone

    def test_display_zone_does_not_touch_office_or_schedules(
        self, service, settings_actor, organization, office
    ):
        """Смена пояса ПОКАЗА не переписывает то, по чему считают работу."""
        from humotech.core.timeframes import office_zone

        before = office.timezone
        service.update(
            settings_actor, self.KEY, {"crm_timezone": "Pacific/Auckland"}
        )
        office.refresh_from_db()
        assert office.timezone == before
        assert str(office_zone(office)) == before

    def test_unknown_field_of_the_group_is_refused(self, service, settings_actor):
        with pytest.raises(ValidationFailed) as exc:
            service.update(settings_actor, self.KEY, {"logo_url": "http://x"})
        assert exc.value.details["unknown"] == ["logo_url"]

    def test_empty_name_is_refused(self, service, settings_actor):
        with pytest.raises(ValidationFailed):
            service.update(settings_actor, self.KEY, {"name": "   "})


# --- защита от незаметной перезаписи -----------------------------------------


@pytest.mark.django_db
class TestConcurrentEditing:
    """Второй администратор не должен молча отменить работу первого."""

    KEY = "organization.defaults"

    def edition(self, service, actor):
        return next(
            item for item in service.all(actor)["items"] if item["key"] == self.KEY
        )["updated_at"]

    def test_stale_edition_is_refused(self, service, settings_actor):
        from humotech.core.errors import Conflict

        seen = self.edition(service, settings_actor)
        service.update(settings_actor, self.KEY, {"description": "первый"})

        with pytest.raises(Conflict):
            service.update(
                settings_actor,
                self.KEY,
                {"description": "второй"},
                expected_updated_at=seen,
                check_expected=True,
            )

    def test_fresh_edition_goes_through(self, service, settings_actor):
        service.update(settings_actor, self.KEY, {"description": "первый"})
        service.update(
            settings_actor,
            self.KEY,
            {"description": "второй"},
            expected_updated_at=self.edition(service, settings_actor),
            check_expected=True,
        )
        section = next(
            item for item in service.all(settings_actor)["items"]
            if item["key"] == self.KEY
        )
        assert section["values"]["description"] == "второй"

    def test_edition_follows_both_sources_of_the_group(
        self, service, settings_actor
    ):
        """Правка соседа в другой половине группы тоже считается.

        Название лежит в колонке, описание — в JSONB. Если брать редакцию
        только одного источника, правка второго осталась бы незамеченной.
        """
        from humotech.core.errors import Conflict

        seen = self.edition(service, settings_actor)
        service.update(settings_actor, self.KEY, {"name": "Сосед переименовал"})

        with pytest.raises(Conflict):
            service.update(
                settings_actor,
                self.KEY,
                {"description": "а я про описание"},
                expected_updated_at=seen,
                check_expected=True,
            )


# --- вложения ----------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentTypes:
    def test_format_the_storage_cannot_verify_is_refused(
        self, service, settings_actor
    ):
        """Иначе настройка выглядит применённой, а загрузка отвергает файл."""
        with pytest.raises(ValidationFailed) as exc:
            service.update(
                settings_actor,
                POLICY_KEY,
                {"allowed_document_types": ["application/msword"]},
            )
        assert exc.value.details["unsupported"] == ["application/msword"]

    def test_storable_types_are_reported_next_to_the_choice(
        self, service, settings_actor
    ):
        from humotech.files.storage import EXTENSIONS

        section = next(
            item for item in service.all(settings_actor)["items"]
            if item["key"] == POLICY_KEY
        )
        assert section["effective"]["storable_document_types"] == sorted(EXTENSIONS)


# --- подключения -------------------------------------------------------------


@pytest.mark.django_db
class TestIntegrations:
    def test_requires_settings_manage(self, service, nobody_actor):
        with pytest.raises(PermissionDenied):
            service.integrations(nobody_actor)

    def test_no_secret_ever_reaches_the_answer(
        self, service, settings_actor, settings
    ):
        """Ни токена, ни его начала.

        По обрывку токен не восстановить, но и пользы от него никакой,
        а в журнале браузера он останется.
        """
        import json

        secret = "1234567890:AAHsecret-value-for-the-test"
        telegram = dict(settings.TELEGRAM)
        telegram["BOT_TOKEN"] = secret
        settings.TELEGRAM = telegram

        body = json.dumps(service.integrations(settings_actor), default=str)
        assert secret not in body
        for length in (8, 10, 12):
            assert secret[:length] not in body

    def test_configured_is_not_the_same_as_working(
        self, service, settings_actor, settings
    ):
        telegram = dict(settings.TELEGRAM)
        telegram["BOT_TOKEN"] = "1234567890:AAH-token"
        settings.TELEGRAM = telegram

        bot = next(
            item for item in service.integrations(settings_actor)["items"]
            if item["key"] == "telegram_bot"
        )
        assert bot["configured"] is True
        # Отправок не было — значит «не удалось проверить», а не «работает».
        assert bot["state"] == "unknown"

    def test_disabled_ai_is_called_disabled(self, service, settings_actor):
        from humotech.ai_assistant.config import ai_settings

        assert ai_settings.ai_assistant_enabled is False
        ai = next(
            item for item in service.integrations(settings_actor)["items"]
            if item["key"] == "ai_assistant"
        )
        # «Выключено» — принятое решение, а не сбой проверки.
        assert ai["state"] == "off"

    def test_viewing_sends_nothing(self, service, settings_actor):
        """Открытая страница настроек не ставит ничего в очередь."""
        from humotech.notifications.models import Notification

        before = Notification.objects.count()
        service.integrations(settings_actor)
        assert Notification.objects.count() == before
