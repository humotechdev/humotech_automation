"""«Фокус HR» на главной: отборы, которыми открываются его пункты.

Главная считает «ждут справку», «справка ждёт проверки» и «без отметки»,
а ссылка ведёт в список. Число и список обязаны быть одной выборкой —
тогда и проверять их надо одним правилом.

**Стадия в базе совпадает со стадией в ответе.** `stage_of` считает её
на Python, а очередь отбирает в базе. Каждое значение фильтра здесь
сравнивается с `stage_of` тех же заявок: вторая запись правил, которая
разошлась с первой, дала бы список, не совпадающий с числом.

**Отбор сужает, но не расширяет.** Область видимости и офис применяются
к стадии так же, как ко всей очереди.

**«Без отметки» — у кого смена уже началась.** Человек со сменой с шести
вечера в девять утра ещё никуда не опоздал.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from django_tests.test_absences import (  # noqa: F401 — фикстуры
    annual_leave,
    balance,
    certificate,
    context,
    sick_leave,
    soon,
)
from django_tests.test_hr_attendance import DAY, give_schedule
from humotech.absences.models import AbsenceDocument, AbsenceRequest
from humotech.absences.services import AbsenceService, stage_of
from humotech.analytics.dashboard import DashboardService
from humotech.attendance.hr import AttendanceHrService
from humotech.core.timeframes import office_zone

API = "/api/v1"

pytestmark = pytest.mark.django_db

QUEUE_READER = ("absences.read", "attendance.read", "employees.read")
OPEN = "SUBMITTED,IN_REVIEW"


@pytest.fixture()
def queue_client(api_client, make_user, organization):
    api_client.force_authenticate(user=make_user(organization, permissions=QUEUE_READER))
    return api_client


def _sick(context, first: int, *, paper: str | None):
    """Больничный со справкой в заданном состоянии (`None` — без справки)."""
    view = AbsenceService().create(
        context, absence_type_code="SICK_LEAVE",
        first_day=soon(first), last_day=soon(first + 1),
        **({"document": certificate()} if paper else {}),
    )
    if paper:
        AbsenceDocument.objects.filter(absence_request=view.request).exclude(
            document_type="APPLICATION",
        ).update(verification_status=paper)
    return view.request


def _colleague(organization, office, n: int):
    """Ещё один сотрудник того же офиса."""
    from humotech.employees.models import Employee, EmployeeAssignment

    person = Employee.objects.create(
        organization=organization, employee_number=f"EMP-9{n:03d}",
        first_name="Коллега", last_name=f"Номер{n}",
        hire_date=date(2024, 2, 1), employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=person, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 2, 1),
    )
    return person


@pytest.fixture()
def board(context, sick_leave, annual_leave, balance, organization, office):
    """По заявке на каждую стадию, плюс решённая и отпуск.

    Открытый больничный у человека один (это держит база), поэтому
    каждый готовый передаётся коллеге из того же офиса — область
    видимости у всех одна.
    """
    rows = {}
    for n, (name, first, paper) in enumerate((
        ("waiting", 0, None), ("review", 4, "PENDING"), ("verified", 8, "VERIFIED"),
        ("fix", 12, "REJECTED"), ("decided", 16, "PENDING"),
    )):
        rows[name] = _sick(context, first, paper=paper)
        AbsenceRequest.objects.filter(pk=rows[name].pk).update(
            employee=_colleague(organization, office, n),
        )
    rows["leave"] = AbsenceService().create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=soon(30), last_day=soon(31),
    ).request
    AbsenceRequest.objects.filter(pk=rows["decided"].pk).update(status="REJECTED")
    return rows


def _ids(answer) -> set[str]:
    assert answer.status_code == 200, answer.content
    return {row["id"] for row in answer.json()["items"]}


def _stage(request) -> str:
    fresh = AbsenceRequest.objects.select_related("absence_type").prefetch_related(
        "documents", "actions",
    ).get(pk=request.pk)
    return stage_of(fresh)


class TestStageFilter:
    @pytest.mark.parametrize(
        "stage", ["WAITING_DOCUMENTS", "HR_REVIEW", "NEEDS_FIX", "PENDING", "REJECTED"],
    )
    def test_matches_stage_of(self, queue_client, board, stage):
        answer = queue_client.get(f"{API}/requests?kind=absence&stage={stage}")

        expected = {str(one.id) for one in board.values() if _stage(one) == stage}
        assert expected, "у каждой проверяемой стадии есть хотя бы одна заявка"
        assert _ids(answer) == expected

    def test_waiting_for_paper_is_what_home_counts(self, queue_client, board):
        """«Ждут справку»: нет справки или её вернули. Проверенная — нет."""
        answer = queue_client.get(
            f"{API}/requests?kind=absence&type=SICK_LEAVE&request_kind=CREATE,EXTEND"
            f"&status={OPEN}&stage=WAITING_DOCUMENTS,NEEDS_FIX"
        )

        assert _ids(answer) == {str(board["waiting"].id), str(board["fix"].id)}

    def test_paper_on_review_excludes_verified_and_decided(self, queue_client, board):
        """«Справка ждёт проверки»: принятая и решённая сюда не попадают."""
        answer = queue_client.get(
            f"{API}/requests?kind=absence&type=SICK_LEAVE&request_kind=CREATE,EXTEND"
            f"&status={OPEN}&stage=HR_REVIEW&certificate=PENDING"
        )

        assert _ids(answer) == {str(board["review"].id)}

    def test_verified_paper_outweighs_a_pending_one(self, queue_client, board):
        """Самая сильная бумага решает — как в `certificate_state`."""
        AbsenceDocument.objects.create(
            organization_id=board["verified"].organization_id,
            absence_request=board["verified"],
            file=AbsenceDocument.objects.filter(absence_request=board["review"]).first().file,
            document_type="MEDICAL_CERTIFICATE",
            verification_status="PENDING",
        )

        answer = queue_client.get(f"{API}/requests?kind=absence&certificate=PENDING")

        assert str(board["verified"].id) not in _ids(answer)

    def test_unknown_value_is_an_error_not_an_empty_list(self, queue_client, board):
        """Опечатка в ссылке не должна выглядеть как «заявок нет»."""
        answer = queue_client.get(f"{API}/requests?kind=absence&stage=open")

        assert answer.status_code == 400

    def test_corrections_never_match_a_paper_filter(self, queue_client, board):
        answer = queue_client.get(f"{API}/requests?stage=WAITING_DOCUMENTS")

        assert all(row["kind"] == "absence" for row in answer.json()["items"])

    def test_office_filter_still_narrows(self, queue_client, board, other_office):
        answer = queue_client.get(
            f"{API}/requests?kind=absence&stage=WAITING_DOCUMENTS&office_id={other_office.id}"
        )

        assert _ids(answer) == set()

    def test_scope_is_not_widened(
        self, api_client, make_user, organization, other_office, board
    ):
        """Кадровик чужого офиса стадией не открывает чужие заявки."""
        api_client.force_authenticate(user=make_user(
            organization, permissions=QUEUE_READER, office=other_office,
        ))

        answer = api_client.get(f"{API}/requests?kind=absence&stage=WAITING_DOCUMENTS")

        assert _ids(answer) == set()


# --- «без отметки» ----------------------------------------------------------------


@pytest.fixture()
def attendance_actor(make_actor, organization):
    return make_actor(
        organization, permissions=("attendance.read", "employees.read", "offices.read"),
    )


def _clock(monkeypatch, office, at: time) -> None:
    """Часы сервера — DAY, `at` в поясе офиса."""
    moment = datetime.combine(DAY, at, tzinfo=office_zone(office))
    monkeypatch.setattr("humotech.attendance.hr._now", lambda: moment)


class TestShiftStarted:
    def test_before_the_shift_nobody_is_missing_yet(
        self, monkeypatch, attendance_actor, organization, employee, office
    ):
        give_schedule(organization, employee, start=time(9, 0))
        _clock(monkeypatch, office, time(8, 0))

        report = AttendanceHrService().presence(attendance_actor, day=DAY, office_id=office.id)

        row = report.rows[0]
        # Состояние то же: день рабочий и без отметки. Меняется только
        # признак, по которому главная решает, звать ли кадровика.
        assert row.state == "NOT_COME"
        assert row.shift_started is False
        assert AttendanceHrService().presence(
            attendance_actor, day=DAY, office_id=office.id, state="NOT_COME", started=True,
        ).rows == []

    def test_after_the_start_the_person_is_missing(
        self, monkeypatch, attendance_actor, organization, employee, office
    ):
        give_schedule(organization, employee, start=time(9, 0))
        _clock(monkeypatch, office, time(9, 30))

        rows = AttendanceHrService().presence(
            attendance_actor, day=DAY, office_id=office.id, state="NOT_COME", started=True,
        ).rows

        assert [row.employee_id for row in rows] == [employee.id]

    def test_a_past_day_has_started_whole(
        self, monkeypatch, attendance_actor, organization, employee, office
    ):
        give_schedule(organization, employee, start=time(18, 0))
        _clock(monkeypatch, office, time(8, 0))

        row = AttendanceHrService().presence(
            attendance_actor, day=date(2026, 3, 9), office_id=office.id,
        ).rows[0]

        assert row.shift_started is True

    def test_dashboard_card_and_its_link_agree(
        self, monkeypatch, attendance_actor, organization, employee, office, api_client,
        make_user,
    ):
        """Число на главной и список по его ссылке — одна выборка."""
        give_schedule(organization, employee, start=time(9, 0))

        _clock(monkeypatch, office, time(8, 0))
        early = DashboardService().summary(attendance_actor, day=DAY, office_id=office.id)
        card = next(one for one in early.cards if one.key == "not_come_started")
        assert card.value == 0
        # Прежнее «не пришли» не тронуто: его читают и другие экраны.
        assert next(one for one in early.cards if one.key == "not_come").value == 1

        _clock(monkeypatch, office, time(9, 30))
        late = DashboardService().summary(attendance_actor, day=DAY, office_id=office.id)
        card = next(one for one in late.cards if one.key == "not_come_started")
        assert card.value == 1
        assert card.params == {
            "date": DAY.isoformat(), "office_id": str(office.id),
            "state": "NOT_COME", "started": "true",
        }

        api_client.force_authenticate(user=make_user(
            organization, permissions=("attendance.read", "employees.read", "offices.read"),
        ))
        answer = api_client.get(f"{API}/attendance/presence", card.params)
        assert answer.status_code == 200
        assert [row["employee_id"] for row in answer.json()["items"]] == [str(employee.id)]
        assert answer.json()["items"][0]["shift_started"] is True
