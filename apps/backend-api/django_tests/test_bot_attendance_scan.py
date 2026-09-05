"""Отметка, пришедшая от бота, а не из открытого Mini App.

Кнопка нижней клавиатуры Telegram не даёт приложению подписи запуска —
это записано в документации Bot API. Поэтому оттуда Mini App отдаёт
данные боту через `sendData`, а бот приходит сюда сам.

Отдельного endpoint у этого пути НЕТ и не появилось: `/me/attendance/scan`
уже принимает вход бота (`BotEmployeeAuthentication`), а за ним стоит тот
же `scanning.scan()`. Второй адрес означал бы вторую систему
посещаемости, которую пришлось бы чинить дважды.

Здесь проверяется ровно граница доверия этого пути: что бот без секрета
никто, что сотрудника определяет сервер, и что подделать направление,
офис или время нечем, потому что таких параметров у запроса нет.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from django_tests.conftest import TEST_BOT_SECRET, bot_headers
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.qr_codes.tokens import issue

pytestmark = pytest.mark.django_db

SCAN = "/api/v1/me/attendance/scan"
TG_ID = 777_000_111

OFFICE_LAT, OFFICE_LON = Decimal("38.559772"), Decimal("68.787038")
NEAR = {"latitude": "38.560200", "longitude": "68.787400", "accuracy_m": "15"}
FAR = {"latitude": "38.600000", "longitude": "68.850000", "accuracy_m": "12"}


@pytest.fixture()
def qr_settings(settings):
    """Секрет подписи кода для тестов. В conftest её нет намеренно:
    набор, проверяющий поведение без секрета, должен уметь его убрать."""
    settings.QR = {
        **settings.QR,
        "SIGNING_SECRET": "test-qr-signing-secret-not-real",
        "TOKEN_TTL_SECONDS": 30,
        "CLOCK_SKEW_SECONDS": 10,
    }
    return settings.QR


@pytest.fixture()
def code(qr_point, now, qr_settings, telegram_settings):
    """Свежий подписанный код с той же точки, что висит на стене."""

    def make(moment=None, ttl=30):
        token, _ = issue(
            organization_id=qr_point.organization_id,
            office_id=qr_point.office_id,
            qr_point_id=qr_point.id,
            direction_mode=qr_point.direction_mode,
            issued_at=moment or now,
            ttl_seconds=ttl,
        )
        return token

    return make


@pytest.fixture()
def located_office(office):
    office.latitude = OFFICE_LAT
    office.longitude = OFFICE_LON
    office.geofence_radius_m = 100
    office.save(update_fields=["latitude", "longitude", "geofence_radius_m"])
    return office


def post(client, token, *, headers=None, **extra):
    body = {"token": token, **extra}
    return client.post(
        SCAN, body, format="json", **(headers if headers is not None else bot_headers(TG_ID))
    )


# --- вход бота --------------------------------------------------------------

def test_without_the_secret_the_telegram_id_is_nobody(
    bot_client, linked_account, code
):
    """Главная проверка пути.

    Без секрета заголовок с Telegram ID — просто число, которое написал
    отправитель запроса. Принимать его значило бы разрешить отметиться
    за любого, чей Telegram ID известен.
    """
    response = post(
        bot_client, code(), headers={"HTTP_X_TELEGRAM_USER_ID": str(TG_ID)}
    )

    assert response.status_code == 401
    assert AttendanceEvent.objects.count() == 0


def test_a_wrong_secret_is_refused(bot_client, linked_account, code):
    response = post(
        bot_client, code(), headers=bot_headers(TG_ID, secret="почти-верный")
    )

    assert response.status_code == 401
    assert AttendanceEvent.objects.count() == 0


def test_an_unconfigured_secret_closes_the_door(
    bot_client, linked_account, code, settings
):
    """Ненастроенный секрет закрывает вход, а не открывает его всем."""
    settings.TELEGRAM = {**settings.TELEGRAM, "BOT_API_SECRET": ""}

    assert post(bot_client, code()).status_code == 401


def test_an_unknown_telegram_id_gets_nothing(bot_client, linked_account, code):
    response = post(bot_client, code(), headers=bot_headers(424_242))

    assert response.status_code in (401, 403)
    assert AttendanceEvent.objects.count() == 0


def test_a_revoked_binding_cannot_mark_attendance(
    bot_client, linked_account, code
):
    linked_account.status = "REVOKED"
    linked_account.save(update_fields=["status"])

    response = post(bot_client, code())

    assert response.status_code in (401, 403)
    assert AttendanceEvent.objects.count() == 0


# --- сама отметка -----------------------------------------------------------

def test_entry_and_exit_are_decided_by_the_server(
    bot_client, linked_account, employee, code
):
    """Направление сервер выводит сам: прислать его нечем."""
    first = post(bot_client, code(), client_event_id="a1")
    second = post(bot_client, code(), client_event_id="a2")

    assert first.json()["status"] == "ENTERED"
    assert second.json()["status"] == "EXITED"
    assert AttendanceSession.objects.filter(
        employee=employee, status="OPEN"
    ).count() == 0


def test_the_client_cannot_choose_the_direction(bot_client, linked_account, code):
    """Лишние поля не принимаются: сервер решает сам.

    Даже если изменённый клиент пришлёт «я выхожу», открытой сессии нет,
    и первой отметкой всё равно будет вход.
    """
    response = post(
        bot_client, code(), event_type="EXIT", direction="EXIT",
        office_id="00000000-0000-0000-0000-000000000000",
    )

    assert response.json()["status"] == "ENTERED"


def test_the_time_is_the_servers(bot_client, linked_account, employee, code):
    """Время отметки серверное: `occurred_at` от клиента не принимается."""
    post(bot_client, code(), occurred_at="2001-01-01T00:00:00Z")

    event = AttendanceEvent.objects.get(employee=employee)
    assert event.occurred_at.year >= 2020


def test_an_expired_code_is_refused(bot_client, linked_account, code, now):
    stale = code(moment=now - timedelta(minutes=5), ttl=30)

    assert post(bot_client, stale).json()["status"] == "QR_EXPIRED"


def test_the_same_code_twice_is_refused(bot_client, linked_account, code):
    token = code()
    post(bot_client, token, client_event_id="first")

    again = post(bot_client, token, client_event_id="second")

    assert again.json()["status"] == "QR_ALREADY_USED"


def test_the_same_attempt_twice_answers_the_same_thing(
    bot_client, linked_account, employee, code
):
    """Автоповтор сети не должен превращаться в отказ у двери."""
    token = code()
    first = post(bot_client, token, client_event_id="one-attempt")
    second = post(bot_client, token, client_event_id="one-attempt")

    assert first.json()["status"] == "ENTERED"
    assert second.json() == first.json()
    assert AttendanceEvent.objects.filter(employee=employee).count() == 1


def test_a_foreign_organization_code_is_refused(
    bot_client, linked_account, other_organization, foreign_office, now,
    code
):
    """Чужой код не работает, даже будучи подписанным нами."""
    from django_tests.conftest import make_qr_point

    foreign_point = make_qr_point(
        other_organization, foreign_office, code="FOREIGN_DOOR"
    )
    token, _ = issue(
        organization_id=other_organization.id,
        office_id=foreign_office.id,
        qr_point_id=foreign_point.id,
        direction_mode=foreign_point.direction_mode,
        issued_at=now,
        ttl_seconds=30,
    )

    assert post(bot_client, token).json()["status"] == "OFFICE_NOT_ALLOWED"


# --- геолокация -------------------------------------------------------------

def test_a_scan_from_the_office_is_accepted(
    bot_client, linked_account, located_office, code
):
    response = post(bot_client, code(), **NEAR)

    assert response.json()["status"] == "ENTERED"


def test_a_scan_from_far_away_is_refused(
    bot_client, linked_account, located_office, code
):
    response = post(bot_client, code(), **FAR)

    assert response.json()["status"] == "OUTSIDE_GEOFENCE"
    assert not response.json()["accepted"]


def test_a_hopeless_accuracy_is_refused(
    bot_client, linked_account, located_office, code
):
    response = post(
        bot_client, code(),
        latitude=FAR["latitude"], longitude=FAR["longitude"],
        accuracy_m="50000",
    )

    assert response.json()["status"] == "LOCATION_TOO_VAGUE"


def test_impossible_coordinates_are_refused_as_a_bad_field(
    bot_client, linked_account, located_office, code
):
    """Широта 900 — неверное поле, а не «слишком далеко».

    Отвечать на мусор расстоянием значило бы врать про причину.
    """
    response = post(
        bot_client, code(), latitude="900", longitude="0", accuracy_m="10"
    )

    assert response.status_code == 400


def test_one_coordinate_without_the_other_is_refused(
    bot_client, linked_account, code
):
    response = post(bot_client, code(), latitude="38.56")

    assert response.status_code == 400


def test_a_scan_without_coordinates_still_works(
    bot_client, linked_account, located_office, code
):
    """Точка не требует геолокации — старый путь не сломан."""
    assert post(bot_client, code()).json()["status"] == "ENTERED"
