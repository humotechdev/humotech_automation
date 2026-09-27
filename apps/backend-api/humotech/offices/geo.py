"""Геозона офиса: где человек относительно неё.

Одно место, где решается «на месте ли». Геометрия на голой стандартной
библиотеке: geopy или shapely ради нескольких функций потянули бы за
собой зависимость, которую пришлось бы обновлять годами.

Земля здесь — шар радиусом 6371 км. Эллипсоид точнее на доли процента,
и эти доли не значат ничего рядом с погрешностью самого телефона:
она измеряется десятками метров, а не сантиметрами.

**Про честную границу.** Координаты приходят от клиента, и подделать их
можно — на Android программой-подменителем, без всякого взлома. Проверка
здесь не доказательство присутствия, а ещё один сигнал рядом с
подписанным кодом, его тридцатью секундами и одноразовостью. Выдавать её
за Face ID нельзя.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

EARTH_RADIUS_M = 6_371_000.0

#: Пределы координат. Значения вне их — не «далеко», а мусор.
LATITUDE_RANGE = (Decimal("-90"), Decimal("90"))
LONGITUDE_RANGE = (Decimal("-180"), Decimal("180"))


def looks_like_coordinates(latitude, longitude) -> bool:
    """Похоже ли это вообще на точку на Земле."""
    if latitude is None or longitude is None:
        return False
    try:
        lat, lon = Decimal(str(latitude)), Decimal(str(longitude))
    except (ArithmeticError, TypeError, ValueError):
        return False
    if not lat.is_finite() or not lon.is_finite():
        return False
    return (
        LATITUDE_RANGE[0] <= lat <= LATITUDE_RANGE[1]
        and LONGITUDE_RANGE[0] <= lon <= LONGITUDE_RANGE[1]
    )


def distance_m(lat1, lon1, lat2, lon2) -> float:
    """Расстояние по поверхности между двумя точками, в метрах."""
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    d_phi = phi2 - phi1
    d_lambda = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


# --- форма геозоны ------------------------------------------------------------
#
# Круг — прежний способ: точка и радиус. Контур — многоугольник по углам
# участка. Круг вокруг здания в городе неизбежно захватывает дорогу и
# соседей: чтобы накрыть прямоугольный корпус 110 × 90 м, круг нужен
# радиусом в семьдесят метров, и треть его площади — чужая.

CIRCLE = "CIRCLE"
POLYGON = "POLYGON"
GEOFENCE_KINDS = (CIRCLE, POLYGON)

#: Пределы контура. Меньше трёх углов — не участок. Больше пятидесяти —
#: уже не обводка здания, а попытка нарисовать карту.
POLYGON_MIN_VERTICES = 3
POLYGON_MAX_VERTICES = 50
#: Две вершины ближе метра — двойной щелчок, а не угол участка.
POLYGON_MIN_EDGE_M = 1.0
#: Сто квадратных метров — комната. Меньше контур не бывает осмысленным:
#: обычная погрешность телефона больше самого участка.
POLYGON_MIN_AREA_M2 = 100.0
#: Километр от края до края — большой кампус. Шире — это уже район, и
#: проверка «на месте ли человек» превращается в «в городе ли он».
POLYGON_MAX_SPAN_M = 1000.0

#: Погрешность, при которой точке внутри контура верят без запаса.
#:
#: Требовать «внутри на всю погрешность» нельзя: здание шириной тридцать
#: метров тогда не пропустило бы никого — обычная погрешность телефона
#: в десять-двадцать метров в него целиком не помещается. Лучшая оценка
#: места — сама точка, и если она внутри при хорошем приёме, человек
#: внутри. Порог тот же, что у Mini App: уверенный спутниковый приём.
CONFIDENT_ACCURACY_M = 35.0


class Decision:
    """Что можно сказать о месте человека относительно геозоны."""

    #: Внутри. Отметку можно принимать.
    INSIDE = "INSIDE"
    #: Снаружи, и погрешность этого не меняет.
    OUTSIDE = "OUTSIDE"
    #: Погрешность пересекает границу: может быть и внутри, и снаружи.
    #: Ни принять, ни отказать честно нельзя — нужна новая точка.
    AMBIGUOUS = "AMBIGUOUS"
    #: Сравнивать не с чем: у офиса не задана геозона.
    UNCHECKED = "UNCHECKED"


@dataclass(frozen=True)
class Verdict:
    """Что решили про присланное местоположение.

    `checked=False` означает «сравнивать было не с чем»: у офиса нет
    геозоны. Это НЕ нарушение и не повод отказать — отсутствие точки
    отсчёта нельзя записывать в вину человеку.

    `distance_m` у круга — до центра, у контура — до границы (ноль,
    если точка внутри). Смысл у обоих один: «насколько далеко от зоны».
    """

    decision: str
    kind: str | None = None
    distance_m: float | None = None

    @property
    def checked(self) -> bool:
        return self.decision != Decision.UNCHECKED

    @property
    def inside(self) -> bool | None:
        """Для журнала: да, нет или «не установлено»."""
        if self.decision == Decision.INSIDE:
            return True
        if self.decision == Decision.OUTSIDE:
            return False
        return None


class PolygonError(ValueError):
    """Контур, который нельзя сохранить. Текст — для человека."""


def geofence_kind(office) -> str | None:
    """Какая геозона у офиса действует на самом деле, либо None.

    Единственное место, где решается «офис настроен». Контур действует,
    если выбран и сохранён; круг — если есть точка и радиус. Старые
    офисы, у которых о контуре никто не слышал, остаются кругом.
    """
    if office is None:
        return None
    if getattr(office, "geofence_kind", CIRCLE) == POLYGON:
        return POLYGON if getattr(office, "geofence_polygon", None) else None
    if office.latitude is None or office.longitude is None:
        return None
    radius = office.geofence_radius_m
    if not radius or radius <= 0:
        return None
    return CIRCLE


def within_office(
    office, latitude, longitude, accuracy_m=None, *, unknown_accuracy_m=100.0
) -> Verdict:
    """Где человек относительно геозоны офиса. Одна функция на всё.

    **Круг** решает по-прежнему: погрешность прибавляется к радиусу.
    Отказать тому, кто стоит у самой двери, потому что GPS ошибся на
    двадцать метров, — худший из двух промахов. Так было до контура, и
    офисы, настроенные кругом, перемены не заметят.

    **Контур** погрешность к себе не прибавляет — иначе он снова
    захватил бы дорогу и соседей, ради которых его и рисуют. Вместо
    этого три исхода:

      * внутри при хорошем приёме (или до границы дальше погрешности) —
        внутри;
      * снаружи дальше погрешности — снаружи, с расстоянием до границы;
      * погрешность пересекает границу — «не установлено»: честного
        ответа нет, и нужна новая точка, а не догадка.

    Погрешность, которую телефон не сообщил, считается худшей из
    допустимых (`unknown_accuracy_m`), а не хорошей: придумывать
    точность за телефон — ровно то, что делало грубую точку уверенной.

    Потолок погрешности проверяется ОТДЕЛЬНО, до этой функции. Иначе
    «accuracy: 50000» проглотила бы любой радиус.
    """
    kind = geofence_kind(office)
    if kind is None:
        return Verdict(decision=Decision.UNCHECKED)

    if kind == CIRCLE:
        metres = distance_m(office.latitude, office.longitude, latitude, longitude)
        allowance = float(accuracy_m) if accuracy_m else 0.0
        inside = metres <= office.geofence_radius_m + allowance
        return Verdict(
            decision=Decision.INSIDE if inside else Decision.OUTSIDE,
            kind=CIRCLE,
            distance_m=metres,
        )

    vertices = office.geofence_polygon
    origin = (float(vertices[0][0]), float(vertices[0][1]))
    shape = [_project(origin, lat, lon) for lat, lon in vertices]
    here = _project(origin, latitude, longitude)
    edge = _distance_to_boundary(here, shape)
    known = accuracy_m is not None and float(accuracy_m) > 0
    accuracy = float(accuracy_m) if known else float(unknown_accuracy_m)

    if _contains(here, shape):
        confident = (known and accuracy <= CONFIDENT_ACCURACY_M) or edge >= accuracy
        return Verdict(
            decision=Decision.INSIDE if confident else Decision.AMBIGUOUS,
            kind=POLYGON,
            distance_m=0.0,
        )
    return Verdict(
        decision=Decision.OUTSIDE if edge > accuracy else Decision.AMBIGUOUS,
        kind=POLYGON,
        distance_m=edge,
    )


def clean_polygon(raw) -> list[list[float]]:
    """Проверенный контур в виде `[[широта, долгота], …]`, либо PolygonError.

    Проверяется то, что делает контур негодным для решения «внутри ли
    человек»: мусорные координаты, слишком мало или много углов,
    совпадающие вершины, самопересечение (у «восьмёрки» нет внутренности
    в обычном смысле) и размер — от комнаты до кампуса.

    Замыкающая вершина, повторяющая первую, отбрасывается: контур
    хранится незамкнутым и замыкается при расчёте.
    """
    if not isinstance(raw, (list, tuple)):
        raise PolygonError("Контур — список углов участка.")
    vertices: list[list[float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise PolygonError("Каждый угол — пара «широта, долгота».")
        lat, lon = item
        if isinstance(lat, bool) or isinstance(lon, bool):
            raise PolygonError("Координаты угла — числа.")
        if not looks_like_coordinates(lat, lon):
            raise PolygonError("Угол контура вне Земли: проверьте координаты.")
        vertices.append([round(float(lat), 6), round(float(lon), 6)])

    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices.pop()
    if len(vertices) < POLYGON_MIN_VERTICES:
        raise PolygonError(
            f"В контуре нужно не меньше {POLYGON_MIN_VERTICES} углов."
        )
    if len(vertices) > POLYGON_MAX_VERTICES:
        raise PolygonError(f"В контуре не больше {POLYGON_MAX_VERTICES} углов.")

    origin = (vertices[0][0], vertices[0][1])
    shape = [_project(origin, lat, lon) for lat, lon in vertices]
    count = len(shape)

    for index in range(count):
        if math.dist(shape[index], shape[(index + 1) % count]) < POLYGON_MIN_EDGE_M:
            raise PolygonError(
                "Две соседние вершины почти совпадают: уберите лишнюю."
            )

    if _self_intersects(shape):
        raise PolygonError(
            "Стороны контура пересекаются. Обведите участок по порядку, "
            "угол за углом, не перекрещивая линии."
        )

    if abs(_signed_area(shape)) < POLYGON_MIN_AREA_M2:
        raise PolygonError(
            f"Контур слишком мал: меньше {POLYGON_MIN_AREA_M2:.0f} м²."
        )
    span = max(math.dist(a, b) for a in shape for b in shape)
    if span > POLYGON_MAX_SPAN_M:
        raise PolygonError(
            f"Контур шире {POLYGON_MAX_SPAN_M:.0f} м — это уже не участок "
            "офиса. Обведите само здание или территорию."
        )
    return vertices


def polygon_centre(vertices) -> tuple[float, float]:
    """Центр тяжести контура: точка офиса для карты и списков."""
    origin = (float(vertices[0][0]), float(vertices[0][1]))
    shape = [_project(origin, lat, lon) for lat, lon in vertices]
    area = _signed_area(shape)
    cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(shape, shape[1:] + shape[:1]):
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return _unproject(origin, cx / (6 * area), cy / (6 * area))


def polygon_area_m2(vertices) -> float:
    origin = (float(vertices[0][0]), float(vertices[0][1]))
    return abs(_signed_area([_project(origin, lat, lon) for lat, lon in vertices]))


# --- плоская геометрия ----------------------------------------------------
#
# Участок офиса меньше километра, и на таком размере Землю можно считать
# плоской: равнопромежуточная проекция вокруг первой вершины ошибается
# на доли метра — меньше, чем шестой знак координат телефона.


def _project(origin, latitude, longitude) -> tuple[float, float]:
    lat0, lon0 = origin
    x = (
        math.radians(float(longitude) - lon0)
        * EARTH_RADIUS_M
        * math.cos(math.radians(lat0))
    )
    y = math.radians(float(latitude) - lat0) * EARTH_RADIUS_M
    return (x, y)


def _unproject(origin, x, y) -> tuple[float, float]:
    lat0, lon0 = origin
    lat = lat0 + math.degrees(y / EARTH_RADIUS_M)
    lon = lon0 + math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(lat0))))
    return (lat, lon)


def _signed_area(shape) -> float:
    return sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(shape, shape[1:] + shape[:1])
    ) / 2


def _contains(point, shape) -> bool:
    """Луч вправо: нечётное число пересечений сторон — внутри."""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(shape, shape[-1:] + shape[:-1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _segment_distance(point, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = point, a, b
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = (
        0.0 if length == 0
        else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    )
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _distance_to_boundary(point, shape) -> float:
    return min(
        _segment_distance(point, a, b)
        for a, b in zip(shape, shape[1:] + shape[:1])
    )


_EPS = 1e-9


def _orientation(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, c) -> bool:
    """Лежит ли c в пределах отрезка ab (все три на одной прямой)."""
    return (
        min(a[0], b[0]) - _EPS <= c[0] <= max(a[0], b[0]) + _EPS
        and min(a[1], b[1]) - _EPS <= c[1] <= max(a[1], b[1]) + _EPS
    )


def _segments_touch(p1, p2, q1, q2) -> bool:
    d1 = _orientation(q1, q2, p1)
    d2 = _orientation(q1, q2, p2)
    d3 = _orientation(p1, p2, q1)
    d4 = _orientation(p1, p2, q2)
    if ((d1 > _EPS > d2) or (d1 < -_EPS < d2)) and (
        (d3 > _EPS > d4) or (d3 < -_EPS < d4)
    ):
        return True
    return (
        (abs(d1) <= _EPS and _on_segment(q1, q2, p1))
        or (abs(d2) <= _EPS and _on_segment(q1, q2, p2))
        or (abs(d3) <= _EPS and _on_segment(p1, p2, q1))
        or (abs(d4) <= _EPS and _on_segment(p1, p2, q2))
    )


def _self_intersects(shape) -> bool:
    """Пересекаются ли стороны контура между собой.

    Несмежные стороны не должны касаться вовсе. Смежные делят вершину, и
    это нормально, — кроме случая, когда вторая идёт назад по первой:
    такой «шип» нулевой ширины ничего не обводит.
    """
    count = len(shape)
    edges = [(shape[i], shape[(i + 1) % count]) for i in range(count)]
    for i in range(count):
        a, b = edges[i]
        c = edges[(i + 1) % count][1]
        ab = (b[0] - a[0], b[1] - a[1])
        bc = (c[0] - b[0], c[1] - b[1])
        cross = ab[0] * bc[1] - ab[1] * bc[0]
        dot = ab[0] * bc[0] + ab[1] * bc[1]
        if abs(cross) <= 1e-6 * math.hypot(*ab) * math.hypot(*bc) and dot < 0:
            return True
        for j in range(i + 2, count):
            if i == 0 and j == count - 1:
                continue  # первая и последняя стороны смежны
            if _segments_touch(a, b, *edges[j]):
                return True
    return False


__all__ = [
    "CIRCLE",
    "CONFIDENT_ACCURACY_M",
    "Decision",
    "EARTH_RADIUS_M",
    "GEOFENCE_KINDS",
    "POLYGON",
    "PolygonError",
    "Verdict",
    "clean_polygon",
    "distance_m",
    "geofence_kind",
    "looks_like_coordinates",
    "polygon_area_m2",
    "polygon_centre",
    "within_office",
]
