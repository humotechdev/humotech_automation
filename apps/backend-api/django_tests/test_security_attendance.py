"""Аудит безопасности зоны посещаемости: атака на каждую точку ввода.

Каждый тест — попытка злоумышленника (сотрудника, кадровика с узкой
областью, постороннего экрана) и проверка того, что сервер её отбил без
500 и без побочной записи. Данные вымышленные, секреты — фиктивные.

Разделы:
  * QR-код экрана: подделка, чужой ключ, повтор, чужая организация/офис;
  * печатный код (наклейка, deep-link);
  * сопряжение экрана и выдача кодов, права на устройства в CRM;
  * геопозиция: мусорные числа, погрешность, потолок точки;
  * время: клиентское `occurred_at` не принимается;
  * ручные отметки и решения по заявкам в CRM: область офиса;
  * фильтры/даты журналов: инъекции, крайние даты, 500.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

import pytest
from django.utils import timezone

from humotech.attendance.models import AttendanceEvent, AttendanceSession
from humotech.qr_codes import stickers
from humotech.qr_codes.models import OfficeQrPoint, QrDisplayDevice
from humotech.qr_codes.services import QrDisplayService
from humotech.qr_codes.tokens import _LAYOUT, DOMAIN, PREFIX, issue

from .conftest import (
    bot_headers,
    create_actor,
    link_telegram,
    make_office,
    make_qr_point,
)

pytestmark = pytest.mark.django_db

API = "/api/v1"
SCAN = f"{API}/me/attendance/scan"
PAIR = f"{API}/qr-display/pair"
CODE = f"{API}/qr-display/code"
TG_ID = 777_000_111
TG_ID_2 = 777_000_222

OFFICE_LAT, OFFICE_LON = Decimal("38.559772"), Decimal("68.787038")
NEAR = {"latitude": "38.560200", "longitude": "68.787400", "accuracy_m": "15"}


# --------------------------------------------------------------- фикстуры


@pytest.fixture()
def qr_settings(settings):
    settings.QR = {
        **settings.QR,
        "SIGNING_SECRET": "test-qr-signing-secret-not-real",
        "TOKEN_TTL_SECONDS": 30,
        "CLOCK_SKEW_SECONDS": 10,
        "DISPLAY_CREDENTIAL_TTL_SECONDS": 2_592_000,
        "DISPLAY_PAIRING_TTL_SECONDS": 3600,
        "MAX_LOCATION_ACCURACY_M": 100,
    }
    return settings.QR


@pytest.fixture()
def employee_client(bot_client, employee, telegram_settings, qr_settings):
    link_telegram(employee, telegram_user_id=TG_ID)
    return bot_client


def make_token(point, *, moment=None, ttl=30, **override):
    fields = dict(
        organization_id=point.organization_id,
        office_id=point.office_id,
        qr_point_id=point.id,
        direction_mode=point.direction_mode,
        issued_at=moment or timezone.now(),
        ttl_seconds=ttl,
    )
    fields.update(override)
    token, _ = issue(**fields)
    return token


def scan(client, token, tg=TG_ID, **extra):
    return client.post(SCAN, {"token": token, **extra}, format="json",
                       **bot_headers(tg))


def second_employee(organization, office, number="EMP-0002"):
    from humotech.employees.models import Employee, EmployeeAssignment

    emp = Employee.objects.create(
        organization=organization, employee_number=number,
        first_name="Пётр", last_name="Петров",
        hire_date=date(2024, 2, 1), employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=emp, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE", is_primary=True,
        valid_from=date(2024, 2, 1),
    )
    return emp


@pytest.fixture()
def located_office(office):
    office.latitude = OFFICE_LAT
    office.longitude = OFFICE_LON
    office.geofence_radius_m = 100
    office.save(update_fields=["latitude", "longitude", "geofence_radius_m"])
    return office


# ========================================================= QR-код экрана


class TestSignedCode:
    def test_flipped_byte_is_rejected(self, employee_client, qr_point):
        token = make_token(qr_point)
        raw = bytearray(base64.urlsafe_b64decode(token[3:] + "=" * (-len(token[3:]) % 4)))
        raw[40] ^= 0x01  # меняем байт идентификатора точки
        forged = PREFIX + base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")

        response = scan(employee_client, forged)

        assert response.status_code == 200
        assert response.json()["status"] == "QR_INVALID"
        assert AttendanceEvent.objects.count() == 0

    def test_code_signed_with_a_guessed_key_is_rejected(
        self, employee_client, qr_point
    ):
        """Злоумышленник знает формат, но не ключ."""
        now = int(timezone.now().timestamp())
        body = _LAYOUT.pack(
            1, qr_point.organization_id.bytes, qr_point.office_id.bytes,
            qr_point.id.bytes, 2, now, 30, b"\x00" * 8,
        )
        sig = hmac.new(b"guessed-key", DOMAIN + body, hashlib.sha256).digest()[:16]
        forged = PREFIX + base64.urlsafe_b64encode(body + sig).decode().rstrip("=")

        assert scan(employee_client, forged).json()["status"] == "QR_INVALID"
        assert AttendanceEvent.objects.count() == 0

    def test_empty_signing_secret_never_accepts(
        self, employee_client, qr_point, settings
    ):
        token = make_token(qr_point)
        settings.QR = {**settings.QR, "SIGNING_SECRET": ""}
        response = scan(employee_client, token)
        assert response.status_code == 200
        assert response.json()["accepted"] is False

    @pytest.mark.parametrize("junk", [
        "HT1", "HT1" + "A" * 400, "HT1!!!!", "HT1" + "‮" * 50,
        "HT1\x00abc", "{\"a\":1}", "' OR 1=1 --",
    ])
    def test_garbage_is_invalid_not_500(self, employee_client, junk):
        response = scan(employee_client, junk)
        assert response.status_code in (200, 400)
        if response.status_code == 200:
            assert response.json()["accepted"] is False
        assert AttendanceEvent.objects.count() == 0

    def test_token_not_a_string(self, employee_client):
        for body in ({"token": ["a", "b"]}, {"token": {"x": 1}}, {"token": None}):
            response = employee_client.post(SCAN, body, format="json",
                                            **bot_headers(TG_ID))
            assert response.status_code == 400

    def test_replay_after_expiry_is_rejected(self, employee_client, qr_point):
        stale = make_token(qr_point, moment=timezone.now() - timedelta(seconds=35))
        response = scan(employee_client, stale)
        assert response.json()["status"] == "QR_EXPIRED"
        assert AttendanceSession.objects.count() == 0

    def test_code_from_the_future_is_rejected(self, employee_client, qr_point):
        future = make_token(qr_point, moment=timezone.now() + timedelta(minutes=5))
        assert scan(employee_client, future).json()["status"] == "QR_EXPIRED"

    def test_same_code_twice_by_same_employee(self, employee_client, qr_point):
        token = make_token(qr_point)
        assert scan(employee_client, token).json()["status"] == "ENTERED"
        second = scan(employee_client, token).json()
        assert second["status"] == "QR_ALREADY_USED"
        assert AttendanceSession.objects.get().status == "OPEN"

    def test_same_code_for_another_employee_is_accepted_by_design(
        self, employee_client, qr_point, organization, office
    ):
        """Один экран — очередь людей у двери: код общий на окно в 30 с.

        Это осознанная граница: пересланный коллеге код в пределах окна
        сработает. Смягчается сроком, журналом и геозоной/сетью точки.
        """
        other = second_employee(organization, office)
        link_telegram(other, telegram_user_id=TG_ID_2)
        token = make_token(qr_point)
        assert scan(employee_client, token).json()["status"] == "ENTERED"
        assert scan(employee_client, token, tg=TG_ID_2).json()["status"] == "ENTERED"

    def test_code_of_other_organization(
        self, employee_client, other_organization, foreign_office
    ):
        foreign_point = make_qr_point(other_organization, foreign_office,
                                      code="FOREIGN_DOOR")
        response = scan(employee_client, make_token(foreign_point))
        assert response.json()["status"] == "OFFICE_NOT_ALLOWED"
        assert AttendanceSession.objects.count() == 0

    def test_code_of_office_without_access(
        self, employee_client, other_qr_point
    ):
        response = scan(employee_client, make_token(other_qr_point))
        assert response.json()["status"] == "OFFICE_NOT_ALLOWED"
        assert AttendanceSession.objects.count() == 0

    def test_code_after_point_moved_to_other_office(
        self, employee_client, qr_point, other_office
    ):
        token = make_token(qr_point)
        OfficeQrPoint.objects.filter(id=qr_point.id).update(office=other_office)
        assert scan(employee_client, token).json()["status"] == "QR_INVALID"

    def test_code_of_disabled_point(self, employee_client, qr_point):
        token = make_token(qr_point)
        OfficeQrPoint.objects.filter(id=qr_point.id).update(is_active=False)
        assert scan(employee_client, token).json()["status"] == "QR_POINT_INACTIVE"

    def test_code_forged_with_other_direction(self, employee_client, qr_point):
        """Подписанный код со «своим» направлением, отличным от точки."""
        token = make_token(qr_point, direction_mode="EXIT")
        assert scan(employee_client, token).json()["status"] == "QR_INVALID"

    def test_client_event_id_replay_does_not_duplicate(
        self, employee_client, qr_point
    ):
        token = make_token(qr_point)
        first = scan(employee_client, token, client_event_id="abc-1").json()
        again = scan(employee_client, make_token(qr_point),
                     client_event_id="abc-1").json()
        assert first["status"] == again["status"] == "ENTERED"
        assert AttendanceEvent.objects.count() == 1


# ======================================================== время клиента


class TestClientTime:
    def test_occurred_at_and_direction_from_body_are_ignored(
        self, employee_client, qr_point, office, other_office, employee
    ):
        yesterday = (timezone.now() - timedelta(days=1)).isoformat()
        response = scan(
            employee_client, make_token(qr_point),
            occurred_at=yesterday, event_type="EXIT",
            office_id=str(other_office.id), employee_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()), verification_status="ACCEPTED",
        )
        assert response.status_code == 200
        event = AttendanceEvent.objects.get()
        assert abs((event.occurred_at - timezone.now()).total_seconds()) < 60
        assert event.event_type == "ENTRY"
        assert event.office_id == office.id
        assert event.employee_id == employee.id


# ============================================================ наклейка


@pytest.fixture()
def sticker_point(organization, located_office):
    secret = "S" * 43
    point = OfficeQrPoint.objects.create(
        organization=organization, office=located_office, code="STICKER",
        name="Наклейка", direction_mode="BOTH", qr_mode="STATIC",
        static_token_hash=stickers.token_hash(secret),
    )
    return point, secret


class TestSticker:
    def test_sticker_without_coordinates_is_refused(
        self, employee_client, sticker_point
    ):
        _, secret = sticker_point
        response = scan(employee_client, f"https://t.me/humotech_test_bot?start=qr_{secret}")
        assert response.json()["status"] == "GEOLOCATION_REQUIRED"
        assert AttendanceSession.objects.count() == 0

    def test_sticker_far_away_is_refused(self, employee_client, sticker_point):
        _, secret = sticker_point
        response = scan(employee_client, f"qr_{secret}",
                        latitude="38.600000", longitude="68.850000", accuracy_m="10")
        assert response.json()["status"] == "OUTSIDE_GEOFENCE"

    def test_sticker_near_is_accepted(self, employee_client, sticker_point):
        _, secret = sticker_point
        response = scan(employee_client, f"qr_{secret}", **NEAR)
        assert response.json()["status"] == "ENTERED"

    def test_sticker_on_foreign_host_is_not_ours(
        self, employee_client, sticker_point
    ):
        _, secret = sticker_point
        response = scan(employee_client,
                        f"https://evil.example/?start=qr_{secret}", **NEAR)
        assert response.json()["accepted"] is False
        assert AttendanceSession.objects.count() == 0

    def test_sticker_of_other_organization(
        self, employee_client, other_organization, foreign_office
    ):
        secret = "F" * 43
        OfficeQrPoint.objects.create(
            organization=other_organization, office=foreign_office,
            code="F_STICKER", name="Чужая", direction_mode="BOTH",
            qr_mode="STATIC", static_token_hash=stickers.token_hash(secret),
        )
        response = scan(employee_client, f"qr_{secret}", **NEAR)
        assert response.json()["status"] == "OFFICE_NOT_ALLOWED"

    def test_unknown_sticker_is_revoked(self, employee_client, sticker_point):
        response = scan(employee_client, "qr_" + "Z" * 43, **NEAR)
        assert response.json()["status"] == "QR_REVOKED"


# ============================================================ геопозиция


class TestLocationInput:
    @pytest.mark.parametrize("lat,lon", [
        ("NaN", "68.787400"), ("Infinity", "68.787400"), ("-Infinity", "0"),
        ("1e309", "0"), ("abc", "0"), ("900", "0"), ("0", "181"),
        ("38.5602001234567", "68.7874"), ("9" * 5000, "0"),
        (["38.5"], "68.7"), ({"a": 1}, "68.7"),
    ])
    def test_bad_coordinates_are_400(self, employee_client, qr_point, lat, lon):
        response = scan(employee_client, make_token(qr_point),
                        latitude=lat, longitude=lon)
        assert response.status_code == 400
        assert AttendanceEvent.objects.count() == 0

    @pytest.mark.parametrize("accuracy", ["NaN", "Infinity", "-1", "1e10",
                                          "999999999", "abc"])
    def test_bad_accuracy_is_400(self, employee_client, qr_point, accuracy):
        response = scan(employee_client, make_token(qr_point),
                        latitude=NEAR["latitude"], longitude=NEAR["longitude"],
                        accuracy_m=accuracy)
        assert response.status_code == 400
        assert AttendanceEvent.objects.count() == 0

    def test_only_one_coordinate_is_400(self, employee_client, qr_point):
        response = scan(employee_client, make_token(qr_point), latitude="38.5")
        assert response.status_code == 400

    def test_huge_accuracy_cannot_stretch_geofence(
        self, employee_client, qr_point, located_office
    ):
        """«Погрешность 50 км» не растягивает радиус до города."""
        response = scan(employee_client, make_token(qr_point),
                        latitude="38.600000", longitude="68.850000",
                        accuracy_m="50000")
        assert response.status_code == 200
        assert response.json()["status"] == "LOCATION_TOO_VAGUE"
        assert AttendanceSession.objects.count() == 0

    def test_zero_accuracy_is_rejected(
        self, employee_client, qr_point, located_office
    ):
        response = scan(employee_client, make_token(qr_point),
                        **{**NEAR, "accuracy_m": "0"})
        assert response.json()["accepted"] is False

    def test_point_accuracy_ceiling_is_enforced(
        self, employee_client, qr_point, located_office
    ):
        """Точка с потолком 20 м не принимает погрешность 90 м.

        Иначе настройка в карточке точки — видимость: HR думает, что
        ужесточил проверку, а сервер сравнивает с общим потолком 100 м.
        """
        qr_point.allowed_location_accuracy_m = 20
        qr_point.save(update_fields=["allowed_location_accuracy_m"])
        response = scan(employee_client, make_token(qr_point),
                        latitude="38.560200", longitude="68.787400",
                        accuracy_m="90")
        assert response.json()["status"] == "LOCATION_TOO_VAGUE"
        assert AttendanceSession.objects.count() == 0

    def test_required_geolocation_missing(self, employee_client, qr_point):
        qr_point.require_geolocation = True
        qr_point.save(update_fields=["require_geolocation"])
        assert scan(employee_client, make_token(qr_point)).json()["status"] == (
            "GEOLOCATION_REQUIRED"
        )


class TestOfficeNetwork:
    def test_forged_forwarded_for_does_not_pass_network_check(
        self, employee_client, qr_point, office, settings
    ):
        """XFF не читается без настроенного прокси: подмена не проходит."""
        from humotech.offices.models import OfficeNetwork

        settings.TRUSTED_PROXY_COUNT = 0
        OfficeNetwork.objects.create(
            organization=office.organization, office=office,
            network_cidr="10.10.0.0/16", is_active=True, name="LAN",
        )
        qr_point.require_office_network = True
        qr_point.save(update_fields=["require_office_network"])

        response = employee_client.post(
            SCAN, {"token": make_token(qr_point)}, format="json",
            HTTP_X_FORWARDED_FOR="10.10.1.5", REMOTE_ADDR="203.0.113.7",
            **bot_headers(TG_ID),
        )
        assert response.json()["status"] == "NETWORK_REQUIRED"
        event = AttendanceEvent.objects.get()
        assert str(event.ip_address) == "203.0.113.7"


# ========================================== сопряжение экрана и устройства


class TestDisplayPairing:
    def test_wrong_and_reused_codes(self, api_client, qr_point, qr_settings):
        issued = QrDisplayService().create_device_for_point(qr_point, name="T")
        assert api_client.post(PAIR, {"pairing_code": "nope"},
                               format="json").status_code == 403
        assert api_client.post(PAIR, {"pairing_code": issued.pairing_code},
                               format="json").status_code == 201
        assert api_client.post(PAIR, {"pairing_code": issued.pairing_code},
                               format="json").status_code == 403

    def test_expired_pairing_code(self, api_client, qr_point, qr_settings):
        issued = QrDisplayService().create_device_for_point(qr_point, name="T")
        QrDisplayDevice.objects.filter(id=issued.device.id).update(
            pairing_expires_at=timezone.now() - timedelta(seconds=1)
        )
        assert api_client.post(PAIR, {"pairing_code": issued.pairing_code},
                               format="json").status_code == 403

    @pytest.mark.parametrize("body", [
        {}, {"pairing_code": ""}, {"pairing_code": "x" * 5000},
        {"pairing_code": ["a"]}, {"pairing_code": {"$ne": ""}},
    ])
    def test_pairing_garbage_is_4xx(self, api_client, body, qr_settings):
        response = api_client.post(PAIR, body, format="json")
        assert 400 <= response.status_code < 500

    def test_pairing_is_throttled(self, api_client, qr_settings):
        codes = [
            api_client.post(PAIR, {"pairing_code": f"guess-{n}"},
                            format="json").status_code
            for n in range(15)
        ]
        assert 429 in codes

    @pytest.mark.parametrize("header", [
        None, "Bearer", "Bearer ", "Basic abc", "Bearer a b",
        "Bearer " + "x" * 5000,
    ])
    def test_code_without_valid_credential(self, api_client, header, qr_settings):
        extra = {"HTTP_AUTHORIZATION": header} if header is not None else {}
        assert api_client.get(CODE, **extra).status_code == 403

    def test_expired_credential(self, api_client, qr_point, qr_settings):
        service = QrDisplayService()
        paired = service.pair(
            service.create_device_for_point(qr_point, name="T").pairing_code
        )
        QrDisplayDevice.objects.filter(id=paired.device.id).update(
            credential_expires_at=timezone.now() - timedelta(seconds=1)
        )
        assert api_client.get(
            CODE, HTTP_AUTHORIZATION=f"Bearer {paired.credential}"
        ).status_code == 403


@pytest.fixture()
def office_admin_client(api_client, organization, office):
    """Администратор ОДНОГО офиса с правом управлять точками."""
    user, _ = create_actor(
        organization, office=office,
        permissions=("qr_points.read", "qr_points.manage"),
    )
    api_client.force_authenticate(user=user)
    return api_client


class TestDeviceScope:
    def test_office_admin_cannot_create_display_for_other_office(
        self, office_admin_client, other_qr_point, qr_settings
    ):
        response = office_admin_client.post(
            f"{API}/qr/devices",
            {"qr_point_id": str(other_qr_point.id), "name": "Чужой"},
            format="json",
        )
        assert response.status_code in (403, 404)
        assert QrDisplayDevice.objects.count() == 0

    def test_office_admin_cannot_reissue_or_revoke_other_office_display(
        self, office_admin_client, other_qr_point, qr_settings
    ):
        device = QrDisplayService().create_device_for_point(
            other_qr_point, name="Чужой"
        ).device
        for verb in ("reissue", "revoke"):
            response = office_admin_client.post(
                f"{API}/qr/devices/{device.id}/{verb}", format="json"
            )
            assert response.status_code in (403, 404), verb
        device.refresh_from_db()
        assert device.status == "PENDING"

    def test_office_admin_lists_only_own_office_displays(
        self, office_admin_client, qr_point, other_qr_point, qr_settings
    ):
        service = QrDisplayService()
        service.create_device_for_point(qr_point, name="Свой")
        service.create_device_for_point(other_qr_point, name="Чужой")
        response = office_admin_client.get(f"{API}/qr/devices")
        assert response.status_code == 200
        assert [row["name"] for row in response.json()] == ["Свой"]

    def test_foreign_org_device_is_not_found(
        self, office_admin_client, other_organization, foreign_office, qr_settings
    ):
        point = make_qr_point(other_organization, foreign_office, code="FX")
        device = QrDisplayService().create_device_for_point(point, name="X").device
        response = office_admin_client.post(
            f"{API}/qr/devices/{device.id}/reissue", format="json"
        )
        assert response.status_code == 404

    def test_unknown_action_does_not_reissue(
        self, office_admin_client, qr_point, qr_settings
    ):
        service = QrDisplayService()
        paired = service.pair(
            service.create_device_for_point(qr_point, name="T").pairing_code
        )
        response = office_admin_client.post(
            f"{API}/qr/devices/{paired.device.id}/anything", format="json"
        )
        assert response.status_code in (400, 404)
        paired.device.refresh_from_db()
        assert paired.device.status == "ACTIVE"

    def test_list_with_bad_point_id_is_400(self, office_admin_client, qr_settings):
        response = office_admin_client.get(f"{API}/qr/devices",
                                           {"qr_point_id": "not-a-uuid"})
        assert response.status_code == 400

    def test_viewer_cannot_create_display(
        self, api_client, organization, qr_point, qr_settings
    ):
        user, _ = create_actor(organization, permissions=("qr_points.read",))
        api_client.force_authenticate(user=user)
        response = api_client.post(
            f"{API}/qr/devices",
            {"qr_point_id": str(qr_point.id), "name": "X"}, format="json",
        )
        assert response.status_code == 403

    def test_huge_accuracy_ceiling_on_point_is_400(
        self, office_admin_client, qr_point
    ):
        response = office_admin_client.patch(
            f"{API}/qr-points/{qr_point.id}/",
            {"allowed_location_accuracy_m": 10 ** 12}, format="json",
        )
        assert response.status_code == 400

    def test_qr_points_search_with_nul_is_400(self, office_admin_client, qr_point):
        response = office_admin_client.get(f"{API}/qr-points/", {"search": "a\x00b"})
        assert response.status_code == 400

    def test_bad_office_timezone_does_not_break_point_list(
        self, office_admin_client, qr_point, office
    ):
        type(office).objects.filter(id=office.id).update(timezone="Not/AZone")
        response = office_admin_client.get(f"{API}/qr-points/")
        assert response.status_code == 200


# ================================================ ручные отметки в CRM


@pytest.fixture()
def hr_client(api_client, organization):
    user, _ = create_actor(
        organization,
        permissions=("attendance.read", "attendance.correct", "attendance.manual"),
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture()
def narrow_hr_client(api_client, organization, office):
    user, _ = create_actor(
        organization, office=office,
        permissions=("attendance.read", "attendance.correct", "attendance.manual"),
    )
    api_client.force_authenticate(user=user)
    return api_client


def manual(client, **fields):
    body = {
        "event_type": "ENTRY",
        "occurred_at": (timezone.now() - timedelta(hours=1)).isoformat(),
        "reason": "Забыл отметиться",
        **fields,
    }
    return client.post(f"{API}/attendance/manual", body, format="json")


class TestManualEvent:
    def test_other_office_is_forbidden_for_narrow_hr(
        self, narrow_hr_client, employee, other_office
    ):
        response = manual(narrow_hr_client, employee_id=str(employee.id),
                          office_id=str(other_office.id))
        assert response.status_code in (403, 404)
        assert AttendanceEvent.objects.count() == 0

    def test_employee_of_other_office_is_not_found(
        self, narrow_hr_client, organization, office, other_office
    ):
        stranger = second_employee(organization, other_office)
        response = manual(narrow_hr_client, employee_id=str(stranger.id),
                          office_id=str(office.id))
        assert response.status_code == 404
        assert AttendanceEvent.objects.count() == 0

    def test_foreign_organization_ids(
        self, hr_client, other_organization, foreign_office, employee
    ):
        response = manual(hr_client, employee_id=str(employee.id),
                          office_id=str(foreign_office.id))
        assert response.status_code == 404
        assert AttendanceEvent.objects.count() == 0

    def test_organization_and_source_in_body_are_ignored(
        self, hr_client, employee, office, organization, other_organization
    ):
        response = manual(
            hr_client, employee_id=str(employee.id), office_id=str(office.id),
            organization_id=str(other_organization.id), source="QR",
            verification_status="REJECTED",
        )
        assert response.status_code == 201
        event = AttendanceEvent.objects.get()
        assert event.organization_id == organization.id
        assert event.source == "MANUAL"
        assert event.verification_status == "ACCEPTED"

    def test_no_permission(self, api_client, organization, employee, office):
        user, _ = create_actor(organization, permissions=("attendance.read",))
        api_client.force_authenticate(user=user)
        response = manual(api_client, employee_id=str(employee.id),
                          office_id=str(office.id))
        assert response.status_code == 403

    @pytest.mark.parametrize("moment", [
        "9999-12-31T23:59:59-12:00", "0001-01-01T00:00:00+14:00",
        "not-a-date", "2026-02-30T10:00:00Z",
    ])
    def test_extreme_or_bad_occurred_at_is_400(
        self, hr_client, employee, office, moment
    ):
        response = manual(hr_client, employee_id=str(employee.id),
                          office_id=str(office.id), occurred_at=moment)
        assert response.status_code == 400
        assert AttendanceEvent.objects.count() == 0

    def test_far_future_manual_mark_is_400(self, hr_client, employee, office):
        response = manual(
            hr_client, employee_id=str(employee.id), office_id=str(office.id),
            occurred_at=(timezone.now() + timedelta(days=30)).isoformat(),
        )
        assert response.status_code == 400
        assert AttendanceEvent.objects.count() == 0

    def test_huge_or_nul_reason(self, hr_client, employee, office):
        for reason in ("x" * 5000, "abc\x00def"):
            response = manual(hr_client, employee_id=str(employee.id),
                              office_id=str(office.id), reason=reason)
            assert response.status_code == 400
        assert AttendanceEvent.objects.count() == 0

    def test_html_reason_is_stored_as_text(self, hr_client, employee, office):
        payload = "<img src=x onerror=alert(1)>"
        response = manual(hr_client, employee_id=str(employee.id),
                          office_id=str(office.id), reason=payload)
        assert response.status_code == 201
        assert AttendanceEvent.objects.get().event_metadata["reason"] == payload


class TestCorrectionDecision:
    def test_unknown_decision_is_400(self, hr_client):
        response = hr_client.post(
            f"{API}/attendance/corrections/{uuid.uuid4()}/delete", {}, format="json"
        )
        assert response.status_code == 400

    def test_foreign_correction_is_404(
        self, hr_client, other_organization, foreign_office
    ):
        from humotech.attendance.models import AttendanceCorrectionRequest

        stranger = second_employee(other_organization, foreign_office, "F-1")
        request = AttendanceCorrectionRequest.objects.create(
            organization=other_organization, employee=stranger,
            requested_entry_at=timezone.now(), reason="x", status="SUBMITTED",
            submitted_at=timezone.now(),
        )
        response = hr_client.post(
            f"{API}/attendance/corrections/{request.id}/approve", {}, format="json"
        )
        assert response.status_code == 404
        request.refresh_from_db()
        assert request.status == "SUBMITTED"

    def test_out_of_scope_correction_is_404(
        self, narrow_hr_client, organization, other_office
    ):
        from humotech.attendance.models import AttendanceCorrectionRequest

        stranger = second_employee(organization, other_office)
        request = AttendanceCorrectionRequest.objects.create(
            organization=organization, employee=stranger,
            requested_entry_at=timezone.now(), reason="x", status="SUBMITTED",
            submitted_at=timezone.now(),
        )
        response = narrow_hr_client.post(
            f"{API}/attendance/corrections/{request.id}/approve", {}, format="json"
        )
        assert response.status_code == 404

    def test_huge_comment_is_400(self, hr_client):
        response = hr_client.post(
            f"{API}/attendance/corrections/{uuid.uuid4()}/reject",
            {"comment": "x" * 20000}, format="json",
        )
        assert response.status_code == 400


# ============================================ фильтры и даты журналов CRM


class TestHrFilters:
    @pytest.mark.parametrize("path", [
        "attendance/events", "attendance/sessions",
    ])
    @pytest.mark.parametrize("params", [
        {"event_type": "' OR 1=1 --"}, {"source": "QR'; DROP TABLE x;--"},
        {"verification_status": "ACCEPTED\x00"}, {"status": "OPEN' OR '1'='1"},
        {"employee_id": "1 OR 1=1"}, {"office_id": "../../etc"},
        {"limit": "abc"}, {"limit": "-1"}, {"limit": "99999999999999999999"},
        {"cursor": "!!!"}, {"cursor": "eyJ4IjoxfQ"},
        {"date_from": "2026-13-01"}, {"date_from": "0001-01-01"},
        {"date_to": "9999-12-31"},
        {"date_from": "0001-01-01", "date_to": "9999-12-31"},
    ])
    def test_hostile_params_never_500(self, hr_client, employee, path, params):
        response = hr_client.get(f"{API}/{path}", params)
        assert response.status_code in (200, 400), response.content[:300]
        if response.status_code == 200:
            assert isinstance(response.json()["items"], list)

    @pytest.mark.parametrize("params", [
        {"date": "0001-01-01"}, {"date": "9999-12-31"}, {"date": "x"},
        {"state": "' OR 1=1"}, {"search": "%' OR 1=1 --"},
        {"search": "\x00"}, {"search": "_" * 5000},
        {"department_id": "zzz"}, {"schedule_id": str(uuid.uuid4())},
    ])
    def test_presence_hostile_params(self, hr_client, employee, params):
        response = hr_client.get(f"{API}/attendance/presence", params)
        assert response.status_code in (200, 400), response.content[:300]

    def test_like_wildcards_do_not_widen_search(
        self, hr_client, employee, organization, office
    ):
        second_employee(organization, office)
        response = hr_client.get(f"{API}/attendance/presence", {"search": "%"})
        assert response.status_code == 200
        assert response.json()["total"] == 0

    @pytest.mark.parametrize("first,last", [
        ("0001-01-01", "0001-01-02"), ("9999-12-30", "9999-12-31"),
        ("2020-01-01", "2030-01-01"),
    ])
    def test_daily_extreme_ranges(self, hr_client, employee, first, last):
        response = hr_client.get(
            f"{API}/attendance/daily",
            {"employee_id": str(employee.id), "date_from": first, "date_to": last},
        )
        assert response.status_code == 400

    def test_corrections_hostile_status(self, hr_client):
        response = hr_client.get(f"{API}/attendance/corrections",
                                 {"status": "x' OR 1=1," * 50})
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_narrow_hr_cannot_widen_to_other_office(
        self, narrow_hr_client, other_office
    ):
        for path in ("attendance/events", "attendance/sessions",
                     "attendance/presence", "attendance/corrections"):
            response = narrow_hr_client.get(f"{API}/{path}",
                                            {"office_id": str(other_office.id)})
            assert response.status_code in (403, 404), path


# ============================================== личный кабинет: /me/*


class TestSelfService:
    @pytest.mark.parametrize("params", [
        {"offset": "abc"}, {"limit": "abc"}, {"offset": "1e9"},
        {"offset": "²"}, {"offset": "--5"}, {"limit": "¹"},
        {"limit": "99999999999999999999999"},
        {"date_from": "0001-01-01", "date_to": "0001-01-02"},
        {"date_from": "9999-12-30", "date_to": "9999-12-31"},
    ])
    def test_history_hostile_params(self, employee_client, params):
        response = employee_client.get(f"{API}/me/history", params,
                                       **bot_headers(TG_ID))
        assert response.status_code in (200, 400), response.content[:300]

    @pytest.mark.parametrize("params", [
        {"date_from": "0001-01-01", "date_to": "0001-01-02"},
        {"date_from": "9999-12-30", "date_to": "9999-12-31"},
        {"period": "' OR 1=1"},
    ])
    def test_statistics_hostile_params(self, employee_client, params):
        response = employee_client.get(f"{API}/me/statistics", params,
                                       **bot_headers(TG_ID))
        assert response.status_code in (200, 400), response.content[:300]

    def test_day_notice_bad_kind_and_huge_comment(self, employee_client):
        for body in ({"kind": "DROP"}, {"kind": "LATE", "comment": "x" * 5000}):
            response = employee_client.post(f"{API}/me/attendance/notice", body,
                                            format="json", **bot_headers(TG_ID))
            assert response.status_code == 400
