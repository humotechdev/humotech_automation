"""Границы суток, недели и месяца — в часовом поясе офиса.

Единственное место в проекте, где решается, что такое «сегодня».

Поясов в схеме три, и они законно разные:

  * `Office.timezone` — где физически стоит турникет. Смена «за 3 сентября»
    для сотрудника новосибирского офиса началась в 00:00 по Новосибирску,
    а не по Москве и не по UTC;
  * `WorkSchedule.timezone` — в каком поясе записаны часы самого графика
    (09:00–18:00). Он отвечает на вопрос «во сколько начинается смена»,
    но НЕ на вопрос «какие сутки считать»;
  * `Organization.default_timezone` — запасной вариант, когда офиса ещё нет.

Границей суток объявлен пояс офиса. Причина простая: сотрудник видит свою
статистику там, где он работает, и «сегодня» на экране обязано совпадать
с «сегодня» за окном. Если бы каждый экран выбирал пояс сам, личный кабинет
и история показывали бы разное число за одно и то же число месяца —
и никакой тест этого бы не поймал, потому что каждый по отдельности прав.

Пояс сервера не участвует нигде. `TIME_ZONE` в настройках — деталь
развёртывания, и переезд сервера не имеет права сдвинуть чью-то смену.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

UTC = ZoneInfo("UTC")

# Что считать началом недели. Понедельник — не настройка: производственный
# календарь и график недели в этой схеме уже описаны от понедельника
# (`ScheduleDay.weekday` по ISO-8601, 1 = понедельник).
WEEK_STARTS_ON = 0  # date.weekday(): 0 = понедельник


def zone(name: str | None) -> ZoneInfo:
    """Пояс по имени, с падением в UTC вместо исключения.

    Кривое имя пояса в справочнике — повод показать чуть смещённое время,
    но не повод уронить личный кабинет целиком. Само значение задаёт HR
    в карточке офиса, и опечатка там не должна закрывать людям доступ
    к их же данным.
    """
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def office_zone(office) -> ZoneInfo:
    """Пояс офиса, с откатом на пояс организации."""
    if office is None:
        return UTC
    own = getattr(office, "timezone", None)
    if own:
        return zone(own)
    organization = getattr(office, "organization", None)
    return zone(getattr(organization, "default_timezone", None))


def organization_zone(organization_id) -> ZoneInfo:
    """Один пояс на организацию — для экранов, у которых нет своего офиса.

    Список учётных записей, каталог ролей и журнал действий охватывают всю
    организацию сразу, и «свой» офис у строки не определён. Час при этом
    показать надо один и тот же для всех, кто смотрит: иначе один и тот же
    вход в систему выглядит произошедшим в разное время у двух людей,
    открывших журнал из разных городов.

    Порядок такой. Сначала явно выбранный пояс отображения CRM
    (`organization.defaults.crm_timezone`): его задают на странице
    настроек ровно для этого. Если он не задан — пояс первого
    заведённого офиса, то же правило, по которому считает сутки список
    уведомлений. Если офисов нет — `Organization.default_timezone`.

    Это пояс ПОКАЗА. Отметки, графики и рабочие дни считаются по поясу
    ОФИСА (`office_zone`) и от этой настройки не зависят: смена пояса
    отображения не переписывает события и не пересчитывает историю.
    """
    from humotech.offices.models import Office
    from humotech.organizations.models import Organization, OrganizationSetting

    chosen = (
        OrganizationSetting.objects.filter(
            organization_id=organization_id, key="organization.defaults"
        )
        .values_list("value", flat=True)
        .first()
    )
    if isinstance(chosen, dict):
        named = chosen.get("crm_timezone")
        if isinstance(named, str) and named.strip():
            return zone(named)

    office = (
        Office.objects.filter(organization_id=organization_id)
        .exclude(timezone="")
        .order_by("created_at")
        .first()
    )
    if office is not None:
        return office_zone(office)
    organization = Organization.objects.filter(id=organization_id).first()
    return zone(getattr(organization, "default_timezone", None))


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    """Какое число было в этом поясе в указанный момент."""
    return moment.astimezone(tz).date()


def today(tz: ZoneInfo, *, now: datetime | None = None) -> date:
    return local_date(now or timezone.now(), tz)


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Полуинтервал [начало суток, начало следующих) в UTC.

    Именно полуинтервал, а не «с 00:00 до 23:59:59»: отметка в 23:59:59.7
    иначе не попала бы ни в один день.

    Полночь в поясе может не существовать вовсе — при переводе часов вперёд
    сутки начинаются в 01:00. Поэтому нижняя граница берётся не как
    «00:00 этого дня», а как «полночь, приведённая к реальному времени»:
    `astimezone` разложит несуществующий момент в ближайший существующий.
    """
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def range_bounds(first: date, last: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Полуинтервал от начала первого дня до начала дня после последнего.

    `last` включается целиком — период задают датами, и «по 30 сентября»
    для человека означает вместе с тридцатым.
    """
    if last < first:
        raise ValueError("конец периода раньше начала")
    start, _ = day_bounds(first, tz)
    _, end = day_bounds(last, tz)
    return start, end


def closed_range_bounds(
    first: date, last: date, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """Границы периода, у которого конец ВКЛЮЧЁН в период.

    Нужны там, где момент времени хранится как «по такое-то число»:
    `employee_absences.end_at` — именно такое поле, и читают его через
    `local_date(end_at)`. Полуинтервал там дал бы начало СЛЕДУЮЩИХ суток,
    то есть лишний день в каждом больничном и в каждом отпуске.

    Две разные функции вместо одной — не дублирование: у выборок событий
    конец исключается (иначе отметка в 00:00:00 попадёт в оба дня), а у
    периодов отсутствия включается. Свести их к одной значило бы выбрать
    неверно в одном из двух мест.
    """
    start, end = range_bounds(first, last, tz)
    return start, end - timedelta(microseconds=1)


def week_range(day: date) -> tuple[date, date]:
    """Понедельник и воскресенье недели, в которую попал день."""
    first = day - timedelta(days=(day.weekday() - WEEK_STARTS_ON) % 7)
    return first, first + timedelta(days=6)


def month_range(day: date) -> tuple[date, date]:
    """Первое и последнее число месяца, в который попал день."""
    first = day.replace(day=1)
    if first.month == 12:
        next_first = first.replace(year=first.year + 1, month=1)
    else:
        next_first = first.replace(month=first.month + 1)
    return first, next_first - timedelta(days=1)


def days_in(first: date, last: date):
    """Все даты периода включительно."""
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


__all__ = [
    "UTC",
    "closed_range_bounds",
    "day_bounds",
    "days_in",
    "local_date",
    "month_range",
    "office_zone",
    "range_bounds",
    "today",
    "week_range",
    "zone",
]
