"""Лента событий кадровика: источники, права, область и прочтение.

`test_notifications_crm.py` проверяет очередь отправки — «ушло ли
сообщение сотруднику». Здесь проверяется другое: что кадровик видит в
колокольчике и, главное, чего он там НЕ видит.

Четыре вещи, ради которых написан каждый тест:

  1. **Область видимости.** У каждого источника свой путь до офиса
     сотрудника, и проверять его приходится отдельно: у заявки это
     `employee`, у сессии — `office`, у выгрузки нет ни того, ни
     другого. Поэтому тест на чужой офис есть у КАЖДОГО источника;
  2. **Право на источник.** Нет права на обращения — нет строк об
     обращениях, но заявки остаются. Общего отказа лента не даёт:
     колокольчик есть у всех, и отсутствие одного права не должно
     гасить всё остальное;
  3. **Прочтение у каждого своё.** Отметка одного кадровика не гасит
     событие у другого — очередь разбирают вдвоём;
  4. **Ключ события угадываем.** Он выводится из данных, поэтому
     карточка обязана проверять доступ заново, а не доверять тому,
     что ключ вообще откуда-то взялся.

Выдуманных событий в ленте нет: каждый тест сначала заводит настоящую
запись-источник и лишь потом ждёт строку.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from django_tests.conftest import make_qr_point
from humotech.absences.models import AbsenceDocument, AbsenceRequest, AbsenceType
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.attendance.models import AttendanceCorrectionRequest
from humotech.core.errors import NotFound, ValidationFailed
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.files.models import File
from humotech.notifications.feed import (
    FEED_TYPES,
    FILTERS,
    GROUPS,
    FeedService,
)
from humotech.notifications.models import FeedRead, Notification
from humotech.questions.models import EmployeeQuestion
from humotech.reports.models import ExportJob

pytestmark = pytest.mark.django_db

API = "/api/v1"

#: Полный набор прав чтения: кадровик, который видит все источники.
ALL_READ = (
    "absences.read",
    "attendance.read",
    "questions.read",
    "notifications.read",
    "employees.read",
    "offices.read",
)


# --- вспомогательные записи-источники ---------------------------------------


@pytest.fixture()
def service() -> FeedService:
    return FeedService()


@pytest.fixture()
def feed_actor(make_actor, organization):
    """Кадровик, которому видны ВСЕ источники ленты.

    Общая фикстура `feed_actor` сюда не подходит: в ней есть управление
    сотрудниками и офисами, но нет чтения заявок, отметок, обращений и
    очереди отправки — то есть ровно тех источников, из которых лента
    и собрана.
    """
    return make_actor(organization, permissions=ALL_READ)


@pytest.fixture()
def vacation_type(db, organization) -> AbsenceType:
    return AbsenceType.objects.create(
        organization=organization,
        code="ANNUAL_LEAVE",
        name="Ежегодный отпуск",
        is_paid=True,
        requires_approval=True,
        requires_document=False,
        deducts_leave_balance=True,
        is_active=True,
    )


@pytest.fixture()
def sick_type(db, organization) -> AbsenceType:
    return AbsenceType.objects.create(
        organization=organization,
        code="SICK_LEAVE",
        name="Больничный",
        is_paid=True,
        requires_approval=True,
        requires_document=True,
        document_required_after_days=3,
        deducts_leave_balance=False,
        is_active=True,
    )


def other_employee(organization, office, *, number="EMP-0002") -> Employee:
    """Сотрудник другого офиса — чтобы проверять область видимости."""
    employee = Employee.objects.create(
        organization=organization,
        employee_number=number,
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2024, 3, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        employment_type="FULL_TIME",
        work_mode="ONSITE",
        is_primary=True,
        valid_from=date(2024, 3, 1),
    )
    return employee


def absence(organization, employee, absence_type, *, kind="CREATE",
            status="SUBMITTED", days=5, parent=None) -> AbsenceRequest:
    start = timezone.now() + timedelta(days=7)
    return AbsenceRequest.objects.create(
        organization=organization,
        employee=employee,
        absence_type=absence_type,
        request_kind=kind,
        parent_request=parent,
        requested_start_at=start,
        requested_end_at=start + timedelta(days=days - 1),
        employee_comment="Плановый отпуск",
        status=status,
        submitted_at=timezone.now(),
    )


def certificate(organization, request) -> AbsenceDocument:
    file = File.objects.create(
        organization=organization,
        storage_provider="local",
        storage_key=f"absence/{uuid4().hex}.pdf",
        original_filename="spravka.pdf",
        mime_type="application/pdf",
        size_bytes=48_512,
        # Контрольная сумма ровно 64 символа: её длину проверяет сама база.
        checksum_sha256="a" * 64,
        scan_status="CLEAN",
    )
    return AbsenceDocument.objects.create(
        organization=organization,
        absence_request=request,
        file=file,
        document_type="SICK_CERTIFICATE",
        verification_status="PENDING",
    )


def open_session(organization, employee, office, *, hours_ago=3) -> AttendanceSession:
    started = timezone.now() - timedelta(hours=hours_ago)
    entry = AttendanceEvent.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        qr_point=make_qr_point(organization, office, code=f"P-{uuid4().hex[:8]}"),
        event_type="ENTRY",
        source="QR",
        verification_status="ACCEPTED",
        occurred_at=started,
    )
    return AttendanceSession.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        entry_event=entry,
        started_at=started,
        status="OPEN",
    )


def correction(organization, employee, *, status="SUBMITTED"):
    return AttendanceCorrectionRequest.objects.create(
        organization=organization,
        employee=employee,
        requested_exit_at=timezone.now() - timedelta(hours=2),
        reason="Забыл отсканировать выход",
        status=status,
        submitted_at=timezone.now(),
    )


def question(organization, employee, *, status="NEW", priority="NORMAL"):
    return EmployeeQuestion.objects.create(
        organization=organization,
        employee=employee,
        number=EmployeeQuestion.objects.filter(
            organization=organization
        ).count() + 1,
        question_text="Как перенести отпуск на октябрь?",
        normalized_topic="Перенос отпуска",
        status=status,
        priority=priority,
        last_message_at=timezone.now(),
    )


def failed_message(organization, employee) -> Notification:
    return Notification.objects.create(
        organization=organization,
        employee=employee,
        channel="TELEGRAM",
        notification_type="absence.request.approved",
        title="Заявка рассмотрена",
        body="Ваша заявка подтверждена",
        status="FAILED",
        attempts=3,
        error_message="chat_not_found",
    )


def ready_report(organization, user) -> ExportJob:
    return ExportJob.objects.create(
        organization=organization,
        kind="attendance",
        fmt="xlsx",
        status="SUCCEEDED",
        requested_by_user=user,
        finished_at=timezone.now(),
        file_name="attendance.xlsx",
        size_bytes=12_345,
        storage_key=f"exports/{uuid4().hex}.xlsx",
    )


def kinds(items) -> list[str]:
    return [one["type"] for one in items]


def pick(page, kind: str) -> dict:
    """Строка ленты нужного вида.

    Именно по виду, а не по первому месту в списке: в тесте всё
    создаётся ОДНОЙ транзакцией, а `created_at` по умолчанию — время
    транзакции, одно на все записи. Порядок внутри такой ленты
    определяется тай-брейком по идентификатору, то есть случаен, и
    проверять по нему смысла нет. Сам порядок проверяется отдельно,
    записями с РАЗНЫМ временем.
    """
    found = [one for one in page["items"] if one["type"] == kind]
    assert found, f"события вида {kind} в ленте нет: {kinds(page['items'])}"
    return found[0]


def age(model, row_id, *, minutes: int) -> None:
    """Состарить запись: время создания задаётся явно, а не транзакцией."""
    model.objects.filter(id=row_id).update(
        created_at=timezone.now() - timedelta(minutes=minutes)
    )


# --- состав ленты ------------------------------------------------------------


class TestSources:
    """Каждый вид события приходит из НАСТОЯЩЕЙ записи, а не из словаря."""

    def test_vacation_request_becomes_an_event(
        self, service, feed_actor, organization, employee, vacation_type
    ):
        row = absence(organization, employee, vacation_type)
        item = pick(service.page(feed_actor), "absence_request")

        assert item["id"] == f"absence_request:{row.id}"
        assert item["id"] == f"absence_request:{row.id}"
        assert item["title"] == "Новая заявка на отпуск"
        assert item["employee_name"] == "Иванов Иван"
        assert item["status_label"] == "На рассмотрении"
        assert item["requires_action"] is True
        assert item["related_entity_type"] == "absence_requests"
        assert item["action_url"] == f"/requests?request={row.id}"

    def test_sick_leave_has_its_own_kind(
        self, service, feed_actor, organization, employee, sick_type
    ):
        """Больничный и отпуск — разные карточки, значит и разные виды."""
        row = absence(organization, employee, sick_type)
        item = pick(service.page(feed_actor), "sick_leave")
        assert item["id"] == f"sick_leave:{row.id}"
        assert item["title"] == "Новый больничный"

    def test_cancel_request_says_what_is_being_cancelled(
        self, service, feed_actor, organization, employee, sick_type
    ):
        parent = absence(organization, employee, sick_type, status="APPROVED")
        absence(organization, employee, sick_type, kind="CANCEL", parent=parent)

        first = pick(service.page(feed_actor), "absence_cancel")
        assert first["title"] == "Запрошена отмена больничного"
        assert first["status_label"] == "Запрошена отмена"

    def test_uploaded_certificate_shows_metadata_only(
        self, service, feed_actor, organization, employee, sick_type
    ):
        """Справка в ленте — это факт загрузки, а не её содержимое."""
        request = absence(organization, employee, sick_type)
        document = certificate(organization, request)

        item = pick(service.page(feed_actor), "absence_document")
        assert item["title"] == "Загружена справка"
        assert item["short_text"] == "Больничный"

        card = service.detail(feed_actor, f"absence_document:{document.id}")
        file_card = card["absence"]["document"]
        assert file_card["file_name"] == "spravka.pdf"
        assert file_card["size_bytes"] == 48_512
        assert file_card["verification_label"] == "На проверке"
        # Ни ссылки на файл, ни его содержимого в карточке нет.
        assert "storage_key" not in file_card
        assert "url" not in file_card

    def test_open_session_and_its_details(
        self, service, feed_actor, organization, employee, office
    ):
        session = open_session(organization, employee, office)
        item = pick(service.page(feed_actor), "attendance_open")
        assert item["title"] == "Не закрыт выход"
        assert item["office_name"] == office.name

        card = service.detail(feed_actor, f"attendance_open:{session.id}")
        assert card["session"]["open_minutes"] >= 3 * 60
        assert card["session"]["qr_point_name"]

    def test_long_open_session_is_critical(
        self, service, feed_actor, organization, employee, office
    ):
        """Красный указатель — только у критичного, иначе он ничего не значит."""
        open_session(organization, employee, office, hours_ago=20)
        assert pick(service.page(feed_actor), "attendance_open")["priority"] == (
            "CRITICAL"
        )

    def test_correction_request_becomes_an_event(
        self, service, feed_actor, organization, employee
    ):
        row = correction(organization, employee)
        item = pick(service.page(feed_actor), "attendance_correction")
        assert item["action_url"] == f"/requests?tab=fixes&request={row.id}"

        card = service.detail(feed_actor, f"attendance_correction:{row.id}")
        assert card["correction"]["event_kind"] == "Выход"
        assert card["comment"] == "Забыл отсканировать выход"

    def test_question_becomes_an_event(
        self, service, feed_actor, organization, employee
    ):
        row = question(organization, employee)
        item = pick(service.page(feed_actor), "question")
        assert item["title"] == "Новое обращение"
        assert item["short_text"] == "Перенос отпуска"
        assert item["status_label"] == "Ждёт ответа"

        card = service.detail(feed_actor, f"question:{row.id}")
        assert card["question"]["channel"] == "TELEGRAM"

    def test_delivery_error_hides_provider_answer(
        self, service, feed_actor, organization, employee
    ):
        row = failed_message(organization, employee)
        item = pick(service.page(feed_actor), "delivery_error")
        assert item["priority"] == "CRITICAL"

        card = service.detail(feed_actor, f"delivery_error:{row.id}")
        assert card["delivery"]["attempts"] == 3
        assert card["delivery"]["channel"] == "TELEGRAM"
        # Причина — короткий код очереди, а не ответ Telegram целиком.
        assert card["comment"] == "chat_not_found"

    def test_ready_report_is_visible_to_its_author_only(
        self, make_actor, organization, other_organization
    ):
        """У выгрузки нет офиса: её область — это её заказчик."""
        author = make_actor(organization, permissions=ALL_READ)
        colleague = make_actor(organization, permissions=ALL_READ)
        from humotech.accounts.models import User

        job = ready_report(organization, User.objects.get(id=author.user_id))

        assert kinds(FeedService().page(author)["items"]) == ["report_ready"]
        assert FeedService().page(colleague)["items"] == []
        with pytest.raises(NotFound):
            FeedService().detail(colleague, f"report_ready:{job.id}")

    def test_new_employee_becomes_an_event(
        self, service, feed_actor, organization, employee
    ):
        item = pick(service.page(feed_actor), "employee_added")
        assert item["action_url"] == f"/employees/{employee.id}"

    def test_every_kind_belongs_to_exactly_one_tab(self):
        """Вкладки в сумме дают весь набор, и ни одна не берёт лишнего."""
        tabs = {key for key, _ in FILTERS} - {"all", "unread", "action"}
        assert set(GROUPS) == set(FEED_TYPES)
        assert set(GROUPS.values()) == tabs


# --- область видимости -------------------------------------------------------


class TestScope:
    """У каждого источника свой путь до офиса — и свой тест на чужой."""

    @pytest.fixture()
    def local_hr(self, make_actor, organization, office):
        """Кадровик ОДНОГО офиса: видит только его."""
        return make_actor(organization, permissions=ALL_READ, office=office)

    @pytest.fixture()
    def stranger(self, organization, other_office):
        return other_employee(organization, other_office)

    def test_absence_of_another_office_is_invisible(
        self, service, local_hr, organization, stranger, vacation_type
    ):
        row = absence(organization, stranger, vacation_type)
        assert service.page(local_hr)["items"] == []
        with pytest.raises(NotFound):
            service.detail(local_hr, f"absence_request:{row.id}")

    def test_certificate_of_another_office_is_invisible(
        self, service, local_hr, organization, stranger, sick_type
    ):
        document = certificate(
            organization, absence(organization, stranger, sick_type)
        )
        assert service.page(local_hr)["items"] == []
        with pytest.raises(NotFound):
            service.detail(local_hr, f"absence_document:{document.id}")

    def test_session_of_another_office_is_invisible(
        self, service, local_hr, organization, stranger, other_office
    ):
        session = open_session(organization, stranger, other_office)
        assert service.page(local_hr)["items"] == []
        with pytest.raises(NotFound):
            service.detail(local_hr, f"attendance_open:{session.id}")

    def test_correction_of_another_office_is_invisible(
        self, service, local_hr, organization, stranger
    ):
        row = correction(organization, stranger)
        assert service.page(local_hr)["items"] == []
        with pytest.raises(NotFound):
            service.detail(local_hr, f"attendance_correction:{row.id}")

    def test_question_of_another_office_is_invisible(
        self, service, local_hr, organization, stranger
    ):
        row = question(organization, stranger)
        assert service.page(local_hr)["items"] == []
        with pytest.raises(NotFound):
            service.detail(local_hr, f"question:{row.id}")

    def test_delivery_error_of_another_office_is_invisible(
        self, service, local_hr, organization, stranger
    ):
        row = failed_message(organization, stranger)
        assert service.page(local_hr)["items"] == []
        with pytest.raises(NotFound):
            service.detail(local_hr, f"delivery_error:{row.id}")

    def test_employee_of_another_office_is_invisible(
        self, service, local_hr, organization, stranger
    ):
        assert service.page(local_hr)["items"] == []
        with pytest.raises(NotFound):
            service.detail(local_hr, f"employee_added:{stranger.id}")

    def test_foreign_organization_sees_nothing(
        self, service, foreign_actor, organization, employee, vacation_type
    ):
        row = absence(organization, employee, vacation_type)
        assert service.page(foreign_actor)["items"] == []
        with pytest.raises(NotFound):
            service.detail(foreign_actor, f"absence_request:{row.id}")

    def test_office_without_people_shows_nothing(
        self, service, make_actor, organization, employee, other_office,
        vacation_type,
    ):
        """Пустая область — это «ничего», а не «вся организация».

        Здесь легче всего ошибиться проверкой на истинность: пустое
        множество офисов и «доступ ко всему» — разные состояния, и
        склеив их, лента показала бы кадровику пустого офиса всю
        организацию целиком.
        """
        absence(organization, employee, vacation_type)
        empty = make_actor(
            organization, permissions=ALL_READ, office=other_office
        )
        page = service.page(empty)
        assert page["items"] == []
        assert page["counts"]["all"] == 0


# --- права -------------------------------------------------------------------


class TestPermissions:
    def test_missing_permission_hides_only_its_own_source(
        self, make_actor, organization, employee, vacation_type
    ):
        """Нет права на обращения — нет обращений, но заявки остаются."""
        question(organization, employee)
        absence(organization, employee, vacation_type)
        actor = make_actor(
            organization, permissions=("absences.read", "employees.read"),
        )
        assert "question" not in kinds(FeedService().page(actor)["items"])
        assert "absence_request" in kinds(FeedService().page(actor)["items"])

    def test_no_permissions_means_empty_feed_not_refusal(
        self, nobody_actor, organization, employee, vacation_type
    ):
        """Колокольчик есть у всех: без прав он пуст, а не сломан."""
        absence(organization, employee, vacation_type)
        page = FeedService().page(nobody_actor)
        assert page["items"] == []
        assert page["counts"]["unread"] == 0

    def test_detail_refuses_without_the_source_permission(
        self, make_actor, organization, employee
    ):
        row = question(organization, employee)
        actor = make_actor(organization, permissions=("absences.read",))
        with pytest.raises(NotFound):
            FeedService().detail(actor, f"question:{row.id}")


# --- прочтение ---------------------------------------------------------------


class TestReadState:
    def test_reading_is_personal(
        self, make_actor, organization, employee, vacation_type
    ):
        """Прочтение одного кадровика не гасит событие у другого."""
        row = absence(organization, employee, vacation_type)
        first = make_actor(organization, permissions=ALL_READ)
        second = make_actor(organization, permissions=ALL_READ)

        FeedService().mark_read(first, f"absence_request:{row.id}")

        mine = FeedService().page(first)
        theirs = FeedService().page(second)
        assert pick(mine, "absence_request")["read_at"] is not None
        assert pick(theirs, "absence_request")["read_at"] is None
        assert mine["counts"]["unread"] < theirs["counts"]["unread"]

    def test_marking_twice_is_not_an_error(
        self, service, feed_actor, organization, employee, vacation_type
    ):
        row = absence(organization, employee, vacation_type)
        key = f"absence_request:{row.id}"
        service.mark_read(feed_actor, key)
        service.mark_read(feed_actor, key)
        assert FeedRead.objects.filter(user_id=feed_actor.user_id).count() == 1

    def test_mark_all_touches_only_what_this_user_sees(
        self, make_actor, organization, office, other_office, vacation_type,
        employee,
    ):
        """«Прочитать все» ограничено областью того, кто нажал."""
        stranger = other_employee(organization, other_office)
        absence(organization, employee, vacation_type)
        theirs = absence(organization, stranger, vacation_type)

        local = make_actor(organization, permissions=ALL_READ, office=office)
        result = FeedService().mark_all(local)

        assert result["unread"] == 0
        assert not FeedRead.objects.filter(
            user_id=local.user_id, entity_id=theirs.id
        ).exists()

        # У кадровика с доступом ко всей организации не прочитано ничего:
        # два сотрудника и две заявки. Чужая отметка его ленту не трогает.
        wide = make_actor(organization, permissions=ALL_READ)
        assert FeedService().page(wide)["counts"]["unread"] == 4

    def test_mark_read_refuses_a_foreign_event(
        self, make_actor, organization, office, other_office, vacation_type
    ):
        stranger = other_employee(organization, other_office)
        row = absence(organization, stranger, vacation_type)
        local = make_actor(organization, permissions=ALL_READ, office=office)

        with pytest.raises(NotFound):
            FeedService().mark_read(local, f"absence_request:{row.id}")
        assert not FeedRead.objects.filter(user_id=local.user_id).exists()


# --- ключ события ------------------------------------------------------------


class TestEventKey:
    def test_broken_key_is_a_bad_request(self, service, feed_actor):
        for broken in ("", "absence_request", "unknown:1", "absence_request:x"):
            with pytest.raises(ValidationFailed):
                service.detail(feed_actor, broken)

    def test_kind_must_match_the_record(
        self, service, feed_actor, organization, employee, vacation_type
    ):
        """Заявку на отпуск нельзя открыть как больничный."""
        row = absence(organization, employee, vacation_type)
        with pytest.raises(NotFound):
            service.detail(feed_actor, f"sick_leave:{row.id}")


# --- список, счётчики и фильтры ----------------------------------------------


class TestPage:
    def test_newest_first_and_counts_describe_the_whole_window(
        self, service, feed_actor, organization, employee, office, vacation_type
    ):
        absence(organization, employee, vacation_type)
        question(organization, employee)
        open_session(organization, employee, office)

        page = service.page(feed_actor, limit=2)
        times = [one["created_at"] for one in page["items"]]
        assert times == sorted(times, reverse=True)
        assert len(page["items"]) == 2
        assert page["has_more"] is True
        # Счётчики считают всё окно, а не показанную страницу.
        assert page["counts"]["all"] == 4  # + карточка сотрудника
        assert page["counts"]["unread"] == 4

    def test_cursor_walks_the_whole_feed_without_repeats(
        self, service, feed_actor, organization, employee, office, vacation_type
    ):
        absence(organization, employee, vacation_type)
        question(organization, employee)
        open_session(organization, employee, office)

        seen: list[str] = []
        cursor = None
        while True:
            page = service.page(feed_actor, limit=2, cursor=cursor)
            seen += [one["id"] for one in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert len(seen) == len(set(seen)) == 4

    def test_filters_narrow_the_list_but_not_the_counts(
        self, service, feed_actor, organization, employee, vacation_type
    ):
        absence(organization, employee, vacation_type)
        question(organization, employee)

        page = service.page(feed_actor, scope="questions")
        assert kinds(page["items"]) == ["question"]
        assert page["counts"]["all"] == 3
        assert page["counts"]["questions"] == 1

    def test_action_filter_keeps_only_what_waits_for_a_decision(
        self, service, feed_actor, organization, employee, vacation_type
    ):
        absence(organization, employee, vacation_type, status="APPROVED")
        absence(organization, employee, vacation_type)
        page = service.page(feed_actor, scope="action")
        assert all(one["requires_action"] for one in page["items"])
        assert len(page["items"]) == 1

    def test_unknown_filter_is_a_bad_request(self, service, feed_actor):
        with pytest.raises(ValidationFailed):
            service.page(feed_actor, scope="что-нибудь")

    def test_old_events_fall_out_of_the_window(
        self, service, feed_actor, organization, employee, vacation_type
    ):
        row = absence(organization, employee, vacation_type)
        AbsenceRequest.objects.filter(id=row.id).update(
            created_at=timezone.now() - timedelta(days=45)
        )
        assert "absence_request" not in kinds(service.page(feed_actor)["items"])


# --- подробности заявки ------------------------------------------------------


class TestAbsenceCard:
    def test_card_counts_days_and_balance(
        self, service, feed_actor, organization, employee, vacation_type
    ):
        """Дни и остаток берутся из правил отсутствий, а не считаются заново."""
        from humotech.absences.models import LeaveBalance

        row = absence(organization, employee, vacation_type, days=5)
        LeaveBalance.objects.create(
            organization=organization,
            employee=employee,
            absence_type=vacation_type,
            year=row.requested_start_at.year,
            allocated_minutes=14 * 8 * 60,
            used_minutes=0,
            # Минуты поданной заявки уже в резерве — так их кладёт
            # `AbsenceService.create`. Баланс без резерва означал бы
            # заявку, которой в остатке ещё нет, и проверялся бы не тот
            # случай.
            reserved_minutes=5 * 8 * 60,
            adjustment_minutes=0,
        )

        card = service.detail(feed_actor, f"absence_request:{row.id}")
        block = card["absence"]
        assert block["calendar_days"] == 5
        assert block["working_days"] == 5  # графика нет — считаются все дни
        assert block["balance_before_days"] == 14
        assert block["balance_after_days"] == 9
        assert block["overlaps"] is False
        assert card["comment"] == "Плановый отпуск"

    def test_card_reports_an_overlapping_request(
        self, service, feed_actor, organization, employee, vacation_type
    ):
        first = absence(organization, employee, vacation_type)
        absence(organization, employee, vacation_type)
        card = service.detail(feed_actor, f"absence_request:{first.id}")
        assert card["absence"]["overlaps"] is True

    def test_card_has_place_of_work(
        self, service, feed_actor, organization, employee, office, vacation_type
    ):
        row = absence(organization, employee, vacation_type)
        card = service.detail(feed_actor, f"absence_request:{row.id}")
        assert card["employee"]["full_name"] == "Иванов Иван"
        assert card["employee"]["office_name"] == office.name


# --- HTTP --------------------------------------------------------------------


class TestApi:
    @pytest.fixture()
    def hr(self, api_client, make_user, organization):
        api_client.force_authenticate(
            user=make_user(organization, permissions=ALL_READ)
        )
        return api_client

    def test_feed_endpoint_returns_items_and_counts(
        self, hr, organization, employee, vacation_type
    ):
        absence(organization, employee, vacation_type)
        response = hr.get(f"{API}/notification-feed")
        assert response.status_code == 200
        assert response.data["counts"]["unread"] == 2
        assert response.data["window_days"] == 30

    def test_counts_endpoint_answers_alone(
        self, hr, organization, employee, vacation_type
    ):
        absence(organization, employee, vacation_type)
        response = hr.get(f"{API}/notification-feed/counts")
        assert response.status_code == 200
        assert response.data["unread"] == 2

    def test_read_and_read_all_over_http(
        self, hr, organization, employee, vacation_type
    ):
        row = absence(organization, employee, vacation_type)
        one = hr.post(f"{API}/notification-feed/absence_request:{row.id}")
        assert one.status_code == 200
        assert one.data["unread"] == 1

        everything = hr.post(f"{API}/notification-feed/read-all")
        assert everything.status_code == 200
        assert everything.data["unread"] == 0
        assert everything.data["marked"] == 1

    def test_detail_over_http(self, hr, organization, employee, vacation_type):
        row = absence(organization, employee, vacation_type)
        response = hr.get(f"{API}/notification-feed/absence_request:{row.id}")
        assert response.status_code == 200
        assert response.data["type"] == "absence_request"
        assert response.data["absence"]["type_name"] == "Ежегодный отпуск"

    def test_anonymous_is_refused(self, api_client):
        assert api_client.get(f"{API}/notification-feed").status_code in (401, 403)
