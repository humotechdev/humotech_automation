"""Трудовой статус: названия и состав смены.

Проверяется не «поле поменялось», а то, из-за чего кадровый учёт расходится
с действительностью: уволенный не должен оставаться в составе смены, а
работающий — должен в неё попадать. Трудовой слой (кем числится) и дневной
(где сегодня) намеренно проверяются рядом: смешать их — главная ошибка.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone as django_timezone

from django_tests.conftest import make_qr_point
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.core.enums import EMPLOYMENT_STATUSES
from humotech.employees.lifecycle import EMPLOYMENT_TITLES, title_of
from humotech.employees.services import EmployeeService

pytestmark = pytest.mark.django_db


@pytest.fixture()
def hr_actor(make_actor, organization):
    return make_actor(
        organization,
        permissions=("employees.read", "employees.manage", "employees.archive",
                     "attendance.read", "offices.read"),
    )


def open_session(organization, employee, office):
    """Сессия собирается из события, как в бою: `entry_event_id` объявлен
    NOT NULL, и сессии «самой по себе» в базе не бывает."""
    entry = AttendanceEvent.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        qr_point=make_qr_point(organization, office, code="LIFE-1"),
        event_type="ENTRY",
        source="QR",
        verification_status="ACCEPTED",
        occurred_at=django_timezone.now(),
    )
    return AttendanceSession.objects.create(
        organization=organization,
        employee=employee,
        office=office,
        entry_event=entry,
        started_at=entry.occurred_at,
        status="OPEN",
    )


# --- названия статусов -------------------------------------------------------


class TestTitles:
    def test_active_is_shown_as_working_not_as_active(self):
        # «Активен» — это про учётную запись, а не про человека.
        assert title_of("ACTIVE") == "Работает"
        assert title_of("TERMINATED") == "Уволен"

    def test_unknown_code_is_returned_as_is(self):
        assert title_of("SOMETHING_NEW") == "SOMETHING_NEW"

    def test_every_status_of_the_database_has_a_title(self):
        # Каждому значению из ограничения базы положена подпись: иначе
        # экран однажды покажет сырой код.
        for status in EMPLOYMENT_STATUSES:
            assert status in EMPLOYMENT_TITLES


# --- трудовой статус и состав смены — разные ответы --------------------------


class TestShiftMembership:
    def test_active_employee_is_part_of_the_shift(
        self, hr_actor, organization, employee, office
    ):
        open_session(organization, employee, office)

        report = AttendanceHrService().presence(hr_actor, day=date.today())

        assert [row.state for row in report.rows] == ["IN_OFFICE"]

    def test_dismissed_person_leaves_the_shift(
        self, hr_actor, organization, employee, office
    ):
        open_session(organization, employee, office)
        EmployeeService().terminate(
            hr_actor, employee.id,
            termination_date=date.today() - timedelta(days=1),
        )

        report = AttendanceHrService().presence(hr_actor, day=date.today())

        # Ни в составе смены, ни в числе работающих: назначение закрыто
        # вчерашним днём, и сегодня человека в компании нет.
        assert report.rows == []
