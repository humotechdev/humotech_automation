"""Confirmed off-site work counts as work without manufacturing office scans."""

from datetime import date, time, timedelta

import pytest
from django.utils import timezone

from django_tests.test_hr_analytics import five_day_schedule
from humotech.analytics.metrics import AnalyticsService
from humotech.analytics.overview import OverviewService
from humotech.attendance.models import AttendanceEvent, AttendanceSession, FieldWorkRequest
from humotech.reports.builder import ReportBuilderService, ReportSpec
from humotech.reports.sheets import build_sheet
from humotech.schedules.models import ScheduleBreak

pytestmark = pytest.mark.django_db

DAY = date(2026, 3, 2)


def confirmed(organization, employee, office, user, *, day=DAY, status="CONFIRMED",
              norm_seconds=9 * 3600):
    now = timezone.now()
    return FieldWorkRequest.objects.create(
        organization=organization, employee=employee, office=office,
        date=day, status=status, scheduled_start=time(9), scheduled_end=time(18),
        norm_seconds=norm_seconds, requested_by_user=user, requested_at=now,
        responded_at=now if status == "CONFIRMED" else None,
        expires_at=now + timedelta(hours=1),
    )


def test_confirmed_day_credits_work_without_arrival(
    make_actor, make_user, organization, employee, office,
):
    five_day_schedule(organization, employee)
    user = make_user(organization)
    confirmed(organization, employee, office, user)
    actor = make_actor(organization, permissions=("analytics.read",))

    report = AnalyticsService().office(actor, office.id, first=DAY, last=DAY)
    assert report.totals["attended_days"] == 1
    assert report.totals["missed_days"] == 0
    assert report.totals["worked_seconds"] == 9 * 3600
    assert report.totals["office_seconds"] == 0
    assert report.totals["field_work_seconds"] == 9 * 3600
    assert report.totals["field_work_days"] == 1
    assert report.totals["late_arrivals"] == 0
    assert next(r for r in report.ratios if r.key == "punctuality").denominator == 0
    assert AttendanceSession.objects.count() == 0
    assert AttendanceEvent.objects.count() == 0

    overview = OverviewService().overview(
        actor, first=DAY, last=DAY, now=timezone.now(),
    )
    assert overview["summary"]["attendance"]["numerator"] == 1
    assert overview["summary"]["field_work_days"] == 1
    assert overview["summary"]["field_work_seconds"] == 9 * 3600
    assert overview["summary"]["on_time"]["denominator"] == 0
    assert overview["days"][0]["field_work_days"] == 1


def test_pending_does_not_credit_day(
    make_actor, make_user, organization, employee, office,
):
    five_day_schedule(organization, employee)
    confirmed(organization, employee, office, make_user(organization), status="PENDING")
    actor = make_actor(organization, permissions=("analytics.read",))
    report = AnalyticsService().office(actor, office.id, first=DAY, last=DAY)
    assert report.totals["missed_days"] == 1
    assert report.totals["field_work_days"] == 0


def test_report_labels_and_separates_office_from_credited_hours(
    make_actor, make_user, organization, employee, office,
):
    schedule = five_day_schedule(organization, employee)
    schedule.weekly_minutes = 40 * 60
    schedule.save(update_fields=["weekly_minutes"])
    monday = schedule.days.get(weekday=1)
    ScheduleBreak.objects.create(
        schedule_day=monday, name="Обед", start_time=time(13),
        end_time=time(14), is_paid=False,
    )
    confirmed(organization, employee, office, make_user(organization), norm_seconds=8 * 3600)
    actor = make_actor(organization, permissions=(
        "reports.export", "attendance.read", "analytics.read",
    ))
    for kind in ("attendance", "worktime"):
        fields = ("employee", "date", "day_status", "office_time", "work_source") \
            if kind == "attendance" else ("employee", "date", "actual", "office_time", "work_source")
        spec = ReportSpec.build(kind=kind, date_from=DAY, date_to=DAY, fields=fields)
        report = ReportBuilderService().open(actor, spec, author="HR")
        rows = list(report.rows())
        assert len(rows) == 1
        assert rows[0]["office_time"] == 0
        assert rows[0]["work_source"] == "Выездная работа"
        if kind == "attendance":
            assert rows[0]["day_status"] == "FIELD_WORK"
            assert rows[0]["first_entry"] is None
            assert rows[0]["marks"] is None
        else:
            assert rows[0]["actual"] == 8 * 3600
            assert rows[0]["planned"] == rows[0]["actual"]
            assert rows[0]["shortfall"] == 0
    sheet = build_sheet("summary", actor, filters={"date_from": DAY, "date_to": DAY}, author="HR")
    assert "Отработано часов" in sheet.columns
    assert "Часов в офисе" in sheet.columns
    assert "Из них выездных" in sheet.columns
