"""Тексты про отметку по QR.

Каждый исход — своя фраза, потому что действия человека разные:
«код устарел» значит «отсканируйте ещё раз», «слишком далеко» —
«подойдите ближе», «привязка ждёт» — «идите в отдел кадров». Один общий
текст на всё превратил бы их в одинаковую беспомощность.

Ни строки кода, ни координат, ни идентификаторов здесь нет и быть не
должно: сообщение остаётся в переписке, а по коду собирают рабочий код.
"""

from __future__ import annotations

from src.utils.safe import escape

SCAN_FAILED = (
    "Не получилось отметиться. Попробуйте ещё раз через минуту — "
    "если не выйдет, напишите в отдел кадров."
)

PAYLOAD_REJECTED = (
    "Не удалось разобрать данные сканирования. "
    "Откройте «📷 Отметиться» и попробуйте снова."
)

#: Отказы доступа приходят кодом из backend.
ACCESS_MESSAGES = {
    "not_linked": (
        "Этот Telegram не связан с учётной записью сотрудника. "
        "Попросите персональную ссылку в отделе кадров."
    ),
    "pending_confirmation": (
        "Привязка ещё не подтверждена. Отдел кадров подтвердит её, "
        "и отметка заработает — заново привязываться не нужно."
    ),
    "revoked": (
        "Привязка отключена. Обратитесь в отдел кадров."
    ),
    "employee_inactive": (
        "Учётная запись сотрудника неактивна. Это к отделу кадров."
    ),
}

#: Исход отметки -> что показать. Ключи те же, что у Mini App: словарь
#: один на продукт, и расходиться им нельзя.
OUTCOMES = {
    "ENTERED": "Приход отмечен",
    "EXITED": "Уход отмечен",
    "ALREADY_INSIDE": (
        "Вы уже отмечены как в офисе.\n"
        "Открытая сессия одна — чтобы закрыть её, отсканируйте код на выходе."
    ),
    "NOT_INSIDE": "Открытой сессии нет. Уход отмечается только после прихода.",
    "QR_EXPIRED": "Код устарел. Код на экране меняется — отсканируйте новый.",
    "QR_ALREADY_USED": "Этот код уже использован. Дождитесь следующего.",
    "QR_INVALID": "Код не распознан. Отсканируйте код на экране у входа.",
    "QR_POINT_INACTIVE": (
        "Этот QR-код больше не действует: точка отметки выключена. "
        "Обратитесь в отдел кадров."
    ),
    "QR_REVOKED": (
        "Этот QR-код больше не действует. Отсканируйте код, который висит "
        "у входа сейчас, — старый заменили."
    ),
    "GEOFENCE_NOT_CONFIGURED": (
        "Офис ещё не настроен для отметки по QR: у него нет точки на карте. "
        "Сообщите в отдел кадров."
    ),
    "TOO_SOON": (
        "Вы только что отметились на этой точке. "
        "Повторная отметка — не раньше чем через минуту."
    ),
    "OFFICE_NOT_ALLOWED": (
        "Этот офис вам не назначен. Отметиться можно там, где вы числитесь."
    ),
    "NETWORK_REQUIRED": (
        "Нужно быть в сети офиса. Подключитесь к рабочему Wi-Fi и попробуйте снова."
    ),
    "GEOLOCATION_REQUIRED": (
        "Нужно разрешить доступ к местоположению и отсканировать код заново."
    ),
    "OUTSIDE_GEOFENCE": (
        "Вы слишком далеко от офиса. Отметиться можно на месте."
    ),
    "LOCATION_TOO_VAGUE": (
        "Местоположение определилось слишком приблизительно. "
        "Выйдите на открытое место или подойдите к окну и попробуйте снова."
    ),
}


#: Направление точки -> как о нём сказать в отказе.
ONLY_ENTRY = (
    "Эта QR-точка предназначена только для входа. Вход у вас уже отмечен — "
    "чтобы уйти, отсканируйте QR-код выхода."
)
ONLY_EXIT = (
    "Эта QR-точка предназначена только для выхода. Сначала отметьте вход."
)


def outcome(result: dict) -> str:
    """Ответ backend -> сообщение человеку.

    Время, офис и расстояние берутся ТОЛЬКО из ответа: часы телефона и
    его представление о том, где он находится, сюда не попадают.
    """
    status = str(result.get("status") or "")
    head = _head(status, result)
    if head is None:
        return SCAN_FAILED

    lines = [f"<b>{head}</b>"]
    # Названия офиса и точки заводит администратор в CRM: это чужой текст,
    # а сообщение уходит с HTML-разметкой.
    where = escape(result.get("office_name"))
    point = escape(result.get("point_name"))
    if status in ("ENTERED", "EXITED"):
        if where:
            lines.append(f"Офис: {where}")
        if point:
            lines.append(f"QR-точка: {point}")
        metres = _metres(result.get("distance_m"))
        if metres is not None:
            lines.append(f"Расстояние до офиса: {metres} м")
        at = result.get("occurred_at_local")
        if at:
            lines.append(f"Время: {escape(at)}")
    elif where:
        lines.append(f"{where}, {point}" if point else where)

    session = result.get("session") or {}
    seconds = session.get("duration_seconds")
    if status == "EXITED" and isinstance(seconds, int) and seconds > 0:
        lines.append(f"Сегодня в офисе: {duration(seconds)}")

    return "\n".join(lines)


def _head(status: str, result: dict) -> str | None:
    """Первая строка ответа. Для части отказов — с числами из ответа."""
    mode = result.get("point_mode")
    if status == "OUTSIDE_GEOFENCE":
        metres = _metres(result.get("distance_m"))
        radius = result.get("radius_m")
        if metres is not None and isinstance(radius, int) and radius > 0:
            return f"Вы слишком далеко от офиса: {metres} м. Допустимо: {radius} м"
    if status == "ALREADY_INSIDE" and mode == "ENTRY":
        return ONLY_ENTRY
    if status == "NOT_INSIDE" and mode == "EXIT":
        return ONLY_EXIT
    return OUTCOMES.get(status)


def _metres(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


def duration(seconds: int) -> str:
    """«8 ч 24 мин». Единственная арифметика в этом модуле."""
    hours, minutes = divmod(seconds // 60, 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


#: Печатный QR: бот просит геопозицию, потому что ссылка её не несёт.
ASK_LOCATION = (
    "Отметка по QR-коду офиса. "
    "Отправьте геопозицию кнопкой ниже — сверим её с расположением офиса. "
    "Отметка возможна только рядом с офисом."
)
LOCATION_EXPIRED = (
    "Прошло слишком много времени после сканирования. "
    "Отсканируйте QR-код ещё раз."
)
LOCATION_FORWARDED = (
    "Нужна ваша текущая геопозиция, а не пересланная. "
    "Нажмите кнопку «Отправить геопозицию»."
)
LOCATION_CANCELLED = "Отметка отменена."


__all__ = [
    "ACCESS_MESSAGES",
    "ASK_LOCATION",
    "LOCATION_CANCELLED",
    "LOCATION_EXPIRED",
    "LOCATION_FORWARDED",
    "ONLY_ENTRY",
    "ONLY_EXIT",
    "OUTCOMES",
    "PAYLOAD_REJECTED",
    "SCAN_FAILED",
    "duration",
    "outcome",
]
