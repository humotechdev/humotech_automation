"""Контур территории офиса: где человек относительно участка, а не круга.

Круг вокруг прямоугольного корпуса в городе всегда захватывает дорогу и
соседей. Контур обводит сам участок — и потому не прибавляет к себе
погрешность телефона: иначе он снова захватил бы ровно то, ради чего его
рисовали. Вместо прибавки — три исхода: внутри, снаружи, «не установлено».

Геометрия — открытые данные OpenStreetMap, не координаты сотрудников:
огороженная промышленная территория южнее улицы Махтумкули в Ташкенте
(way 406552826) и восьмиэтажное здание в полутора сотнях метров к востоку
от её стены (way 1383513886).
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from humotech.attendance.services import (
    RejectionReason,
    office_is_located,
    register_scan,
)
from humotech.audit.models import AuditLog
from humotech.offices.geo import (
    CIRCLE,
    POLYGON,
    Decision,
    PolygonError,
    clean_polygon,
    geofence_kind,
    polygon_centre,
    within_office,
)
from humotech.offices.models import Office

pytestmark = pytest.mark.django_db

API = "/api/v1"

#: Огороженная территория южнее ул. Махтумкули (OSM way 406552826).
COMPOUND = [
    [41.30495, 69.331616], [41.304857, 69.331617], [41.304812, 69.331057],
    [41.304803, 69.330988], [41.303657, 69.331005], [41.303354, 69.331019],
    [41.303334, 69.331781], [41.303395, 69.331784], [41.303364, 69.332934],
    [41.303436, 69.333213], [41.305025, 69.333295], [41.305025, 69.333208],
    [41.305036, 69.332542],
]

# Точки проверки. Расстояния до границы — по той же проекции, в метрах.
INSIDE_BUILDING = (Decimal("41.304290"), Decimal("69.332414"))  # 70 м до края
DEEP_INSIDE = (Decimal("41.304200"), Decimal("69.332000"))      # 80 м до края
EDGE_INSIDE = (Decimal("41.304900"), Decimal("69.332500"))      # 15 м внутри
EDGE_OUTSIDE = (Decimal("41.305120"), Decimal("69.332500"))     # 10 м снаружи
ACROSS_ROAD = (Decimal("41.305700"), Decimal("69.332400"))      # за Махтумкули, 75 м
NEIGHBOUR = (Decimal("41.305220"), Decimal("69.335210"))        # соседнее здание, 161 м


@pytest.fixture()
def fenced_office(office):
    """Офис с контуром территории. Радиус от прежней настройки остался."""
    office.geofence_kind = POLYGON
    office.geofence_polygon = clean_polygon(COMPOUND)
    lat, lon = polygon_centre(office.geofence_polygon)
    office.latitude = Decimal(f"{lat:.6f}")
    office.longitude = Decimal(f"{lon:.6f}")
    office.geofence_radius_m = 500
    office.save()
    return office


@pytest.fixture()
def circle_office(office):
    """Старый офис: круг 70 м, о контуре ничего не знает."""
    office.latitude, office.longitude = INSIDE_BUILDING
    office.geofence_radius_m = 70
    office.save(update_fields=["latitude", "longitude", "geofence_radius_m"])
    return office


def mark(employee, qr_point, now, fresh_qr, place, accuracy="12", nonce="n1", **extra):
    return register_scan(
        employee_id=employee.id, qr_point=qr_point, now=now,
        qr_nonce_hash=nonce, latitude=place[0], longitude=place[1],
        location_accuracy_m=Decimal(accuracy) if accuracy is not None else None,
        **fresh_qr, **extra,
    )


# --- решение по месту ---------------------------------------------------------

def test_inside_the_building_is_accepted(employee, qr_point, now, fresh_qr, fenced_office):
    result = mark(employee, qr_point, now, fresh_qr, INSIDE_BUILDING)

    assert result.accepted
    assert result.event.inside_geofence is True
    # У контура расстояние — до границы, и внутри оно ноль.
    assert result.event.distance_m == 0
    assert result.event.event_metadata["geofence_kind"] == POLYGON


def test_near_the_edge_inside_with_a_good_fix_is_accepted(
    employee, qr_point, now, fresh_qr, fenced_office
):
    """У самой стены изнутри при обычной погрешности — внутри.

    Требовать «внутри на всю погрешность» значило бы не пустить никого в
    узкое здание: десять-двадцать метров GPS в него целиком не помещаются.
    """
    result = mark(employee, qr_point, now, fresh_qr, EDGE_INSIDE, accuracy="20")

    assert result.accepted


def test_just_outside_with_a_sharp_fix_is_refused_with_the_distance(
    employee, qr_point, now, fresh_qr, fenced_office
):
    result = mark(employee, qr_point, now, fresh_qr, EDGE_OUTSIDE, accuracy="4")

    assert result.rejection_reason == RejectionReason.OUTSIDE_GEOFENCE
    assert float(result.event.distance_m) == pytest.approx(9.7, abs=1)
    assert result.event.inside_geofence is False


def test_an_error_circle_across_the_boundary_is_ambiguous(
    employee, qr_point, now, fresh_qr, fenced_office
):
    """Погрешность 20 м, до границы 10 м снаружи: может быть и внутри.

    Ни принять, ни отказать «слишком далеко» честно нельзя. Отметка не
    засчитывается, человек получает просьбу повторить, а в журнале
    «внутри» не установлено — это не нарушение.
    """
    result = mark(employee, qr_point, now, fresh_qr, EDGE_OUTSIDE, accuracy="20")

    assert not result.accepted
    assert result.rejection_reason == RejectionReason.LOCATION_AMBIGUOUS
    assert result.session is None
    assert result.event.inside_geofence is None


def test_across_the_road_is_refused(employee, qr_point, now, fresh_qr, fenced_office):
    """Дорога не входит в участок — ради этого контур и рисуют."""
    result = mark(employee, qr_point, now, fresh_qr, ACROSS_ROAD, accuracy="15")

    assert result.rejection_reason == RejectionReason.OUTSIDE_GEOFENCE
    assert float(result.event.distance_m) == pytest.approx(75, abs=2)


def test_the_neighbouring_building_is_refused(
    employee, qr_point, now, fresh_qr, fenced_office
):
    """Соседнее здание в полутора сотнях метров — не офис, даже при
    погрешности в тридцать метров: контур её к себе не прибавляет."""
    result = mark(employee, qr_point, now, fresh_qr, NEIGHBOUR, accuracy="30")

    assert result.rejection_reason == RejectionReason.OUTSIDE_GEOFENCE
    assert float(result.event.distance_m) == pytest.approx(161, abs=2)


def test_a_coarse_fix_inside_is_ambiguous_unless_deep_enough(
    employee, qr_point, now, fresh_qr, fenced_office
):
    """Грубая точка внутри: верим, только если до края дальше погрешности."""
    shallow = mark(employee, qr_point, now, fresh_qr, INSIDE_BUILDING,
                   accuracy="90", nonce="a")
    assert shallow.rejection_reason == RejectionReason.LOCATION_AMBIGUOUS

    deep = mark(employee, qr_point, now, fresh_qr, DEEP_INSIDE,
                accuracy="75", nonce="b")
    assert deep.accepted


def test_a_hopeless_fix_is_too_vague_not_outside(
    employee, qr_point, now, fresh_qr, fenced_office
):
    result = mark(employee, qr_point, now, fresh_qr, INSIDE_BUILDING, accuracy="500")

    assert result.rejection_reason == RejectionReason.LOCATION_TOO_VAGUE
    assert result.event.inside_geofence is None


def test_a_missing_accuracy_is_treated_as_the_worst_allowed(
    employee, qr_point, now, fresh_qr, fenced_office
):
    """Телефон не сообщил погрешность — её не придумывают хорошей.

    Считается худшая допустимая (потолок, 100 м): точка в 70 м от края
    при такой погрешности может быть и снаружи.
    """
    result = mark(employee, qr_point, now, fresh_qr, INSIDE_BUILDING, accuracy=None)

    assert result.rejection_reason == RejectionReason.LOCATION_AMBIGUOUS


def test_a_stale_fix_is_refused_and_a_fresh_one_is_recorded(
    employee, qr_point, now, fresh_qr, fenced_office
):
    """Точка, полученная давно, — место, где человек был, а не где он есть."""
    stale = mark(employee, qr_point, now, fresh_qr, INSIDE_BUILDING,
                 nonce="a", location_age_s=Decimal("45"),
                 location_source="telegram")
    assert stale.rejection_reason == RejectionReason.LOCATION_STALE
    assert stale.event.inside_geofence is None

    fresh = mark(employee, qr_point, now, fresh_qr, INSIDE_BUILDING,
                 nonce="b", location_age_s=Decimal("3.5"),
                 location_source="browser")
    assert fresh.accepted
    # Источник и возраст — для разбора спорной отметки.
    assert fresh.event.event_metadata == {
        "geofence_kind": POLYGON,
        "location_source": "browser",
        "location_age_s": 3.5,
    }


def test_the_contour_wins_over_a_leftover_radius(
    employee, qr_point, now, fresh_qr, fenced_office
):
    """Радиус 500 м остался от прежней настройки — но выбран контур.

    Соседнее здание внутри старого круга, и принять его значило бы молча
    вернуть ту самую зону, от которой отказались.
    """
    assert fenced_office.geofence_radius_m == 500

    result = mark(employee, qr_point, now, fresh_qr, NEIGHBOUR, accuracy="10")

    assert result.rejection_reason == RejectionReason.OUTSIDE_GEOFENCE


# --- старые офисы кругом --------------------------------------------------------

def test_a_legacy_circle_office_keeps_its_old_rule(
    employee, qr_point, now, fresh_qr, circle_office
):
    """Офис кругом решает как раньше: погрешность прибавляется к радиусу."""
    assert circle_office.geofence_kind == CIRCLE

    # 80 м от центра при радиусе 70 и погрешности 15 — внутри.
    near = (Decimal("41.305010"), Decimal("69.332414"))
    inside = mark(employee, qr_point, now, fresh_qr, near, accuracy="15", nonce="a")
    assert inside.accepted
    assert float(inside.event.distance_m) == pytest.approx(80, abs=1)
    assert inside.event.event_metadata["geofence_kind"] == CIRCLE

    far = mark(employee, qr_point, now, fresh_qr, NEIGHBOUR, accuracy="20", nonce="b")
    assert far.rejection_reason == RejectionReason.OUTSIDE_GEOFENCE


def test_a_circle_office_without_accuracy_is_decided_as_before(
    employee, qr_point, now, fresh_qr, circle_office
):
    """Без погрешности круг не прибавляет ничего — прежнее поведение."""
    near = (Decimal("41.304700"), Decimal("69.332414"))  # 46 м
    assert mark(employee, qr_point, now, fresh_qr, near, accuracy=None).accepted


# --- «офис настроен» -----------------------------------------------------------

def test_a_contour_alone_makes_the_office_located(office):
    """Печатный код требует настроенной геозоны; контур без радиуса — она."""
    assert not office_is_located(office)

    office.geofence_kind = POLYGON
    office.geofence_polygon = clean_polygon(COMPOUND)
    assert geofence_kind(office) == POLYGON
    assert office_is_located(office)


def test_the_polygon_kind_without_a_contour_is_not_located(office):
    office.geofence_kind = POLYGON
    office.geofence_polygon = None
    office.latitude, office.longitude = INSIDE_BUILDING
    office.geofence_radius_m = 100

    assert geofence_kind(office) is None


def test_a_sticker_is_accepted_at_an_office_with_only_a_contour(
    employee, qr_point, now, fenced_office
):
    fenced_office.geofence_radius_m = None
    fenced_office.save(update_fields=["geofence_radius_m"])

    result = register_scan(
        employee_id=employee.id, qr_point=qr_point, now=now,
        latitude=INSIDE_BUILDING[0], longitude=INSIDE_BUILDING[1],
        location_accuracy_m=Decimal("12"), strict_location=True,
    )

    assert result.accepted


def test_the_client_cannot_hand_in_a_verdict():
    """«Внутри ли» решает сервер: передать готовый ответ нечем."""
    parameters = inspect.signature(register_scan).parameters

    assert "inside_geofence" not in parameters


# --- проверка контура -----------------------------------------------------------

def test_a_closed_contour_is_stored_open():
    closed = COMPOUND + [COMPOUND[0]]

    assert clean_polygon(closed) == clean_polygon(COMPOUND)
    assert len(clean_polygon(closed)) == len(COMPOUND)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([[41.30, 69.33], [41.301, 69.331]], "не меньше 3"),
        ([[41.30 + i * 1e-5, 69.33 + (i % 2) * 1e-4] for i in range(51)], "не больше 50"),
        ([[41.300, 69.330], [41.301, 69.331], [41.300, 69.331], [41.301, 69.330]],
         "пересекаются"),
        ([[41.300, 69.330], [41.301, 69.330], [41.3005, 69.330]], "пересекаются"),
        ([[41.300, 69.330], [41.300, 69.330], [41.301, 69.331]], "почти совпадают"),
        ([[41.30000, 69.33000], [41.30005, 69.33000], [41.30005, 69.33005]], "мал"),
        ([[41.30, 69.33], [41.32, 69.33], [41.32, 69.35]], "шире"),
        ([[91, 69.33], [41.301, 69.331], [41.300, 69.331]], "вне Земли"),
        ([[float("nan"), 69.33], [41.301, 69.331], [41.300, 69.331]], "вне Земли"),
        ([[41.30, 69.33, 1], [41.301, 69.331], [41.300, 69.331]], "пара"),
        ("41.30,69.33", "список"),
    ],
)
def test_bad_contours_are_refused(raw, message):
    with pytest.raises(PolygonError, match=message):
        clean_polygon(raw)


def test_the_decision_is_unchecked_without_any_geofence(office):
    verdict = within_office(office, *INSIDE_BUILDING, accuracy_m=10)

    assert verdict.decision == Decision.UNCHECKED
    assert verdict.inside is None


# --- сохранение через API ------------------------------------------------------

@pytest.fixture()
def hr(api_client, make_user, organization):
    user = make_user(
        organization, permissions=("offices.read", "offices.manage", "audit.read")
    )
    api_client.force_authenticate(user=user)
    return api_client


def test_hr_saves_and_edits_a_contour(hr, office):
    saved = hr.patch(f"{API}/offices/{office.id}/", {
        "geofence_kind": "POLYGON", "geofence_polygon": COMPOUND,
    }, format="json")

    assert saved.status_code == 200, saved.content
    body = saved.json()
    assert body["geofence_kind"] == "POLYGON"
    assert body["geofence_polygon"] == clean_polygon(COMPOUND)
    # Точка офиса — центр контура, её ставит сервер.
    lat, lon = polygon_centre(clean_polygon(COMPOUND))
    assert float(body["latitude"]) == pytest.approx(lat, abs=1e-6)
    assert float(body["longitude"]) == pytest.approx(lon, abs=1e-6)

    # Правка: один угол сдвинут.
    moved = [list(vertex) for vertex in COMPOUND]
    moved[0] = [41.30490, 69.331620]
    edited = hr.patch(f"{API}/offices/{office.id}/", {
        "geofence_polygon": moved,
    }, format="json")
    assert edited.status_code == 200, edited.content
    assert edited.json()["geofence_polygon"][0] == [41.3049, 69.33162]

    # В журнале — контур до и после, по вершинам, а не строкой.
    entries = list(
        AuditLog.objects.filter(entity_id=office.id, action="office.update")
        .order_by("occurred_at")
    )
    assert entries[-1].old_values["geofence_polygon"][0] == [41.30495, 69.331616]
    assert entries[-1].new_values["geofence_polygon"][0] == [41.3049, 69.33162]
    assert entries[0].old_values["geofence_kind"] == "CIRCLE"
    assert entries[0].new_values["geofence_kind"] == "POLYGON"


def test_hr_switches_back_to_the_circle(hr, fenced_office):
    back = hr.patch(f"{API}/offices/{fenced_office.id}/", {
        "geofence_kind": "CIRCLE",
        "latitude": "41.304290", "longitude": "69.332414",
        "geofence_radius_m": 100,
    }, format="json")

    assert back.status_code == 200, back.content
    assert back.json()["geofence_kind"] == "CIRCLE"
    fenced_office.refresh_from_db()
    assert geofence_kind(fenced_office) == CIRCLE


def test_a_self_intersecting_contour_is_not_saved(hr, office):
    refused = hr.patch(f"{API}/offices/{office.id}/", {
        "geofence_kind": "POLYGON",
        "geofence_polygon": [[41.300, 69.330], [41.301, 69.331],
                             [41.300, 69.331], [41.301, 69.330]],
    }, format="json")

    assert refused.status_code == 400
    assert "geofence_polygon" in str(refused.content.decode())
    office.refresh_from_db()
    assert office.geofence_kind == CIRCLE
    assert office.geofence_polygon is None


def test_the_polygon_kind_needs_a_contour(hr, office):
    refused = hr.patch(f"{API}/offices/{office.id}/", {
        "geofence_kind": "POLYGON",
    }, format="json")

    assert refused.status_code == 400


def test_the_contour_cannot_be_changed_without_offices_manage(
    api_client, make_user, organization, office
):
    reader = make_user(organization, permissions=("offices.read",))
    api_client.force_authenticate(user=reader)

    refused = api_client.patch(f"{API}/offices/{office.id}/", {
        "geofence_kind": "POLYGON", "geofence_polygon": COMPOUND,
    }, format="json")

    assert refused.status_code == 403
    office.refresh_from_db()
    assert office.geofence_polygon is None


def test_a_foreign_office_contour_is_invisible(hr, foreign_office):
    refused = hr.patch(f"{API}/offices/{foreign_office.id}/", {
        "geofence_kind": "POLYGON", "geofence_polygon": COMPOUND,
    }, format="json")

    assert refused.status_code == 404
    assert Office.objects.get(id=foreign_office.id).geofence_polygon is None


def test_the_database_refuses_a_polygon_kind_without_a_contour(office):
    from django.db import IntegrityError, transaction

    office.geofence_kind = POLYGON
    office.geofence_polygon = None
    with pytest.raises(IntegrityError), transaction.atomic():
        office.save(update_fields=["geofence_kind", "geofence_polygon"])
