"""Кадровые операции: приём, карточка, перевод, деактивация, увольнение.

Всё на настоящем PostgreSQL: EXCLUDE-ограничения на периоды назначений и
внешние ключи с RESTRICT — часть проверяемого поведения, а не фон.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select

from src.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from src.modules.audit.models import AuditLog
from src.modules.departments.models import Department
from src.modules.employees.models import Employee, EmployeeAssignment
from src.modules.employees.schemas import (
    AssignmentChangeRequest,
    EmployeeCreateRequest,
    EmployeeUpdateRequest,
)
from src.modules.employees.service import EmployeeService
from src.modules.offices.service import OfficeService
from src.modules.positions.models import Position
from src.modules.qr_attendance.models import AttendanceEvent, AttendanceSession
from src.modules.schedules.service import WorkScheduleService
from src.modules.telegram.models import TelegramAccount

pytestmark = pytest.mark.usefixtures("engine")


def _new_employee(office, **overrides) -> EmployeeCreateRequest:
    payload = dict(
        employee_number="EMP-1000",
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2025, 1, 15),
        office_id=office.id,
        phone="+992 900 11 22 33",
        corporate_email="p.petrov@humotech.tj",
    )
    payload.update(overrides)
    return EmployeeCreateRequest(**payload)


def _position(db, organization, code="DEV", status="ACTIVE") -> Position:
    position = Position(organization_id=organization.id, code=code,
                        name="Разработчик", status=status)
    db.add(position)
    db.flush()
    return position


def _department(db, organization, office, code="IT", status="ACTIVE") -> Department:
    department = Department(organization_id=organization.id, office_id=office.id,
                            code=code, name="ИТ-отдел", status=status)
    db.add(department)
    db.flush()
    return department


# --- 2. Приём сотрудника и назначение в офис --------------------------------

def test_hr_creates_employee_and_assigns_to_office(
    db, organization, region, office, hr_actor
):
    position = _position(db, organization)
    department = _department(db, organization, office)

    card = EmployeeService(db).create(
        hr_actor,
        _new_employee(office, position_id=position.id,
                      department_id=department.id, region_id=region.id),
    )

    assert card.employment_status == "ACTIVE"
    assert card.full_name == "Петров Пётр"
    assert card.current_assignment is not None
    assert card.current_assignment.office_id == office.id
    assert card.current_assignment.region_id == region.id
    assert card.current_assignment.department_name == "ИТ-отдел"
    assert card.current_assignment.position_name == "Разработчик"
    assert card.current_assignment.valid_from == date(2025, 1, 15)
    assert card.current_assignment.valid_to is None
    assert card.telegram.connected is False


def test_employee_is_created_together_with_assignment(db, office, hr_actor):
    """Сотрудник без назначения не виден HR с территориальной областью,
    поэтому обе строки создаются одной операцией."""
    card = EmployeeService(db).create(hr_actor, _new_employee(office))
    count = db.scalar(
        select(func.count(EmployeeAssignment.id)).where(
            EmployeeAssignment.employee_id == card.id
        )
    )
    assert count == 1


def test_region_not_matching_office_is_rejected(
    db, office, other_region, hr_actor
):
    with pytest.raises(ValidationFailed, match="не совпадает с регионом офиса"):
        EmployeeService(db).create(
            hr_actor, _new_employee(office, region_id=other_region.id)
        )


def test_contacts_are_validated_and_stored(db, office, hr_actor):
    service = EmployeeService(db)
    card = service.create(hr_actor, _new_employee(
        office, phone=" +992 900 11 22 33 ", corporate_email=" HR@humotech.tj "))
    assert card.phone == "+992 900 11 22 33", "лишние пробелы обрезаны"
    assert card.corporate_email == "HR@humotech.tj"

    with pytest.raises(ValidationFailed, match="телефона"):
        service.create(hr_actor, _new_employee(
            office, employee_number="EMP-2", phone="позвоните мне"))
    with pytest.raises(ValidationFailed, match="почты"):
        service.create(hr_actor, _new_employee(
            office, employee_number="EMP-3", corporate_email="без-собаки.tj"))


def test_inactive_department_and_foreign_position_are_rejected(
    db, organization, office, other_office, hr_actor
):
    service = EmployeeService(db)
    closed = _department(db, organization, office, code="OLD", status="INACTIVE")
    with pytest.raises(Conflict, match="Отдел не активен"):
        service.create(hr_actor, _new_employee(office, department_id=closed.id))

    elsewhere = _department(db, organization, other_office, code="ELSE")
    with pytest.raises(ValidationFailed, match="другому офису"):
        service.create(hr_actor, _new_employee(
            office, employee_number="EMP-9", department_id=elsewhere.id))


# --- 9. Деактивированный офис нельзя назначить ------------------------------

def test_inactive_office_cannot_receive_new_employee(db, office, hr_actor):
    OfficeService(db).deactivate(hr_actor, office.id)
    with pytest.raises(Conflict, match="Офис не активен"):
        EmployeeService(db).create(hr_actor, _new_employee(office))


def test_inactive_office_cannot_receive_transfer(
    db, organization, office, other_office, employee, hr_actor
):
    OfficeService(db).deactivate(hr_actor, other_office.id)
    with pytest.raises(Conflict, match="Офис не активен"):
        EmployeeService(db).change_assignment(
            hr_actor, employee.id,
            AssignmentChangeRequest(effective_from=date(2025, 3, 1),
                                    office_id=other_office.id),
        )


# --- 11. Офис другой организации нельзя назначить ---------------------------

def test_foreign_office_cannot_be_assigned(db, foreign_office, hr_actor, employee):
    service = EmployeeService(db)
    with pytest.raises(NotFound, match="Офис не найден"):
        service.create(hr_actor, _new_employee(foreign_office))
    with pytest.raises(NotFound, match="Офис не найден"):
        service.change_assignment(
            hr_actor, employee.id,
            AssignmentChangeRequest(effective_from=date(2025, 3, 1),
                                    office_id=foreign_office.id),
        )


def test_foreign_employee_is_not_found(db, employee, foreign_actor):
    with pytest.raises(NotFound, match="Сотрудник не найден"):
        EmployeeService(db).get(foreign_actor, employee.id)


# --- 4. Список и карточка ---------------------------------------------------

def test_employee_appears_in_list_and_card(db, organization, office, hr_actor):
    service = EmployeeService(db)
    position = _position(db, organization)
    created = service.create(hr_actor, _new_employee(office, position_id=position.id))

    listed = service.list(hr_actor, at=date(2025, 6, 1))
    item = next(row for row in listed.items if row.id == created.id)
    assert item.full_name == "Петров Пётр"
    assert item.office_id == office.id
    assert item.office_name == office.name
    assert item.region_name == "Душанбе"
    assert item.position_name == "Разработчик"
    assert item.employment_status == "ACTIVE"

    card = service.get(hr_actor, created.id, at=date(2025, 6, 1))
    assert card.employee_number == "EMP-1000"
    assert card.corporate_email == "p.petrov@humotech.tj"
    assert len(card.assignment_history) == 1


def test_card_shows_schedule_and_telegram_binding(
    db, organization, employee, work_schedule, hr_actor, now
):
    WorkScheduleService(db).assign_to_employee(
        hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
        valid_from=date(2025, 1, 1),
    )
    db.add(TelegramAccount(
        organization_id=organization.id, employee_id=employee.id,
        telegram_user_id=987654321, telegram_chat_id=987654321,
        telegram_username="petrov", status="ACTIVE", connected_at=now,
    ))
    db.flush()

    card = EmployeeService(db).get(hr_actor, employee.id, at=date(2025, 6, 1))
    assert card.current_schedule is not None
    assert card.current_schedule.name == work_schedule.name
    assert card.current_schedule.timezone == "Asia/Dushanbe"
    assert card.telegram.connected is True
    assert card.telegram.username == "petrov"
    assert card.telegram.status == "ACTIVE"


def test_revoked_telegram_binding_reads_as_disconnected(
    db, organization, employee, hr_actor, now
):
    db.add(TelegramAccount(
        organization_id=organization.id, employee_id=employee.id,
        telegram_user_id=111222333, telegram_chat_id=111222333,
        status="REVOKED", connected_at=now,
    ))
    db.flush()
    card = EmployeeService(db).get(hr_actor, employee.id)
    assert card.telegram.connected is False
    assert card.telegram.status == "REVOKED"


# --- 5. Поиск, фильтры, пагинация -------------------------------------------

def test_employee_search_filters_and_pagination(
    db, organization, region, other_region, office, other_office, hr_actor
):
    service = EmployeeService(db)
    for index in range(6):
        service.create(hr_actor, _new_employee(
            office if index % 2 == 0 else other_office,
            employee_number=f"EMP-{index:03d}",
            first_name="Али" if index % 2 == 0 else "Зульфия",
            last_name=f"Фамилия{index}",
        ))

    at = date(2025, 6, 1)
    assert service.list(hr_actor, at=at, search="Али").size == 3
    assert service.list(hr_actor, at=at, search="EMP-003").size == 1
    assert service.list(hr_actor, at=at, office_id=office.id).size == 3
    assert service.list(hr_actor, at=at, region_id=other_region.id).size == 3

    service.deactivate(hr_actor, service.list(hr_actor, at=at).items[0].id)
    assert service.list(hr_actor, at=at, status="SUSPENDED").size == 1
    assert service.list(hr_actor, at=at, status="ACTIVE").size == 5

    seen, cursor, pages = [], None, 0
    while True:
        page = service.list(hr_actor, at=at, limit=4, cursor=cursor)
        seen.extend(row.id for row in page.items)
        pages += 1
        if not page.next_cursor:
            break
        cursor = page.next_cursor
        assert pages < 10
    assert len(seen) == 6 and len(set(seen)) == 6


def test_employee_list_does_not_grow_queries_with_page_size(
    db, organization, office, hr_actor
):
    """Число запросов не должно зависеть от размера страницы.

    Проверяется счётчиком операторов, а не наличием `selectinload` в коде:
    иначе тест подтверждал бы намерение, а не результат.
    """
    service = EmployeeService(db)
    for index in range(15):
        service.create(hr_actor, _new_employee(
            office, employee_number=f"N-{index:03d}", first_name=f"Имя{index}"))

    counter = {"n": 0}

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    try:
        counter["n"] = 0
        service.list(hr_actor, at=date(2025, 6, 1), limit=3)
        for_three = counter["n"]

        counter["n"] = 0
        service.list(hr_actor, at=date(2025, 6, 1), limit=15)
        for_fifteen = counter["n"]
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", _count)

    assert for_three == for_fifteen, (
        f"запросов на 3 сотрудника: {for_three}, на 15: {for_fifteen} — это N+1"
    )
    assert for_fifteen <= 3, f"на список уходит {for_fifteen} запросов"


# --- 6. Изменение офиса -----------------------------------------------------

def test_office_change_closes_old_period_and_opens_new(
    db, organization, office, other_office, employee, hr_actor
):
    service = EmployeeService(db)
    result = service.change_assignment(
        hr_actor, employee.id,
        AssignmentChangeRequest(effective_from=date(2025, 3, 1),
                                office_id=other_office.id),
    )
    assert result.office_id == other_office.id
    assert result.valid_from == date(2025, 3, 1)
    assert result.valid_to is None

    periods = db.scalars(
        select(EmployeeAssignment)
        .where(EmployeeAssignment.employee_id == employee.id)
        .order_by(EmployeeAssignment.valid_from)
    ).all()
    assert len(periods) == 2, "старое назначение сохранено, а не переписано"
    assert periods[0].office_id == office.id
    assert periods[0].valid_to == date(2025, 2, 28), (
        "границы периода включительные: старый закрывается днём раньше нового"
    )
    assert periods[1].office_id == other_office.id

    # карточка «на вчера» показывает прежний офис
    before = service.get(hr_actor, employee.id, at=date(2025, 2, 1))
    assert before.current_assignment.office_id == office.id
    after = service.get(hr_actor, employee.id, at=date(2025, 6, 1))
    assert after.current_assignment.office_id == other_office.id


def test_transfer_keeps_position_and_drops_department_of_old_office(
    db, organization, office, other_office, hr_actor
):
    service = EmployeeService(db)
    position = _position(db, organization)
    department = _department(db, organization, office)
    card = service.create(hr_actor, _new_employee(
        office, position_id=position.id, department_id=department.id))

    moved = service.change_assignment(
        hr_actor, card.id,
        AssignmentChangeRequest(effective_from=date(2025, 5, 1),
                                office_id=other_office.id),
    )
    assert moved.position_name == "Разработчик", "должность переносится"
    assert moved.department_id is None, "отдел принадлежал прежнему офису"


def test_position_change_creates_new_period(db, organization, employee, hr_actor):
    service = EmployeeService(db)
    senior = _position(db, organization, code="SENIOR")
    result = service.change_assignment(
        hr_actor, employee.id,
        AssignmentChangeRequest(effective_from=date(2025, 4, 1),
                                position_id=senior.id),
    )
    assert result.position_id == senior.id, "должность действительно сменилась"
    history = service.assignment_history(hr_actor, employee.id)
    assert len(history) == 2


def test_transfer_before_hire_date_is_rejected(db, employee, other_office, hr_actor):
    with pytest.raises(ValidationFailed, match="раньше даты приёма"):
        EmployeeService(db).change_assignment(
            hr_actor, employee.id,
            AssignmentChangeRequest(effective_from=date(2023, 1, 1),
                                    office_id=other_office.id),
        )


def test_backdated_transfer_is_rejected_with_clear_message(
    db, employee, other_office, hr_actor
):
    """Перевод «в прошлое» отклоняется понятной ошибкой, а не нарушением
    ограничения в базе."""
    service = EmployeeService(db)
    service.change_assignment(hr_actor, employee.id, AssignmentChangeRequest(
        effective_from=date(2025, 6, 1), office_id=other_office.id))
    with pytest.raises(ValidationFailed, match="позже начала действующего"):
        service.change_assignment(hr_actor, employee.id, AssignmentChangeRequest(
            effective_from=date(2025, 3, 1), office_id=other_office.id))


# --- 16. Ошибка в середине операции откатывает всё ---------------------------

def test_failed_transfer_leaves_previous_assignment_untouched(
    db, organization, office, other_office, employee, hr_actor
):
    """Перевод состоит из двух шагов: закрыть старый период и вставить новый.

    Если второй шаг падает, первый обязан откатиться — иначе у сотрудника
    остаётся закрытое назначение и ни одного действующего, то есть он
    исчезает из всех списков по текущему составу.
    """
    service = EmployeeService(db)
    # заранее занимаем период, в который попытаемся перевести
    service.change_assignment(hr_actor, employee.id, AssignmentChangeRequest(
        effective_from=date(2025, 3, 1), office_id=other_office.id))

    open_period = db.scalars(
        select(EmployeeAssignment).where(
            EmployeeAssignment.employee_id == employee.id,
            EmployeeAssignment.valid_to.is_(None),
        )
    ).one()
    assert open_period.valid_from == date(2025, 3, 1)

    # ломаем вторую вставку: делаем период, который пересечётся с уже закрытым
    forced = EmployeeAssignment(
        organization_id=organization.id, employee_id=employee.id,
        office_id=office.id, employment_type="FULL_TIME", work_mode="ONSITE",
        is_primary=True, valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31),
    )
    with pytest.raises(Conflict, match="пересекаются"):
        with service.atomic():
            open_period.valid_to = date(2025, 5, 31)
            service.flush()
            db.add(forced)
            service.flush()

    db.expire_all()
    still_open = db.scalars(
        select(EmployeeAssignment).where(
            EmployeeAssignment.employee_id == employee.id,
            EmployeeAssignment.valid_to.is_(None),
        )
    ).one()
    assert still_open.valid_from == date(2025, 3, 1), (
        "закрытие периода откатилось вместе с неудавшейся вставкой"
    )
    assert db.scalar(
        select(func.count(EmployeeAssignment.id)).where(
            EmployeeAssignment.employee_id == employee.id
        )
    ) == 2, "частично сохранённых строк не осталось"


def test_duplicate_employee_number_is_a_controlled_error(db, office, hr_actor):
    service = EmployeeService(db)
    service.create(hr_actor, _new_employee(office, employee_number="EMP-777"))
    with pytest.raises(Conflict) as info:
        service.create(hr_actor, _new_employee(
            office, employee_number="EMP-777", first_name="Другой"))
    assert info.value.details["constraint"] == "uq_employees_org_number"
    assert "табельным номером" in info.value.message

    # сессия рабочая: сотрудник с другим номером создаётся сразу после ошибки
    assert service.create(hr_actor, _new_employee(
        office, employee_number="EMP-778")).employee_number == "EMP-778"


# --- 7. Деактивация сохраняет историю ---------------------------------------

def test_deactivation_keeps_employee_and_history(
    db, organization, employee, work_schedule, hr_actor
):
    service = EmployeeService(db)
    WorkScheduleService(db).assign_to_employee(
        hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
        valid_from=date(2025, 1, 1))

    card = service.deactivate(hr_actor, employee.id)
    assert card.employment_status == "SUSPENDED"
    assert card.termination_date is None
    assert len(card.assignment_history) == 1
    assert card.current_assignment is not None, "назначение осталось действующим"
    assert card.current_schedule is not None

    db.expire_all()
    assert db.get(Employee, employee.id) is not None

    assert service.reactivate(hr_actor, employee.id).employment_status == "ACTIVE"


# --- 8. Увольнение сохраняет посещаемость и связанные записи ----------------

def test_termination_keeps_attendance_and_all_history(
    db, organization, office, employee, work_schedule, qr_point, hr_actor, now
):
    service = EmployeeService(db)
    WorkScheduleService(db).assign_to_employee(
        hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
        valid_from=date(2025, 1, 1))

    entry = AttendanceEvent(
        organization_id=organization.id, employee_id=employee.id,
        office_id=office.id, qr_point_id=qr_point.id, event_type="ENTRY",
        source="QR", verification_status="ACCEPTED",
        occurred_at=now - timedelta(hours=9), received_at=now - timedelta(hours=9),
    )
    db.add(entry)
    db.flush()
    db.add(AttendanceSession(
        organization_id=organization.id, employee_id=employee.id,
        office_id=office.id, entry_event_id=entry.id,
        started_at=now - timedelta(hours=9), status="OPEN",
    ))
    db.flush()

    card = service.terminate(hr_actor, employee.id,
                             termination_date=date(2025, 7, 31))
    assert card.employment_status == "TERMINATED"
    assert card.termination_date == date(2025, 7, 31)

    db.expire_all()
    assert db.get(Employee, employee.id) is not None, "запись сотрудника осталась"
    assert db.scalar(select(func.count(AttendanceEvent.id)).where(
        AttendanceEvent.employee_id == employee.id)) == 1
    assert db.scalar(select(func.count(AttendanceSession.id)).where(
        AttendanceSession.employee_id == employee.id)) == 1

    # периоды закрыты датой увольнения, но не удалены
    assignments = db.scalars(select(EmployeeAssignment).where(
        EmployeeAssignment.employee_id == employee.id)).all()
    assert len(assignments) == 1
    assert assignments[0].valid_to == date(2025, 7, 31)

    schedules = WorkScheduleService(db).history(hr_actor, employee.id)
    assert len(schedules) == 1
    assert schedules[0].valid_to == date(2025, 7, 31)


def test_termination_before_hire_date_is_rejected(db, employee, hr_actor):
    with pytest.raises(ValidationFailed, match="раньше даты приёма"):
        EmployeeService(db).terminate(hr_actor, employee.id,
                                      termination_date=date(2023, 1, 1))


def test_terminated_employee_cannot_be_transferred_or_reactivated(
    db, employee, other_office, work_schedule, hr_actor
):
    service = EmployeeService(db)
    service.terminate(hr_actor, employee.id, termination_date=date(2025, 7, 31))

    with pytest.raises(Conflict, match="уволен"):
        service.change_assignment(hr_actor, employee.id, AssignmentChangeRequest(
            effective_from=date(2025, 9, 1), office_id=other_office.id))
    with pytest.raises(Conflict, match="уволен"):
        service.reactivate(hr_actor, employee.id)
    with pytest.raises(Conflict, match="уволен"):
        WorkScheduleService(db).assign_to_employee(
            hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
            valid_from=date(2025, 9, 1))
    with pytest.raises(Conflict, match="уже уволен"):
        service.terminate(hr_actor, employee.id, termination_date=date(2025, 8, 31))


def test_termination_requires_archive_permission(db, employee, make_actor,
                                                 organization):
    """Увольнение — отдельное право: `employees.manage` его не даёт."""
    actor = make_actor(organization,
                       permissions=("employees.read", "employees.manage"))
    with pytest.raises(PermissionDenied, match="employees.archive"):
        EmployeeService(db).terminate(actor, employee.id,
                                      termination_date=date(2025, 7, 31))


# --- 12/13. Права -----------------------------------------------------------

def test_readonly_user_cannot_change_employees(db, employee, office, readonly_actor):
    service = EmployeeService(db)
    assert service.get(readonly_actor, employee.id).id == employee.id

    with pytest.raises(PermissionDenied, match="employees.manage"):
        service.create(readonly_actor, _new_employee(office))
    with pytest.raises(PermissionDenied, match="employees.manage"):
        service.update(readonly_actor, employee.id,
                       EmployeeUpdateRequest(first_name="Новое"))
    with pytest.raises(PermissionDenied, match="employees.manage"):
        service.deactivate(readonly_actor, employee.id)
    with pytest.raises(PermissionDenied, match="employees.archive"):
        service.terminate(readonly_actor, employee.id,
                          termination_date=date(2025, 7, 31))


def test_user_without_permissions_cannot_even_read(db, employee, nobody_actor):
    with pytest.raises(PermissionDenied, match="employees.read"):
        EmployeeService(db).list(nobody_actor)
    with pytest.raises(PermissionDenied, match="employees.read"):
        EmployeeService(db).get(nobody_actor, employee.id)


def test_regional_hr_sees_only_employees_of_own_region(
    db, organization, region, other_region, office, other_office, make_actor,
    hr_actor,
):
    service = EmployeeService(db)
    mine = service.create(hr_actor, _new_employee(office, employee_number="R-1"))
    theirs = service.create(hr_actor, _new_employee(
        other_office, employee_number="R-2", first_name="Чужой"))

    actor = make_actor(organization,
                       permissions=("employees.read", "employees.manage"),
                       region=region)
    at = date(2025, 6, 1)
    visible = {row.id for row in service.list(actor, at=at).items}
    assert mine.id in visible
    assert theirs.id not in visible

    with pytest.raises(PermissionDenied, match="области видимости"):
        service.get(actor, theirs.id)


# --- 14. Изоляция организаций -----------------------------------------------

def test_employee_of_other_organization_is_invisible(
    db, employee, foreign_actor, other_organization, foreign_office
):
    service = EmployeeService(db)
    stranger = service.create(foreign_actor, _new_employee(
        foreign_office, employee_number="F-1", first_name="Сосед"))

    assert {row.id for row in service.list(foreign_actor,
                                           at=date(2025, 6, 1)).items} == {
        stranger.id
    }
    with pytest.raises(NotFound):
        service.get(foreign_actor, employee.id)
    with pytest.raises(NotFound):
        service.update(foreign_actor, employee.id,
                       EmployeeUpdateRequest(first_name="Захвачено"))


# --- 15. Аудит --------------------------------------------------------------

def test_all_hr_actions_are_audited(
    db, organization, office, other_office, work_schedule, hr_actor
):
    service = EmployeeService(db)
    card = service.create(hr_actor, _new_employee(office))
    service.update(hr_actor, card.id, EmployeeUpdateRequest(phone="+992 900 00 00 01"))
    service.change_assignment(hr_actor, card.id, AssignmentChangeRequest(
        effective_from=date(2025, 3, 1), office_id=other_office.id))
    WorkScheduleService(db).assign_to_employee(
        hr_actor, employee_id=card.id, schedule_id=work_schedule.id,
        valid_from=date(2025, 3, 1))
    service.deactivate(hr_actor, card.id)
    service.reactivate(hr_actor, card.id)
    service.terminate(hr_actor, card.id, termination_date=date(2025, 8, 31))

    actions = [
        row.action for row in db.scalars(
            select(AuditLog)
            .where(AuditLog.organization_id == organization.id)
            .order_by(AuditLog.occurred_at)
        )
    ]
    assert actions == [
        "employee.create",
        "employee.update",
        "employee.assignment.change",
        "employee.schedule.assign",
        "employee.deactivate",
        "employee.reactivate",
        "employee.terminate",
    ]

    terminate_entry = db.scalars(
        select(AuditLog).where(AuditLog.action == "employee.terminate")
    ).one()
    assert terminate_entry.old_values["employment_status"] == "ACTIVE"
    assert terminate_entry.new_values["employment_status"] == "TERMINATED"
    assert terminate_entry.new_values["termination_date"] == "2025-08-31"
    assert terminate_entry.actor_user_id == hr_actor.user_id


def test_manage_without_read_permission_still_returns_the_card(
    db, organization, office, make_actor
):
    """Роль с правом на изменение, но без права на чтение, обязана работать.

    Операции записи возвращают карточку результата. Если её сборка требует
    `employees.read`, то роль без этого права сохранила бы данные и следом
    получила отказ — исключение при уже применённых изменениях, то есть ровно
    тот случай «частично сохранённых данных», который запрещён требованиями.
    """
    actor = make_actor(organization,
                       permissions=("employees.manage", "employees.archive"))
    service = EmployeeService(db)

    card = service.create(actor, _new_employee(office))
    assert card.employment_status == "ACTIVE"
    assert service.update(actor, card.id,
                          EmployeeUpdateRequest(first_name="Пётр")).id == card.id
    assert service.deactivate(actor, card.id).employment_status == "SUSPENDED"
    assert service.reactivate(actor, card.id).employment_status == "ACTIVE"
    assert service.terminate(
        actor, card.id, termination_date=date(2025, 9, 30)
    ).employment_status == "TERMINATED"

    # но читать чужую карточку такой роли по-прежнему нельзя
    with pytest.raises(PermissionDenied, match="employees.read"):
        service.get(actor, card.id)


def test_failed_audit_rolls_back_the_whole_transfer(
    db, employee, other_office, hr_actor, monkeypatch
):
    """Откат обязан снимать всю операцию, а не только нарушение ограничения.

    Предыдущий тест строит точку отката вручную и проверяет поведение самой
    базы. Здесь ломается шаг ВНУТРИ `change_assignment` — так проверяется, что
    метод действительно выполняется целиком или никак.
    """
    service = EmployeeService(db)

    def failing_record(*args, **kwargs):
        raise RuntimeError("сбой при записи в журнал")

    monkeypatch.setattr(service.audit, "record", failing_record)

    with pytest.raises(RuntimeError):
        service.change_assignment(
            hr_actor, employee.id,
            AssignmentChangeRequest(effective_from=date(2025, 3, 1),
                                    office_id=other_office.id),
        )

    db.expire_all()
    periods = db.scalars(
        select(EmployeeAssignment).where(
            EmployeeAssignment.employee_id == employee.id
        )
    ).all()
    assert len(periods) == 1, "новое назначение не осталось"
    assert periods[0].valid_to is None, "закрытие старого периода откатилось"


def test_transferred_out_employee_stays_readable_for_former_region(
    db, organization, region, other_region, office, other_office, make_actor,
    hr_actor,
):
    """Список показывает по ТЕКУЩЕМУ офису, карточка — по всей истории.

    Различие намеренное. Сузить карточку до текущего назначения нельзя:
    увольнение закрывает все открытые периоды, и тогда HR своего же региона
    потерял бы доступ к карточкам собственных уволенных сотрудников.
    """
    service = EmployeeService(db)
    card = service.create(hr_actor, _new_employee(office, employee_number="T-1"))
    service.change_assignment(hr_actor, card.id, AssignmentChangeRequest(
        effective_from=date(2025, 4, 1), office_id=other_office.id))

    actor = make_actor(organization, permissions=("employees.read",),
                       region=region)
    at = date(2025, 6, 1)

    assert card.id not in {row.id for row in service.list(actor, at=at).items}, (
        "в списке текущего состава переведённого сотрудника уже нет"
    )
    assert service.get(actor, card.id, at=at).id == card.id, (
        "карточка остаётся доступной по истории назначений"
    )


def test_terminated_employee_stays_visible_to_regional_hr(
    db, organization, region, office, make_actor, hr_actor
):
    """Увольнение закрывает периоды, но не отбирает доступ к карточке."""
    service = EmployeeService(db)
    card = service.create(hr_actor, _new_employee(office, employee_number="T-2"))
    service.terminate(hr_actor, card.id, termination_date=date(2025, 5, 31))

    actor = make_actor(organization, permissions=("employees.read",),
                       region=region)
    assert service.get(actor, card.id, at=date(2025, 9, 1)).employment_status == (
        "TERMINATED"
    )
