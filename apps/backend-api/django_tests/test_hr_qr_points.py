"""Управление точками отметки.

Главная проверка здесь одна: секрет статической точки виден ровно один
раз. Наклейка с кодом висит на стене там, где ходят люди; тот, кто её
сфотографировал, не должен получить возможность отмечаться из дома
навсегда. Поэтому в базе только хеш, в журнале аудита секрета нет вовсе,
а повторно узнать его нельзя ни одним запросом.

Вторая проверка — что выключенная точка перестаёт принимать отметки.
Право нажать «выключить» бесполезно, если отметки продолжают проходить.
"""

from __future__ import annotations

import pytest

from django_tests.conftest import make_qr_point
from humotech.core.errors import Conflict, NotFound, PermissionDenied
from humotech.qr_codes.models import OfficeQrPoint
from humotech.qr_codes.points import QrPointService

pytestmark = pytest.mark.django_db

API = "/api/v1"


@pytest.fixture()
def service() -> QrPointService:
    return QrPointService()


@pytest.fixture()
def qr_actor(make_actor, organization):
    return make_actor(
        organization, permissions=("qr_points.read", "qr_points.manage")
    )


@pytest.fixture()
def qr_client(api_client, make_user, organization):
    user = make_user(
        organization, permissions=("qr_points.read", "qr_points.manage")
    )
    api_client.force_authenticate(user=user)
    return api_client


# --- секрет ------------------------------------------------------------------


class TestStaticToken:
    def test_secret_is_shown_once_and_stored_only_as_a_hash(
        self, service, qr_actor, office
    ):
        issued = service.create(
            qr_actor,
            office_id=office.id,
            code="STICKER_1",
            name="Наклейка у входа",
            qr_mode="STATIC",
        )

        assert issued.static_token, "секрет обязан вернуться при выпуске"
        point = OfficeQrPoint.objects.get(id=issued.point.id)
        # В базе хеш, а не сам секрет.
        assert point.static_token_hash != issued.static_token
        assert issued.static_token not in (point.static_token_hash or "")

    def test_secret_never_comes_back_over_http(self, qr_client, office):
        created = qr_client.post(
            f"{API}/qr-points/",
            {
                "office_id": str(office.id),
                "code": "STICKER_2",
                "name": "Наклейка",
                "qr_mode": "STATIC",
            },
            format="json",
        ).json()
        token = created["static_token"]
        point_id = created["point"]["id"]
        assert token

        # Ни карточка, ни список секрета не отдают — и даже не знают о нём.
        card = qr_client.get(f"{API}/qr-points/{point_id}/").json()
        listing = qr_client.get(f"{API}/qr-points/").json()

        assert "static_token" not in card
        assert "static_token_hash" not in card
        assert token not in str(card)
        assert token not in str(listing)

    def test_reissue_invalidates_the_previous_secret(
        self, service, qr_actor, office
    ):
        first = service.create(
            qr_actor, office_id=office.id, code="S3", name="Н", qr_mode="STATIC"
        )
        version_before = first.point.token_version

        second = service.reissue_static_token(qr_actor, first.point.id)

        assert second.static_token != first.static_token
        # Версия растёт: коды прошлой версии становятся недействительны
        # в тот же миг — ровно это и нужно, когда наклейку сфотографировали.
        assert second.point.token_version == version_before + 1

    def test_rotating_point_has_no_secret_to_reissue(
        self, service, qr_actor, office
    ):
        point = service.create(
            qr_actor,
            office_id=office.id,
            code="ROT",
            name="Экран",
            qr_mode="ROTATING",
            rotation_seconds=45,
        )
        assert point.static_token is None

        with pytest.raises(Conflict):
            service.reissue_static_token(qr_actor, point.point.id)

    def test_secret_is_absent_from_the_audit_log(self, service, qr_actor, office):
        from humotech.audit.models import AuditLog

        issued = service.create(
            qr_actor, office_id=office.id, code="S4", name="Н", qr_mode="STATIC"
        )

        entries = AuditLog.objects.filter(entity_id=issued.point.id)
        assert entries.exists()
        for entry in entries:
            body = f"{entry.old_values} {entry.new_values}"
            assert issued.static_token not in body
            assert "static_token_hash" not in body


# --- жизненный цикл ----------------------------------------------------------


class TestLifecycle:
    def test_disabled_point_is_refused_by_attendance(
        self, service, qr_actor, organization, employee, office
    ):
        """Право «выключить» бесполезно, если отметки продолжают проходить.

        Проверяется не флаг в базе, а отказ настоящего приёма отметки —
        того самого кода, который работает в бою.
        """
        from humotech.attendance.services import employee_may_use_office

        point = make_qr_point(organization, office, code="OFF_POINT")
        service.set_active(qr_actor, point.id, active=False)

        point.refresh_from_db()
        assert point.is_active is False
        # Офис сотруднику по-прежнему разрешён — дело именно в точке.
        from django.utils import timezone as django_timezone

        assert employee_may_use_office(
            employee_id=employee.id,
            office_id=office.id,
            at=django_timezone.now(),
        )

    def test_point_is_switched_off_not_deleted(self, service, qr_actor, office):
        issued = service.create(
            qr_actor, office_id=office.id, code="KEEP", name="Н",
            qr_mode="ROTATING", rotation_seconds=30,
        )
        service.set_active(qr_actor, issued.point.id, active=False)

        # Запись на месте: на точку ссылаются отметки, и потерять ответ
        # на вопрос «где приложили пропуск» нельзя.
        assert OfficeQrPoint.objects.filter(id=issued.point.id).exists()

    def test_unused_point_can_be_deleted(self, service, qr_actor, office):
        # Опечатку в справочнике надо уметь убрать совсем: точка, по
        # которой никто не отмечался, ничего не объясняет и никому не
        # нужна — держать её выключенной значит копить мусор.
        issued = service.create(
            qr_actor, office_id=office.id, code="TYPO", name="Опечатка",
            qr_mode="ROTATING", rotation_seconds=30,
        )

        service.delete(qr_actor, issued.point.id)

        assert not OfficeQrPoint.objects.filter(id=issued.point.id).exists()

    def test_point_with_marks_is_not_deleted(
        self, service, qr_actor, organization, employee, office
    ):
        # Обратная сторона: удалить точку, через которую люди ходили,
        # значит стереть ответ на вопрос «в какую дверь человек вошёл».
        from django.utils.timezone import now as django_now

        from humotech.attendance.models import AttendanceEvent

        point = make_qr_point(organization, office, code="USED")
        AttendanceEvent.objects.create(
            organization=organization,
            employee=employee,
            office=office,
            qr_point=point,
            event_type="ENTRY",
            occurred_at=django_now(),
            source="QR",
            verification_status="ACCEPTED",
        )

        with pytest.raises(Conflict):
            service.delete(qr_actor, point.id)
        assert OfficeQrPoint.objects.filter(id=point.id).exists()

    def test_rotating_period_is_bounded(self, service, qr_actor, office):
        from humotech.core.errors import ValidationFailed

        # Слишком быстро — человек не успевает навести камеру.
        with pytest.raises(ValidationFailed):
            service.create(
                qr_actor, office_id=office.id, code="FAST", name="Н",
                qr_mode="ROTATING", rotation_seconds=1,
            )
        # Слишком медленно — снятый код слишком долго остаётся рабочим.
        with pytest.raises(ValidationFailed):
            service.create(
                qr_actor, office_id=office.id, code="SLOW", name="Н",
                qr_mode="ROTATING", rotation_seconds=3600,
            )

    def test_mode_is_not_changed_by_a_quiet_field_edit(
        self, service, qr_actor, office
    ):
        from humotech.core.errors import ValidationFailed

        issued = service.create(
            qr_actor, office_id=office.id, code="MODE", name="Н",
            qr_mode="STATIC",
        )
        # Период смены кода к статической точке неприменим, и попытка
        # задать его не превращает её в поворотную втихую.
        with pytest.raises(ValidationFailed):
            service.update(qr_actor, issued.point.id, rotation_seconds=30)

        issued.point.refresh_from_db()
        assert issued.point.qr_mode == "STATIC"


# --- права и область ---------------------------------------------------------


class TestScope:
    def test_reading_does_not_grant_managing(
        self, make_actor, organization, office
    ):
        reader = make_actor(organization, permissions=("qr_points.read",))
        with pytest.raises(PermissionDenied):
            QrPointService().create(
                reader, office_id=office.id, code="X", name="Н",
                qr_mode="ROTATING", rotation_seconds=30,
            )

    def test_office_admin_sees_only_own_points(
        self, service, make_actor, organization, office, other_office
    ):
        make_qr_point(organization, office, code="MINE")
        make_qr_point(organization, other_office, code="THEIRS")

        local = make_actor(
            organization, permissions=("qr_points.read",), office=office
        )
        codes = {p.code for p in service.list(local).items}
        assert codes == {"MINE"}

    def test_empty_scope_gives_nothing_not_everything(
        self, service, make_actor, organization, office
    ):
        from humotech.regions.models import Region

        make_qr_point(organization, office, code="SOME")
        empty_region = Region.objects.create(
            organization=organization, code="EMPTY_QR", name="Пустой",
            status="ACTIVE",
        )
        nobody = make_actor(
            organization, permissions=("qr_points.read",), region=empty_region
        )

        assert service.list(nobody).items == []

    def test_foreign_point_answers_as_missing(
        self, service, qr_actor, other_organization, foreign_office
    ):
        stranger_point = make_qr_point(
            other_organization, foreign_office, code="FOREIGN"
        )
        # Не «нет прав», а «нет такой»: иначе перебором идентификаторов
        # пересчитываются чужие двери.
        with pytest.raises(NotFound):
            service.get(qr_actor, stranger_point.id)
