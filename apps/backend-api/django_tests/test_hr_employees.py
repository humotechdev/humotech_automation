"""Кадровые операции: приём, карточка, перевод, деактивация, увольнение.

Перенос `tests/integration/test_hr_employees.py` на Django ORM. Всё на
настоящем PostgreSQL: EXCLUDE-ограничения на периоды назначений и внешние
ключи с RESTRICT — часть проверяемого поведения, а не фон.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.db import connection, transaction

from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.audit.models import AuditLog
from humotech.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from humotech.departments.models import Department
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.services import EmployeeService
from humotech.offices.services import OfficeService
from humotech.positions.models import Position
from humotech.schedules.services import WorkScheduleService
from humotech.telegram.models import TelegramAccount

pytestmark = pytest.mark.django_db


def _new_employee(office, **overrides) -> dict:
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
    return payload


def _position(organization, code="DEV", status="ACTIVE") -> Position:
    return Position.objects.create(
        organization=organization, code=code, name="Разработчик", status=status
    )


def _department(organization, office, code="IT", status="ACTIVE") -> Department:
    return Department.objects.create(
        organization=organization, office=office, code=code, name="ИТ-отдел",
        status=status,
    )


# --- 2. Приём сотрудника и назначение в офис --------------------------------

def test_hr_creates_employee_and_assigns_to_office(
    organization, region, office, hr_actor
):
    position = _position(organization)
    department = _department(organization, office)

    card = EmployeeService().create(
        hr_actor,
        **_new_employee(office, position_id=position.id,
                        department_id=department.id, region_id=region.id),
    )

    assert card.employee.employment_status == "ACTIVE"
    assert card.full_name == "Петров Пётр"
    assert card.current_assignment is not None
    assert card.current_assignment.office_id == office.id
    assert card.current_assignment.office.region_id == region.id
    assert card.current_assignment.department.name == "ИТ-отдел"
    assert card.current_assignment.position.name == "Разработчик"
    assert card.current_assignment.valid_from == date(2025, 1, 15)
    assert card.current_assignment.valid_to is None
    assert card.telegram.connected is False


def test_employee_is_created_together_with_assignment(office, hr_actor):
    """Сотрудник без назначения не виден HR с территориальной областью,
    поэтому обе строки создаются одной операцией."""
    card = EmployeeService().create(hr_actor, **_new_employee(office))
    assert EmployeeAssignment.objects.filter(employee=card.employee).count() == 1


def test_region_not_matching_office_is_rejected(office, other_region, hr_actor):
    with pytest.raises(ValidationFailed, match="не совпадает с регионом офиса"):
        EmployeeService().create(
            hr_actor, **_new_employee(office, region_id=other_region.id)
        )


def test_contacts_are_validated_and_stored(office, hr_actor):
    service = EmployeeService()
    card = service.create(hr_actor, **_new_employee(
        office, phone=" +992 900 11 22 33 ", corporate_email=" HR@humotech.tj "))
    assert card.employee.phone == "+992 900 11 22 33", "лишние пробелы обрезаны"
    assert card.employee.corporate_email == "HR@humotech.tj"

    with pytest.raises(ValidationFailed, match="телефона"):
        service.create(hr_actor, **_new_employee(
            office, employee_number="EMP-2", phone="позвоните мне"))
    with pytest.raises(ValidationFailed, match="почты"):
        service.create(hr_actor, **_new_employee(
            office, employee_number="EMP-3", corporate_email="без-собаки.tj"))


def test_inactive_department_and_foreign_position_are_rejected(
    organization, office, other_office, hr_actor
):
    service = EmployeeService()
    closed = _department(organization, office, code="OLD", status="INACTIVE")
    with pytest.raises(Conflict, match="Отдел не активен"):
        service.create(hr_actor, **_new_employee(office, department_id=closed.id))

    elsewhere = _department(organization, other_office, code="ELSE")
    with pytest.raises(ValidationFailed, match="другому офису"):
        service.create(hr_actor, **_new_employee(
            office, employee_number="EMP-9", department_id=elsewhere.id))


# --- 9. Деактивированный офис нельзя назначить ------------------------------

def test_inactive_office_cannot_receive_new_employee(office, hr_actor):
    OfficeService().deactivate(hr_actor, office.id)
    with pytest.raises(Conflict, match="Офис не активен"):
        EmployeeService().create(hr_actor, **_new_employee(office))


def test_inactive_office_cannot_receive_transfer(
    office, other_office, employee, hr_actor
):
    OfficeService().deactivate(hr_actor, other_office.id)
    with pytest.raises(Conflict, match="Офис не активен"):
        EmployeeService().change_assignment(
            hr_actor, employee.id, effective_from=date(2025, 3, 1),
            office_id=other_office.id,
        )


# --- 11. Офис другой организации нельзя назначить ---------------------------

def test_foreign_office_cannot_be_assigned(foreign_office, hr_actor, employee):
    service = EmployeeService()
    with pytest.raises(NotFound, match="Офис не найден"):
        service.create(hr_actor, **_new_employee(foreign_office))
    with pytest.raises(NotFound, match="Офис не найден"):
        service.change_assignment(
            hr_actor, employee.id, effective_from=date(2025, 3, 1),
            office_id=foreign_office.id,
        )


def test_foreign_employee_is_not_found(employee, foreign_actor):
    with pytest.raises(NotFound, match="Сотрудник не найден"):
        EmployeeService().get(foreign_actor, employee.id)


# --- 4. Список и карточка ---------------------------------------------------

def test_employee_appears_in_list_and_card(organization, office, hr_actor):
    service = EmployeeService()
    position = _position(organization)
    created = service.create(
        hr_actor, **_new_employee(office, position_id=position.id)
    )

    listed = service.list(hr_actor, at=date(2025, 6, 1))
    item = next(row for row in listed.items if row.id == created.employee.id)
    assert item.last_name == "Петров"
    assert item.current_assignment.office_id == office.id
    assert item.current_assignment.office.name == office.name
    assert item.current_assignment.office.region.name == "Душанбе"
    assert item.current_assignment.position.name == "Разработчик"
    assert item.employment_status == "ACTIVE"

    card = service.get(hr_actor, created.employee.id, at=date(2025, 6, 1))
    assert card.employee.employee_number == "EMP-1000"
    assert card.employee.corporate_email == "p.petrov@humotech.tj"
    assert len(card.assignment_history) == 1


def test_card_shows_schedule_and_telegram_binding(
    organization, employee, work_schedule, hr_actor, now
):
    WorkScheduleService().assign_to_employee(
        hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
        valid_from=date(2025, 1, 1),
    )
    TelegramAccount.objects.create(
        organization=organization, employee=employee,
        telegram_user_id=987654321, telegram_chat_id=987654321,
        telegram_username="petrov", status="ACTIVE", connected_at=now,
    )

    card = EmployeeService().get(hr_actor, employee.id, at=date(2025, 6, 1))
    assert card.current_schedule is not None
    assert card.current_schedule.schedule.name == work_schedule.name
    assert card.current_schedule.schedule.timezone == "Asia/Dushanbe"
    assert card.telegram.connected is True
    assert card.telegram.username == "petrov"
    assert card.telegram.status == "ACTIVE"


def test_revoked_telegram_binding_reads_as_disconnected(
    organization, employee, hr_actor, now
):
    TelegramAccount.objects.create(
        organization=organization, employee=employee,
        telegram_user_id=111222333, telegram_chat_id=111222333,
        status="REVOKED", connected_at=now,
    )
    card = EmployeeService().get(hr_actor, employee.id)
    assert card.telegram.connected is False
    assert card.telegram.status == "REVOKED"


# --- 5. Поиск, фильтры, пагинация -------------------------------------------

def test_employee_search_filters_and_pagination(
    region, other_region, office, other_office, hr_actor
):
    service = EmployeeService()
    for index in range(6):
        service.create(hr_actor, **_new_employee(
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


def test_employee_list_does_not_grow_queries_with_page_size(office, hr_actor):
    """Число запросов не должно зависеть от размера страницы.

    Проверяется счётчиком операторов, а не наличием `select_related` в коде:
    иначе тест подтверждал бы намерение, а не результат.
    """
    service = EmployeeService()
    for index in range(15):
        service.create(hr_actor, **_new_employee(
            office, employee_number=f"N-{index:03d}", first_name=f"Имя{index}"))

    service.access.permissions(hr_actor)  # кэш прав прогреваем отдельно

    def count_queries(limit: int) -> int:
        with connection.execute_wrapper(_Counter()) as _:
            pass
        counter = _Counter()
        with connection.execute_wrapper(counter):
            list(service.list(hr_actor, at=date(2025, 6, 1), limit=limit).items)
        return counter.count

    for_three = count_queries(3)
    for_fifteen = count_queries(15)
    assert for_three == for_fifteen, (
        f"запросов на 3 сотрудника: {for_three}, на 15: {for_fifteen} — это N+1"
    )
    # Пять операторов: сотрудники, назначения, графики, привязки Telegram
    # и проверка прав. Важно не само число, а что оно НЕ зависит от размера
    # страницы — это и проверяет сравнение выше. Растёт этот предел только
    # вместе с новой колонкой списка, и каждый раз одним запросом на всю
    # страницу, а не одним на строку.
    assert for_fifteen <= 5, f"на список уходит {for_fifteen} запросов"


class _Counter:
    """Считает выполненные операторы SQL."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)


# --- 6. Изменение офиса -----------------------------------------------------

def test_office_change_closes_old_period_and_opens_new(
    office, other_office, employee, hr_actor
):
    service = EmployeeService()
    result = service.change_assignment(
        hr_actor, employee.id, effective_from=date(2025, 3, 1),
        office_id=other_office.id,
    )
    assert result.office_id == other_office.id
    assert result.valid_from == date(2025, 3, 1)
    assert result.valid_to is None

    periods = list(
        EmployeeAssignment.objects.filter(employee=employee).order_by("valid_from")
    )
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
    organization, office, other_office, hr_actor
):
    service = EmployeeService()
    position = _position(organization)
    department = _department(organization, office)
    card = service.create(hr_actor, **_new_employee(
        office, position_id=position.id, department_id=department.id))

    moved = service.change_assignment(
        hr_actor, card.employee.id, effective_from=date(2025, 5, 1),
        office_id=other_office.id,
    )
    assert moved.position_id == position.id, "должность переносится"
    assert moved.department_id is None, "отдел принадлежал прежнему офису"


def test_position_change_creates_new_period(organization, employee, hr_actor):
    service = EmployeeService()
    senior = _position(organization, code="SENIOR")
    result = service.change_assignment(
        hr_actor, employee.id, effective_from=date(2025, 4, 1),
        position_id=senior.id,
    )
    assert result.position_id == senior.id, "должность действительно сменилась"
    assert len(service.assignment_history(hr_actor, employee.id)) == 2


def test_transfer_before_hire_date_is_rejected(employee, other_office, hr_actor):
    with pytest.raises(ValidationFailed, match="раньше даты приёма"):
        EmployeeService().change_assignment(
            hr_actor, employee.id, effective_from=date(2023, 1, 1),
            office_id=other_office.id,
        )


def test_backdated_transfer_is_rejected_with_clear_message(
    employee, other_office, hr_actor
):
    """Перевод «в прошлое» отклоняется понятной ошибкой, а не нарушением
    ограничения в базе."""
    service = EmployeeService()
    service.change_assignment(hr_actor, employee.id,
                              effective_from=date(2025, 6, 1),
                              office_id=other_office.id)
    with pytest.raises(ValidationFailed, match="позже начала действующего"):
        service.change_assignment(hr_actor, employee.id,
                                  effective_from=date(2025, 3, 1),
                                  office_id=other_office.id)


# --- 16. Ошибка в середине операции откатывает всё ---------------------------

def test_failed_audit_rolls_back_the_whole_transfer(
    employee, other_office, hr_actor, monkeypatch
):
    """Перевод состоит из двух шагов: закрыть старый период и вставить новый.

    Если второй шаг падает, первый обязан откатиться — иначе у сотрудника
    остаётся закрытое назначение и ни одного действующего, то есть он
    исчезает из всех списков по текущему составу. Ломается шаг ВНУТРИ
    метода, поэтому проверяется сама операция, а не абстрактная транзакция.
    """
    service = EmployeeService()

    def failing_record(*args, **kwargs):
        raise RuntimeError("сбой при записи в журнал")

    monkeypatch.setattr(service.audit, "record", failing_record)

    with pytest.raises(RuntimeError):
        service.change_assignment(hr_actor, employee.id,
                                  effective_from=date(2025, 3, 1),
                                  office_id=other_office.id)

    periods = list(EmployeeAssignment.objects.filter(employee=employee))
    assert len(periods) == 1, "новое назначение не осталось"
    assert periods[0].valid_to is None, "закрытие старого периода откатилось"


def test_overlapping_period_is_rejected_by_the_database(
    organization, office, employee, hr_actor
):
    """EXCLUDE-ограничение не даёт создать пересекающийся период даже в обход
    сервиса — и ошибка приходит понятной, а не сырой."""
    service = EmployeeService()
    with pytest.raises(Conflict, match="пересекаются"):
        with service.atomic():
            EmployeeAssignment.objects.create(
                organization=organization, employee=employee, office=office,
                employment_type="FULL_TIME", work_mode="ONSITE",
                is_primary=True, valid_from=date(2024, 6, 1),
            )
    # соединение живо
    assert EmployeeAssignment.objects.filter(employee=employee).count() == 1


def test_duplicate_employee_number_is_a_controlled_error(office, hr_actor):
    service = EmployeeService()
    service.create(hr_actor, **_new_employee(office, employee_number="EMP-777"))
    with pytest.raises(Conflict) as info:
        service.create(hr_actor, **_new_employee(
            office, employee_number="EMP-777", first_name="Другой"))
    assert info.value.details["constraint"] == "uq_employees_org_number"
    assert "табельным номером" in info.value.message

    # соединение рабочее: сотрудник с другим номером создаётся сразу после ошибки
    created = service.create(hr_actor, **_new_employee(
        office, employee_number="EMP-778"))
    assert created.employee.employee_number == "EMP-778"


# --- 7. Деактивация сохраняет историю ---------------------------------------

def test_deactivation_keeps_employee_and_history(
    employee, work_schedule, hr_actor
):
    service = EmployeeService()
    WorkScheduleService().assign_to_employee(
        hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
        valid_from=date(2025, 1, 1))

    card = service.deactivate(hr_actor, employee.id)
    assert card.employee.employment_status == "SUSPENDED"
    assert card.employee.termination_date is None
    assert len(card.assignment_history) == 1
    assert card.current_assignment is not None, "назначение осталось действующим"
    assert card.current_schedule is not None

    assert Employee.objects.filter(id=employee.id).exists()
    assert service.reactivate(hr_actor, employee.id).employee.employment_status == (
        "ACTIVE"
    )


# --- 8. Увольнение сохраняет посещаемость и связанные записи ----------------

def test_termination_keeps_attendance_and_all_history(
    organization, office, employee, work_schedule, qr_point, hr_actor, now
):
    service = EmployeeService()
    WorkScheduleService().assign_to_employee(
        hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
        valid_from=date(2025, 1, 1))

    entry = AttendanceEvent.objects.create(
        organization=organization, employee=employee, office=office,
        qr_point=qr_point, event_type="ENTRY", source="QR",
        verification_status="ACCEPTED",
        occurred_at=now - timedelta(hours=9),
        received_at=now - timedelta(hours=9),
    )
    AttendanceSession.objects.create(
        organization=organization, employee=employee, office=office,
        entry_event=entry, started_at=now - timedelta(hours=9), status="OPEN",
    )

    card = service.terminate(hr_actor, employee.id,
                             termination_date=date(2025, 7, 31))
    assert card.employee.employment_status == "TERMINATED"
    assert card.employee.termination_date == date(2025, 7, 31)

    assert Employee.objects.filter(id=employee.id).exists(), "запись осталась"
    assert AttendanceEvent.objects.filter(employee=employee).count() == 1
    assert AttendanceSession.objects.filter(employee=employee).count() == 1

    # периоды закрыты датой увольнения, но не удалены
    assignments = list(EmployeeAssignment.objects.filter(employee=employee))
    assert len(assignments) == 1
    assert assignments[0].valid_to == date(2025, 7, 31)

    schedules = WorkScheduleService().history(hr_actor, employee.id)
    assert len(schedules) == 1
    assert schedules[0].valid_to == date(2025, 7, 31)


def test_termination_before_hire_date_is_rejected(employee, hr_actor):
    with pytest.raises(ValidationFailed, match="раньше даты приёма"):
        EmployeeService().terminate(hr_actor, employee.id,
                                    termination_date=date(2023, 1, 1))


def test_terminated_employee_cannot_be_transferred_or_reactivated(
    employee, other_office, work_schedule, hr_actor
):
    service = EmployeeService()
    service.terminate(hr_actor, employee.id, termination_date=date(2025, 7, 31))

    with pytest.raises(Conflict, match="уволен"):
        service.change_assignment(hr_actor, employee.id,
                                  effective_from=date(2025, 9, 1),
                                  office_id=other_office.id)
    with pytest.raises(Conflict, match="уволен"):
        service.reactivate(hr_actor, employee.id)
    with pytest.raises(Conflict, match="уволен"):
        WorkScheduleService().assign_to_employee(
            hr_actor, employee_id=employee.id, schedule_id=work_schedule.id,
            valid_from=date(2025, 9, 1))
    with pytest.raises(Conflict, match="уже уволен"):
        service.terminate(hr_actor, employee.id,
                          termination_date=date(2025, 8, 31))


def test_termination_requires_archive_permission(employee, make_actor, organization):
    """Увольнение — отдельное право: `employees.manage` его не даёт."""
    actor = make_actor(organization,
                       permissions=("employees.read", "employees.manage"))
    with pytest.raises(PermissionDenied, match="employees.archive"):
        EmployeeService().terminate(actor, employee.id,
                                    termination_date=date(2025, 7, 31))


# --- 12/13. Права -----------------------------------------------------------

def test_readonly_user_cannot_change_employees(employee, office, readonly_actor):
    service = EmployeeService()
    assert service.get(readonly_actor, employee.id).employee.id == employee.id

    with pytest.raises(PermissionDenied, match="employees.manage"):
        service.create(readonly_actor, **_new_employee(office))
    with pytest.raises(PermissionDenied, match="employees.manage"):
        service.update(readonly_actor, employee.id, first_name="Новое")
    with pytest.raises(PermissionDenied, match="employees.manage"):
        service.deactivate(readonly_actor, employee.id)
    with pytest.raises(PermissionDenied, match="employees.archive"):
        service.terminate(readonly_actor, employee.id,
                          termination_date=date(2025, 7, 31))


def test_user_without_permissions_cannot_even_read(employee, nobody_actor):
    with pytest.raises(PermissionDenied, match="employees.read"):
        EmployeeService().list(nobody_actor)
    with pytest.raises(PermissionDenied, match="employees.read"):
        EmployeeService().get(nobody_actor, employee.id)


def test_manage_without_read_permission_still_returns_the_card(
    organization, office, make_actor
):
    """Роль с правом на изменение, но без права на чтение, обязана работать.

    Иначе она сохранила бы данные и следом получила отказ — исключение
    при уже применённых изменениях.
    """
    actor = make_actor(organization,
                       permissions=("employees.manage", "employees.archive"))
    service = EmployeeService()

    card = service.create(actor, **_new_employee(office))
    assert card.employee.employment_status == "ACTIVE"
    assert service.update(
        actor, card.employee.id, first_name="Пётр"
    ).employee.id == card.employee.id
    assert service.deactivate(
        actor, card.employee.id
    ).employee.employment_status == "SUSPENDED"
    assert service.reactivate(
        actor, card.employee.id
    ).employee.employment_status == "ACTIVE"
    assert service.terminate(
        actor, card.employee.id, termination_date=date(2025, 9, 30)
    ).employee.employment_status == "TERMINATED"

    with pytest.raises(PermissionDenied, match="employees.read"):
        service.get(actor, card.employee.id)


def test_regional_hr_sees_only_employees_of_own_region(
    organization, region, other_region, office, other_office, make_actor, hr_actor
):
    service = EmployeeService()
    mine = service.create(hr_actor, **_new_employee(office, employee_number="R-1"))
    theirs = service.create(hr_actor, **_new_employee(
        other_office, employee_number="R-2", first_name="Чужой"))

    actor = make_actor(organization,
                       permissions=("employees.read", "employees.manage"),
                       region=region)
    at = date(2025, 6, 1)
    visible = {row.id for row in service.list(actor, at=at).items}
    assert mine.employee.id in visible
    assert theirs.employee.id not in visible

    with pytest.raises(PermissionDenied, match="области видимости"):
        service.get(actor, theirs.employee.id)


def test_transferred_out_employee_stays_readable_for_former_region(
    organization, region, office, other_office, make_actor, hr_actor
):
    """Список показывает по ТЕКУЩЕМУ офису, карточка — по всей истории.

    Различие намеренное. Сузить карточку до текущего назначения нельзя:
    увольнение закрывает все открытые периоды, и тогда HR своего же региона
    потерял бы доступ к карточкам собственных уволенных сотрудников.
    """
    service = EmployeeService()
    card = service.create(hr_actor, **_new_employee(office, employee_number="T-1"))
    service.change_assignment(hr_actor, card.employee.id,
                              effective_from=date(2025, 4, 1),
                              office_id=other_office.id)

    actor = make_actor(organization, permissions=("employees.read",), region=region)
    at = date(2025, 6, 1)

    assert card.employee.id not in {
        row.id for row in service.list(actor, at=at).items
    }, "в списке текущего состава переведённого сотрудника уже нет"
    assert service.get(actor, card.employee.id, at=at).employee.id == card.employee.id


def test_terminated_employee_stays_visible_to_regional_hr(
    organization, region, office, make_actor, hr_actor
):
    """Увольнение закрывает периоды, но не отбирает доступ к карточке."""
    service = EmployeeService()
    card = service.create(hr_actor, **_new_employee(office, employee_number="T-2"))
    service.terminate(hr_actor, card.employee.id,
                      termination_date=date(2025, 5, 31))

    actor = make_actor(organization, permissions=("employees.read",), region=region)
    assert service.get(
        actor, card.employee.id, at=date(2025, 9, 1)
    ).employee.employment_status == "TERMINATED"


# --- 14. Изоляция организаций -----------------------------------------------

def test_employee_of_other_organization_is_invisible(
    employee, foreign_actor, foreign_office
):
    service = EmployeeService()
    stranger = service.create(foreign_actor, **_new_employee(
        foreign_office, employee_number="F-1", first_name="Сосед"))

    assert {row.id for row in service.list(
        foreign_actor, at=date(2025, 6, 1)
    ).items} == {stranger.employee.id}
    with pytest.raises(NotFound):
        service.get(foreign_actor, employee.id)
    with pytest.raises(NotFound):
        service.update(foreign_actor, employee.id, first_name="Захвачено")


# --- 15. Аудит --------------------------------------------------------------

def test_all_hr_actions_are_audited(
    organization, office, other_office, work_schedule, hr_actor
):
    service = EmployeeService()
    card = service.create(hr_actor, **_new_employee(office))
    employee_id = card.employee.id
    service.update(hr_actor, employee_id, phone="+992 900 00 00 01")
    service.change_assignment(hr_actor, employee_id,
                              effective_from=date(2025, 3, 1),
                              office_id=other_office.id)
    WorkScheduleService().assign_to_employee(
        hr_actor, employee_id=employee_id, schedule_id=work_schedule.id,
        valid_from=date(2025, 3, 1))
    service.deactivate(hr_actor, employee_id)
    service.reactivate(hr_actor, employee_id)
    service.terminate(hr_actor, employee_id, termination_date=date(2025, 8, 31))

    actions = list(
        AuditLog.objects.filter(organization=organization)
        .order_by("occurred_at").values_list("action", flat=True)
    )
    assert actions == [
        "employee.create",
        "employee.update",
        "employee.assignment.change",
        "employee.schedule.assign",
        "employee.deactivate",
        "employee.reactivate",
        "employee.terminate",
    ]

    entry = AuditLog.objects.get(action="employee.terminate")
    assert entry.old_values["employment_status"] == "ACTIVE"
    assert entry.new_values["employment_status"] == "TERMINATED"
    assert entry.new_values["termination_date"] == "2025-08-31"
    assert entry.actor_user_id == hr_actor.user_id
