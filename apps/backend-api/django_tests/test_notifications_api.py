"""Уведомления через HTTP: кто видит, кто повторяет и что попадает в журнал.

Очередь уже проверена в `test_notifications.py` со стороны отправщика.
Здесь проверяется другое: сторона кадровика. Свойства, ради которых этот
слой вообще существует:

  1. видно ровно то, что и должно быть видно — уведомление сотрудника
     чужого офиса не показывается и не открывается по прямой ссылке;
  2. повтор не превращается во второе сообщение человеку: из SENT его
     нет вовсе, из RUNNING — тоже, там строку держит отправщик;
  3. каждое управляющее действие остаётся в журнале. «Почему человеку
     пришло это второй раз» обязано иметь ответ.

Ни один тест не обращается в Telegram: сообщения здесь только строки
в таблице, отправляет их отдельный процесс, которого в тестах нет.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.utils import timezone

from humotech.audit.models import AuditLog
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications.models import Notification
from humotech.notifications.service import MAX_BULK_RETRY

pytestmark = pytest.mark.django_db

API = "/api/v1"
READ = ("notifications.read", "employees.read")
MANAGE = ("notifications.read", "notifications.manage", "employees.read")


def make_notification(
    organization,
    employee,
    *,
    status: str = "PENDING",
    channel: str = "TELEGRAM",
    notification_type: str = "absence.request.approved",
    attempts: int = 0,
    error: str | None = None,
    **kwargs,
) -> Notification:
    return Notification.objects.create(
        organization=organization,
        employee=employee,
        channel=channel,
        notification_type=notification_type,
        title="Заявка рассмотрена",
        body="Ваша заявка подтверждена",
        status=status,
        attempts=attempts,
        error_message=error,
        **kwargs,
    )


@pytest.fixture()
def other_employee(db, organization, other_office) -> Employee:
    """Сотрудник соседнего офиса — на нём проверяется область видимости."""
    employee = Employee.objects.create(
        organization=organization,
        employee_number="EMP-0002",
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2024, 2, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization,
        employee=employee,
        office=other_office,
        employment_type="FULL_TIME",
        work_mode="ONSITE",
        is_primary=True,
        valid_from=date(2024, 2, 1),
    )
    return employee


@pytest.fixture()
def manager(api_client, make_user, organization):
    """Кадровик с полными правами на уведомления по всей организации."""
    api_client.force_authenticate(
        user=make_user(organization, permissions=MANAGE)
    )
    return api_client


# --- права -------------------------------------------------------------------


class TestPermissions:
    def test_reading_needs_the_read_permission(
        self, api_client, make_user, organization
    ):
        api_client.force_authenticate(
            user=make_user(organization, permissions=("employees.read",))
        )

        assert api_client.get(f"{API}/notifications/").status_code == 403

    def test_retry_needs_more_than_reading(
        self, api_client, make_user, organization, employee
    ):
        """Смотреть очередь и трогать её — разные права.

        Иначе любой, кому показали журнал доставки, мог бы разослать
        людям повторные сообщения.
        """
        row = make_notification(organization, employee, status="FAILED")
        api_client.force_authenticate(
            user=make_user(organization, permissions=READ)
        )

        assert (
            api_client.post(f"{API}/notifications/{row.id}/retry/").status_code
            == 403
        )
        assert (
            api_client.post(f"{API}/notifications/{row.id}/cancel/").status_code
            == 403
        )
        assert (
            api_client.post(f"{API}/notifications/retry-failed/").status_code
            == 403
        )

    def test_anonymous_gets_nothing(self, api_client):
        assert api_client.get(f"{API}/notifications/").status_code in (401, 403)


# --- область видимости -------------------------------------------------------


class TestVisibility:
    def test_foreign_organization_looks_like_nothing(
        self, api_client, make_user, other_organization, organization, employee
    ):
        """Чужая организация отвечает как несуществующая запись.

        Иначе перебором идентификаторов можно пересчитать, сколько
        уведомлений у соседей.
        """
        row = make_notification(organization, employee)
        api_client.force_authenticate(
            user=make_user(other_organization, permissions=MANAGE)
        )

        assert (
            api_client.get(f"{API}/notifications/{row.id}/").status_code == 404
        )

    def test_other_office_is_not_listed(
        self, api_client, make_user, organization, office, employee,
        other_employee,
    ):
        mine = make_notification(organization, employee)
        make_notification(organization, other_employee)
        api_client.force_authenticate(
            user=make_user(organization, permissions=READ, office=office)
        )

        body = api_client.get(f"{API}/notifications/").json()

        assert [item["id"] for item in body["items"]] == [str(mine.id)]

    def test_other_office_is_not_reachable_by_link(
        self, api_client, make_user, organization, office, other_employee
    ):
        """Пропасть из списка мало: прямая ссылка тоже обязана отказать."""
        row = make_notification(organization, other_employee)
        api_client.force_authenticate(
            user=make_user(organization, permissions=MANAGE, office=office)
        )

        assert (
            api_client.get(f"{API}/notifications/{row.id}/").status_code == 403
        )

    def test_filter_by_foreign_employee_is_refused_not_emptied(
        self, api_client, make_user, organization, office, other_employee
    ):
        """Отказ, а не пустой список.

        Пустой список неотличим от «уведомлений нет» и молча скрывает
        нехватку прав — кадровик решит, что сообщений не было.
        """
        api_client.force_authenticate(
            user=make_user(organization, permissions=READ, office=office)
        )

        response = api_client.get(
            f"{API}/notifications/", {"employee_id": str(other_employee.id)}
        )

        assert response.status_code == 403


# --- повтор ------------------------------------------------------------------


class TestRetry:
    def test_failed_goes_back_to_the_queue(self, manager, organization, employee):
        row = make_notification(
            organization, employee, status="FAILED", attempts=5,
            error="telegram: bad request",
        )

        response = manager.post(f"{API}/notifications/{row.id}/retry/")

        assert response.status_code == 200
        row.refresh_from_db()
        assert row.status == "PENDING"
        assert row.error_message is None
        assert row.next_attempt_at is not None

    def test_retry_resets_the_attempt_counter(
        self, manager, organization, employee, settings
    ):
        """Иначе повтор давал бы ровно одну попытку и снова FAILED.

        Автоматический предел защищает от молчаливого повторения вечно.
        Ручной повтор — это человек, который уже посмотрел причину и
        устранил её, поэтому счётчик начинается заново.
        """
        row = make_notification(
            organization, employee, status="FAILED",
            attempts=settings.NOTIFICATIONS["MAX_ATTEMPTS"],
        )

        manager.post(f"{API}/notifications/{row.id}/retry/")

        row.refresh_from_db()
        assert row.attempts == 0

    def test_cancelled_can_be_retried(self, manager, organization, employee):
        """Отменённое повторяется: обычно это «не было привязки Telegram».

        Очередь сама переводит такие строки в CANCELLED. Человек
        привязал аккаунт — сообщение должно уйти.
        """
        row = make_notification(
            organization, employee, status="CANCELLED", error="not_linked"
        )

        assert (
            manager.post(f"{API}/notifications/{row.id}/retry/").status_code
            == 200
        )

    def test_sent_is_not_retried(self, manager, organization, employee):
        """Сообщение уже в чате: «повторить» означало бы прислать второе."""
        row = make_notification(
            organization, employee, status="SENT", sent_at=timezone.now()
        )

        response = manager.post(f"{API}/notifications/{row.id}/retry/")

        assert response.status_code == 409
        row.refresh_from_db()
        assert row.status == "SENT"

    def test_running_is_not_retried(self, manager, organization, employee):
        row = make_notification(
            organization, employee, status="RUNNING", locked_at=timezone.now()
        )

        assert (
            manager.post(f"{API}/notifications/{row.id}/retry/").status_code
            == 409
        )


# --- снятие с отправки -------------------------------------------------------


class TestCancel:
    def test_pending_is_cancelled(self, manager, organization, employee):
        row = make_notification(organization, employee, status="PENDING")

        response = manager.post(f"{API}/notifications/{row.id}/cancel/")

        assert response.status_code == 200
        row.refresh_from_db()
        assert row.status == "CANCELLED"
        assert row.next_attempt_at is None

    def test_running_is_not_cancelled(self, manager, organization, employee):
        """Строку держит отправщик, и она прямо сейчас уходит в Telegram.

        Записать рядом «отменено» значило бы соврать в журнале: сообщение
        человек получит. Зависшую строку вернёт в очередь `reclaim_stale`.
        """
        row = make_notification(
            organization, employee, status="RUNNING", locked_at=timezone.now()
        )

        response = manager.post(f"{API}/notifications/{row.id}/cancel/")

        assert response.status_code == 409
        row.refresh_from_db()
        assert row.status == "RUNNING"

    def test_sent_is_not_cancelled(self, manager, organization, employee):
        row = make_notification(
            organization, employee, status="SENT", sent_at=timezone.now()
        )

        assert (
            manager.post(f"{API}/notifications/{row.id}/cancel/").status_code
            == 409
        )


# --- массовый повтор ---------------------------------------------------------


class TestRetryFailed:
    def test_only_failed_is_requeued(self, manager, organization, employee):
        """Отмена — решение человека, и массовый повтор его не отменяет."""
        failed = make_notification(organization, employee, status="FAILED")
        cancelled = make_notification(
            organization, employee, status="CANCELLED"
        )
        sent = make_notification(
            organization, employee, status="SENT", sent_at=timezone.now()
        )

        response = manager.post(f"{API}/notifications/retry-failed/")

        assert response.json() == {"requeued": 1}
        failed.refresh_from_db()
        cancelled.refresh_from_db()
        sent.refresh_from_db()
        assert failed.status == "PENDING"
        assert cancelled.status == "CANCELLED"
        assert sent.status == "SENT"

    def test_type_prefix_narrows_the_batch(
        self, manager, organization, employee
    ):
        absence = make_notification(
            organization, employee, status="FAILED",
            notification_type="absence.request.approved",
        )
        link = make_notification(
            organization, employee, status="FAILED",
            notification_type="telegram.link.confirmed",
        )

        response = manager.post(
            f"{API}/notifications/retry-failed/",
            {"notification_type": "absence."},
            format="json",
        )

        assert response.json() == {"requeued": 1}
        absence.refresh_from_db()
        link.refresh_from_db()
        assert absence.status == "PENDING"
        assert link.status == "FAILED"

    def test_the_batch_stays_inside_the_scope(
        self, api_client, make_user, organization, office, employee,
        other_employee,
    ):
        """«Повторить всё» повторяет всё СВОЁ, а не всё вообще."""
        mine = make_notification(organization, employee, status="FAILED")
        theirs = make_notification(
            organization, other_employee, status="FAILED"
        )
        api_client.force_authenticate(
            user=make_user(organization, permissions=MANAGE, office=office)
        )

        response = api_client.post(f"{API}/notifications/retry-failed/")

        assert response.json() == {"requeued": 1}
        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert mine.status == "PENDING"
        assert theirs.status == "FAILED"

    def test_oversized_batch_is_refused(
        self, manager, organization, employee, monkeypatch
    ):
        """Потолок — защита от нажатия, смысла которого не представляют.

        Проверяется настоящим отказом, а не арифметикой: потолок
        опускается до двух, и три упавших уведомления его перебивают.
        """
        monkeypatch.setattr(
            "humotech.notifications.service.MAX_BULK_RETRY", 2
        )
        rows = [
            make_notification(organization, employee, status="FAILED")
            for _ in range(3)
        ]

        response = manager.post(f"{API}/notifications/retry-failed/")

        assert response.status_code == 409
        for row in rows:
            row.refresh_from_db()
            assert row.status == "FAILED"

    def test_the_ceiling_is_a_real_number(self):
        assert MAX_BULK_RETRY > 0


# --- журнал ------------------------------------------------------------------


class TestAudit:
    def _actions(self, entity_id) -> list[str]:
        return list(
            AuditLog.objects.filter(
                entity_type="notifications", entity_id=entity_id
            ).values_list("action", flat=True)
        )

    def test_retry_is_recorded(self, manager, organization, employee):
        row = make_notification(organization, employee, status="FAILED")

        manager.post(f"{API}/notifications/{row.id}/retry/")

        assert self._actions(row.id) == ["notification.retry"]

    def test_cancel_is_recorded_with_the_previous_state(
        self, manager, organization, employee
    ):
        row = make_notification(organization, employee, status="PENDING")

        manager.post(f"{API}/notifications/{row.id}/cancel/")

        entry = AuditLog.objects.get(
            entity_type="notifications", entity_id=row.id
        )
        assert entry.action == "notification.cancel"
        assert entry.old_values["status"] == "PENDING"
        assert entry.new_values["status"] == "CANCELLED"

    def test_bulk_retry_writes_a_row_per_notification(
        self, manager, organization, employee
    ):
        """Разбирают потом по уведомлению, а не по нажатию кнопки."""
        rows = [
            make_notification(organization, employee, status="FAILED")
            for _ in range(3)
        ]

        manager.post(f"{API}/notifications/retry-failed/")

        for row in rows:
            assert self._actions(row.id) == ["notification.retry"]

    def test_refused_action_leaves_no_trace(
        self, manager, organization, employee
    ):
        """Журнал изменений, а не журнал попыток.

        Запись о том, чего не произошло, делает журнал бесполезным:
        по нему перестаёт быть видно, что на самом деле менялось.
        """
        row = make_notification(
            organization, employee, status="SENT", sent_at=timezone.now()
        )

        manager.post(f"{API}/notifications/{row.id}/retry/")

        assert self._actions(row.id) == []


# --- отбор и постраничный вывод ----------------------------------------------


class TestListing:
    def test_status_filter(self, manager, organization, employee):
        make_notification(organization, employee, status="PENDING")
        failed = make_notification(organization, employee, status="FAILED")

        body = manager.get(f"{API}/notifications/", {"status": "FAILED"}).json()

        assert [item["id"] for item in body["items"]] == [str(failed.id)]

    def test_unknown_status_is_refused(self, manager):
        """Опечатка в фильтре обязана быть видна.

        Молча вернуть пустой список — значит показать кадровику «ничего
        не найдено» там, где на самом деле неверный запрос.
        """
        response = manager.get(f"{API}/notifications/", {"status": "SENDING"})

        assert response.status_code == 400

    def test_channel_filter(self, manager, organization, employee):
        telegram = make_notification(organization, employee)
        make_notification(organization, employee, channel="EMAIL")

        body = manager.get(
            f"{API}/notifications/", {"channel": "TELEGRAM"}
        ).json()

        assert [item["id"] for item in body["items"]] == [str(telegram.id)]

    def test_pagination_walks_the_whole_list(
        self, manager, organization, employee
    ):
        created = {
            str(make_notification(organization, employee).id)
            for _ in range(5)
        }

        seen: set[str] = set()
        cursor = None
        for _ in range(5):
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            body = manager.get(f"{API}/notifications/", params).json()
            seen.update(item["id"] for item in body["items"])
            cursor = body["next_cursor"]
            if not cursor:
                break

        assert seen == created

    def test_the_card_carries_who_it_is_for(
        self, manager, organization, employee
    ):
        row = make_notification(organization, employee)

        body = manager.get(f"{API}/notifications/{row.id}/").json()

        assert body["employee"]["id"] == str(employee.id)
        assert body["employee"]["full_name"] == "Иванов Иван"
