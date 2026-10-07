"""Настройка офиса из CRM и отметка по печатному QR-коду.

Печатный код висит на стене месяцами, срока и одноразовости у него нет.
Поэтому здесь проверяется то, что защищает его вместо них: координаты
обязательны, офис обязан быть на карте, радиус соблюдается, перевыпуск
сразу гасит прежнюю наклейку, выключенная точка не принимает отметок, а
направление определяется самой точкой.

Последний тест — сквозной сценарий через HTTP, ровно так, как это делает
HR и бот: завести офис, поставить точку на карте, задать 100 м, выпустить
QR входа и отметиться.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from django_tests.conftest import bot_headers, link_telegram
from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.attendance.scanning import ScanStatus, scan
from humotech.audit.models import AuditLog
from humotech.core.errors import ValidationFailed
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.offices.geo import EARTH_RADIUS_M, distance_m
from humotech.offices.services import OfficeService
from humotech.qr_codes.models import OfficeQrPoint
from humotech.qr_codes.points import QrPointService
from humotech.qr_codes.stickers import link, secret_of
from humotech.telegram.identity import resolve_by_telegram_user_id

pytestmark = pytest.mark.django_db

API = "/api/v1"
TG_ID = 777_000_111

# Офис в Ташкенте и точки на заданном расстоянии к северу от него.
OFFICE_LAT, OFFICE_LON = Decimal("41.311081"), Decimal("69.240562")
METRES_PER_DEGREE = 2 * math.pi * EARTH_RADIUS_M / 360


def north(metres: float) -> tuple[Decimal, Decimal]:
    """Точка в `metres` метрах к северу от офиса."""
    lat = float(OFFICE_LAT) + metres / METRES_PER_DEGREE
    return Decimal(f"{lat:.6f}"), OFFICE_LON


NEAR = north(30)
FAR = north(184)


@pytest.fixture()
def located_office(office):
    office.latitude = OFFICE_LAT
    office.longitude = OFFICE_LON
    office.geofence_radius_m = 100
    office.save(update_fields=["latitude", "longitude", "geofence_radius_m"])
    return office


@pytest.fixture()
def manager(make_actor, organization):
    return make_actor(
        organization,
        permissions=("offices.read", "offices.manage",
                     "qr_points.read", "qr_points.manage"),
    )


@pytest.fixture()
def context(db, employee, telegram_settings):
    link_telegram(employee)
    return resolve_by_telegram_user_id(TG_ID)


@pytest.fixture()
def sticker(manager, located_office, telegram_settings):
    """Выпустить печатный код точки нужного направления."""

    def make(direction="ENTRY", name="Главный вход"):
        issued = QrPointService().create(
            manager, office_id=located_office.id, name=name,
            direction_mode=direction, qr_mode="STATIC",
        )
        return issued.point, link(issued.static_token)

    return make


def mark(context, code, *, at=None, place=NEAR, accuracy="12", **extra):
    location = (
        {"latitude": place[0], "longitude": place[1], "accuracy_m": accuracy}
        if place is not None else {}
    )
    return scan(context, token=code, now=at or timezone.now(), **location, **extra)


# --- расстояние и разбор кода -------------------------------------------------

def test_distance_to_a_point_184_metres_away():
    lat, lon = FAR
    assert distance_m(OFFICE_LAT, OFFICE_LON, lat, lon) == pytest.approx(184, abs=1)


def test_sticker_link_is_recognised_in_every_form(telegram_settings):
    secret = "A" * 43
    assert secret_of(link(secret)) == secret
    assert secret_of(f"qr_{secret}") == secret
    assert secret_of(f"tg://resolve?domain=humotech_test_bot&start=qr_{secret}") == secret


def test_foreign_strings_are_not_stickers():
    secret = "A" * 43
    # Меняющийся код экрана, чужой сайт с похожим параметром и огрызок.
    assert secret_of("HT1" + "x" * 100) is None
    assert secret_of(f"https://evil.example/?start=qr_{secret}") is None
    assert secret_of("qr_short") is None
    assert secret_of(None) is None


# --- отметка по печатному коду ------------------------------------------------

def test_entry_inside_the_zone_opens_a_session_and_keeps_the_distance(
    context, sticker
):
    point, code = sticker("ENTRY")

    outcome = mark(context, code)

    assert outcome.status == ScanStatus.ENTERED
    assert outcome.point_mode == "ENTRY"
    assert outcome.radius_m == 100
    assert outcome.distance_m == pytest.approx(30, abs=1)
    event = AttendanceEvent.objects.get(qr_point=point)
    assert event.verification_status == "ACCEPTED"
    assert event.inside_geofence is True
    assert float(event.distance_m) == pytest.approx(30, abs=1)
    assert AttendanceSession.objects.filter(status="OPEN").count() == 1


def test_too_far_is_refused_with_the_distance_and_the_limit(context, sticker):
    _, code = sticker("ENTRY")

    outcome = mark(context, code, place=FAR)

    assert outcome.status == ScanStatus.OUTSIDE_GEOFENCE
    assert outcome.distance_m == pytest.approx(184, abs=1)
    assert outcome.radius_m == 100
    # Отказ в журнале, сессии нет.
    event = AttendanceEvent.objects.get()
    assert event.verification_status == "REJECTED"
    assert float(event.distance_m) == pytest.approx(184, abs=1)
    assert not AttendanceSession.objects.exists()


def test_a_sticker_without_coordinates_is_refused(context, sticker):
    _, code = sticker("ENTRY")

    assert mark(context, code, place=None).status == ScanStatus.GEOLOCATION_REQUIRED


def test_a_vague_location_is_refused(context, sticker, settings):
    _, code = sticker("ENTRY")

    outcome = mark(context, code, accuracy="50000")

    assert outcome.status == ScanStatus.LOCATION_TOO_VAGUE


def test_an_office_without_a_point_on_the_map_refuses_stickers(
    context, manager, office, telegram_settings
):
    """Без точки на карте сравнивать не с чем — наклейка не работает вовсе."""
    issued = QrPointService().create(
        manager, office_id=office.id, name="Вход", direction_mode="ENTRY",
        qr_mode="STATIC",
    )

    outcome = mark(context, link(issued.static_token))

    assert outcome.status == ScanStatus.GEOFENCE_NOT_CONFIGURED


def test_a_disabled_point_refuses(context, sticker, manager):
    point, code = sticker("ENTRY")
    QrPointService().set_active(manager, point.id, active=False)

    assert mark(context, code).status == ScanStatus.QR_POINT_INACTIVE


def test_reissue_kills_the_old_sticker_at_once(context, sticker, manager):
    point, old = sticker("BOTH")

    issued = QrPointService().reissue_static_token(manager, point.id)
    point.refresh_from_db()

    assert point.rotated_at is not None
    assert mark(context, old).status == ScanStatus.QR_REVOKED
    assert mark(context, link(issued.static_token)).status == ScanStatus.ENTERED


def test_an_entry_point_never_closes_a_session(context, sticker):
    _, code = sticker("ENTRY")
    start = timezone.now()

    assert mark(context, code, at=start).status == ScanStatus.ENTERED
    # Сразу второй раз — «только что отметились», а не второй вход.
    assert mark(context, code, at=start + timedelta(seconds=5)).status == (
        ScanStatus.TOO_SOON
    )
    # Позже — точка входа выход не отмечает.
    later = mark(context, code, at=start + timedelta(minutes=3))
    assert later.status == ScanStatus.ALREADY_INSIDE
    assert later.point_mode == "ENTRY"
    assert AttendanceSession.objects.filter(status="OPEN").count() == 1


def test_an_exit_point_needs_an_open_session(context, sticker):
    _, code = sticker("EXIT", name="Выход со склада")

    outcome = mark(context, code)

    assert outcome.status == ScanStatus.NOT_INSIDE
    assert outcome.point_mode == "EXIT"


def test_entry_and_exit_points_work_as_a_pair(context, sticker):
    _, entry = sticker("ENTRY")
    _, leave = sticker("EXIT", name="Служебный выход")
    start = timezone.now()

    assert mark(context, entry, at=start).status == ScanStatus.ENTERED
    closed = mark(context, leave, at=start + timedelta(hours=8))

    assert closed.status == ScanStatus.EXITED
    session = AttendanceSession.objects.get()
    assert session.status == "CLOSED"


def test_a_both_point_alternates_but_not_instantly(context, sticker):
    _, code = sticker("BOTH", name="Главный вход")
    start = timezone.now()

    assert mark(context, code, at=start).status == ScanStatus.ENTERED
    assert mark(context, code, at=start + timedelta(seconds=10)).status == (
        ScanStatus.TOO_SOON
    )
    assert mark(context, code, at=start + timedelta(hours=1)).status == (
        ScanStatus.EXITED
    )


# --- границы геозоны в карточке офиса -----------------------------------------

def test_radius_is_kept_between_50_and_500_metres(manager, office):
    service = OfficeService()
    with pytest.raises(ValidationFailed):
        service.update(manager, office.id, latitude=OFFICE_LAT,
                       longitude=OFFICE_LON, geofence_radius_m=30)
    with pytest.raises(ValidationFailed):
        service.update(manager, office.id, latitude=OFFICE_LAT,
                       longitude=OFFICE_LON, geofence_radius_m=900)

    saved = service.update(manager, office.id, latitude=OFFICE_LAT,
                           longitude=OFFICE_LON, geofence_radius_m=100)
    assert saved.geofence_radius_m == 100


def test_latitude_without_longitude_is_not_a_place(manager, office):
    with pytest.raises(ValidationFailed):
        OfficeService().update(manager, office.id, latitude=OFFICE_LAT)


def test_the_location_cannot_be_cleared(manager, located_office):
    """Офис без места не остаётся: снять геозону нельзя, только заменить."""
    with pytest.raises(ValidationFailed):
        OfficeService().update(
            manager, located_office.id, latitude=None, longitude=None,
            geofence_radius_m=None,
        )
    located_office.refresh_from_db()
    assert located_office.latitude == OFFICE_LAT
    assert located_office.geofence_radius_m == 100
    assert located_office.pending_location is None


def test_an_office_is_not_created_without_a_location(manager, region):
    with pytest.raises(ValidationFailed):
        OfficeService().create(manager, region_id=region.id, name="Без места")
    # Полкруга — тоже не место: точка без радиуса.
    with pytest.raises(ValidationFailed):
        OfficeService().create(
            manager, region_id=region.id, name="Без радиуса",
            latitude=OFFICE_LAT, longitude=OFFICE_LON,
        )


def test_a_new_location_waits_and_the_old_one_keeps_working(
    context, sticker, manager, located_office
):
    """Пока новое место не подтвердили, отметки сверяются с прежним."""
    point, code = sticker("ENTRY")
    far_lat, far_lon = north(3000)

    proposed = OfficeService().update(
        manager, located_office.id, latitude=far_lat, longitude=far_lon,
        geofence_radius_m=100,
    )

    # Действует прежнее место, новое лежит отдельно.
    assert proposed.latitude == OFFICE_LAT
    assert proposed.pending_location["latitude"] == str(far_lat)
    assert proposed.pending_location_by_user_id == manager.user_id
    # И отметка у прежнего входа по-прежнему принимается.
    assert mark(context, code).status == ScanStatus.ENTERED


def test_confirming_switches_the_location(manager, located_office):
    far_lat, far_lon = north(3000)
    OfficeService().update(
        manager, located_office.id, latitude=far_lat, longitude=far_lon,
        geofence_radius_m=200,
    )

    confirmed = OfficeService().confirm_location(manager, located_office.id)

    assert confirmed.latitude == far_lat
    assert confirmed.geofence_radius_m == 200
    assert confirmed.pending_location is None
    actions = list(
        AuditLog.objects.filter(entity_id=located_office.id)
        .values_list("action", flat=True)
    )
    assert "office.location.confirm" in actions


def test_discarding_keeps_the_old_location(manager, located_office):
    far_lat, far_lon = north(3000)
    OfficeService().update(
        manager, located_office.id, latitude=far_lat, longitude=far_lon,
        geofence_radius_m=200,
    )

    kept = OfficeService().discard_location(manager, located_office.id)

    assert kept.latitude == OFFICE_LAT
    assert kept.geofence_radius_m == 100
    assert kept.pending_location is None


def test_nothing_to_confirm_is_a_conflict(manager, located_office):
    from humotech.core.errors import Conflict

    with pytest.raises(Conflict):
        OfficeService().confirm_location(manager, located_office.id)


def test_an_office_from_before_without_a_location_gets_one_at_once(manager, office):
    """Старому офису без места беречь нечего: место ставится сразу."""
    assert office.latitude is None

    placed = OfficeService().update(
        manager, office.id, latitude=OFFICE_LAT, longitude=OFFICE_LON,
        geofence_radius_m=100,
    )

    assert placed.latitude == OFFICE_LAT
    assert placed.pending_location is None


def test_the_sticker_secret_is_still_shown_only_once(manager, located_office,
                                                      telegram_settings):
    issued = QrPointService().create(
        manager, office_id=located_office.id, name="Вход", qr_mode="STATIC",
        direction_mode="ENTRY",
    )
    point = OfficeQrPoint.objects.get(id=issued.point.id)

    assert issued.static_token not in (point.static_token_hash or "")
    assert point.created_by_user_id == manager.user_id


# --- сквозной сценарий ----------------------------------------------------------

def test_office_setup_scenario_end_to_end(
    api_client, bot_client, make_user, organization, region, telegram_settings
):
    """Создать офис → поставить точку → 100 м → QR входа → отметиться."""
    hr = make_user(
        organization,
        permissions=("regions.read", "offices.read", "offices.manage",
                     "qr_points.read", "qr_points.manage"),
    )
    api_client.force_authenticate(user=hr)

    # 1–3. Новый офис сразу с точкой на карте и радиусом 100 м: без места
    # офис не заводится.
    created = api_client.post(f"{API}/offices/", {
        "region_id": str(region.id), "code": "TASHKENT_CITY",
        "name": "Ташкент Сити", "address": "ул. Амира Темура, 107",
        "timezone": "Asia/Tashkent",
        "latitude": str(OFFICE_LAT), "longitude": str(OFFICE_LON),
        "geofence_radius_m": 100,
    }, format="json")
    assert created.status_code == 201, created.content
    assert created.json()["geofence_radius_m"] == 100
    office_id = created.json()["id"]

    # Перенос точки — в два шага: сначала предложение, место прежнее.
    moved_lat, moved_lon = north(10)
    proposed = api_client.patch(f"{API}/offices/{office_id}/", {
        "latitude": str(moved_lat), "longitude": str(moved_lon),
        "geofence_radius_m": 100,
    }, format="json")
    assert proposed.status_code == 200, proposed.content
    assert proposed.json()["latitude"] == str(OFFICE_LAT)
    assert proposed.json()["pending_location"]["latitude"] == str(moved_lat)
    # Подтверждение — и новое место начинает действовать.
    confirmed = api_client.post(f"{API}/offices/{office_id}/location/confirm/")
    assert confirmed.status_code == 200, confirmed.content
    assert confirmed.json()["latitude"] == str(moved_lat)
    assert confirmed.json()["pending_location"] is None

    # 4. QR-точка входа.
    point = api_client.post(f"{API}/qr-points/", {
        "office_id": office_id, "name": "Главный вход",
        "direction_mode": "ENTRY", "qr_mode": "STATIC",
        "description": "Слева от ресепшен",
    }, format="json")
    assert point.status_code == 201, point.content
    body = point.json()
    sticker_link = body["sticker_link"]
    assert sticker_link.startswith("https://t.me/humotech_test_bot?start=qr_")
    assert body["point"]["code"].startswith("QR-")

    # Сотрудник этого офиса с подтверждённой привязкой Telegram.
    person = Employee.objects.create(
        organization=organization, employee_number="EMP-SETUP-1",
        first_name="Алишер", last_name="Каримов", hire_date=date(2025, 1, 10),
        employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=person, office_id=office_id,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2025, 1, 10),
    )
    link_telegram(person, telegram_user_id=TG_ID)

    # 5. Бот присылает отсканированную ссылку и геопозицию у входа.
    lat, lon = NEAR
    scanned = bot_client.post(f"{API}/me/attendance/scan", {
        "token": sticker_link, "client_event_id": "tg-1-100",
        "latitude": str(lat), "longitude": str(lon), "accuracy_m": "12",
    }, format="json", **bot_headers(TG_ID))

    assert scanned.status_code == 200, scanned.content
    result = scanned.json()
    assert result["status"] == "ENTERED"
    assert result["office_name"] == "Ташкент Сити"
    assert result["point_name"] == "Главный вход"
    assert result["point_mode"] == "ENTRY"
    assert result["geofence_kind"] == "CIRCLE"
    assert len(result["occurred_at_local"]) == 5

    # В карточке точки — автор и сегодняшний скан.
    listed = api_client.get(f"{API}/qr-points/", {"office_id": office_id}).json()
    card = listed["items"][0]
    assert card["scans_today"] == 1
    assert card["created_by_name"] == hr.email
    assert card["description"] == "Слева от ресепшен"
    assert "static_token" not in card

    # 6. Попытка из-за пределов радиуса. Чисел в отказе нет: координаты
    # присылает клиент, и точное расстояние подсказывало бы, какую точку
    # подставить. Расстояние остаётся в записи отметки.
    far_lat, far_lon = FAR
    far = bot_client.post(f"{API}/me/attendance/scan", {
        "token": sticker_link, "client_event_id": "tg-1-101",
        "latitude": str(far_lat), "longitude": str(far_lon), "accuracy_m": "12",
    }, format="json", **bot_headers(TG_ID))

    assert far.status_code == 200, far.content
    refused = far.json()
    assert refused["status"] == "OUTSIDE_GEOFENCE"
    assert refused["accepted"] is False
    assert "distance_m" not in refused and "radius_m" not in refused
    assert AttendanceEvent.objects.get(client_event_id="tg-1-101").distance_m > 100

    # 7. Переименовать точку и убедиться, что удалить её уже нельзя:
    # по ней отмечались, и это часть истории.
    point_id = card["id"]
    renamed = api_client.patch(f"{API}/qr-points/{point_id}/",
                               {"name": "Вход с улицы"}, format="json")
    assert renamed.status_code == 200, renamed.content
    assert renamed.json()["name"] == "Вход с улицы"

    refused_delete = api_client.delete(f"{API}/qr-points/{point_id}/")
    assert refused_delete.status_code == 409, refused_delete.content

    # А точка без единой отметки убирается совсем — опечатку в
    # справочнике надо уметь стереть.
    spare = api_client.post(f"{API}/qr-points/", {
        "office_id": office_id, "name": "Опечатка",
        "direction_mode": "EXIT", "qr_mode": "STATIC",
    }, format="json")
    assert spare.status_code == 201, spare.content
    gone = api_client.delete(f"{API}/qr-points/{spare.json()['point']['id']}/")
    assert gone.status_code == 204, gone.content


# --- признаки для HR: отметку не отменяют, а подсвечивают --------------------

def test_a_normal_mark_carries_no_anomalies(context, sticker):
    point, code = sticker("ENTRY")

    assert mark(context, code).status == ScanStatus.ENTERED

    event = AttendanceEvent.objects.get(qr_point=point)
    assert "anomalies" not in (event.event_metadata or {})


def test_a_too_perfect_accuracy_is_flagged_but_accepted(context, sticker):
    """«1 м» телефон почти не отвечает — частый почерк подмены геолокации."""
    point, code = sticker("ENTRY")

    assert mark(context, code, accuracy="1").status == ScanStatus.ENTERED

    event = AttendanceEvent.objects.get(qr_point=point)
    assert event.verification_status == "ACCEPTED"
    assert event.event_metadata["anomalies"] == ["PERFECT_ACCURACY"]


def test_a_code_accepted_for_someone_else_from_another_address_is_flagged(
    context, sticker
):
    import uuid as _uuid

    from humotech.attendance.services import _anomalies

    point, code = sticker("ENTRY")
    mark(context, code)
    AttendanceEvent.objects.filter(qr_point=point).update(
        qr_nonce_hash="nonce-1", ip_address="10.0.0.1",
    )
    common = {"latitude": NEAR[0], "longitude": NEAR[1], "accuracy_m": "12"}
    stranger = _uuid.uuid4()

    assert _anomalies(employee_id=stranger, qr_nonce_hash="nonce-1",
                      ip_address="10.0.0.2", **common) == ("SHARED_CODE",)
    # Тот же адрес — коллеги у одной двери в одной сети: это норма.
    assert _anomalies(employee_id=stranger, qr_nonce_hash="nonce-1",
                      ip_address="10.0.0.1", **common) == ()
    # Свой же код — не «чужой».
    own = AttendanceEvent.objects.get(qr_point=point).employee_id
    assert _anomalies(employee_id=own, qr_nonce_hash="nonce-1",
                      ip_address="10.0.0.2", **common) == ()


def test_no_location_is_flagged_unless_the_office_network_confirmed_it():
    import uuid as _uuid

    from humotech.attendance.services import _anomalies

    bare = {"employee_id": _uuid.uuid4(), "qr_nonce_hash": None,
            "ip_address": None, "latitude": None, "longitude": None,
            "accuracy_m": None}
    assert _anomalies(**bare) == ("NO_LOCATION",)
    assert _anomalies(**bare, inside_office_network=True) == ()


def test_the_journal_shows_anomalies_to_hr(context, sticker):
    from humotech.attendance.serializers import AttendanceEventSerializer

    point, code = sticker("ENTRY")
    mark(context, code, accuracy="0.5")
    event = AttendanceEvent.objects.select_related("office", "qr_point", "employee").get(
        qr_point=point
    )

    data = AttendanceEventSerializer(event).data
    assert data["anomalies"] == ["PERFECT_ACCURACY"]
    # Координат и расстояния в журнале по-прежнему нет.
    assert "latitude" not in data and "distance_m" not in data
