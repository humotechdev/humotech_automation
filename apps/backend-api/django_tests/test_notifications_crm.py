"""Страница уведомлений: сводка, область, исторический офис и попытки.

`test_notifications.py` проверяет очередь со стороны отправщика,
`test_notifications_api.py` — базовые права и повтор. Здесь проверяется
то, что добавлено ради разбора «почему человек не получил сообщение»:

  1. сводка считается по ВСЕМУ доступному набору и по тому же правилу,
     что и вкладка. Число рядом с вкладкой обязано совпадать с числом
     строк под ней, а вкладки в сумме — давать весь набор;
  2. офис в строке — тот, в котором человек работал ТОГДА. Подставлять
     сегодняшний значит переписывать историю;
  3. история попыток состоит только из состоявшихся попыток. Ручной
     повтор туда не пишется, а у старых строк истории нет вовсе, и это
     сказано отдельным признаком, а не пустым списком;
  4. запрос про офис вне доступа — отказ, а не пустой список.

Ни один тест не обращается в Telegram.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from django.utils import timezone

from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications import outbox
from humotech.notifications.models import Notification, NotificationAttempt
from humotech.notifications.service import STATUS_GROUPS

pytestmark = pytest.mark.django_db

API = "/api/v1"
READ = ("notifications.read", "employees.read", "offices.read")
MANAGE = (*READ, "notifications.manage")


def make(organization, employee, *, status="PENDING", **kwargs) -> Notification:
    kwargs.setdefault("notification_type", "absence.request.approved")
    kwargs.setdefault("title", "Заявка рассмотрена")
    kwargs.setdefault("body", "Ваша заявка подтверждена")
    return Notification.objects.create(
        organization=organization,
        employee=employee,
        channel=kwargs.pop("channel", "TELEGRAM"),
        status=status,
        **kwargs,
    )


@pytest.fixture()
def hr(api_client, make_user, organization):
    api_client.force_authenticate(user=make_user(organization, permissions=MANAGE))
    return api_client


# --- сводка ------------------------------------------------------------------


class TestCounts:
    def test_every_status_lands_on_exactly_one_tab(self):
        """Вкладки в сумме дают весь набор, и ни одна не берёт лишнего.

        Иначе какое-то состояние не видно ни на одной вкладке, кроме
        «Все», — а именно это спрятало бы снятые уведомления.
        """
        from humotech.core.enums import NOTIFICATION_STATUSES

        placed = [code for codes in STATUS_GROUPS.values() for code in codes]
        assert sorted(placed) == sorted(NOTIFICATION_STATUSES)
        assert len(placed) == len(set(placed))

    def test_summary_counts_the_whole_set_not_the_page(
        self, hr, organization, employee
    ):
        for _ in range(25):
            make(organization, employee, status="SENT")
        make(organization, employee, status="FAILED")

        page = hr.get(f"{API}/notifications/?limit=5").json()
        counts = hr.get(f"{API}/notifications/counts/").json()

        assert len(page["items"]) == 5
        assert page["has_more"] is True
        # Сводка не имеет права повторять длину страницы.
        assert counts["total"] == 26
        assert counts["sent"] == 25
        assert counts["failed"] == 1

    def test_read_is_counted_as_sent_and_running_as_queued(
        self, hr, organization, employee
    ):
        make(organization, employee, status="SENT")
        make(organization, employee, status="READ")
        make(organization, employee, status="PENDING")
        make(organization, employee, status="RUNNING", locked_at=timezone.now())
        make(organization, employee, status="CANCELLED")

        counts = hr.get(f"{API}/notifications/counts/").json()

        assert counts["sent"] == 2
        assert counts["queued"] == 2
        assert counts["cancelled"] == 1
        assert (
            counts["sent"] + counts["queued"] + counts["failed"]
            + counts["cancelled"] == counts["total"]
        )

    def test_the_tab_number_matches_the_rows_under_it(
        self, hr, organization, employee
    ):
        """Главный инвариант страницы, и проверяется он через HTTP."""
        make(organization, employee, status="SENT")
        make(organization, employee, status="READ")
        make(organization, employee, status="RUNNING", locked_at=timezone.now())

        counts = hr.get(f"{API}/notifications/counts/").json()
        for group, codes in STATUS_GROUPS.items():
            rows = hr.get(
                f"{API}/notifications/?status={','.join(codes)}&limit=100"
            ).json()
            assert len(rows["items"]) == counts[group], group

    def test_the_open_tab_does_not_change_the_numbers(
        self, hr, organization, employee
    ):
        make(organization, employee, status="SENT")
        make(organization, employee, status="FAILED")

        # Фильтр состояния в сводку не передаётся вовсе.
        with_filter = hr.get(f"{API}/notifications/counts/?status=SENT").json()
        assert with_filter["total"] == 2

    def test_search_and_period_narrow_the_summary_too(
        self, hr, organization, employee
    ):
        """Сводка и список описывают ОДИН набор, иначе числа врут."""
        make(organization, employee, status="SENT", title="Отпуск согласован")
        make(organization, employee, status="SENT", title="Заявка рассмотрена")

        counts = hr.get(f"{API}/notifications/counts/?search=Отпуск").json()
        rows = hr.get(f"{API}/notifications/?search=Отпуск").json()

        assert counts["total"] == 1 == len(rows["items"])

    def test_summary_names_the_timezone_it_is_counted_in(
        self, hr, organization, office
    ):
        counts = hr.get(f"{API}/notifications/counts/").json()
        # Интерфейс не должен угадывать пояс: границы суток режет сервер.
        assert counts["timezone"] == "Asia/Dushanbe"


# --- отбор -------------------------------------------------------------------


class TestFilters:
    def test_search_finds_the_person_not_only_the_text(
        self, hr, organization, employee
    ):
        make(organization, employee, status="SENT", body="Ваша заявка подтверждена")

        found = hr.get(f"{API}/notifications/?search=Иванов").json()

        assert len(found["items"]) == 1

    def test_several_statuses_come_through_one_parameter(
        self, hr, organization, employee
    ):
        make(organization, employee, status="SENT")
        make(organization, employee, status="READ")
        make(organization, employee, status="FAILED")

        rows = hr.get(f"{API}/notifications/?status=SENT,READ").json()

        assert len(rows["items"]) == 2

    def test_unknown_status_is_refused_not_ignored(self, hr):
        """Молча вернуть весь список на опечатку — худший из вариантов."""
        assert hr.get(f"{API}/notifications/?status=DELIVERED").status_code == 400

    def test_period_is_cut_by_the_organization_day(
        self, hr, organization, employee
    ):
        row = make(organization, employee, status="SENT")
        # 06 сентября 23:30 UTC — это уже 07 сентября в Asia/Dushanbe.
        Notification.objects.filter(id=row.id).update(
            created_at=datetime(2026, 9, 6, 23, 30, tzinfo=UTC)
        )

        seventh = hr.get(
            f"{API}/notifications/?date_from=2026-09-07&date_to=2026-09-07"
        ).json()
        sixth = hr.get(
            f"{API}/notifications/?date_from=2026-09-06&date_to=2026-09-06"
        ).json()

        assert len(seventh["items"]) == 1
        assert len(sixth["items"]) == 0

    def test_backwards_period_is_refused(self, hr):
        answer = hr.get(
            f"{API}/notifications/?date_from=2026-09-10&date_to=2026-09-01"
        )
        assert answer.status_code == 400

    def test_office_outside_the_scope_is_a_refusal_not_an_empty_list(
        self, api_client, make_user, organization, region, other_office, employee
    ):
        """Пустой список неотличим от «уведомлений нет» и скрывает отказ."""
        make(organization, employee, status="SENT")
        api_client.force_authenticate(
            user=make_user(organization, permissions=READ, region=region)
        )

        answer = api_client.get(f"{API}/notifications/?office_id={other_office.id}")

        assert answer.status_code == 403

    def test_office_filter_keeps_only_that_office(
        self, hr, organization, employee, other_office, office
    ):
        stranger = Employee.objects.create(
            organization=organization, employee_number="EMP-0009",
            first_name="Пётр", last_name="Петров",
            hire_date=date(2024, 2, 1), employment_status="ACTIVE",
        )
        EmployeeAssignment.objects.create(
            organization=organization, employee=stranger, office=other_office,
            employment_type="FULL_TIME", work_mode="ONSITE",
            is_primary=True, valid_from=date(2024, 2, 1),
        )
        make(organization, employee, status="SENT")
        make(organization, stranger, status="SENT")

        rows = hr.get(f"{API}/notifications/?office_id={office.id}").json()

        assert len(rows["items"]) == 1
        assert rows["items"][0]["employee"]["full_name"].startswith("Иванов")


# --- офис на момент отправки -------------------------------------------------


class TestHistoricalOffice:
    def test_the_row_shows_the_office_of_that_time(
        self, hr, organization, employee, office, other_office
    ):
        """Человека перевели — прошлое сообщение уходило прежнему офису.

        Подставить сегодняшний офис значит переписать историю: разбирая
        через год, почему сообщение ушло в этот филиал, ответа не найти.
        """
        EmployeeAssignment.objects.filter(employee=employee).update(
            valid_to=date(2026, 6, 30)
        )
        EmployeeAssignment.objects.create(
            organization=organization, employee=employee, office=other_office,
            employment_type="FULL_TIME", work_mode="ONSITE",
            is_primary=True, valid_from=date(2026, 7, 1),
        )
        old = make(organization, employee, status="SENT")
        Notification.objects.filter(id=old.id).update(
            created_at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
        )
        fresh = make(organization, employee, status="SENT")

        rows = {
            item["id"]: item
            for item in hr.get(f"{API}/notifications/?limit=50").json()["items"]
        }

        assert rows[str(old.id)]["office_name"] == office.name
        assert rows[str(fresh.id)]["office_name"] == other_office.name

    def test_the_card_shows_the_same_office_as_the_row(
        self, hr, organization, employee, office
    ):
        row = make(organization, employee, status="SENT")

        card = hr.get(f"{API}/notifications/{row.id}/").json()
        listed = hr.get(f"{API}/notifications/").json()["items"][0]

        assert card["office_name"] == listed["office_name"] == office.name
        assert card["region_name"] == listed["region_name"]

    def test_retry_answers_with_the_office_too(
        self, hr, organization, employee, office
    ):
        """Ответ действия кладётся на место карточки без второго запроса."""
        row = make(organization, employee, status="FAILED")

        answer = hr.post(f"{API}/notifications/{row.id}/retry/").json()

        assert answer["status"] == "PENDING"
        assert answer["office_name"] == office.name


# --- история попыток ---------------------------------------------------------


class TestAttempts:
    def test_a_failed_send_is_written_down(self, hr, organization, employee):
        row = make(organization, employee, status="RUNNING",
                   locked_at=timezone.now())

        outbox.mark_failed(row.id, error="TelegramNetworkError")

        answer = hr.get(f"{API}/notifications/{row.id}/attempts/").json()
        assert answer["kept"] is True
        assert len(answer["items"]) == 1
        assert answer["items"][0]["outcome"] == "FAILED"
        assert answer["items"][0]["reason"] == "TelegramNetworkError"
        assert answer["items"][0]["attempted_at"]

    def test_a_successful_send_is_written_down_without_a_reason(
        self, hr, organization, employee
    ):
        row = make(organization, employee, status="RUNNING",
                   locked_at=timezone.now())

        outbox.mark_sent(row.id)

        item = hr.get(f"{API}/notifications/{row.id}/attempts/").json()["items"][0]
        assert item["outcome"] == "SENT"
        # «Отправлено, потому что» не бывает.
        assert item["reason"] is None

    def test_the_whole_history_is_kept_not_only_the_last_one(
        self, hr, organization, employee
    ):
        row = make(organization, employee, status="RUNNING",
                   locked_at=timezone.now())
        outbox.mark_failed(row.id, error="TelegramNetworkError")
        Notification.objects.filter(id=row.id).update(
            status="RUNNING", locked_at=timezone.now()
        )
        outbox.mark_failed(row.id, error="TelegramRetryAfter")
        Notification.objects.filter(id=row.id).update(
            status="RUNNING", locked_at=timezone.now()
        )
        outbox.mark_sent(row.id)

        items = hr.get(f"{API}/notifications/{row.id}/attempts/").json()["items"]

        assert [item["outcome"] for item in items] == ["FAILED", "FAILED", "SENT"]
        assert [item["number"] for item in items] == [1, 2, 3]

    def test_a_manual_retry_is_not_an_attempt(self, hr, organization, employee):
        """Повтор — решение человека, а не попытка отправки.

        Он остаётся в журнале действий; смешивать его с попытками
        отправщика значило бы показывать кадровику его же нажатие как
        событие доставки.
        """
        row = make(organization, employee, status="RUNNING",
                   locked_at=timezone.now())
        outbox.mark_failed(row.id, error="TelegramNetworkError")
        Notification.objects.filter(id=row.id).update(status="FAILED")

        hr.post(f"{API}/notifications/{row.id}/retry/")

        items = hr.get(f"{API}/notifications/{row.id}/attempts/").json()["items"]
        assert len(items) == 1

    def test_numbering_survives_a_retry_that_resets_the_counter(
        self, hr, organization, employee
    ):
        """Ручной повтор обнуляет `attempts`, но не переписывает историю."""
        row = make(organization, employee, status="RUNNING",
                   locked_at=timezone.now())
        outbox.mark_failed(row.id, error="TelegramNetworkError")
        Notification.objects.filter(id=row.id).update(status="FAILED")
        hr.post(f"{API}/notifications/{row.id}/retry/")
        Notification.objects.filter(id=row.id).update(
            status="RUNNING", locked_at=timezone.now()
        )
        outbox.mark_sent(row.id)

        items = hr.get(f"{API}/notifications/{row.id}/attempts/").json()["items"]
        assert [item["number"] for item in items] == [1, 2]

    def test_an_old_row_says_the_history_was_not_kept(
        self, hr, organization, employee
    ):
        """Пустой список у отправленного значил бы «попыток не было»."""
        row = make(organization, employee, status="SENT", attempts=3,
                   sent_at=timezone.now())

        answer = hr.get(f"{API}/notifications/{row.id}/attempts/").json()

        assert answer["items"] == []
        assert answer["kept"] is False

    def test_a_fresh_row_without_attempts_is_not_called_old(
        self, hr, organization, employee
    ):
        row = make(organization, employee, status="PENDING")

        answer = hr.get(f"{API}/notifications/{row.id}/attempts/").json()

        assert answer["items"] == []
        assert answer["kept"] is True

    def test_history_of_a_foreign_notification_is_not_readable(
        self, hr, other_organization, db
    ):
        stranger = Employee.objects.create(
            organization=other_organization, employee_number="EMP-X",
            first_name="Чужой", last_name="Чужаков",
            hire_date=date(2024, 1, 1), employment_status="ACTIVE",
        )
        row = make(other_organization, stranger, status="FAILED")

        assert hr.get(f"{API}/notifications/{row.id}/attempts/").status_code == 404

    def test_history_needs_the_read_permission(
        self, api_client, make_user, organization, employee
    ):
        row = make(organization, employee, status="FAILED")
        api_client.force_authenticate(
            user=make_user(organization, permissions=("employees.read",))
        )

        assert (
            api_client.get(f"{API}/notifications/{row.id}/attempts/").status_code
            == 403
        )

    def test_history_of_another_office_is_not_readable(
        self, api_client, make_user, organization, region, other_office
    ):
        """Знание идентификатора не открывает чужое сообщение.

        Историю попыток закрывает та же проверка области, что и саму
        карточку: иначе причину неудачи можно было бы прочитать в обход.
        """
        stranger = Employee.objects.create(
            organization=organization, employee_number="EMP-0007",
            first_name="Пётр", last_name="Петров",
            hire_date=date(2024, 2, 1), employment_status="ACTIVE",
        )
        EmployeeAssignment.objects.create(
            organization=organization, employee=stranger, office=other_office,
            employment_type="FULL_TIME", work_mode="ONSITE",
            is_primary=True, valid_from=date(2024, 2, 1),
        )
        row = make(organization, stranger, status="FAILED")
        api_client.force_authenticate(
            user=make_user(organization, permissions=READ, region=region)
        )

        # Отказ, а не «не найдено»: внутри организации запись существует,
        # и делать вид, что её нет, значило бы врать про свою же базу.
        assert (
            api_client.get(f"{API}/notifications/{row.id}/attempts/").status_code
            == 403
        )
        assert (
            api_client.get(f"{API}/notifications/{row.id}/").status_code == 403
        )


# --- состояние действий ------------------------------------------------------


class TestActionState:
    def test_the_row_says_what_is_allowed_right_now(
        self, hr, organization, employee
    ):
        """Интерфейс не должен переизобретать правила переходов."""
        failed = make(organization, employee, status="FAILED")
        sent = make(organization, employee, status="SENT")

        rows = {
            item["id"]: item
            for item in hr.get(f"{API}/notifications/?limit=50").json()["items"]
        }

        assert rows[str(failed.id)]["can_retry"] is True
        assert rows[str(failed.id)]["can_cancel"] is True
        assert rows[str(sent.id)]["can_retry"] is False
        assert rows[str(sent.id)]["can_cancel"] is False

    def test_retrying_a_sent_message_is_refused(self, hr, organization, employee):
        """Второе сообщение человеку — не то, чего хотят нажатием «повторить»."""
        row = make(organization, employee, status="SENT", sent_at=timezone.now())

        answer = hr.post(f"{API}/notifications/{row.id}/retry/")

        assert answer.status_code == 409
        assert answer.json()["error"]["code"] == "conflict"

    def test_a_row_the_worker_already_took_cannot_be_cancelled(
        self, hr, organization, employee
    ):
        row = make(organization, employee, status="RUNNING",
                   locked_at=timezone.now())

        assert (
            hr.post(f"{API}/notifications/{row.id}/cancel/").status_code == 409
        )

    def test_the_second_press_does_not_queue_twice(
        self, hr, organization, employee
    ):
        """Гонка двух кадровиков: второй получает отказ, а не второй заказ."""
        row = make(organization, employee, status="FAILED")

        first = hr.post(f"{API}/notifications/{row.id}/retry/")
        second = hr.post(f"{API}/notifications/{row.id}/retry/")

        assert first.status_code == 200
        assert second.status_code == 409
        row.refresh_from_db()
        assert row.status == "PENDING"


# --- очередь пишет историю сама ---------------------------------------------


class TestQueueWritesHistory:
    def test_a_recipient_without_a_link_leaves_a_record(
        self, organization, employee
    ):
        """Снятие без адресата — состоявшийся исход, а не отсутствие попытки.

        Без записи карточка показала бы «снято» и пустую историю: когда
        именно это случилось, ответа бы не было.
        """
        row = make(organization, employee, status="PENDING")

        assert outbox.claim() == []

        row.refresh_from_db()
        assert row.status == "CANCELLED"
        attempt = NotificationAttempt.objects.get(notification_id=row.id)
        assert attempt.outcome == "CANCELLED"
        assert attempt.reason == "not_linked"

    def test_a_second_mark_sent_does_not_add_a_phantom_attempt(
        self, organization, employee
    ):
        """Отчёт пришёл дважды — попытка всё равно была одна."""
        row = make(organization, employee, status="RUNNING",
                   locked_at=timezone.now())

        outbox.mark_sent(row.id)
        outbox.mark_sent(row.id)

        assert NotificationAttempt.objects.filter(notification_id=row.id).count() == 1

    def test_the_attempt_keeps_a_code_not_the_telegram_answer(
        self, organization, employee
    ):
        """Ответ Telegram может содержать эхо запроса — то есть сам текст."""
        row = make(organization, employee, status="RUNNING",
                   locked_at=timezone.now())

        outbox.mark_failed(row.id, error="x" * 400)

        attempt = NotificationAttempt.objects.get(notification_id=row.id)
        assert len(attempt.reason) <= 200

    def test_history_outlives_the_backoff_window(self, organization, employee):
        """Через сутки видно, что попыток было три, а не «attempts = 3»."""
        row = make(organization, employee, status="RUNNING",
                   locked_at=timezone.now())
        moment = timezone.now() - timedelta(hours=2)
        outbox.mark_failed(row.id, error="TelegramNetworkError", now=moment)

        attempt = NotificationAttempt.objects.get(notification_id=row.id)
        assert attempt.attempted_at == moment
