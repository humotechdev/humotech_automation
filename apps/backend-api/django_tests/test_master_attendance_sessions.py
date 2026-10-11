"""Автовыход в полночь, ручные исправления сессий HR, проверка REVIEW (ТЗ 12.10).

Решения владельца:
* Р-1: вход без выхода закрывается в 00:00 следующего дня по поясу офиса;
  сессия технически закрыта, статус «Автовыход», часы до полуночи НЕ
  подтверждаются и показываются отдельно; HR уточняет фактический выход с
  причиной; повторный запуск не плодит изменений.
* Р-2: HR создаёт, закрывает и исправляет сессии — с причиной, старыми и
  новыми значениями в журнале, проверкой области и защитой от гонок.
* Р-3: подозрительная отметка учитывается до решения; HR подтверждает или
  аннулирует; аннулирование отражается во всех расчётах и в истории.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from django_tests.conftest import make_office, make_qr_point
from humotech.attendance import statistics
from humotech.attendance.auto_close import close_overdue_sessions
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.attendance.services import register_scan
from humotech.attendance.sessions_hr import AttendanceSessionService
from humotech.audit.models import AuditLog
from humotech.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from humotech.core.timeframes import office_zone
from humotech.schedules.services import BreakSpec, DaySpec, WorkScheduleService

pytestmark = pytest.mark.django_db

HR = (
    "employees.read", "attendance.read", "attendance.correct", "attendance.manual",
    "analytics.read", "schedules.read", "schedules.manage",
)
DAY = date(2026, 9, 7)  # понедельник
NEXT = DAY + timedelta(days=1)


def _local(office, day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=office_zone(office))


def _open(organization, employee, office, at, *, status="ACCEPTED"):
    point = make_qr_point(organization, office, code=f"E-{at:%d%H%M}")
    entry = AttendanceEvent.objects.create(
        organization=organization, employee=employee, office=office, qr_point=point,
        event_type="ENTRY", source="QR", verification_status=status, occurred_at=at,
    )
    return AttendanceSession.objects.create(
        organization=organization, employee=employee, office=office,
        entry_event=entry, started_at=at, status="OPEN",
    )


def _closed(organization, employee, office, start, end, *, exit_status="ACCEPTED"):
    session = _open(organization, employee, office, start)
    point = make_qr_point(organization, office, code=f"X-{end:%d%H%M}")
    exit_event = AttendanceEvent.objects.create(
        organization=organization, employee=employee, office=office, qr_point=point,
        event_type="EXIT", source="QR", verification_status=exit_status, occurred_at=end,
    )
    session.exit_event = exit_event
    session.ended_at = end
    session.duration_seconds = int((end - start).total_seconds())
    session.status = "CLOSED"
    session.save()
    return session


@pytest.fixture()
def hr(make_actor, organization):
    return make_actor(organization, permissions=HR)


@pytest.fixture()
def scheduled(organization, employee, hr):
    service = WorkScheduleService()
    schedule = service.create(
        hr, name="5/2", timezone="Asia/Dushanbe", weekly_minutes=2400,
        days=[DaySpec(weekday=w, is_working_day=w <= 5,
                      start_time=time(9) if w <= 5 else None,
                      end_time=time(18) if w <= 5 else None,
                      breaks=(BreakSpec("Обед", time(13), time(14)),) if w <= 5 else ())
              for w in range(1, 8)])
    service.assign_to_employee(hr, employee_id=employee.id, schedule_id=schedule.id,
                               valid_from=date(2026, 1, 5))
    return schedule


# --- автовыход -----------------------------------------------------------------

class TestAutoClose:
    def test_open_session_is_closed_at_local_midnight_without_confirmed_hours(
        self, organization, employee, office
    ):
        session = _open(organization, employee, office, _local(office, DAY, 9))
        closed = close_overdue_sessions(now=_local(office, NEXT, 0, 5))

        session.refresh_from_db()
        assert closed == 1
        assert session.status == "AUTO_CLOSED"
        assert session.closure_kind == "AUTO_MIDNIGHT"
        assert session.ended_at == _local(office, NEXT, 0)
        assert session.duration_seconds == 0          # подтверждённого времени нет
        assert session.unconfirmed_seconds == 15 * 3600
        assert session.exit_event_id is None          # выход не выдуман
        log = AuditLog.objects.get(action="attendance.session.auto_closed",
                                   entity_id=session.id)
        assert log.new_values["status"] == "AUTO_CLOSED"

    def test_not_closed_before_midnight_and_rerun_changes_nothing(
        self, organization, employee, office
    ):
        session = _open(organization, employee, office, _local(office, DAY, 9))
        assert close_overdue_sessions(now=_local(office, DAY, 23, 59)) == 0
        session.refresh_from_db()
        assert session.status == "OPEN"

        assert close_overdue_sessions(now=_local(office, NEXT, 0, 1)) == 1
        session.refresh_from_db()
        stamp = session.updated_at
        # пропущенный запуск и повтор: тот же результат, без новых записей
        assert close_overdue_sessions(now=_local(office, DAY + timedelta(days=3), 12)) == 0
        session.refresh_from_db()
        assert session.updated_at == stamp
        assert AuditLog.objects.filter(action="attendance.session.auto_closed").count() == 1

    def test_dry_run_counts_without_changing(self, organization, employee, office):
        session = _open(organization, employee, office, _local(office, DAY, 9))
        assert close_overdue_sessions(now=_local(office, NEXT, 1), dry_run=True) == 1
        session.refresh_from_db()
        assert session.status == "OPEN"

    def test_next_morning_scan_starts_a_new_session(self, organization, employee, office):
        """Утренний скан на точке «вход и выход» не закрывает вчерашнюю сессию
        выходом на 24 часа: вчерашняя сначала закрывается автовыходом."""
        yesterday = _open(organization, employee, office, _local(office, DAY, 9))
        point = make_qr_point(organization, office, code="BOTH-1", direction_mode="BOTH")
        moment = _local(office, NEXT, 8, 55)
        result = register_scan(employee_id=employee.id, qr_point=point, now=moment,
                               qr_nonce_hash="n-1", qr_issued_at=moment,
                               qr_expires_at=moment + timedelta(seconds=45))
        assert result.accepted
        assert result.event.event_type == "ENTRY"
        yesterday.refresh_from_db()
        assert yesterday.status == "AUTO_CLOSED"
        assert result.session.status == "OPEN"

    def test_auto_closed_hours_are_not_confirmed_anywhere(
        self, organization, employee, office, hr, scheduled
    ):
        _open(organization, employee, office, _local(office, DAY, 9))
        close_overdue_sessions(now=_local(office, NEXT, 0, 5))

        daily = AttendanceHrService().daily(hr, employee.id, first=DAY, last=DAY)
        row = daily["days"][0]
        assert row["seconds"] == 0
        assert row["unconfirmed_seconds"] == 15 * 3600
        assert row["auto_closed"] is True
        assert row["state"] != "NOT_COME"   # человек пришёл
        assert daily["totals"]["unconfirmed_seconds"] == 15 * 3600

        from humotech.analytics.metrics import AnalyticsService
        totals = AnalyticsService().office(hr, office.id, first=DAY, last=DAY).totals
        assert totals["worked_seconds"] == 0
        assert totals["unconfirmed_seconds"] == 15 * 3600
        assert totals["attended_days"] == 1 and totals["missed_days"] == 0


# --- ручные исправления HR -----------------------------------------------------

class TestHrSessionTools:
    def test_hr_sets_actual_exit_of_auto_closed_session(
        self, organization, employee, office, hr
    ):
        session = _open(organization, employee, office, _local(office, DAY, 9))
        close_overdue_sessions(now=_local(office, NEXT, 0, 5))
        session.refresh_from_db()

        AttendanceSessionService().set_exit(
            hr, session.id, ended_at=_local(office, DAY, 18),
            reason="Забыл отметить выход, подтвердил руководитель",
            expected_updated_at=session.updated_at)

        session.refresh_from_db()
        assert session.status == "CORRECTED"
        assert session.duration_seconds == 9 * 3600
        assert session.unconfirmed_seconds == 0
        assert session.exit_event.source == "MANUAL"
        log = AuditLog.objects.get(action="attendance.session.exit_set", entity_id=session.id)
        assert log.old_values["status"] == "AUTO_CLOSED"
        assert log.new_values["duration_seconds"] == 9 * 3600
        assert log.new_values["reason"].startswith("Забыл")

    def test_reason_is_required(self, organization, employee, office, hr):
        session = _open(organization, employee, office, _local(office, DAY, 9))
        with pytest.raises(ValidationFailed):
            AttendanceSessionService().set_exit(hr, session.id, ended_at=_local(office, DAY, 18),
                                                reason=" ")

    def test_stale_version_is_refused(self, organization, employee, office, hr):
        session = _closed(organization, employee, office, _local(office, DAY, 9),
                          _local(office, DAY, 18))
        stale = session.updated_at - timedelta(seconds=5)
        with pytest.raises(Conflict):
            AttendanceSessionService().correct(
                hr, session.id, started_at=_local(office, DAY, 9, 30),
                reason="Опечатка во входе", expected_updated_at=stale)

    def test_correct_records_old_and_new_values(self, organization, employee, office, hr):
        session = _closed(organization, employee, office, _local(office, DAY, 9),
                          _local(office, DAY, 18))
        AttendanceSessionService().correct(
            hr, session.id, started_at=_local(office, DAY, 8, 30),
            reason="Сбой считывателя у входа", expected_updated_at=session.updated_at)
        session.refresh_from_db()
        assert session.status == "CORRECTED"
        assert session.duration_seconds == int(9.5 * 3600)
        log = AuditLog.objects.get(action="attendance.session.corrected", entity_id=session.id)
        assert log.old_values["duration_seconds"] == 9 * 3600
        assert log.new_values["duration_seconds"] == int(9.5 * 3600)

    def test_hr_creates_session_for_a_past_day(self, organization, employee, office, hr):
        created = AttendanceSessionService().create_session(
            hr, employee_id=employee.id, office_id=office.id,
            started_at=_local(office, DAY, 9), ended_at=_local(office, DAY, 18),
            reason="Сканер офиса не работал весь день")
        assert created.status == "CORRECTED"
        assert created.duration_seconds == 9 * 3600
        assert created.entry_event.source == "MANUAL"
        assert created.exit_event.source == "MANUAL"

    def test_overlap_with_existing_session_is_refused(self, organization, employee, office, hr):
        _closed(organization, employee, office, _local(office, DAY, 9), _local(office, DAY, 18))
        with pytest.raises(Conflict):
            AttendanceSessionService().create_session(
                hr, employee_id=employee.id, office_id=office.id,
                started_at=_local(office, DAY, 17), ended_at=_local(office, DAY, 19),
                reason="Повторный ввод")

    def test_other_office_is_not_found(self, make_actor, organization, region, employee, office):
        other = make_office(organization, region, "OTHER")
        actor = make_actor(organization, permissions=HR, office=other)
        session = _closed(organization, employee, office, _local(office, DAY, 9),
                          _local(office, DAY, 18))
        with pytest.raises(NotFound):
            AttendanceSessionService().correct(actor, session.id, started_at=_local(office, DAY, 8),
                                               reason="Попытка из чужого офиса")

    def test_own_session_is_refused(self, organization, employee, office, hr):
        from humotech.accounts.models import User

        User.objects.filter(id=hr.user_id).update(employee=employee)
        session = _closed(organization, employee, office, _local(office, DAY, 9),
                          _local(office, DAY, 18))
        with pytest.raises(PermissionDenied):
            AttendanceSessionService().correct(hr, session.id, started_at=_local(office, DAY, 8),
                                               reason="Себе поправить вход")


# --- проверка подозрительных отметок -------------------------------------------

class TestReview:
    def test_annulled_entry_invalidates_session_everywhere(
        self, organization, employee, office, hr, scheduled
    ):
        session = _closed(organization, employee, office, _local(office, DAY, 9),
                          _local(office, DAY, 18))
        AttendanceEvent.objects.filter(id=session.entry_event_id).update(
            verification_status="REVIEW")

        AttendanceSessionService().review_event(
            hr, session.entry_event_id, decision="ANNUL", reason="Код переслан коллегой")

        session.refresh_from_db()
        assert session.status == "INVALID"
        event = AttendanceEvent.objects.get(id=session.entry_event_id)
        assert event.verification_status == "REVIEW"   # событие не переписывается
        daily = AttendanceHrService().daily(hr, employee.id, first=DAY, last=DAY)
        assert daily["days"][0]["sessions"] == 0
        log = AuditLog.objects.get(action="attendance.event.reviewed",
                                   entity_id=session.entry_event_id)
        assert log.new_values["decision"] == "ANNUL"

    def test_annulled_exit_leaves_exit_unconfirmed(self, organization, employee, office, hr):
        session = _closed(organization, employee, office, _local(office, DAY, 9),
                          _local(office, DAY, 18), exit_status="REVIEW")
        AttendanceSessionService().review_event(
            hr, session.exit_event_id, decision="ANNUL", reason="Выход отмечен чужим кодом")
        session.refresh_from_db()
        assert session.status == "AUTO_CLOSED"
        assert session.closure_kind == "EXIT_ANNULLED"
        assert session.duration_seconds == 0
        assert session.unconfirmed_seconds == 9 * 3600

    def test_confirm_keeps_counting_and_cannot_be_decided_twice(
        self, organization, employee, office, hr
    ):
        session = _closed(organization, employee, office, _local(office, DAY, 9),
                          _local(office, DAY, 18))
        AttendanceEvent.objects.filter(id=session.entry_event_id).update(
            verification_status="REVIEW")
        service = AttendanceSessionService()
        service.review_event(hr, session.entry_event_id, decision="CONFIRM",
                             reason="Проверено по камере")
        session.refresh_from_db()
        assert session.status == "CLOSED"
        with pytest.raises(Conflict):
            service.review_event(hr, session.entry_event_id, decision="ANNUL", reason="Передумал")

    def test_only_review_events_can_be_decided(self, organization, employee, office, hr):
        session = _closed(organization, employee, office, _local(office, DAY, 9),
                          _local(office, DAY, 18))
        with pytest.raises(Conflict):
            AttendanceSessionService().review_event(hr, session.entry_event_id,
                                                    decision="ANNUL", reason="Не та отметка")

    def test_annulled_events_are_not_counted_as_marks(self, organization, employee, office, hr):
        from humotech.attendance.statistics import _last_marks

        session = _closed(organization, employee, office, _local(office, DAY, 9),
                          _local(office, DAY, 18))
        AttendanceEvent.objects.filter(id=session.entry_event_id).update(
            verification_status="REVIEW")
        AttendanceSessionService().review_event(hr, session.entry_event_id, decision="ANNUL",
                                                reason="Код переслан коллегой")
        last_entry, _ = _last_marks(employee.id)
        assert last_entry is None


def test_employee_sees_auto_exit_and_unconfirmed_time(
    organization, employee, office, telegram_settings
):
    from django_tests.conftest import link_telegram
    from humotech.telegram.identity import resolve_by_telegram_user_id

    link_telegram(employee)
    context = resolve_by_telegram_user_id(777_000_111)
    _open(organization, employee, office, _local(office, DAY, 9))
    close_overdue_sessions(now=_local(office, NEXT, 0, 5))

    report = statistics.for_period(context, DAY, DAY,
                                   now=_local(office, DAY + timedelta(days=2), 10))
    record = report.days[0]
    assert record.seconds == 0
    assert record.unconfirmed_seconds == 15 * 3600
    assert record.sessions[0].auto_closed is True
    assert record.attended is True


def test_assistant_does_not_count_auto_closed_hours(organization, employee, office):
    from humotech.ai_assistant.services import personal_data_service as module

    service_class = next(getattr(module, name) for name in dir(module)
                         if hasattr(getattr(module, name), "_worked_minutes"))
    session = _open(organization, employee, office, _local(office, DAY, 9))
    close_overdue_sessions(now=_local(office, NEXT, 0, 5))
    session.refresh_from_db()
    minutes, has_open = service_class._worked_minutes(
        object.__new__(service_class), [session], now=_local(office, NEXT, 10))
    assert (minutes, has_open) == (0, False)
