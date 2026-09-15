"""Фоновые выгрузки: заказ, сборка, скачивание и срок хранения.

Свойства, ради которых очередь заведена, и каждое проверяется отдельно.

**Файл принадлежит заказчику.** Он собран по ЕГО области видимости
в момент сборки. Коллега с другой областью, скачав его, получил бы
данные, закрытых для себя на экране, — и никакой отдельной проверки при
скачивании для этого не потребовалось бы.

**Права проверяются при сборке, а не при заказе.** Заказ вчерашний,
сборка сегодняшняя. Кадровик, которого перевели в один офис, не должен
получить файл по всей компании потому, что нажал кнопку до перевода.
И отказ по правам не повторяется: повтор его не вылечит, а попытки сожжёт.

**Два исполнителя не берут одно задание.** Проверяется настоящими
двумя соединениями: одной откатываемой транзакции теста для этого мало —
второе соединение не увидит незакоммиченных строк.

**Файл не лежит вечно.** Выгрузка кадровых данных без срока — это
утечка, отложенная во времени.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from django.db import connections, transaction
from django.utils import timezone

from humotech.accounts.models import User, UserRoleScope
from humotech.audit.models import AuditLog
from humotech.employees.models import Employee
from humotech.organizations.models import Organization
from humotech.reports import storage
from humotech.reports.models import ExportJob
from humotech.reports.service import purge_expired
from humotech.reports.worker import claim_job, process_job, reclaim_stale, run_once

pytestmark = pytest.mark.django_db

API = "/api/v1"
EXPORTER = ("reports.export", "employees.read", "attendance.read",
            "analytics.read", "schedules.read")


@pytest.fixture(autouse=True)
def exports_in_a_temporary_place(settings, tmp_path):
    """Файлы тестов не должны попадать в рабочий каталог выгрузок."""
    settings.EXPORTS = {**settings.EXPORTS, "PRIVATE_ROOT": str(tmp_path)}
    return tmp_path


@pytest.fixture()
def exporter(api_client, make_user, organization):
    user = make_user(organization, permissions=EXPORTER)
    api_client.force_authenticate(user=user)
    api_client.user = user
    return api_client


def order(client, **payload):
    body = {"kind": "employees", "fmt": "csv"}
    body.update(payload)
    return client.post(f"{API}/export-jobs/", body, format="json")


# --- заказ -------------------------------------------------------------------


class TestOrdering:
    def test_a_job_is_queued(self, exporter):
        response = order(exporter)

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "QUEUED"
        assert body["kind"] == "employees"
        assert body["file_name"] is None

    def test_the_filters_are_kept_with_the_job(self, exporter, office):
        """Без них на вопрос «что в этом файле» ответа нет.

        Файл живёт дольше экрана, с которого его заказали.
        """
        response = order(
            exporter, kind="sessions", date_from="2026-01-01",
            date_to="2026-03-31", office_id=str(office.id),
        )

        filters = response.json()["filters"]
        assert filters["date_from"] == "2026-01-01"
        assert filters["office_id"] == str(office.id)

    def test_bad_parameters_are_refused_at_once(self, exporter):
        """Иначе ошибка превращается в задание, которое умирает через минуту.

        Человек к тому моменту уже ушёл с экрана и увидит не отказ,
        а строку со статусом FAILED.
        """
        response = order(exporter, kind="nesuschestvuyet")

        assert response.status_code == 400
        assert ExportJob.objects.count() == 0

    def test_a_bad_date_is_refused_at_once(self, exporter):
        response = order(exporter, date_from="не дата")

        assert response.status_code == 400
        assert ExportJob.objects.count() == 0

    def test_ordering_needs_the_export_permission(
        self, api_client, make_user, organization
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("employees.read",))
        )

        assert order(api_client).status_code == 403

    def test_too_many_pending_orders_are_refused(self, exporter, settings):
        """Сто нажатий подряд — это не сто отчётов.

        Это один отчёт и девяносто девять лишних файлов на диске.
        """
        settings.EXPORTS = {**settings.EXPORTS, "MAX_PENDING_PER_USER": 2}

        assert order(exporter).status_code == 201
        assert order(exporter).status_code == 201
        assert order(exporter).status_code == 409

    def test_the_order_is_recorded(self, exporter):
        job_id = order(exporter).json()["id"]

        assert AuditLog.objects.filter(
            entity_type="export_jobs", entity_id=job_id,
            action="export.job.create",
        ).exists()


# --- сборка ------------------------------------------------------------------


class TestBuilding:
    def test_the_worker_builds_the_file(self, exporter, employee):
        job_id = order(exporter).json()["id"]

        assert run_once() is True

        job = ExportJob.objects.get(id=job_id)
        assert job.status == "SUCCEEDED"
        assert job.size_bytes > 0
        assert job.file_name.endswith(".csv")
        assert storage.exists(job.storage_key)

    def test_an_empty_queue_is_not_work(self, exporter):
        assert run_once() is False

    def test_the_file_carries_the_data(self, exporter, employee):
        job_id = order(exporter).json()["id"]
        run_once()

        job = ExportJob.objects.get(id=job_id)
        with storage.open_export(job.storage_key) as handle:
            content = handle.read().decode("utf-8")

        assert employee.employee_number in content
        assert "Автор выгрузки" in content

    def test_progress_is_reported(self, exporter, employee):
        job_id = order(exporter).json()["id"]
        run_once()

        job = ExportJob.objects.get(id=job_id)
        # «Идёт» без числа неотличимо от «умерла».
        assert job.progress_rows > 0

    def test_the_expiry_is_set_from_readiness(self, exporter, employee):
        job_id = order(exporter).json()["id"]
        run_once()

        job = ExportJob.objects.get(id=job_id)
        assert job.expires_at is not None
        assert job.expires_at > job.finished_at

    def test_the_storage_name_is_never_the_report_name(
        self, exporter, employee
    ):
        """Имя файла видно в списке каталога.

        «zarplata-direktora.xlsx» рассказывает лишнее ещё до открытия.
        """
        job_id = order(exporter).json()["id"]
        run_once()

        job = ExportJob.objects.get(id=job_id)
        assert job.storage_key == f"{job.id}.csv"
        assert job.kind not in job.storage_key


# --- отказы ------------------------------------------------------------------


class TestFailures:
    def test_a_rights_failure_is_not_retried(
        self, exporter, employee, organization
    ):
        """Права проверяются при сборке, и повтор их не вылечит.

        Автор потерял доступ — вторая попытка кончится ровно тем же,
        а попытки сгорят и настоящая причина потеряется.
        """
        job_id = order(exporter).json()["id"]
        UserRoleScope.objects.filter(user=exporter.user).delete()

        run_once()

        job = ExportJob.objects.get(id=job_id)
        assert job.status == "FAILED"
        assert job.attempts == 1
        assert job.next_attempt_at is None

    def test_the_failure_message_has_no_internals(
        self, exporter, employee
    ):
        """Ни путей, ни SQL, ни traceback: это текст для человека."""
        job_id = order(exporter).json()["id"]
        UserRoleScope.objects.filter(user=exporter.user).delete()

        run_once()

        message = ExportJob.objects.get(id=job_id).error_message
        assert message
        for leak in ("Traceback", "SELECT", "psycopg", "\\", "/humotech"):
            assert leak not in message

    def test_a_temporary_failure_comes_back_to_the_queue(
        self, exporter, employee, monkeypatch
    ):
        job_id = order(exporter).json()["id"]

        def boom(*args, **kwargs):
            raise OSError("диск отвалился")

        monkeypatch.setattr(
            "humotech.reports.worker.storage.save_chunks", boom
        )
        run_once()

        job = ExportJob.objects.get(id=job_id)
        assert job.status == "QUEUED"
        assert job.attempts == 1
        assert job.next_attempt_at is not None

    def test_attempts_run_out(self, exporter, employee, monkeypatch, settings):
        settings.EXPORTS = {**settings.EXPORTS, "MAX_ATTEMPTS": 2}
        job_id = order(exporter).json()["id"]

        def boom(*args, **kwargs):
            raise OSError("диск отвалился")

        monkeypatch.setattr(
            "humotech.reports.worker.storage.save_chunks", boom
        )
        for _ in range(2):
            ExportJob.objects.filter(id=job_id).update(next_attempt_at=None)
            run_once()

        job = ExportJob.objects.get(id=job_id)
        assert job.status == "FAILED"
        assert job.attempts == 2

    def test_a_stuck_job_comes_back(self, exporter, settings):
        """Процесс мог упасть между захватом и результатом.

        Блокировка строки снимется сама, статус RUNNING — нет: без этой
        уборки заказчик ждал бы файла, который никто не делает.
        """
        job_id = order(exporter).json()["id"]
        long_ago = timezone.now() - timedelta(hours=2)
        ExportJob.objects.filter(id=job_id).update(
            status="RUNNING", locked_at=long_ago
        )

        assert reclaim_stale() == 1

        job = ExportJob.objects.get(id=job_id)
        assert job.status == "QUEUED"
        assert job.locked_at is None

    def test_a_fresh_running_job_is_left_alone(self, exporter):
        job_id = order(exporter).json()["id"]
        ExportJob.objects.filter(id=job_id).update(
            status="RUNNING", locked_at=timezone.now()
        )

        assert reclaim_stale() == 0


# --- скачивание --------------------------------------------------------------


class TestDownload:
    def test_the_owner_gets_the_file(self, exporter, employee):
        job_id = order(exporter).json()["id"]
        run_once()

        response = exporter.get(f"{API}/export-jobs/{job_id}/download/")

        assert response.status_code == 200
        assert "attachment" in response["Content-Disposition"]
        body = b"".join(response.streaming_content)
        assert employee.employee_number.encode() in body

    def test_a_colleague_does_not_get_it(
        self, exporter, employee, api_client, make_user, organization
    ):
        """Файл собран по области видимости заказчика.

        Отдать его коллеге с другой областью — значит отдать данные,
        которых тот не видит на экране, и никакой отдельной дыры для
        этого не нужно.
        """
        job_id = order(exporter).json()["id"]
        run_once()
        api_client.force_authenticate(
            user=make_user(organization, permissions=EXPORTER)
        )

        response = api_client.get(f"{API}/export-jobs/{job_id}/download/")

        assert response.status_code == 404

    def test_an_anonymous_request_gets_nothing(self, exporter, employee):
        from rest_framework.test import APIClient

        job_id = order(exporter).json()["id"]
        run_once()

        response = APIClient().get(f"{API}/export-jobs/{job_id}/download/")

        assert response.status_code in (401, 403)
        assert not hasattr(response, "streaming_content")

    def test_audit_without_the_whole_organization_is_not_enough(
        self, exporter, employee, api_client, organization, office
    ):
        """Журнал открыт, но область — один офис: файл по всей организации не отдаётся."""
        from django_tests.conftest import create_actor

        job_id = order(exporter).json()["id"]
        run_once()
        user, _ = create_actor(
            organization, permissions=EXPORTER + ("audit.read",), office=office,
        )
        api_client.force_authenticate(user=user)

        response = api_client.get(f"{API}/export-jobs/{job_id}/download/")

        assert response.status_code == 404

    def test_an_auditor_of_the_whole_organization_gets_it(
        self, exporter, employee, api_client, make_user, organization
    ):
        job_id = order(exporter).json()["id"]
        run_once()
        auditor = make_user(organization, permissions=EXPORTER + ("audit.read",))
        api_client.force_authenticate(user=auditor)

        response = api_client.get(f"{API}/export-jobs/{job_id}/download/")

        assert response.status_code == 200
        b"".join(response.streaming_content)
        record = AuditLog.objects.get(
            action="export.job.download", entity_id=job_id, actor_user_id=auditor.id,
        )
        assert record.new_values["by_owner"] is False

    def test_another_organization_never_gets_it(self, exporter, employee, api_client, make_user):
        job_id = order(exporter).json()["id"]
        run_once()
        foreign = Organization.objects.create(
            code=f"ORG{uuid.uuid4().hex[:8]}", name="Чужая", default_timezone="Asia/Dushanbe",
            status="ACTIVE",
        )
        api_client.force_authenticate(
            user=make_user(foreign, permissions=EXPORTER + ("audit.read",))
        )

        response = api_client.get(f"{API}/export-jobs/{job_id}/download/")

        assert response.status_code == 404

    def test_download_needs_the_export_permission(
        self, exporter, employee, api_client, make_user, organization
    ):
        job_id = order(exporter).json()["id"]
        run_once()
        api_client.force_authenticate(
            user=make_user(organization, permissions=("audit.read", "employees.read"))
        )

        response = api_client.get(f"{API}/export-jobs/{job_id}/download/")

        assert response.status_code == 403

    def test_an_unfinished_job_has_nothing_to_download(self, exporter):
        job_id = order(exporter).json()["id"]

        response = exporter.get(f"{API}/export-jobs/{job_id}/download/")

        assert response.status_code == 409

    def test_an_expired_file_is_gone(self, exporter, employee):
        job_id = order(exporter).json()["id"]
        run_once()
        ExportJob.objects.filter(id=job_id).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        response = exporter.get(f"{API}/export-jobs/{job_id}/download/")

        assert response.status_code == 404

    def test_downloading_is_recorded(self, exporter, employee):
        """Файл уходит из системы и живёт дальше своей жизнью."""
        job_id = order(exporter).json()["id"]
        run_once()
        exporter.get(f"{API}/export-jobs/{job_id}/download/")

        assert AuditLog.objects.filter(
            entity_type="export_jobs", entity_id=job_id,
            action="export.job.download",
        ).exists()

    def test_the_path_on_disk_is_never_shown(self, exporter, employee):
        """Клиенту он не нужен, а показанный путь окажется в запросе."""
        job_id = order(exporter).json()["id"]
        run_once()

        body = exporter.get(f"{API}/export-jobs/{job_id}/").json()

        assert "storage_key" not in body


# --- отмена, повтор и срок ---------------------------------------------------


class TestLifecycle:
    def test_a_queued_job_is_cancelled(self, exporter):
        job_id = order(exporter).json()["id"]

        response = exporter.post(f"{API}/export-jobs/{job_id}/cancel/")

        assert response.status_code == 200
        assert ExportJob.objects.get(id=job_id).status == "CANCELLED"

    def test_a_running_job_is_not_cancelled(self, exporter):
        """Строку собирает исполнитель, и «отменено» рядом с ней — неправда."""
        job_id = order(exporter).json()["id"]
        ExportJob.objects.filter(id=job_id).update(
            status="RUNNING", locked_at=timezone.now()
        )

        assert exporter.post(
            f"{API}/export-jobs/{job_id}/cancel/"
        ).status_code == 409

    def test_a_failed_job_is_retried_from_scratch(self, exporter, employee):
        job_id = order(exporter).json()["id"]
        ExportJob.objects.filter(id=job_id).update(
            status="FAILED", attempts=3, error_message="что-то пошло не так"
        )

        response = exporter.post(f"{API}/export-jobs/{job_id}/retry/")

        assert response.status_code == 200
        job = ExportJob.objects.get(id=job_id)
        assert job.status == "QUEUED"
        assert job.attempts == 0
        assert job.error_message is None

    def test_retrying_removes_the_old_file(self, exporter, employee):
        job_id = order(exporter).json()["id"]
        run_once()
        key = ExportJob.objects.get(id=job_id).storage_key
        ExportJob.objects.filter(id=job_id).update(status="FAILED")

        exporter.post(f"{API}/export-jobs/{job_id}/retry/")

        assert not storage.exists(key)

    def test_a_succeeded_job_is_not_retried(self, exporter, employee):
        job_id = order(exporter).json()["id"]
        run_once()

        assert exporter.post(
            f"{API}/export-jobs/{job_id}/retry/"
        ).status_code == 409

    def test_expired_files_are_purged(self, exporter, employee):
        """Запись остаётся, файл уходит.

        По записи видно, что выгрузка была и кем заказана; утечкой был
        именно файл.
        """
        job_id = order(exporter).json()["id"]
        run_once()
        key = ExportJob.objects.get(id=job_id).storage_key
        ExportJob.objects.filter(id=job_id).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )

        assert purge_expired() == 1

        assert not storage.exists(key)
        job = ExportJob.objects.get(id=job_id)
        assert job.storage_key is None
        assert job.status == "SUCCEEDED"

    def test_a_fresh_file_survives_the_purge(self, exporter, employee):
        job_id = order(exporter).json()["id"]
        run_once()

        assert purge_expired() == 0
        assert storage.exists(ExportJob.objects.get(id=job_id).storage_key)


# --- изоляция ----------------------------------------------------------------


class TestIsolation:
    def test_a_foreign_job_looks_like_nothing(
        self, exporter, api_client, make_user, other_organization
    ):
        job_id = order(exporter).json()["id"]
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=EXPORTER)
        )

        assert api_client.get(
            f"{API}/export-jobs/{job_id}/"
        ).status_code == 404

    def test_the_list_shows_only_my_orders(
        self, exporter, api_client, make_user, organization
    ):
        """В списке видны фильтры выгрузки.

        По ним читается, кто чем интересовался, — поэтому чужие заказы
        по умолчанию не показываются.
        """
        order(exporter)
        api_client.force_authenticate(
            user=make_user(organization, permissions=EXPORTER)
        )

        body = api_client.get(f"{API}/export-jobs/").json()

        assert body["items"] == []


# --- гонка исполнителей ------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_workers_do_not_take_the_same_job():
    """Двум соединениям нужны настоящие коммиты.

    Одной откатываемой транзакции теста для этого мало: второе
    соединение попросту не увидит незакоммиченных строк.
    """
    organization = Organization.objects.create(
        code=f"EXP{uuid.uuid4().hex[:6]}", name="Очередь выгрузок",
        default_timezone="Asia/Dushanbe", status="ACTIVE",
    )
    employee = Employee.objects.create(
        organization=organization, employee_number=f"E-{uuid.uuid4().hex[:6]}",
        first_name="Тест", last_name="Выгрузкин",
        hire_date=date(2025, 1, 1), employment_status="ACTIVE",
    )
    user = User(
        organization=organization, employee=employee,
        email=f"exp-{uuid.uuid4().hex[:6]}@humotech.tj", status="ACTIVE",
    )
    user.set_password("не важно")
    user.save()
    job = ExportJob.objects.create(
        organization=organization, requested_by_user=user,
        kind="employees", fmt="csv", status="QUEUED", filters={},
    )

    try:
        second = connections.create_connection("default")
        second.set_autocommit(False)
        try:
            with transaction.atomic():
                first_take = claim_job()
                assert first_take is not None
                assert first_take.id == job.id

                # Второе соединение видит строку уже заблокированной и,
                # благодаря SKIP LOCKED, не ждёт её, а идёт дальше.
                with second.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM export_jobs "
                        "WHERE status = 'QUEUED' "
                        "FOR UPDATE SKIP LOCKED"
                    )
                    assert cursor.fetchall() == []
        finally:
            second.rollback()
            second.close()
    finally:
        ExportJob.objects.filter(id=job.id).delete()
        UserRoleScope.objects.filter(user=user).delete()
        User.objects.filter(id=user.id).delete()
        Employee.objects.filter(id=employee.id).delete()
        Organization.objects.filter(id=organization.id).delete()


@pytest.mark.django_db(transaction=True)
def test_a_claimed_job_is_visible_as_running_to_everyone():
    """Захват коммитится сразу, а не держится до конца сборки.

    Сборка отчёта идёт минуты, и открытая всё это время транзакция
    держала бы блокировку строки и снимок базы. Поэтому RUNNING виден
    другим процессам немедленно.
    """
    organization = Organization.objects.create(
        code=f"EXP{uuid.uuid4().hex[:6]}", name="Очередь выгрузок",
        default_timezone="Asia/Dushanbe", status="ACTIVE",
    )
    employee = Employee.objects.create(
        organization=organization, employee_number=f"E-{uuid.uuid4().hex[:6]}",
        first_name="Тест", last_name="Выгрузкин",
        hire_date=date(2025, 1, 1), employment_status="ACTIVE",
    )
    user = User(
        organization=organization, employee=employee,
        email=f"exp-{uuid.uuid4().hex[:6]}@humotech.tj", status="ACTIVE",
    )
    user.set_password("не важно")
    user.save()
    job = ExportJob.objects.create(
        organization=organization, requested_by_user=user,
        kind="employees", fmt="csv", status="QUEUED", filters={},
    )

    try:
        with transaction.atomic():
            claim_job()

        second = connections.create_connection("default")
        try:
            with second.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM export_jobs WHERE id = %s", [str(job.id)]
                )
                assert cursor.fetchone()[0] == "RUNNING"
        finally:
            second.close()
    finally:
        ExportJob.objects.filter(id=job.id).delete()
        UserRoleScope.objects.filter(user=user).delete()
        User.objects.filter(id=user.id).delete()
        Employee.objects.filter(id=employee.id).delete()
        Organization.objects.filter(id=organization.id).delete()


def test_process_job_is_separate_from_claiming(exporter, employee):
    """Захват и работа разделены намеренно.

    У индексации знаний они в одной транзакции, и там это верно: задача
    короткая. Здесь сборка длинная, и держать транзакцию всё это время
    значит держать блокировку и снимок базы.
    """
    order(exporter)
    with transaction.atomic():
        job = claim_job()

    assert job.status == "RUNNING"
    assert process_job(job) is True
    assert ExportJob.objects.get(id=job.id).status == "SUCCEEDED"
