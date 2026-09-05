"""Расстояние до офиса и допуск по нему.

Одно место, где считается «далеко ли». Формула гаверсинуса на голой
стандартной библиотеке: geopy или shapely ради одной функции потянули бы
за собой зависимость, которую пришлось бы обновлять годами.

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


@dataclass(frozen=True)
class Verdict:
    """Что решили про присланное местоположение.

    `checked=False` означает «сравнивать было не с чем»: у офиса нет
    координат или не задан радиус. Это НЕ нарушение и не повод отказать —
    отсутствие точки отсчёта нельзя записывать в вину человеку.
    """

    checked: bool
    inside: bool | None = None
    distance_m: float | None = None


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


def within_office(office, latitude, longitude, accuracy_m=None) -> Verdict:
    """Стоит ли человек достаточно близко к офису.

    Погрешность телефона прибавляется к радиусу, а не игнорируется.
    Отказать тому, кто стоит у самой двери, потому что GPS ошибся на
    двадцать метров, — худший из двух возможных промахов: у него нет
    способа исправиться, а у нарушителя и так есть подписанный код,
    который надо было где-то взять.

    Прибавка не бесконечная: потолок погрешности проверяется ОТДЕЛЬНО,
    до этой функции. Иначе «accuracy: 50000» проглотила бы любой радиус
    и превратила проверку в её видимость.
    """
    if office is None:
        return Verdict(checked=False)
    if office.latitude is None or office.longitude is None:
        return Verdict(checked=False)
    radius = office.geofence_radius_m
    if not radius or radius <= 0:
        return Verdict(checked=False)

    metres = distance_m(office.latitude, office.longitude, latitude, longitude)
    allowance = float(accuracy_m) if accuracy_m else 0.0
    return Verdict(
        checked=True,
        inside=metres <= radius + allowance,
        distance_m=metres,
    )


__all__ = [
    "EARTH_RADIUS_M",
    "Verdict",
    "distance_m",
    "looks_like_coordinates",
    "within_office",
]
