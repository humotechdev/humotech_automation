"""Приём сотрудника одной операцией.

Проверяется не «endpoint отвечает 201», а то, ради чего приём собрали в
одну операцию: после неё в базе есть всё, что нужно человеку для работы,
а при сбое — ничего. Половина приёма опаснее отказа: отказ видно и его
повторяют, половина молча живёт в базе и всплывает через месяц, когда у
сотрудника не считается посещаемость.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from humotech.audit.models import AuditLog
from humotech.core.errors import Conflict, PermissionDenied, ValidationFailed
from humotech.departments.models import Department
from humotech.employees.models import (
    Employee,
    EmployeeAssignment,
    EmployeeDocument,
    EmployeeOnboardingKey,
)
from humotech.employees.onboarding import EmployeeOnboardingService
from humotech.positions.models import Position
from humotech.schedules.models import EmployeeScheduleAssignment
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation

pytestmark = pytest.mark.django_db


ONBOARD_PERMISSIONS = (
    "employees.read", "employees.manage",
    "schedules.read", "schedules.manage",
    "telegram.read", "telegram.manage",
    "audit.read",
)


@pytest.fixture()
def onboarding_actor(make_actor, organization):
    """HR, которому разрешён весь приём, включая подготовку Telegram."""
    return make_actor(organization, permissions=ONBOARD_PERMISSIONS)


@pytest.fixture()
def position(db, organization) -> Position:
    return Position.objects.create(
        organization=organization, code="HR", name="HR-специалист", status="ACTIVE"
    )


@pytest.fixture()
def department(db, organization, office) -> Department:
    return Department.objects.create(
        organization=organization, office=office, code="HRD",
        name="Отдел кадров", status="ACTIVE",
    )


def form(office, department, position, schedule, **overrides) -> dict:
    """Заполненная форма «Новый сотрудник» — та же, что уходит со страницы."""
    payload = {
        "idempotency_key": uuid.uuid4().hex,
        "last_name": "Каримова",
        "first_name": "Нигина",
        "middle_name": "Рустамовна",
        "birth_date": date(1998, 3, 14),
        "pinfl": "39803141234567",
        "phone": "+998 90 123 45 67",
        "corporate_email": "n.karimova@humotech.uz",
        "telegram_username": "@nigina_karimova",
        "hire_date": date(2026, 9, 15),
        "office_id": office.id,
        "department_id": department.id,
        "position_id": position.id,
        "schedule_id": schedule.id,
        "employment_type": "FULL_TIME",
    }
    payload.update(overrides)
    return payload


# --- успешный приём ---------------------------------------------------------

def test_onboarding_creates_everything_the_person_needs_to_work(
    organization, office, department, position, work_schedule,
    onboarding_actor, telegram_settings,
):
    made = EmployeeOnboardingService().onboard(
        onboarding_actor, **form(office, department, position, work_schedule)
    )

    assert made.created is True
    employee = made.card.employee
    assert employee.last_name == "Каримова"
    assert employee.pinfl == "39803141234567"

    # Назначение: офис, отдел и должность — именно те, что выбрали.
    assignment = made.card.current_assignment
    assert assignment is not None
    assert assignment.office_id == office.id
    assert assignment.department_id == department.id
    assert assignment.position_id == position.id
    assert assignment.valid_from == date(2026, 9, 15)

    # График начинает действовать с первого рабочего дня, а не с сегодня.
    schedule_row = EmployeeScheduleAssignment.objects.get(employee=employee)
    assert schedule_row.schedule_id == work_schedule.id
    assert schedule_row.valid_from == date(2026, 9, 15)
    assert made.schedule_assigned is True

    # Чек-лист документов заведён целиком.
    documents = EmployeeDocument.objects.filter(employee=employee)
    assert documents.count() == 3
    assert set(documents.values_list("kind", flat=True)) == {
        "IDENTITY", "CONTRACT", "HIRE_ORDER"
    }
    # Договор и приказ печатают позже: это не упущение, а обещание.
    assert documents.get(kind="CONTRACT").status == "GENERATED_LATER"
    assert documents.get(kind="IDENTITY").status == "MISSING"


def test_employee_number_is_issued_by_the_system(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    """Табельный номер не вводят руками: помнить свободный номер некому."""
    service = EmployeeOnboardingService()
    first = service.onboard(
        onboarding_actor, **form(office, department, position, work_schedule)
    )
    second = service.onboard(
        onboarding_actor,
        **form(office, department, position, work_schedule,
               pinfl="39901012345678", phone="+998 90 222 33 44",
               corporate_email="second@humotech.uz"),
    )

    numbers = [
        first.card.employee.employee_number,
        second.card.employee.employee_number,
    ]
    assert numbers == ["HT-0001", "HT-0002"]


def test_future_start_date_is_allowed_and_schedule_waits_for_it(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    """Человека оформляют заранее — это обычный случай, а не ошибка."""
    future = timezone.localdate() + timedelta(days=30)
    made = EmployeeOnboardingService().onboard(
        onboarding_actor,
        **form(office, department, position, work_schedule, hire_date=future),
    )

    assert made.card.employee.hire_date == future
    assert made.card.current_assignment.valid_from == future
    assert (
        EmployeeScheduleAssignment.objects.get(
            employee=made.card.employee
        ).valid_from
        == future
    )


def test_start_date_years_ahead_is_refused_as_a_typo(
    office, department, position, work_schedule, onboarding_actor,
):
    far = timezone.localdate() + timedelta(days=800)
    with pytest.raises(ValidationFailed, match="опечатку"):
        EmployeeOnboardingService().onboard(
            onboarding_actor,
            **form(office, department, position, work_schedule, hire_date=far),
        )


# --- обязательные поля и дубликаты ------------------------------------------

def test_pinfl_must_be_fourteen_digits(
    office, department, position, work_schedule, onboarding_actor,
):
    with pytest.raises(ValidationFailed, match="14 цифр"):
        EmployeeOnboardingService().onboard(
            onboarding_actor,
            **form(office, department, position, work_schedule, pinfl="123"),
        )


def test_phone_is_required(
    office, department, position, work_schedule, onboarding_actor,
):
    with pytest.raises(ValidationFailed):
        EmployeeOnboardingService().onboard(
            onboarding_actor,
            **form(office, department, position, work_schedule, phone="   "),
        )


def test_duplicate_pinfl_is_refused(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    service = EmployeeOnboardingService()
    service.onboard(
        onboarding_actor, **form(office, department, position, work_schedule)
    )

    with pytest.raises(Conflict, match="ПИНФЛ"):
        service.onboard(
            onboarding_actor,
            # Тот же ПИНФЛ, другие телефон и почта: совпадение именно по нему.
            **form(office, department, position, work_schedule,
                   phone="+998 90 999 88 77",
                   corporate_email="other@humotech.uz"),
        )


def test_duplicate_pinfl_is_seen_across_offices_the_hr_cannot_see(
    organization, office, other_office, department, position, work_schedule,
    make_actor, onboarding_actor, telegram_settings,
):
    """Проверка дубликата идёт по всей организации, а не по видимым офисам.

    Иначе HR одного региона завёл бы человека, который уже работает в
    соседнем: чужих записей он не видит, и проверка молча прошла бы.
    """
    EmployeeOnboardingService().onboard(
        onboarding_actor, **form(office, department, position, work_schedule)
    )

    narrow = make_actor(
        organization, permissions=ONBOARD_PERMISSIONS, office=other_office
    )
    other_department = Department.objects.create(
        organization=organization, office=other_office, code="HRD2",
        name="Отдел кадров", status="ACTIVE",
    )
    with pytest.raises(Conflict, match="ПИНФЛ"):
        EmployeeOnboardingService().onboard(
            narrow,
            **form(other_office, other_department, position, work_schedule,
                   phone="+998 90 555 44 33",
                   corporate_email="third@humotech.uz"),
        )


def test_duplicate_phone_and_email_are_refused(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    service = EmployeeOnboardingService()
    service.onboard(
        onboarding_actor, **form(office, department, position, work_schedule)
    )

    with pytest.raises(Conflict, match="телефоном"):
        service.onboard(
            onboarding_actor,
            **form(office, department, position, work_schedule,
                   pinfl="39901012345678",
                   corporate_email="unique@humotech.uz"),
        )
    with pytest.raises(Conflict, match="email"):
        service.onboard(
            onboarding_actor,
            **form(office, department, position, work_schedule,
                   pinfl="39901012345678", phone="+998 90 777 66 55"),
        )


# --- права ------------------------------------------------------------------

def test_readonly_actor_cannot_onboard(
    office, department, position, work_schedule, readonly_actor,
):
    with pytest.raises(PermissionDenied):
        EmployeeOnboardingService().onboard(
            readonly_actor, **form(office, department, position, work_schedule)
        )
    assert Employee.objects.count() == 0


# --- двойное нажатие --------------------------------------------------------

def test_same_key_twice_returns_the_same_employee(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    """Повтор отдаёт того же человека, а не заводит второго.

    Именно отдаёт, а не отказывает: клиент, потерявший ответ, должен
    получить ту же карточку — иначе он решит, что приём не прошёл, и
    повторит его руками уже с новым ключом.
    """
    payload = form(office, department, position, work_schedule)
    service = EmployeeOnboardingService()

    first = service.onboard(onboarding_actor, **payload)
    second = service.onboard(onboarding_actor, **payload)

    assert first.created is True
    assert second.created is False
    assert second.card.employee.id == first.card.employee.id
    assert Employee.objects.count() == 1
    assert EmployeeAssignment.objects.count() == 1
    assert EmployeeScheduleAssignment.objects.count() == 1
    assert EmployeeOnboardingKey.objects.count() == 1


# --- откат ------------------------------------------------------------------

def test_failure_on_a_later_step_leaves_nothing_behind(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    """Сбой на любом шаге снимает весь приём.

    Ломается предпоследний шаг — запись ключа. К этому моменту карточка,
    назначение, график и документы уже вставлены, и если транзакция не
    откатится, в базе останется сотрудник, о котором никто не узнает.
    """
    service = EmployeeOnboardingService()
    with patch.object(
        EmployeeOnboardingKey.objects, "create", side_effect=RuntimeError("сбой")
    ):
        with pytest.raises(RuntimeError):
            service.onboard(
                onboarding_actor,
                **form(office, department, position, work_schedule),
            )

    assert Employee.objects.count() == 0
    assert EmployeeAssignment.objects.count() == 0
    assert EmployeeScheduleAssignment.objects.count() == 0
    assert EmployeeDocument.objects.count() == 0
    assert TelegramLinkInvitation.objects.count() == 0


# --- Telegram ---------------------------------------------------------------

def test_unknown_telegram_gets_a_one_time_link(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    """Telegram ID неизвестен — готовим ссылку, а не обещаем подключение.

    Бот не может написать первым и не может запустить себя на чужом
    телефоне: до нажатия Start чата не существует. «Доступ подготовлен» —
    это максимум, который здесь возможен.
    """
    made = EmployeeOnboardingService().onboard(
        onboarding_actor, **form(office, department, position, work_schedule)
    )

    assert made.telegram.state == "INVITED"
    assert made.telegram.link is not None
    assert "humotech_test_bot" in made.telegram.link

    invitation = TelegramLinkInvitation.objects.get(employee=made.card.employee)
    assert invitation.status == "ACTIVE"
    # Наружу ушёл токен, в базе лежит только его хеш.
    assert invitation.token_hash not in made.telegram.link
    assert made.card.employee.telegram_connected is False


def test_known_telegram_id_is_linked_right_away(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    made = EmployeeOnboardingService().onboard(
        onboarding_actor,
        **form(office, department, position, work_schedule,
               telegram_user_id=777_000_111),
    )

    assert made.telegram.state == "CONNECTED"
    account = TelegramAccount.objects.get(employee=made.card.employee)
    assert account.telegram_user_id == 777_000_111
    assert account.status == "ACTIVE"
    assert Employee.objects.get(pk=made.card.employee.id).telegram_connected


def test_username_alone_never_links_an_account(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    """`@username` не доказательство личности.

    Имя можно сменить и передать другому человеку, а отметки о приходе на
    работу привязались бы к прежнему владельцу. Поэтому при заполненном
    username и неизвестном числовом идентификаторе привязки не возникает —
    только ссылка.
    """
    made = EmployeeOnboardingService().onboard(
        onboarding_actor,
        **form(office, department, position, work_schedule,
               telegram_username="@somebody_else"),
    )

    assert made.telegram.state == "INVITED"
    assert not TelegramAccount.objects.filter(
        employee=made.card.employee
    ).exists()


def test_one_time_token_cannot_be_used_twice(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    """Ссылка одноразовая: второй запуск бота по ней не связывает никого."""
    from humotech.telegram.services import TelegramLinkService

    made = EmployeeOnboardingService().onboard(
        onboarding_actor, **form(office, department, position, work_schedule)
    )
    # В ссылке лежит `?start=link_<токен>`: бот получает её целиком и
    # отрезает приставку сам.
    from humotech.telegram.services import TelegramLinkError
    from humotech.telegram.tokens import TOKEN_PREFIX

    payload = made.telegram.link.rsplit("=", 1)[-1]
    assert payload.startswith(TOKEN_PREFIX)
    token = payload[len(TOKEN_PREFIX):]

    service = TelegramLinkService()
    service.consume(
        token=token, telegram_user_id=555_111_222, telegram_chat_id=555_111_222,
        telegram_username="nigina", language_code="ru",
    )
    with pytest.raises(TelegramLinkError):
        service.consume(
            token=token, telegram_user_id=999_888_777,
            telegram_chat_id=999_888_777, telegram_username="stranger",
            language_code="ru",
        )

    # Привязана та учётная запись, что открыла ссылку первой, а не вторая.
    account = TelegramAccount.objects.get(employee=made.card.employee)
    assert account.telegram_user_id == 555_111_222


def test_missing_telegram_permission_does_not_cancel_the_hiring(
    organization, office, department, position, work_schedule, make_actor,
    telegram_settings,
):
    """Нет права на Telegram — приём всё равно состоялся.

    Карточка, назначение и график от этого не становятся неправильными, а
    ссылку выдадут позже из карточки сотрудника. Откатывать приём из-за
    смежного права значило бы наказывать HR за чужую настройку ролей.
    """
    without = make_actor(
        organization,
        permissions=("employees.read", "employees.manage",
                     "schedules.read", "schedules.manage"),
    )
    made = EmployeeOnboardingService().onboard(
        without, **form(office, department, position, work_schedule)
    )

    assert made.created is True
    assert made.telegram.state == "SKIPPED"
    assert made.telegram.link is None
    assert EmployeeScheduleAssignment.objects.filter(
        employee=made.card.employee
    ).exists()


# --- журнал аудита ----------------------------------------------------------

def test_onboarding_is_written_to_the_audit_log(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    made = EmployeeOnboardingService().onboard(
        onboarding_actor, **form(office, department, position, work_schedule)
    )

    entry = AuditLog.objects.get(
        action="employee.onboard", entity_id=made.card.employee.id
    )
    assert entry.new_values["employee_number"] == (
        made.card.employee.employee_number
    )
    assert entry.new_values["office_id"] == str(office.id)
    assert entry.new_values["schedule_id"] == str(work_schedule.id)
    assert entry.new_values["telegram"] == "INVITED"


# --- чтение после перезагрузки ----------------------------------------------

def test_employee_and_assignment_are_read_back_from_the_database(
    office, department, position, work_schedule, onboarding_actor,
    telegram_settings,
):
    """После обновления страницы всё читается с сервера, а не из памяти.

    Карточка берётся новым сервисом заново, без остатков объекта, который
    создавал запись: если что-то не попало в базу, это здесь и всплывёт.
    """
    made = EmployeeOnboardingService().onboard(
        onboarding_actor, **form(office, department, position, work_schedule)
    )

    from humotech.employees.services import EmployeeService

    again = EmployeeService().get(
        onboarding_actor, made.card.employee.id, at=date(2026, 9, 15)
    )
    assert again.employee.pinfl == "39803141234567"
    assert again.current_assignment is not None
    assert again.current_assignment.office_id == office.id
    assert again.current_assignment.position_id == position.id
    assert again.current_schedule is not None
    assert again.current_schedule.schedule_id == work_schedule.id
