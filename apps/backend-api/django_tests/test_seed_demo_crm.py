"""Витрина CRM: та ли она, за что себя выдаёт.

Проверяется не «команда отработала без исключения», а обещания, ради
которых она написана.

1. Числа главной страницы получаются ИЗ ЗАПИСЕЙ, а не записаны готовыми.
   Поэтому тест считает состав смены тем же путём, каким его считает
   дашборд, и сверяет с заданной таблицей.
2. Повторный запуск ничего не размножает.
3. История непротиворечива: выход позже входа, смены не пересекаются,
   отметок нет ни в будущем, ни в нерабочий день графика.
4. В боевых настройках команда не работает — и ключ этого не меняет.

Время здесь заморожено. Не ради удобства: без этого набор проходил бы по
будням и падал по выходным, то есть отвечал про календарь, а не про код.
Замороженный момент один на посев и на подсчёт — иначе команда завела бы
картину одного дня, а тест считал бы другой.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone as utc_offset
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import F
from django.utils import timezone as django_timezone

from humotech.absences.models import AbsenceDocument, AbsenceRequest, EmployeeAbsence
from humotech.accounts.models import User, UserRoleScope
from humotech.analytics.dashboard import DashboardService
from humotech.analytics.metrics import AnalyticsService
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import (
    AttendanceCorrectionRequest,
    AttendanceEvent,
    AttendanceSession,
)
from humotech.core.demo import pdf, photos as photo_pack
from humotech.core.demo.catalog import MAIN_SCHEDULE, OFFICES, SCHEDULES, SHOWCASE
from humotech.core.management.commands.seed_demo_crm import stage_day
from humotech.core.rbac import Actor
from humotech.departments.models import Department
from humotech.employees.models import Employee, EmployeeAssignment, EmployeeDocument
from humotech.employees.services import current_primary_assignment_filter
from humotech.files.models import File
from humotech.files.storage import open_stored
from humotech.notifications.models import Notification
from humotech.offices.models import Office
from humotech.organizations.models import Organization
from humotech.questions.models import EmployeeQuestion
from humotech.rbac.models import Permission, Role, RolePermission
from humotech.schedules.models import EmployeeScheduleAssignment, WorkSchedule
from humotech.telegram.models import TelegramAccount

pytestmark = pytest.mark.django_db

CODE = "DEMOCRM"

#: Среда и суббота. Полдень по UTC — чтобы часовой пояс организации не
#: сдвинул календарную дату ни вперёд, ни назад.
WEDNESDAY = datetime(2026, 9, 9, 12, 0, tzinfo=utc_offset.utc)
SATURDAY = datetime(2026, 9, 12, 12, 0, tzinfo=utc_offset.utc)


@contextmanager
def frozen(moment: datetime):
    """Остановленные часы на время посева и подсчёта.

    Подменяется `django.utils.timezone.now` — через него время берут и
    команда, и `auto_now_add`, и сам тест. Подменять `datetime.now`
    незачем: ни один из этих путей к нему не обращается.
    """
    real = django_timezone.now
    django_timezone.now = lambda: moment
    try:
        yield moment
    finally:
        django_timezone.now = real


@pytest.fixture
def showcase():
    """Организация с витриной. Справочники заводит штатный `seed`.

    Часы стоят на среде и не идут до конца проверки: витрина — картина
    одного дня, и посев с подсчётом обязаны видеть один и тот же день.
    """
    with frozen(WEDNESDAY):
        call_command("seed", create_organization=CODE, verbosity=0)
        call_command("seed_demo_crm", code=CODE, verbosity=0)
        yield Organization.objects.get(code=CODE)


def _stage(org) -> date:
    """Опорный день витрины — тем же правилом, каким его выбрала команда."""
    schedule = WorkSchedule.objects.get(organization=org, name=MAIN_SCHEDULE.name)
    working = frozenset(
        row.weekday for row in schedule.days.all() if row.is_working_day
    )
    tz = ZoneInfo(org.default_timezone)
    return stage_day(django_timezone.now().astimezone(tz).date(), working)


def _bounds(org):
    tz = ZoneInfo(org.default_timezone)
    day = _stage(org)
    return (
        datetime.combine(day, time.min).replace(tzinfo=tz),
        datetime.combine(day, time.max).replace(tzinfo=tz),
    )


def _counts(org) -> dict[str, int]:
    """Состав смены на опорный день — теми же правилами, что у дашборда."""
    low, high = _bounds(org)
    active = Employee.objects.filter(organization=org, employment_status="ACTIVE")
    absent = EmployeeAbsence.objects.filter(
        organization=org, status="ACTIVE", start_at__lte=high, end_at__gte=low
    )
    sessions = AttendanceSession.objects.filter(
        organization=org, started_at__range=(low, high)
    )
    vacation = absent.filter(absence_type__code__in=("ANNUAL_LEAVE", "UNPAID_LEAVE"))
    sick = absent.filter(absence_type__code="SICK_LEAVE")
    in_office = sessions.filter(ended_at__isnull=True).count()
    # Человек мог отметиться и уйти несколькими заходами: считаются люди,
    # а не смены, иначе «ушли» превысит состав.
    left = (
        sessions.filter(ended_at__isnull=False)
        .exclude(employee_id__in=sessions.filter(ended_at__isnull=True)
                 .values("employee_id"))
        .values("employee_id").distinct().count()
    )
    total = active.count()
    return {
        "staff": total,
        "in_office": in_office,
        "left": left,
        "vacation": vacation.count(),
        "sick": sick.count(),
        "not_come": total - in_office - left - vacation.count() - sick.count(),
    }


def _actor(org) -> Actor:
    """Учётка с правами на чтение — для служб дашборда и аналитики."""
    user = User.objects.filter(organization=org).order_by("created_at").first()
    role, _ = Role.objects.get_or_create(
        organization=org, code="DEMO_CHECKER", defaults={"name": "Проверка витрины"}
    )
    for code in ("analytics.read", "attendance.read", "employees.read", "absences.read"):
        permission = Permission.objects.filter(code=code).first()
        if permission is not None:
            RolePermission.objects.get_or_create(role=role, permission=permission)
    UserRoleScope.objects.get_or_create(
        organization=org, user=user, role=role, region=None, office=None,
        defaults={"valid_from": django_timezone.now() - timedelta(days=1)},
    )
    return Actor(user_id=user.id, organization_id=org.id)


# --- состав ------------------------------------------------------------------


def test_showcase_totals_match_the_table(showcase):
    counts = _counts(showcase)
    assert counts["staff"] == sum(item.staff for item in OFFICES) == 248
    assert counts["in_office"] == sum(item.in_office for item in OFFICES) == 193
    assert counts["not_come"] == sum(item.not_come for item in OFFICES) == 9
    assert counts["vacation"] == sum(item.vacation for item in OFFICES) == 22
    assert counts["sick"] == sum(item.sick for item in OFFICES) == 12
    assert counts["left"] == sum(item.left for item in OFFICES) == 12
    # «По графику» — это пришедшие, ушедшие и не пришедшие вместе.
    assert counts["in_office"] + counts["left"] + counts["not_come"] == 214


def test_twelve_offices_with_regions_and_points(showcase):
    offices = Office.objects.filter(organization=showcase, status="ACTIVE")
    assert offices.count() == 12
    names = set(offices.values_list("name", flat=True))
    assert "Головной офис" in names
    assert "Самарканд" in names
    # У каждого офиса есть точка отметки: событие QR без точки база не
    # принимает, и офис без неё показывал бы состав, в который нельзя
    # отметиться.
    for office in offices:
        assert office.qr_points.exists(), office.name
    # У крупных офисов их несколько: служебный вход и зона сотрудников.
    head = offices.get(name="Головной офис")
    assert head.qr_points.count() >= 3


def test_structure_is_not_a_uniform_grid(showcase):
    """Отделы и должности разные, а не один набор на все офисы."""
    departments = Department.objects.filter(organization=showcase, status="ACTIVE")
    names = set(departments.values_list("name", flat=True))
    assert {"Руководство", "Юридический отдел", "Комплаенс"} <= names
    # Юридический отдел заведён только в головном офисе.
    legal = departments.filter(name="Юридический отдел")
    assert legal.count() == 1
    assert legal.first().office.name == "Головной офис"

    used = set(
        EmployeeAssignment.objects.filter(employee__organization=showcase)
        .values_list("position__name", flat=True)
    )
    assert len(used) >= 8, used


def test_employees_are_people_and_not_placeholders(showcase):
    """Анкета заполнена и согласована сама с собой."""
    rows = list(Employee.objects.filter(organization=showcase, employment_status="ACTIVE"))
    assert len(rows) == 248

    numbers = [row.employee_number for row in rows]
    assert len(set(numbers)) == len(numbers)
    phones = [row.phone for row in rows]
    assert len(set(phones)) == len(phones)
    emails = [row.corporate_email for row in rows]
    assert len(set(emails)) == len(emails)

    for row in rows:
        assert row.gender in ("MALE", "FEMALE"), row.employee_number
        assert row.marital_status is not None, row.employee_number
        assert row.birth_date is not None and row.middle_name
        # Отчество согласовано с полом: «Каримов Дилноза Рустамовна»
        # читается как ошибка ввода, а не как редкий случай.
        ending = row.middle_name[-3:]
        assert ending == ("вна" if row.gender == "FEMALE" else "вич"), row.middle_name


def test_recent_hires_and_leavers_exist(showcase):
    stage = _stage(showcase)
    recent = Employee.objects.filter(
        organization=showcase, employment_status="ACTIVE",
        hire_date__gte=stage - timedelta(days=30),
    )
    assert 8 <= recent.count() <= 12, recent.count()
    assert Employee.objects.filter(
        organization=showcase, employment_status="TERMINATED"
    ).count() == 3
    assert Employee.objects.filter(
        organization=showcase, employment_status="SUSPENDED"
    ).count() == 2


def test_every_active_employee_has_a_current_assignment(showcase):
    """Активный сотрудник без действующего назначения не попадёт в состав.

    Он исчез бы из посещаемости и из аналитики, а в списке остался —
    и числа перестали бы сходиться между экранами.
    """
    stage = _stage(showcase)
    active = Employee.objects.filter(organization=showcase, employment_status="ACTIVE")
    current = set(
        EmployeeAssignment.objects.filter(
            current_primary_assignment_filter(stage),
            employee__organization=showcase,
        ).values_list("employee_id", flat=True)
    )
    missing = [row.employee_number for row in active if row.id not in current]
    assert not missing, missing[:5]

    # И ровно одно: два действующих основных назначения означали бы, что
    # человек числится в двух офисах сразу.
    doubled = [
        number for number, count in Counter(
            EmployeeAssignment.objects.filter(
                current_primary_assignment_filter(stage),
                employee__organization=showcase,
            ).values_list("employee__employee_number", flat=True)
        ).items() if count > 1
    ]
    assert not doubled, doubled[:5]


def test_assignment_department_belongs_to_its_office(showcase):
    """Отдел из чужого офиса — это перевод, которого не было."""
    wrong = [
        row.employee.employee_number
        for row in EmployeeAssignment.objects.filter(
            employee__organization=showcase
        ).select_related("employee", "office", "department")
        if row.department.office_id != row.office_id
    ]
    assert not wrong, wrong[:5]


def test_schedules_are_not_all_the_same(showcase):
    used = Counter(
        EmployeeScheduleAssignment.objects.filter(organization=showcase)
        .values_list("schedule__name", flat=True)
    )
    assert len(used) == len(SCHEDULES), dict(used)
    # Большинство на основной пятидневке — иначе «основной» она бы не была.
    assert used[MAIN_SCHEDULE.name] > sum(used.values()) / 2


# --- отметки -----------------------------------------------------------------


def test_attendance_has_no_future_and_no_non_working_days(showcase):
    """Ни одной смены в будущем и ни одной в нерабочий день графика.

    Смена в выходной сделала бы выходной «рабочим днём с явкой», и
    график перестал бы отличать выходной от провала явки. Рабочие дни
    берутся у графика КАЖДОГО человека: у шестидневки суббота рабочая,
    и её смены — не нарушение, а правда.
    """
    tz = ZoneInfo(showcase.default_timezone)
    now = django_timezone.now().astimezone(tz)
    rows = AttendanceSession.objects.filter(organization=showcase)
    assert not rows.filter(started_at__gt=now).exists()

    working = {
        row.employee_id: frozenset(
            day.weekday for day in row.schedule.days.all() if day.is_working_day
        )
        for row in EmployeeScheduleAssignment.objects.filter(organization=showcase)
        .select_related("schedule").prefetch_related("schedule__days")
    }
    bad = [
        (row.employee_id, row.started_at)
        for row in rows.only("employee_id", "started_at")
        if row.started_at.astimezone(tz).date().isoweekday() not in working.get(row.employee_id, frozenset())
    ]
    assert not bad, bad[:5]


def test_sessions_are_possible(showcase):
    """Выход не раньше входа, смены одного человека не пересекаются."""
    rows = AttendanceSession.objects.filter(organization=showcase)
    assert not rows.filter(ended_at__lt=F("started_at")).exists()
    assert not rows.filter(duration_seconds__lt=0).exists()
    # Закрытая смена без выхода — это незакрытая смена, названная закрытой.
    assert not rows.filter(status="CLOSED", exit_event__isnull=True).exists()

    previous = {}
    overlaps = []
    for row in rows.order_by("employee_id", "started_at").only(
        "employee_id", "started_at", "ended_at"
    ):
        before = previous.get(row.employee_id)
        if before is not None and before.ended_at is not None:
            if row.started_at < before.ended_at:
                overlaps.append(row.employee_id)
        previous[row.employee_id] = row
    assert not overlaps, overlaps[:5]


def test_only_one_open_session_is_stale(showcase):
    """Открытые смены — сегодняшние, кроме одной нарочной.

    База держит по одной открытой смене на человека. Случайная открытая
    смена в прошлом отняла бы место у сегодняшней, и человек перестал бы
    считаться находящимся в офисе.
    """
    tz = ZoneInfo(showcase.default_timezone)
    stage = _stage(showcase)
    stale = [
        row for row in AttendanceSession.objects.filter(
            organization=showcase, status="OPEN"
        ).select_related("employee")
        if row.started_at.astimezone(tz).date() != stage
    ]
    assert len(stale) == 1, [row.employee.employee_number for row in stale]
    assert stale[0].employee.employee_number.endswith("09")


def test_absence_never_overlaps_a_shift(showcase):
    """В день отпуска отметок нет.

    Иначе система показала бы человека и в офисе, и в отпуске
    одновременно, и оба числа были бы «настоящими».
    """
    tz = ZoneInfo(showcase.default_timezone)
    busy = set()
    for row in EmployeeAbsence.objects.filter(
        organization=showcase, status__in=("ACTIVE", "COMPLETED")
    ):
        day = row.start_at.astimezone(tz).date()
        while day <= row.end_at.astimezone(tz).date():
            busy.add((row.employee_id, day))
            day += timedelta(days=1)

    clashes = [
        row.employee_id
        for row in AttendanceSession.objects.filter(organization=showcase)
        .only("employee_id", "started_at")
        if (row.employee_id, row.started_at.astimezone(tz).date()) in busy
    ]
    assert not clashes, clashes[:5]


def test_showcase_scenarios_are_all_present(showcase):
    """Каждый сценарий показывает ровно то, что обещает."""
    tz = ZoneInfo(showcase.default_timezone)
    low, high = _bounds(showcase)
    found = {}
    for one in SHOWCASE:
        employee = Employee.objects.filter(
            organization=showcase, employee_number=one.number
        ).first()
        assert employee is not None, one.number
        today = AttendanceSession.objects.filter(
            employee=employee, started_at__range=(low, high)
        )
        found[one.number] = (today.count(), today.filter(ended_at__isnull=True).count())

    assert found["DEMO-S-06"][1] == 1, "«сейчас в офисе» — открытая смена"
    assert found["DEMO-S-07"] == (1, 0), "«уже завершил день» — закрытая смена"
    assert found["DEMO-S-08"] == (0, 0), "«не отметился» — смен нет"
    assert found["DEMO-S-10"] == (2, 0), "два входа и два выхода"
    assert found["DEMO-S-11"] == (3, 0), "три коротких посещения"
    assert found["DEMO-S-13"] == (0, 0), "в отпуске"
    assert found["DEMO-S-14"] == (0, 0), "на больничном"

    # Перевод между офисами: отметки до перевода принадлежат прежнему
    # офису, после — новому. Иначе явка нарисована там, где человека нет.
    moved = Employee.objects.get(organization=showcase, employee_number="DEMO-S-12")
    offices = {
        row.office.name
        for row in AttendanceSession.objects.filter(employee=moved).select_related("office")
    }
    assert len(offices) == 2, offices

    # Шестидневка: у этого человека есть субботние смены, и это не
    # нарушение — суббота у него рабочая.
    six = Employee.objects.get(organization=showcase, employee_number="DEMO-S-15")
    saturdays = [
        row for row in AttendanceSession.objects.filter(employee=six)
        if row.started_at.astimezone(tz).date().isoweekday() == 6
    ]
    assert saturdays


# --- очереди -----------------------------------------------------------------


def test_every_supported_request_status_is_present(showcase):
    """Все состояния, которые модель умеет, — и ни одного выдуманного."""
    statuses = set(
        AbsenceRequest.objects.filter(organization=showcase)
        .values_list("status", flat=True)
    )
    assert statuses == {"DRAFT", "SUBMITTED", "IN_REVIEW", "APPROVED", "REJECTED",
                        "CANCELLED"}, statuses

    kinds = set(
        AbsenceRequest.objects.filter(organization=showcase)
        .values_list("request_kind", flat=True)
    )
    assert kinds == {"CREATE", "EXTEND", "CANCEL"}, kinds
    # Производная заявка обязана ссылаться на исходную: это правило базы,
    # и отмена «сама по себе» означала бы отмену неизвестно чего.
    assert not AbsenceRequest.objects.filter(
        organization=showcase, parent_request__isnull=True
    ).exclude(request_kind="CREATE").exists()

    absences = set(
        EmployeeAbsence.objects.filter(organization=showcase)
        .values_list("status", flat=True)
    )
    assert absences == {"PLANNED", "ACTIVE", "COMPLETED", "CANCELLED"}, absences

    corrections = set(
        AttendanceCorrectionRequest.objects.filter(organization=showcase)
        .values_list("status", flat=True)
    )
    assert {"SUBMITTED", "IN_REVIEW", "APPROVED", "REJECTED"} <= corrections
    # Заявка на исправление без смены — заявка ни к чему.
    assert not AttendanceCorrectionRequest.objects.filter(
        organization=showcase, attendance_session__isnull=True
    ).exists()


def test_telegram_states_differ_and_ids_are_unique(showcase):
    accounts = list(TelegramAccount.objects.filter(organization=showcase))
    assert {row.status for row in accounts} == {"ACTIVE", "PENDING", "REVOKED"}

    ids = [row.telegram_user_id for row in accounts]
    assert len(set(ids)) == len(ids)

    # Признак в карточке обязан совпадать с состоянием привязки.
    active = {row.employee_id for row in accounts if row.status == "ACTIVE"}
    marked = set(
        Employee.objects.filter(organization=showcase, telegram_connected=True)
        .values_list("id", flat=True)
    )
    assert active == marked

    # И у части людей привязки нет вовсе — иначе очередь «требует
    # внимания» была бы пустой, и проверить её нечем.
    assert Employee.objects.filter(
        organization=showcase, employment_status="ACTIVE"
    ).count() > len(accounts)


def test_questions_and_notifications_cover_their_states(showcase):
    statuses = set(
        EmployeeQuestion.objects.filter(organization=showcase)
        .values_list("status", flat=True)
    )
    assert statuses == {"NEW", "AI_ANSWERED", "ESCALATED_TO_HR", "HR_ANSWERED",
                        "CLOSED"}, statuses
    assert 12 <= EmployeeQuestion.objects.filter(organization=showcase).count() <= 20

    # Ответ там, где он обещан состоянием: «HR ответил» без текста
    # ответа — это неправда о состоянии.
    for row in EmployeeQuestion.objects.filter(
        organization=showcase, status__in=("HR_ANSWERED", "CLOSED")
    ):
        assert row.hr_answer_text, row.question_text

    notices = Notification.objects.filter(organization=showcase)
    assert {"READ", "SENT"} == set(notices.values_list("status", flat=True))
    kinds = set(notices.values_list("notification_type", flat=True))
    assert {"announcement", "office_news", "sick_note_reminder",
            "absence_decision"} <= kinds


# --- сходимость чисел --------------------------------------------------------


def test_dashboard_attendance_and_analytics_agree(showcase):
    """Один знаменатель у карточки, состава смены и аналитики.

    Если ростер снова начнёт считать уволенных, сломается здесь, а не на
    экране кадровика.
    """
    actor = _actor(showcase)
    stage = _stage(showcase)
    counts = _counts(showcase)

    board = DashboardService().summary(actor, day=stage)
    cards = {card.key: card.value for card in board.cards}
    expected = counts["in_office"] + counts["left"] + counts["not_come"]
    assert cards["should_work_today"] == expected == 214
    assert cards["in_office"] == counts["in_office"]
    assert cards["came"] == counts["in_office"] + counts["left"]
    assert cards["vacation"] == counts["vacation"]
    assert cards["sick_leave"] == counts["sick"]

    presence = AttendanceHrService().presence(actor, day=stage).counts()
    assert presence["IN_OFFICE"] == counts["in_office"]
    assert presence["NOT_COME"] == counts["not_come"]

    report = AnalyticsService().organization(actor, first=stage, last=stage)
    point = report.series[0]
    assert point.expected == expected
    assert point.attended == counts["in_office"] + counts["left"]


def test_week_two_weeks_and_month_are_all_filled(showcase):
    """Во всех трёх режимах есть данные, и предыдущий период тоже.

    Пустой пунктир сравнения — самая заметная дыра витрины: режим
    «Месяц» существует ровно ради сравнения с прошлым месяцем.
    """
    actor = _actor(showcase)
    stage = _stage(showcase)
    for days in (7, 14, 30):
        first = stage - timedelta(days=days - 1)
        report = AnalyticsService().organization(actor, first=first, last=stage)
        filled = [point for point in report.series if point.expected]
        assert len(filled) >= days // 2, (days, len(filled))
        assert sum(point.attended for point in report.series) > 0

    # Предыдущий месяц — для сравнения.
    previous = AnalyticsService().organization(
        actor, first=stage - timedelta(days=59), last=stage - timedelta(days=30)
    )
    assert sum(point.attended for point in previous.series) > 0


def test_attendance_looks_human(showcase):
    """Время не машинное, дни не одинаковые, офисы различаются.

    Ровная картина выглядит нарисованной, и на ней нельзя проверить ни
    один график: он покажет прямую линию при любом правиле.
    """
    tz = ZoneInfo(showcase.default_timezone)
    rows = list(
        AttendanceSession.objects.filter(organization=showcase)
        .select_related("office").only("started_at", "office_id")[:4000]
    )
    minutes = Counter(row.started_at.astimezone(tz).minute for row in rows)
    # Ни одна минута не собирает больше четверти приходов: при «всем
    # ровно в 09:00» на нулевой минуте было бы почти всё.
    assert max(minutes.values()) < len(rows) // 4, minutes.most_common(3)
    assert len({row.started_at.astimezone(tz).second for row in rows}) > 30

    by_day = Counter(row.started_at.astimezone(tz).date() for row in rows)
    assert len(set(by_day.values())) > 3, "каждый день одинаковый"


# --- файлы -------------------------------------------------------------------


def test_documents_are_real_files(showcase):
    """Каждая запись файла — настоящий файл, который открывается."""
    rows = list(File.objects.filter(organization=showcase))
    assert len(rows) >= 16, len(rows)
    for row in rows:
        stream = open_stored(row)
        body = stream.read()
        stream.close()
        assert len(body) == row.size_bytes, row.original_filename
        if row.mime_type == "application/pdf":
            assert body.startswith(b"%PDF-"), row.original_filename
        elif row.mime_type == "image/png":
            assert body.startswith(b"\x89PNG\r\n\x1a\n"), row.original_filename
        else:
            assert body.startswith(b"\xff\xd8\xff"), row.original_filename


def test_pdf_documents_are_a4_with_a_watermark(showcase):
    """Бумага — не текстовый файл с расширением .pdf.

    Проверяется то, что увидит читалка: размер листа, наличие страницы
    и текст, прочитанный через ту же таблицу `ToUnicode`, по которой его
    копирует человек.
    """
    font = pdf.load_font()
    rows = File.objects.filter(organization=showcase, mime_type="application/pdf")
    assert rows.count() >= 14, rows.count()
    for row in rows:
        stream = open_stored(row)
        body = stream.read()
        stream.close()
        width, height = pdf.page_size(body)
        assert round(width) == 595 and round(height) == 842, row.original_filename
        assert pdf.page_count(body) >= 1
        text = pdf.text_of(body, font)
        assert pdf.WATERMARK in text, row.original_filename
        assert "ДЕМО" in text


def test_document_dates_match_their_request(showcase):
    """Период в бумаге тот же, что в заявке.

    Иначе документ и карточка рассказывают разное, и проверить по
    бумаге нельзя ничего.
    """
    font = pdf.load_font()
    tz = ZoneInfo(showcase.default_timezone)
    checked = 0
    for row in AbsenceDocument.objects.filter(
        absence_request__organization=showcase
    ).select_related("file", "absence_request", "absence_request__employee"):
        if row.file.mime_type != "application/pdf":
            continue
        stream = open_stored(row.file)
        body = stream.read()
        stream.close()
        text = pdf.text_of(body, font)
        request = row.absence_request
        assert request.employee.last_name in text, row.file.original_filename
        start = request.requested_start_at.astimezone(tz).strftime("%d.%m.%Y")
        assert start in text, (row.file.original_filename, start)
        checked += 1
    assert checked >= 5, checked


def test_documents_belong_to_their_employee(showcase):
    for row in AbsenceDocument.objects.filter(
        absence_request__organization=showcase
    ).select_related("file", "absence_request"):
        assert row.file.uploaded_by_employee_id == row.absence_request.employee_id
    for row in EmployeeDocument.objects.filter(
        organization=showcase, file__isnull=False
    ).select_related("file"):
        assert row.file.uploaded_by_employee_id == row.employee_id


def test_photo_pack_is_consistent(showcase):
    """Фотографии, если пакет есть: у каждого своя и правильного размера.

    Пустой пакет — обычное состояние проекта: без ключа к фотобанку
    снимков нет, и карточка показывает инициалы. Проверять в этом случае
    нечего, и притворяться, что есть, — хуже, чем пропустить.
    """
    entries = photo_pack.pack()
    if not entries:
        pytest.skip("локальный пакет фотографий пуст — карточки показывают инициалы")

    from humotech.core.demo import images

    used = [
        row.photo_id for row in
        Employee.objects.filter(organization=showcase, photo__isnull=False)
    ]
    assert len(set(used)) == len(used), "одна фотография у разных сотрудников"

    for employee in Employee.objects.filter(
        organization=showcase, photo__isnull=False
    ).select_related("photo"):
        stream = open_stored(employee.photo)
        body = stream.read()
        stream.close()
        assert body.startswith(b"\xff\xd8\xff") or body.startswith(b"\x89PNG\r\n\x1a\n")
        if body.startswith(b"\x89PNG"):
            assert images.png_size(body) == (photo_pack.SIZE, photo_pack.SIZE)


# --- повторный запуск и день недели -----------------------------------------


def test_rerun_changes_nothing(showcase):
    def snapshot() -> tuple:
        return (
            Employee.objects.filter(organization=showcase).count(),
            Office.objects.filter(organization=showcase).count(),
            Department.objects.filter(organization=showcase).count(),
            EmployeeAssignment.objects.filter(employee__organization=showcase).count(),
            AttendanceSession.objects.filter(organization=showcase).count(),
            AttendanceEvent.objects.filter(organization=showcase).count(),
            AbsenceRequest.objects.filter(organization=showcase).count(),
            EmployeeAbsence.objects.filter(organization=showcase).count(),
            AbsenceDocument.objects.filter(absence_request__organization=showcase).count(),
            AttendanceCorrectionRequest.objects.filter(organization=showcase).count(),
            EmployeeQuestion.objects.filter(organization=showcase).count(),
            Notification.objects.filter(organization=showcase).count(),
            TelegramAccount.objects.filter(organization=showcase).count(),
            File.objects.filter(organization=showcase).count(),
        )

    before = snapshot()
    with frozen(WEDNESDAY):
        call_command("seed_demo_crm", code=CODE, verbosity=0)
    assert snapshot() == before
    assert _counts(showcase)["staff"] == 248


def test_existing_employees_are_kept(showcase):
    """Сотрудник, заведённый до витрины, остаётся в штате и не входит в 248."""
    stranger = Employee.objects.create(
        organization=showcase,
        employee_number="LEGACY-001",
        first_name="Пётр",
        last_name="Легаси",
        hire_date=django_timezone.now().date(),
        employment_status="ACTIVE",
    )
    with frozen(WEDNESDAY):
        call_command("seed_demo_crm", code=CODE, verbosity=0)
    stranger.refresh_from_db()
    assert stranger.employment_status == "ACTIVE"
    assert _counts(showcase)["staff"] == 249


@pytest.mark.parametrize(
    "moment, staged",
    [
        (WEDNESDAY, date(2026, 9, 9)),
        # Суббота: картина уезжает на пятницу, а сама суббота остаётся
        # выходным для пятидневки — пустым, а не «днём с нулевой явкой».
        (SATURDAY, date(2026, 9, 11)),
    ],
    ids=["будний день", "выходной"],
)
def test_seed_gives_the_same_showcase_on_any_day(moment, staged):
    """Один и тот же результат, в какой бы день ни запустили посев."""
    with frozen(moment):
        call_command("seed", create_organization=CODE, verbosity=0)
        call_command("seed_demo_crm", code=CODE, verbosity=0)
        org = Organization.objects.get(code=CODE)

        assert _stage(org) == staged

        counts = _counts(org)
        assert counts["staff"] == 248
        assert counts["in_office"] == 193
        assert counts["left"] == 12
        assert counts["not_come"] == 9
        assert counts["vacation"] == 22
        assert counts["sick"] == 12

        rows = AttendanceSession.objects.filter(organization=org)
        assert not rows.filter(started_at__gt=moment).exists()


def test_stage_day_steps_back_to_a_working_day():
    """Опорный день — ближайший рабочий назад, и только назад."""
    week = frozenset({1, 2, 3, 4, 5})
    assert stage_day(date(2026, 9, 9), week) == date(2026, 9, 9)
    assert stage_day(date(2026, 9, 12), week) == date(2026, 9, 11)  # суббота
    assert stage_day(date(2026, 9, 13), week) == date(2026, 9, 11)  # воскресенье
    # Шестидневка оставляет субботу на месте: правило читается у графика.
    assert stage_day(date(2026, 9, 12), frozenset({1, 2, 3, 4, 5, 6})) == date(2026, 9, 12)


def test_stage_day_refuses_a_schedule_without_working_days():
    with pytest.raises(CommandError, match="нет ни одного рабочего дня"):
        stage_day(date(2026, 9, 9), frozenset())


def test_production_is_refused(settings, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_MODULE", "config.settings.production")
    with pytest.raises(CommandError, match="Боевые настройки"):
        call_command("seed_demo_crm", code=CODE, yes=True, verbosity=0)


def test_unknown_organization_is_refused(db):
    with pytest.raises(CommandError, match="не найдена"):
        call_command("seed_demo_crm", code="NOPE", verbosity=0)
