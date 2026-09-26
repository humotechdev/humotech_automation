"""Сквозной путь всей системы — одним сценарием.

Здесь не проверяются подробности: у каждого модуля есть свой набор, где
разобраны границы, отказы и спорные случаи. Задача этого файла другая —
пройти дорогу целиком и поймать то, что видно только на стыках:

— офис, созданный в CRM, по которому не принимается отметка;
— стажёр, о приёме которого не узнал бот;
— напоминание, ушедшее человеку в отпуске;
— «не приду», молча оформившее отсутствие;
— заявление, которое посчитали принесённой справкой;
— вопрос, заведший обращение до того, как человек об этом попросил;
— уволенный, оставшийся в составе смены и в численности.

Сценарий идёт по шагам из задания и повторяет то, что делает человек:
через HTTP — так же, как CRM, Mini App и бот.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from humotech.absences.services import AbsenceService
from humotech.analytics.movement import MovementService
from humotech.attendance import reminders
from humotech.attendance.hr import AttendanceHrService
from humotech.attendance.models import DayNotice
from humotech.employees.lifecycle import EmployeeLifecycleService
from humotech.employees.models import Employee
from humotech.notifications.models import Notification
from humotech.offices.geo import EARTH_RADIUS_M
from humotech.questions.models import EmployeeQuestion
from humotech.schedules.models import EmployeeScheduleAssignment, ScheduleDay, WorkSchedule
from humotech.telegram.identity import resolve_by_telegram_user_id

from .conftest import bot_headers, link_telegram

pytestmark = pytest.mark.django_db

API = "/api/v1"
TG_ID = 777_000_111

OFFICE_LAT, OFFICE_LON = Decimal("41.311081"), Decimal("69.240562")
METRES_PER_DEGREE = 2 * math.pi * EARTH_RADIUS_M / 360


def north(metres: float) -> tuple[str, str]:
    """Точка в `metres` метрах к северу от офиса."""
    lat = float(OFFICE_LAT) + metres / METRES_PER_DEGREE
    return f"{lat:.6f}", str(OFFICE_LON)


@pytest.fixture()
def hr_client(api_client, make_user, organization):
    """Кадровик, которому открыто всё нужное по сценарию."""
    user = make_user(
        organization,
        permissions=(
            "regions.read", "offices.read", "offices.manage",
            "qr_points.read", "qr_points.manage",
            "employees.read", "employees.manage", "employees.archive",
            "attendance.read", "absences.read", "absences.approve",
            "absences.documents", "questions.read", "questions.answer",
            "schedules.read", "schedules.manage",
            "departments.manage", "positions.manage",
        ),
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture()
def hr_actor(make_actor, organization):
    return make_actor(
        organization,
        permissions=("employees.read", "employees.manage", "employees.archive",
                     "attendance.read", "offices.read", "absences.read",
                     "absences.approve", "absences.documents"),
    )


def test_the_whole_road(
    hr_client, hr_actor, bot_client, api_client, organization, region,
    telegram_settings, make_absence,
):
    # --- 1. Офис, карта, радиус, QR-точка ---------------------------------
    office = hr_client.post(f"{API}/offices/", {
        "region_id": str(region.id), "code": "E2E", "name": "Ташкент Сити",
        "timezone": "UTC",
    }, format="json")
    assert office.status_code == 201, office.content
    office_id = office.json()["id"]

    placed = hr_client.patch(f"{API}/offices/{office_id}/", {
        "latitude": str(OFFICE_LAT), "longitude": str(OFFICE_LON),
        "geofence_radius_m": 100,
    }, format="json")
    assert placed.status_code == 200, placed.content
    assert placed.json()["geofence_radius_m"] == 100

    point = hr_client.post(f"{API}/qr-points/", {
        "office_id": office_id, "name": "Главный вход",
        "direction_mode": "BOTH", "qr_mode": "STATIC",
    }, format="json")
    assert point.status_code == 201, point.content
    sticker = point.json()["sticker_link"]

    # --- 2. Отдел, должность, график --------------------------------------
    department = hr_client.post(f"{API}/departments/", {"name": "Разработка"},
                                format="json")
    assert department.status_code == 201, department.content
    position = hr_client.post(f"{API}/positions/", {"name": "Инженер"},
                              format="json")
    assert position.status_code == 201, position.content

    schedule = WorkSchedule.objects.create(
        organization=organization, name="Пятидневка", timezone="UTC",
        weekly_minutes=40 * 60, status="ACTIVE", late_grace_minutes=15,
    )
    for weekday in range(1, 8):
        ScheduleDay.objects.create(
            schedule=schedule, weekday=weekday, is_working_day=weekday <= 5,
            start_time=time(9, 0) if weekday <= 5 else None,
            end_time=time(18, 0) if weekday <= 5 else None,
        )

    # --- 3. Стажёр и приветствие в Telegram --------------------------------
    hired = date.today()
    onboarded = hr_client.post(f"{API}/employees/onboard/", {
        "idempotency_key": "e2e-1",
        "first_name": "Азизбек", "last_name": "Мурадов",
        "hire_date": hired.isoformat(),
        "office_id": office_id,
        "department_id": department.json()["id"],
        "position_id": position.json()["id"],
        "schedule_id": str(schedule.id),
        "pinfl": "39803141234567",
        "phone": "+998901234567",
        "employment_status": "PROBATION",
    }, format="json")
    assert onboarded.status_code == 201, onboarded.content
    employee_id = onboarded.json()["employee"]["id"]
    employee = Employee.objects.get(id=employee_id)
    assert employee.employment_status == "PROBATION"

    hello = Notification.objects.filter(
        employee_id=employee_id, notification_type="employee.hired"
    ).first()
    assert hello is not None, "человек должен узнать о приёме от бота"
    assert "стажировку" in hello.body
    assert "Инженер" in hello.body and "Разработка" in hello.body

    link_telegram(employee, telegram_user_id=TG_ID)
    context = resolve_by_telegram_user_id(TG_ID)
    # График назначает сам приём: второе назначение на те же даты
    # отвергает ограничение базы — и это правильно, у человека не может
    # быть двух графиков одновременно.
    assert EmployeeScheduleAssignment.objects.filter(
        employee_id=employee_id, schedule_id=schedule.id
    ).exists()

    # --- 4. Вход и выход по печатному коду ---------------------------------
    near_lat, near_lon = north(30)
    entered = bot_client.post(f"{API}/me/attendance/scan", {
        "token": sticker, "client_event_id": "e2e-in",
        "latitude": near_lat, "longitude": near_lon, "accuracy_m": "12",
    }, format="json", **bot_headers(TG_ID))
    assert entered.status_code == 200, entered.content
    assert entered.json()["status"] == "ENTERED"

    far_lat, far_lon = north(400)
    refused = bot_client.post(f"{API}/me/attendance/scan", {
        "token": sticker, "client_event_id": "e2e-far",
        "latitude": far_lat, "longitude": far_lon, "accuracy_m": "12",
    }, format="json", **bot_headers(TG_ID))
    assert refused.json()["status"] == "OUTSIDE_GEOFENCE"
    # Отказ обязан назвать числа: «слишком далеко» без них — спор,
    # в котором человеку нечем проверить, кто прав.
    assert refused.json()["distance_m"] > 100
    assert refused.json()["radius_m"] == 100

    # --- 5. «Опаздываю» ----------------------------------------------------
    late = bot_client.post(f"{API}/me/attendance/notice",
                           {"kind": "LATE", "comment": "Пробки"},
                           format="json", **bot_headers(TG_ID))
    assert late.status_code == 200, late.content
    assert DayNotice.objects.filter(employee_id=employee_id).count() == 1

    # --- 6. «Не приду» ничего не оформляет ---------------------------------
    from humotech.absences.models import EmployeeAbsence

    bot_client.post(f"{API}/me/attendance/notice",
                    {"kind": "ABSENT", "comment": "Заболел"},
                    format="json", **bot_headers(TG_ID))
    # Одна строка на человека и день: второй ответ заменяет первый.
    assert DayNotice.objects.filter(employee_id=employee_id).count() == 1
    # И ничего не оформляет: иначе отсутствие заводилось бы одной
    # кнопкой мимо HR.
    assert not EmployeeAbsence.objects.filter(employee_id=employee_id).exists()

    # --- 7. Отпуск: создать, одобрить --------------------------------------
    from humotech.absences.models import AbsenceType

    AbsenceType.objects.create(
        organization=organization, code="ANNUAL_LEAVE", name="Ежегодный отпуск",
        is_paid=True, requires_approval=True,
    )
    service = AbsenceService()
    first = date.today() + timedelta(days=14)
    vacation = service.create(
        context, absence_type_code="ANNUAL_LEAVE",
        first_day=first, last_day=first + timedelta(days=4),
    )
    # До решения дней в учёте нет.
    assert not EmployeeAbsence.objects.filter(
        origin_request_id=vacation.request.id
    ).exists()

    service.decide(hr_actor, vacation.request.id, approve=True, comment="ок")
    assert EmployeeAbsence.objects.filter(
        origin_request_id=vacation.request.id
    ).exists()

    # --- 8. Больничный: заявление, справка, решение по ней -----------------
    AbsenceType.objects.create(
        organization=organization, code="SICK_LEAVE", name="Больничный",
        is_paid=True, requires_approval=True, requires_document=True,
    )
    sick_first = date.today()
    sick = service.create(
        context, absence_type_code="SICK_LEAVE",
        first_day=sick_first, last_day=sick_first + timedelta(days=3),
    )

    blank = bot_client.get(
        f"{API}/me/absences/{sick.request.id}/application", **bot_headers(TG_ID)
    )
    assert blank.status_code == 200
    assert blank.content.startswith(b"%PDF-")
    # Системный бланк — не принесённая справка: иначе больничный
    # подтверждал бы сам себя.
    assert service.request(context, sick.request.id).documents == 0

    service.attach_document(
        context, sick.request.id,
        SimpleUploadedFile("spravka.pdf", b"%PDF-1.4 x", "application/pdf"),
    )
    assert service.request(context, sick.request.id).documents == 1

    from humotech.absences.models import AbsenceDocument

    document = AbsenceDocument.objects.get(absence_request_id=sick.request.id)
    service.verify_document(
        hr_actor, sick.request.id, document.id, accept=False,
        comment="Фото нечитаемое",
    )
    told = Notification.objects.filter(
        notification_type="absence.document_rejected"
    ).first()
    assert told is not None and "Фото нечитаемое" in told.body

    # --- 9. Напоминание не уходит тому, кто в отпуске ----------------------
    away = make_absence(employee, code="SICK_LEAVE", day=date.today())
    assert away is not None
    moment = datetime.combine(
        date.today(), time(9, 30), tzinfo=dt_timezone.utc
    )
    assert reminders.due(moment) == []

    # --- 10. Вопрос: HR заводится только по просьбе ------------------------
    assert not EmployeeQuestion.objects.filter(employee_id=employee_id).exists()
    asked = bot_client.post(f"{API}/me/ask/escalate",
                            {"text": "Где взять справку 2-НДФЛ?"},
                            format="json", **bot_headers(TG_ID))
    assert asked.status_code == 201, asked.content
    question = EmployeeQuestion.objects.get(employee_id=employee_id)
    assert "2-НДФЛ" in question.question_text

    answered = hr_client.post(
        # Очередь обращений живёт под `knowledge/escalations`: она выросла
        # из эскалаций ассистента, и адрес остался её.
        f"{API}/knowledge/escalations/{question.id}/reply/",
        {"text": "В бухгалтерии, кабинет 204."}, format="json",
    )
    assert answered.status_code in (200, 201), answered.content
    reply = Notification.objects.filter(
        notification_type="question.reply"
    ).first()
    assert reply is not None
    # Ответ узнаётся по номеру и по цитате: человек с тремя вопросами
    # иначе не поймёт, на какой именно ему ответили.
    assert f"№{question.number}" in reply.body
    assert "2-НДФЛ" in reply.body

    # --- 11. Стажёр в штат, потом увольнение ------------------------------
    lifecycle = EmployeeLifecycleService()
    lifecycle.promote_to_staff(hr_actor, employee.id)
    employee.refresh_from_db()
    assert employee.employment_status == "ACTIVE"
    assert Notification.objects.filter(
        notification_type="employee.promoted"
    ).exists()

    # Работающий виден в составе смены и в численности.
    shift = AttendanceHrService().presence(hr_actor, day=date.today())
    assert [row.employee_id for row in shift.rows] == [employee.id]
    movement = MovementService().report(
        hr_actor, first=date.today() - timedelta(days=30), last=date.today()
    )
    assert movement.current.hired == 1
    assert movement.headcount == 1

    # --- 12. Увольнение убирает отовсюду -----------------------------------
    # Увольняем сегодняшним днём: человека приняли сегодня же, и
    # вчерашнее увольнение сервер отклонит — правильно отклонит.
    dismissed = hr_client.post(f"{API}/employees/{employee.id}/terminate/", {
        "termination_date": date.today().isoformat(),
        "reason": "По собственному желанию",
    }, format="json")
    assert dismissed.status_code == 200, dismissed.content

    employee.refresh_from_db()
    assert employee.employment_status == "TERMINATED"
    assert employee.termination_reason == "По собственному желанию"

    # Последний рабочий день человек ещё в смене — назначение закрыто
    # этим днём включительно. А назавтра его нет.
    tomorrow = date.today() + timedelta(days=1)
    after = AttendanceHrService().presence(hr_actor, day=tomorrow)
    assert after.rows == [], "уволенного в составе смены быть не должно"

    later = MovementService().report(
        hr_actor, first=date.today() - timedelta(days=30), last=date.today()
    )
    assert later.current.left == 1
    assert later.headcount == 0
