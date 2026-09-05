"""Геозона отметки: расстояние до офиса и вера в погрешность телефона.

Проверяется не формула, а решения, которые она принимает за человека.

Отдельно и намеренно: **координаты недоверенны**. Их присылает клиент,
и подделать их на Android можно программой-подменителем, без всякого
взлома. Всё, что здесь проверяется, — что присланное и негодное не
проходит молча. Доказательством присутствия это не является, и
относиться к нему так нельзя.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from humotech.attendance.services import RejectionReason, register_scan
from humotech.offices.geo import distance_m, within_office

pytestmark = pytest.mark.django_db

# Офис и точка в трёхстах метрах от него.
OFFICE_LAT, OFFICE_LON = Decimal("38.559772"), Decimal("68.787038")
NEAR_LAT, NEAR_LON = Decimal("38.560200"), Decimal("68.787400")
FAR_LAT, FAR_LON = Decimal("38.600000"), Decimal("68.850000")


@pytest.fixture()
def located_office(office):
    """Офис с координатами и радиусом в сто метров."""
    office.latitude = OFFICE_LAT
    office.longitude = OFFICE_LON
    office.geofence_radius_m = 100
    office.save(update_fields=["latitude", "longitude", "geofence_radius_m"])
    return office


def mark(employee, qr_point, now, fresh_qr, nonce="n1", **location):
    return register_scan(
        employee_id=employee.id, qr_point=qr_point, now=now,
        qr_nonce_hash=nonce, **location, **fresh_qr,
    )


# --- расстояние -------------------------------------------------------------

def test_distance_matches_a_known_pair():
    """Гаверсинус на известной паре: Душанбе — Ташкент, около 300 км."""
    metres = distance_m(38.5598, 68.7870, 41.2995, 69.2401)

    assert 290_000 < metres < 310_000


def test_distance_to_itself_is_zero():
    assert distance_m(38.5598, 68.7870, 38.5598, 68.7870) == pytest.approx(0)


def test_an_office_without_coordinates_is_not_checked(office):
    """Отсутствие точки отсчёта — «не проверялось», а не «нарушение».

    Записывать в вину человеку то, что кадровик не заполнил карточку
    офиса, нельзя.
    """
    verdict = within_office(office, NEAR_LAT, NEAR_LON)

    assert verdict.checked is False
    assert verdict.inside is None


def test_an_office_without_a_radius_is_not_checked(located_office):
    located_office.geofence_radius_m = None
    located_office.save(update_fields=["geofence_radius_m"])

    assert within_office(located_office, FAR_LAT, FAR_LON).checked is False


def test_accuracy_widens_the_circle_but_is_capped_elsewhere(located_office):
    """Погрешность прибавляется к радиусу.

    Отказать тому, кто стоит у двери, потому что GPS ошибся на двадцать
    метров, — худший из двух промахов: исправиться ему нечем. Потолок
    самой погрешности проверяется отдельно, до расстояния.
    """
    strict = within_office(located_office, NEAR_LAT, NEAR_LON)
    generous = within_office(located_office, NEAR_LAT, NEAR_LON, accuracy_m=90)

    assert strict.distance_m == pytest.approx(generous.distance_m)
    assert generous.inside is True


# --- правило отметки --------------------------------------------------------

def test_scan_from_the_office_is_accepted(employee, qr_point, now, fresh_qr,
                                          located_office):
    result = mark(employee, qr_point, now, fresh_qr,
                  latitude=NEAR_LAT, longitude=NEAR_LON,
                  location_accuracy_m=Decimal("15"))

    assert result.accepted
    assert result.event.inside_geofence is True


def test_scan_from_far_away_is_refused(employee, qr_point, now, fresh_qr,
                                       located_office):
    result = mark(employee, qr_point, now, fresh_qr,
                  latitude=FAR_LAT, longitude=FAR_LON,
                  location_accuracy_m=Decimal("10"))

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.OUTSIDE_GEOFENCE
    # Отклонённая попытка не открывает сессию и остаётся в журнале.
    assert result.session is None
    assert result.event.verification_status == "REJECTED"
    assert result.event.inside_geofence is False


def test_a_hopeless_accuracy_is_refused(employee, qr_point, now, fresh_qr,
                                        located_office):
    """Без потолка «accuracy: 50000» проглотила бы любой радиус.

    Проверка превратилась бы в свою видимость: формально она есть,
    а пройти её можно из другого города.
    """
    result = mark(employee, qr_point, now, fresh_qr,
                  latitude=FAR_LAT, longitude=FAR_LON,
                  location_accuracy_m=Decimal("50000"))

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.LOCATION_TOO_VAGUE


def test_a_nonpositive_accuracy_is_refused(employee, qr_point, now, fresh_qr,
                                           located_office):
    """Нулевая погрешность физически невозможна — это подделка или сбой."""
    result = mark(employee, qr_point, now, fresh_qr,
                  latitude=NEAR_LAT, longitude=NEAR_LON,
                  location_accuracy_m=Decimal("0"))

    assert result.rejection_reason == RejectionReason.LOCATION_TOO_VAGUE


def test_the_ceiling_comes_from_settings(employee, qr_point, now, fresh_qr,
                                         located_office, settings):
    """Порог настраивается, а не зашит без объяснения."""
    settings.QR = {**settings.QR, "MAX_LOCATION_ACCURACY_M": 10}

    result = mark(employee, qr_point, now, fresh_qr,
                  latitude=NEAR_LAT, longitude=NEAR_LON,
                  location_accuracy_m=Decimal("40"))

    assert result.rejection_reason == RejectionReason.LOCATION_TOO_VAGUE


def test_a_scan_without_coordinates_still_works(employee, qr_point, now,
                                                fresh_qr, located_office):
    """Прежний путь не сломан.

    Точка не требует геолокации, координат нет — отметка проходит, а
    `inside_geofence` остаётся `None`: не проверялось.
    """
    result = mark(employee, qr_point, now, fresh_qr)

    assert result.accepted
    assert result.event.inside_geofence is None


def test_a_point_that_demands_location_refuses_without_it(
    organization, office, employee, now, fresh_qr
):
    from django_tests.conftest import make_qr_point

    strict = make_qr_point(organization, office, code="GEO_DOOR",
                           require_geolocation=True)

    result = mark(employee, strict, now, fresh_qr)

    assert result.rejection_reason == RejectionReason.GEOLOCATION_REQUIRED


def test_an_office_without_coordinates_does_not_block_the_scan(
    employee, qr_point, now, fresh_qr, office
):
    """Незаполненная карточка офиса не должна запирать людей снаружи."""
    assert office.latitude is None

    result = mark(employee, qr_point, now, fresh_qr,
                  latitude=NEAR_LAT, longitude=NEAR_LON,
                  location_accuracy_m=Decimal("15"))

    assert result.accepted
    assert result.event.inside_geofence is None
