"""Формулы аналитики: числитель, знаменатель и честное «неизвестно».

Каждая проверка здесь про одно и то же — про то, что число можно
пересчитать вручную и получить тот же ответ. Показатель, который нельзя
проверить, в кадровом отчёте бесполезен: спорить с ним нечем, а верить
ему не за что.

Отдельно и подробно проверяется нулевой знаменатель. Ноль процентов
утверждает «никто не приходил»; отсутствие рабочих дней в периоде такого
не утверждает. Разница между `null` и `0` здесь — это разница между
«не знаем» и обвинением.
"""

from __future__ import annotations

from datetime import date, datetime, time
from datetime import timezone as dt_timezone
from uuid import uuid4

import pytest

from django_tests.conftest import make_qr_point
from humotech.analytics.metrics import AnalyticsService, Ratio
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.core.errors import PermissionDenied, ValidationFailed
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleDay,
    WorkSchedule,
)

pytestmark = pytest.mark.django_db

API = "/api/v1"

# Понедельник 2 марта — пятница 6 марта 2026: ровно пять рабочих дней.
FIRST = date(2026, 3, 2)
LAST = date(2026, 3, 6)


@pytest.fixture()
def service() -> AnalyticsService:
    return AnalyticsService()


@pytest.fixture()
def analyst(make_actor, organization):
    return make_actor(organization, permissions=("analytics.read",))


def five_day_schedule(organization, employee, *, grace=0):
    schedule = WorkSchedule.objects.create(
        organization=organization,
        name="Пятидневка 09:00-18:00",
        timezone="Asia/Dushanbe",
        weekly_minutes=45 * 60,
        late_grace_minutes=grace,
        status="ACTIVE",
    )
    for weekday in range(1, 8):
        working = weekday <= 5
        ScheduleDay.objects.create(
            schedule=schedule,
            weekday=weekday,
            is_working_day=working,
            start_time=time(9, 0) if working else None,
            end_time=time(18, 0) if working else None,
        )
    EmployeeScheduleAssignment.objects.create(
        organization=organization,
        employee=employee,
        schedule=schedule,
        valid_from=date(2024, 1, 1),
    )
    return schedule


def worked(organization, employee, office, day, *, came=time(9, 0),
           left=time(18, 0)):
    from humotech.core.timeframes import office_zone

    tz = office_zone(office)
    started = datetime.combine(day, came, tzinfo=tz).astimezone(dt_timezone.utc)
    ended = datetime.combine(day, left, tzinfo=tz).astimezone(dt_timezone.utc)
    point = make_qr_point(organization, office, code=f"P-{uuid4().hex[:10]}")

    entry = AttendanceEvent.objects.create(
        organization=organization, employee=employee, office=office,
        qr_point=point, event_type="ENTRY", source="QR",
        verification_status="ACCEPTED", occurred_at=started,
    )
    exit_event = AttendanceEvent.objects.create(
        organization=organization, employee=employee, office=office,
        qr_point=point, event_type="EXIT", source="QR",
        verification_status="ACCEPTED", occurred_at=ended,
    )
    return AttendanceSession.objects.create(
        organization=organization, employee=employee, office=office,
        entry_event=entry, exit_event=exit_event,
        started_at=started, ended_at=ended,
        duration_seconds=int((ended - started).total_seconds()),
        status="CLOSED",
    )


# --- нулевой знаменатель -----------------------------------------------------


class TestZeroDenominator:
    def test_percent_is_null_not_zero(self):
        """Ноль процентов — это утверждение. Отсутствие данных — нет."""
        empty = Ratio(
            key="attendance", title="Посещаемость",
            numerator=0, denominator=0, formula="x / y", unit="days",
        )
        assert empty.percent is None
        assert empty.as_dict()["percent"] is None
        # А числитель со знаменателем всё равно отдаются: по ним видно,
        # что расчёт состоялся и дал пустой знаменатель.
        assert empty.as_dict()["numerator"] == 0
        assert empty.as_dict()["denominator"] == 0

    def test_office_without_employees_reports_nothing_not_zero(
        self, service, analyst, office
    ):
        report = service.office(analyst, office.id, first=FIRST, last=LAST)

        assert report.headcount == 0
        for ratio in report.ratios:
            assert ratio.percent is None, (
                f"{ratio.key}: пустой офис не имеет посещаемости 0%"
            )

    def test_employee_without_schedule_is_not_counted_as_absent(
        self, service, analyst, organization, employee, office
    ):
        """Нет графика — нет и нормы. Такой человек не попадает ни
        в числитель, ни в знаменатель, а `coverage` называет, сколько
        таких. Иначе неполнота справочника превращается в прогулы."""
        report = service.office(analyst, office.id, first=FIRST, last=LAST)

        assert report.headcount == 1
        assert report.coverage.employees_with_schedule == 0
        assert report.coverage.ratio == 0.0
        attendance = next(r for r in report.ratios if r.key == "attendance")
        assert attendance.denominator == 0
        assert attendance.percent is None
        assert report.totals["missed_days"] == 0


# --- формулы -----------------------------------------------------------------


class TestFormulas:
    def test_attendance_is_attended_over_expected(
        self, service, analyst, organization, employee, office
    ):
        five_day_schedule(organization, employee)
        # Пришёл в понедельник, вторник и среду; четверг и пятницу — нет.
        for day in (FIRST, date(2026, 3, 3), date(2026, 3, 4)):
            worked(organization, employee, office, day)

        report = service.office(analyst, office.id, first=FIRST, last=LAST)
        attendance = next(r for r in report.ratios if r.key == "attendance")

        assert attendance.numerator == 3
        assert attendance.denominator == 5
        assert attendance.percent == 60.0
        # Формула не «на глаз»: пересчёт руками даёт то же самое.
        assert round(3 * 100 / 5, 1) == attendance.percent
        assert report.totals["missed_days"] == 2

    def test_absence_days_leave_both_parts_of_the_fraction(
        self, service, analyst, organization, employee, office, make_absence
    ):
        """День оформленного отсутствия не пропуск и не рабочий день.

        Он уходит и из числителя, и из знаменателя: человек законно
        не должен был приходить, и считать это прогулом нельзя, а
        считать посещённым днём — тем более.
        """
        five_day_schedule(organization, employee)
        make_absence(employee, code="SICK_LEAVE", day=date(2026, 3, 4))
        for day in (FIRST, date(2026, 3, 3), date(2026, 3, 5), date(2026, 3, 6)):
            worked(organization, employee, office, day)

        report = service.office(analyst, office.id, first=FIRST, last=LAST)
        attendance = next(r for r in report.ratios if r.key == "attendance")

        assert attendance.denominator == 4, "среда — больничный, не рабочий день"
        assert attendance.numerator == 4
        assert attendance.percent == 100.0
        assert report.totals["sick_leave_days"] == 1

    def test_punctuality_honours_the_grace_period(
        self, service, analyst, organization, employee, office
    ):
        five_day_schedule(organization, employee, grace=15)
        worked(organization, employee, office, FIRST, came=time(9, 10))
        worked(organization, employee, office, date(2026, 3, 3), came=time(9, 40))

        report = service.office(analyst, office.id, first=FIRST, last=LAST)
        punctuality = next(r for r in report.ratios if r.key == "punctuality")

        # Пришедший в 09:10 при допуске 15 минут не опоздал.
        assert punctuality.numerator == 1
        assert punctuality.denominator == 2
        assert punctuality.percent == 50.0
        assert report.totals["late_arrivals"] == 1
        # Опоздание считается СВЕРХ допуска: 40 - 15 = 25.
        assert report.totals["late_minutes_total"] == 25

    def test_hours_are_hours_in_the_office(
        self, service, analyst, organization, employee, office
    ):
        five_day_schedule(organization, employee)
        worked(organization, employee, office, FIRST)

        report = service.office(analyst, office.id, first=FIRST, last=LAST)
        hours = next(r for r in report.ratios if r.key == "worked_vs_expected")

        assert hours.numerator == 9.0, "с 09:00 до 18:00 — девять часов"
        assert hours.denominator == 45.0, "пять дней по девять часов нормы"
        assert "Перерывы" in hours.formula or "перерыв" in hours.formula.lower()

    def test_every_ratio_explains_itself(self, service, analyst, office):
        report = service.office(analyst, office.id, first=FIRST, last=LAST)
        for ratio in report.ratios:
            assert ratio.formula, f"{ratio.key} без определения формулы"
            assert ratio.unit in ("days", "hours", "people")
            body = ratio.as_dict()
            assert {"percent", "numerator", "denominator", "formula"} <= set(body)

    def test_period_and_timezone_come_with_the_numbers(
        self, service, analyst, office
    ):
        report = service.office(analyst, office.id, first=FIRST, last=LAST)
        body = report.as_dict()

        assert body["period"]["first"] == FIRST.isoformat()
        assert body["period"]["last"] == LAST.isoformat()
        assert body["period"]["timezone"] == "Asia/Dushanbe"
        assert body["generated_at"], "дата актуальности обязательна"

    def test_nothing_is_called_efficiency(self, service, analyst, office):
        """Система измеряет посещаемость, а не качество работы."""
        body = str(service.office(analyst, office.id, first=FIRST, last=LAST)
                   .as_dict()).lower()
        for word in ("эффективност", "продуктивност", "efficiency", "productivity"):
            assert word not in body

    def test_absurd_period_is_refused(self, service, analyst, office):
        with pytest.raises(ValidationFailed):
            service.office(analyst, office.id, first=LAST, last=FIRST)
        with pytest.raises(ValidationFailed):
            service.office(
                analyst, office.id, first=date(1970, 1, 1), last=date(2026, 1, 1)
            )


# --- сравнение ---------------------------------------------------------------


class TestComparison:
    def test_both_sides_are_returned_in_full(
        self, service, analyst, organization, employee, office, other_office
    ):
        five_day_schedule(organization, employee)
        worked(organization, employee, office, FIRST)

        result = service.compare(
            analyst, kind="office", left_id=office.id, right_id=other_office.id,
            first=FIRST, last=LAST,
        )

        assert result["left"]["headcount"] == 1
        assert result["right"]["headcount"] == 0
        # Обе стороны целиком, а не только разница: «+12» без «от чего»
        # бесполезно.
        assert result["left"]["totals"]["attended_days"] == 1
        assert result["right"]["totals"]["attended_days"] == 0

    def test_difference_is_in_points_not_percent(self, service, analyst, office):
        result = service.compare(
            analyst, kind="period", left_id=None, right_id=None,
            first=FIRST, last=LAST,
            right_first=date(2026, 2, 2), right_last=date(2026, 2, 6),
        )
        for row in result["differences"]:
            # Разница двух процентов — процентные ПУНКТЫ. Назвать 95 против
            # 90 «разницей в 5%» неверно: 5% от 90 — это 4,5.
            assert "difference_points" in row

    def test_incomparable_sides_say_so(
        self, service, analyst, office, other_office
    ):
        result = service.compare(
            analyst, kind="office", left_id=office.id, right_id=other_office.id,
            first=FIRST, last=LAST,
        )
        for row in result["differences"]:
            assert row["comparable"] is False
            assert row["difference_points"] is None

    def test_period_comparison_needs_the_second_period(
        self, service, analyst
    ):
        with pytest.raises(ValidationFailed):
            service.compare(
                analyst, kind="period", left_id=None, right_id=None,
                first=FIRST, last=LAST,
            )


# --- права -------------------------------------------------------------------


class TestPermissions:
    def test_employee_analytics_needs_a_separate_permission(
        self, make_actor, organization, employee, office
    ):
        """Сводка по офису обезличена; строка по человеку — наблюдение
        за конкретным работником, и право на неё другое."""
        five_day_schedule(organization, employee)
        analyst = make_actor(organization, permissions=("analytics.read",))
        service = AnalyticsService()

        # Офис — можно.
        service.office(analyst, office.id, first=FIRST, last=LAST)
        # Человек — нет.
        with pytest.raises(PermissionDenied):
            service.employee(analyst, employee.id, first=FIRST, last=LAST)

        allowed = make_actor(
            organization, permissions=("analytics.read", "employees.read")
        )
        report = service.employee(allowed, employee.id, first=FIRST, last=LAST)
        assert report.scope_kind == "employee"

    def test_analytics_respects_scope(
        self, make_actor, organization, employee, office, other_office
    ):
        five_day_schedule(organization, employee)
        stranger = make_actor(
            organization, permissions=("analytics.read",), office=other_office
        )
        report = AnalyticsService().organization(
            stranger, first=FIRST, last=LAST
        )
        assert report.headcount == 0

    def test_empty_scope_gives_nothing_not_everything(
        self, make_actor, organization, employee, office
    ):
        from humotech.regions.models import Region

        five_day_schedule(organization, employee)
        empty_region = Region.objects.create(
            organization=organization, code="EMPTY_AN", name="Пустой",
            status="ACTIVE",
        )
        nobody = make_actor(
            organization, permissions=("analytics.read",), region=empty_region
        )

        report = AnalyticsService().organization(nobody, first=FIRST, last=LAST)
        assert report.headcount == 0
        assert all(r.percent is None for r in report.ratios)

    def test_reading_analytics_is_not_reading_attendance(
        self, make_actor, organization, office
    ):
        no_analytics = make_actor(organization, permissions=("attendance.read",))
        with pytest.raises(PermissionDenied):
            AnalyticsService().office(
                no_analytics, office.id, first=FIRST, last=LAST
            )


# --- HTTP и производительность -----------------------------------------------


class TestHttp:
    @pytest.fixture()
    def analytics_client(self, api_client, make_user, organization):
        user = make_user(
            organization, permissions=("analytics.read", "employees.read")
        )
        api_client.force_authenticate(user=user)
        return api_client

    def test_series_comes_with_the_report(
        self, analytics_client, organization, employee, office
    ):
        five_day_schedule(organization, employee)
        worked(organization, employee, office, FIRST)

        body = analytics_client.get(
            f"{API}/analytics",
            {"office_id": str(office.id), "date_from": FIRST.isoformat(),
             "date_to": LAST.isoformat()},
        ).json()

        assert len(body["series"]) == 5
        assert body["series"][0]["day"] == FIRST.isoformat()
        assert body["series"][0]["attended"] == 1
        assert body["coverage"]["employees_with_schedule"] == 1

    def test_series_can_be_switched_off(
        self, analytics_client, office
    ):
        body = analytics_client.get(
            f"{API}/analytics", {"office_id": str(office.id), "series": "false"}
        ).json()
        assert "series" not in body

    def test_anonymous_gets_nothing(self, api_client):
        assert api_client.get(f"{API}/analytics").status_code in (401, 403)


class TestQueryCount:
    def test_month_costs_the_same_as_a_day(
        self, service, analyst, organization, office, django_assert_max_num_queries
    ):
        """Число запросов не зависит ни от длины периода, ни от штата.

        Расчёт «день за днём» дал бы тридцать раз по пять запросов на
        месячный отчёт. Здесь все данные периода берутся один раз, а
        считается уже в памяти.
        """
        for number in range(6):
            person = Employee.objects.create(
                organization=organization,
                employee_number=f"AN-{number:03d}",
                first_name="Тест", last_name=f"Аналитика{number}",
                hire_date=date(2024, 1, 1), employment_status="ACTIVE",
            )
            EmployeeAssignment.objects.create(
                organization=organization, employee=person, office=office,
                employment_type="FULL_TIME", work_mode="ONSITE",
                is_primary=True, valid_from=date(2024, 1, 1),
            )
            five_day_schedule(organization, person)
            worked(organization, person, office, FIRST)

        with django_assert_max_num_queries(12):
            service.office(analyst, office.id, first=FIRST, last=FIRST)

        with django_assert_max_num_queries(12):
            report = service.office(
                analyst, office.id, first=date(2026, 3, 1), last=date(2026, 3, 31)
            )
        assert len(report.series) == 31
