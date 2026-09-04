"""Сквозной путь QR: от экрана в офисе до записи в журнале отметок.

Здесь проверяется то, что нельзя проверить по частям: экран получает код,
сотрудник его сканирует, сервер решает вход это или выход и открывает или
закрывает сессию. Разрыв в любом звене даёт систему, которая по отдельности
вся правильная, а вместе не работает.

Отдельная тема — что решает сервер, а что мог бы решить клиент. Направление,
офис, время и результат клиент не присылает вовсе: таких параметров нет ни
в одном запросе. Проверяется это прямо, а не подразумевается.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

import pytest
from django.db import connections
from django.utils import timezone

from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.attendance.scanning import ScanOutcome, ScanStatus, scan
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.qr_codes.models import QrDisplayDevice, QrDisplaySession
from humotech.qr_codes.services import QrDisplayService
from humotech.telegram.identity import resolve_by_telegram_user_id

from .conftest import bot_headers, link_telegram, make_qr_point

pytestmark = pytest.mark.django_db

SCAN = "/api/v1/me/attendance/scan"
CODE = "/api/v1/qr-display/code"
PAIR = "/api/v1/qr-display/pair"
TG_ID = 777_000_111


@pytest.fixture()
def qr_settings(settings):
    settings.QR = {
        **settings.QR,
        "SIGNING_SECRET": "test-qr-signing-secret-not-real",
        "TOKEN_TTL_SECONDS": 30,
        "CLOCK_SKEW_SECONDS": 10,
        "DISPLAY_CREDENTIAL_TTL_SECONDS": 2_592_000,
        "DISPLAY_PAIRING_TTL_SECONDS": 3600,
    }
    return settings.QR


@pytest.fixture()
def display(db, qr_point, qr_settings):
    """Сопряжённый экран у главного входа."""
    service = QrDisplayService()
    issued = service.create_device_for_point(qr_point, name="Планшет у входа")
    paired = service.pair(issued.pairing_code)
    return paired


@pytest.fixture()
def context(db, employee, telegram_settings):
    link_telegram(employee)
    return resolve_by_telegram_user_id(TG_ID)


def fresh_token(display) -> str:
    """Очередной код с экрана — так же, как его получил бы сам экран."""
    return QrDisplayService().issue_qr(display.device).token


# --- экран получает коды ---------------------------------------------------

def test_pairing_code_works_once(display, qr_settings):
    """Подсмотренный код сопряжения бесполезен, если экран уже сопряжён."""
    service = QrDisplayService()
    issued = service.create_device_for_point(
        display.device.qr_point, name="Второй планшет"
    )
    service.pair(issued.pairing_code)

    from humotech.core.errors import PermissionDenied

    with pytest.raises(PermissionDenied):
        service.pair(issued.pairing_code)


def test_display_gets_a_new_code_each_time(display, qr_settings):
    first = fresh_token(display)
    second = fresh_token(display)
    assert first != second


def test_display_cannot_ask_for_another_office(api_client, display, qr_settings,
                                               other_office, organization):
    """Офис не выбирается с frontend — его негде передать.

    Экран привязан к точке, точка знает свой офис. Даже присланный
    `office_id` читать некому.
    """
    other_point = make_qr_point(organization, other_office, code="OTHER_ENTRANCE")

    response = api_client.get(
        CODE,
        {"office_id": str(other_office.id), "qr_point_id": str(other_point.id)},
        HTTP_AUTHORIZATION=f"Bearer {display.credential}",
    )

    assert response.status_code == 200
    assert response.json()["point_name"] == display.device.qr_point.name


def test_revoked_display_gets_nothing(api_client, display, qr_settings):
    QrDisplayService().revoke_the_device(display.device)

    response = api_client.get(
        CODE, HTTP_AUTHORIZATION=f"Bearer {display.credential}"
    )
    assert response.status_code == 403


def test_display_credential_is_stored_only_as_a_hash(display):
    device = QrDisplayDevice.objects.get(id=display.device.id)
    assert display.credential not in str(device.__dict__)
    assert device.credential_hash != display.credential


def test_unknown_credential_is_refused(api_client, display, qr_settings):
    response = api_client.get(CODE, HTTP_AUTHORIZATION="Bearer выдуманный")
    assert response.status_code == 403


def test_issuing_a_code_marks_the_screen_alive(display, qr_settings):
    """Связь экрана видна по сессии показа — иначе «экран отвалился»
    заметил бы только тот, кто мимо него прошёл."""
    fresh_token(display)

    session = QrDisplaySession.objects.get(device_id=display.device.id)
    assert session.status == "ACTIVE"
    assert session.last_seen_at is not None


# --- вход и выход ----------------------------------------------------------

def test_both_point_opens_then_closes_a_session(context, display, qr_settings):
    """Точка BOTH решает по факту: есть открытая сессия — значит выход."""
    entered = scan(context, token=fresh_token(display))
    assert entered.status == ScanStatus.ENTERED

    exited = scan(context, token=fresh_token(display))
    assert exited.status == ScanStatus.EXITED

    session = AttendanceSession.objects.get(employee=context.employee)
    assert session.status == "CLOSED"
    assert session.duration_seconds is not None


def test_entry_point_never_closes_a_session(context, organization, office,
                                            qr_settings):
    point = make_qr_point(organization, office, code="TURNSTILE_IN",
                          direction_mode="ENTRY")
    service = QrDisplayService()
    issued = service.create_device_for_point(point, name="Вход")
    paired = service.pair(issued.pairing_code)

    assert scan(context, token=fresh_token(paired)).status == ScanStatus.ENTERED
    # Второй раз тем же входом — уже внутри, а не выход.
    assert scan(context, token=fresh_token(paired)).status == (
        ScanStatus.ALREADY_INSIDE
    )


def test_exit_point_without_an_open_session_is_refused(context, organization,
                                                       office, qr_settings):
    point = make_qr_point(organization, office, code="TURNSTILE_OUT",
                          direction_mode="EXIT")
    service = QrDisplayService()
    issued = service.create_device_for_point(point, name="Выход")
    paired = service.pair(issued.pairing_code)

    assert scan(context, token=fresh_token(paired)).status == ScanStatus.NOT_INSIDE
    assert not AttendanceSession.objects.filter(employee=context.employee).exists()


def test_direction_cannot_be_sent_by_the_employee(bot_client, context, display,
                                                  qr_settings):
    """Прислать «я выхожу» нечем: такого параметра нет.

    Тело запроса разбирает сериализатор, и лишние поля до сервиса не
    доходят — подделать можно только то, что где-то принимается.
    """
    response = bot_client.post(
        SCAN,
        {"token": fresh_token(display), "event_type": "EXIT",
         "direction": "EXIT", "occurred_at": "2020-01-01T00:00:00Z"},
        format="json",
        **bot_headers(TG_ID),
    )

    assert response.status_code == 200
    assert response.json()["status"] == ScanStatus.ENTERED


def test_event_time_comes_from_the_server(context, display, qr_settings):
    before = timezone.now()
    scan(context, token=fresh_token(display))
    after = timezone.now()

    event = AttendanceEvent.objects.get(employee=context.employee)
    assert before <= event.occurred_at <= after


# --- переигрывание ---------------------------------------------------------

def test_the_same_code_cannot_be_used_twice_by_one_employee(context, display,
                                                            qr_settings):
    """Один код — одна отметка, в любом направлении.

    На точке BOTH без этого один и тот же код засчитался бы дважды:
    сначала вход, потом выход. При сроке в полминуты это рабочий сценарий,
    а не теоретический.
    """
    token = fresh_token(display)
    assert scan(context, token=token).status == ScanStatus.ENTERED

    repeated = scan(context, token=token)
    assert repeated.status == ScanStatus.QR_ALREADY_USED
    assert AttendanceSession.objects.filter(
        employee=context.employee, status="OPEN"
    ).count() == 1


def test_the_same_code_serves_different_employees(context, display, qr_settings,
                                                  organization, office,
                                                  telegram_settings):
    """Код на экране общий: мимо него за полминуты проходит несколько человек."""
    colleague = Employee.objects.create(
        organization=organization,
        employee_number="EMP-0002",
        first_name="Пётр",
        last_name="Петров",
        hire_date=date(2024, 2, 1),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=colleague, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 2, 1),
    )
    link_telegram(colleague, telegram_user_id=888_000_222)
    other_context = resolve_by_telegram_user_id(888_000_222)

    token = fresh_token(display)
    assert scan(context, token=token).status == ScanStatus.ENTERED
    assert scan(other_context, token=token).status == ScanStatus.ENTERED


def test_expired_code_is_refused(context, display, qr_settings):
    token = fresh_token(display)
    later = timezone.now() + timedelta(minutes=5)

    assert scan(context, token=token, now=later).status == ScanStatus.QR_EXPIRED


def test_tampered_code_is_refused(context, display, qr_settings):
    token = fresh_token(display)
    broken = token[:-6] + "AAAAAA"

    assert scan(context, token=broken).status == ScanStatus.QR_INVALID
    assert not AttendanceEvent.objects.filter(employee=context.employee).exists()


def test_code_of_a_point_moved_to_another_office_is_refused(
    context, display, qr_settings, other_office
):
    """Подпись подтверждает выпуск, но не то, что код до сих пор верен."""
    token = fresh_token(display)
    point = display.device.qr_point
    point.office = other_office
    point.save(update_fields=["office", "updated_at"])

    assert scan(context, token=token).status == ScanStatus.QR_INVALID


# --- границы доступа -------------------------------------------------------

def test_cross_organization_scan_is_always_forbidden(
    context, qr_settings, other_organization, foreign_office
):
    foreign_point = make_qr_point(
        other_organization, foreign_office, code="FOREIGN_ENTRANCE"
    )
    service = QrDisplayService()
    issued = service.create_device_for_point(foreign_point, name="Чужой экран")
    paired = service.pair(issued.pairing_code)

    outcome = scan(context, token=fresh_token(paired))

    assert outcome.status == ScanStatus.OFFICE_NOT_ALLOWED
    assert not AttendanceSession.objects.filter(employee=context.employee).exists()


def test_employee_of_another_office_is_refused(context, qr_settings,
                                               organization, other_office):
    """Офис своей организации, но не свой — отметка не проходит."""
    point = make_qr_point(organization, other_office, code="BRANCH_ENTRANCE")
    service = QrDisplayService()
    issued = service.create_device_for_point(point, name="Экран филиала")
    paired = service.pair(issued.pairing_code)

    outcome = scan(context, token=fresh_token(paired))
    assert outcome.status == ScanStatus.OFFICE_NOT_ALLOWED


def test_inactive_employee_cannot_check_in(bot_client, context, display,
                                           employee, qr_settings):
    """Уволенный не отмечается: доступ закрывается на входе, до скана."""
    employee.employment_status = "TERMINATED"
    employee.save(update_fields=["employment_status", "updated_at"])

    response = bot_client.post(
        SCAN, {"token": fresh_token(display)}, format="json", **bot_headers(TG_ID)
    )
    assert response.status_code == 401


def test_inactive_point_refuses(context, display, qr_settings):
    point = display.device.qr_point
    token = fresh_token(display)
    point.is_active = False
    point.save(update_fields=["is_active", "updated_at"])

    assert scan(context, token=token).status == ScanStatus.QR_POINT_INACTIVE


# --- одновременность -------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_scans_do_not_open_two_sessions(
    context, organization, office, qr_settings
):
    """Два скана в один момент обязаны дать одну сессию, а не две.

    Точка здесь только на вход: на точке BOTH второй скан законно стал бы
    выходом, и тест перестал бы проверять то, ради чего написан. На входе
    же второй скан не имеет права пройти ни при каком порядке.

    `transaction=True` обязателен: обычный тест обёрнут в одну транзакцию,
    и потоки не увидели бы записей друг друга — проверять было бы нечего.

    Без блокировки строки сотрудника оба запроса увидели бы «открытой
    сессии нет». Что произойдёт дальше, зависит от везения: либо две
    сессии, либо нарушение `uq_attendance_sessions_one_open`. Блокировка
    убирает саму развилку, а ключ в базе остаётся вторым рубежом.
    """
    point = make_qr_point(
        organization, office, code="TURNSTILE_RACE", direction_mode="ENTRY"
    )
    service = QrDisplayService()
    issued = service.create_device_for_point(point, name="Турникет")
    paired = service.pair(issued.pairing_code)

    tokens = [fresh_token(paired), fresh_token(paired)]
    results: list = []
    barrier = threading.Barrier(2)

    def attempt(token: str) -> None:
        barrier.wait()
        try:
            results.append(scan(context, token=token))
        except Exception as error:  # noqa: BLE001 — падение потока молча теряется
            results.append(error)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt, args=(token,)) for token in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(isinstance(item, ScanOutcome) for item in results), results
    assert (
        AttendanceSession.objects.filter(employee=context.employee).count() == 1
    ), "две одновременные отметки создали две сессии"

    statuses = sorted(outcome.status for outcome in results)
    assert statuses == [ScanStatus.ALREADY_INSIDE, ScanStatus.ENTERED]


# --- журнал ----------------------------------------------------------------

def test_every_attempt_is_recorded(context, display, qr_settings, organization,
                                   other_office):
    """Отклонённая попытка тоже попадает в журнал отметок.

    Это основной материал для разбора: без отклонённых попыток видно только
    то, что получилось, а разбираются обычно с тем, что не получилось.
    """
    point = make_qr_point(organization, other_office, code="BRANCH_2")
    service = QrDisplayService()
    issued = service.create_device_for_point(point, name="Экран филиала")
    paired = service.pair(issued.pairing_code)

    scan(context, token=fresh_token(paired))

    event = AttendanceEvent.objects.get(employee=context.employee)
    assert event.verification_status == "REJECTED"
    assert event.rejection_reason == "OFFICE_NOT_ALLOWED"


def test_logs_never_contain_the_code_itself(context, display, qr_settings, caplog):
    """Из логов не должен собираться рабочий код.

    Попытка записывается по существу — точка, причина, время, — но ни строки
    кода, ни подписи в журнале нет.
    """
    token = fresh_token(display)
    broken = token[:-6] + "AAAAAA"

    with caplog.at_level(logging.INFO, logger="humotech.attendance"):
        scan(context, token=broken)
        scan(context, token=token)
        scan(context, token=token)  # повтор — тоже отказ, тоже в журнал

    written = "\n".join(record.getMessage() for record in caplog.records)
    assert written, "отказы должны попадать в журнал"
    assert token not in written
    assert broken not in written
    assert token[8:40] not in written


def test_event_stores_the_code_only_as_a_hash(context, display, qr_settings):
    token = fresh_token(display)
    scan(context, token=token)

    event = AttendanceEvent.objects.get(employee=context.employee)
    assert event.qr_nonce_hash
    assert event.qr_nonce_hash not in token
    assert len(event.qr_nonce_hash) == 64
