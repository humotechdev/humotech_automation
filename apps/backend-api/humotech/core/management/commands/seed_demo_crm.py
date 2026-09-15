"""Демонстрационный стенд CRM: компания, прожившая в системе месяц.

Отличие от соседних команд. `seed` наполняет справочники, без которых
система вообще не работает. `demo_data` заводит ОДИН пример: организацию,
офис, сотрудника, кадровика. Эта команда заводит ВИТРИНУ — связанную
историю, по которой видно работающую компанию: двенадцать офисов,
248 человек, шесть графиков, месяц отметок, очереди заявок, обращения,
уведомления и настоящие файлы документов.

Четыре правила, на которых всё держится.

**Агрегаты не записываются.** Ни одно число главной страницы здесь не
сохраняется. Команда заводит исходные записи — назначения, графики,
события отметок, сессии, отсутствия, — а дашборд, посещаемость и
аналитика считают по ним сами, теми же правилами, что и в бою. Поэтому
расхождение между витриной и логикой невозможно: изменится правило —
изменятся и числа.

**Повторный запуск не размножает.** Всё, что команда создаёт, помечено
табельными номерами с приставками `DEMO-E-` и `DEMO-S-`. Свои же записи
она при повторе переписывает, а чужие не трогает и не удаляет. Второй
запуск даёт ту же базу, что и первый — вплоть до минут в отметках:
случайность берётся от табельного номера и даты, а не от часов.

**Опорный день — рабочий.** Витрина показывает картину одного дня, и
этот день обязан быть рабочим по графику. В выходной картина встаёт на
последний рабочий день, а сам выходной остаётся выходным: смена в
субботу сделала бы выходной «рабочим днём с явкой».

**В бою не работает.** Команда отказывается запускаться на боевых
настройках — без исключений и без ключа, который это обходит.
"""

from __future__ import annotations

import random
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone as django_timezone

from humotech.absences.models import (
    AbsenceAction,
    AbsenceDocument,
    AbsenceRequest,
    AbsenceType,
    EmployeeAbsence,
)
from humotech.accounts.models import User
from humotech.attendance.models import (
    AttendanceCorrectionRequest,
    AttendanceEvent,
    AttendanceSession,
)
from humotech.core.demo import queues
from humotech.core.demo.catalog import (
    DEPARTMENTS,
    EMPLOYEE_PREFIX,
    EMPLOYEE_PREFIXES,
    FEMALE_NAMES,
    GEOFENCE_RADIUS_M,
    HABITS,
    MAIN_SCHEDULE,
    MALE_NAMES,
    OFFICES,
    PATRONYMICS,
    POSITIONS,
    PREFIX,
    QR_POINTS,
    SCHEDULES,
    SHOWCASE,
    SHOWCASE_PREFIX,
    SURNAMES,
    DemoOffice,
    DemoSchedule,
    Habit,
    Scenario,
)
from humotech.departments.models import Department
from humotech.employees.models import Employee, EmployeeAssignment, EmployeeDocument
from humotech.files.models import File
from humotech.notifications.models import Notification
from humotech.offices.models import Office
from humotech.organizations.models import Organization
from humotech.positions.models import Position
from humotech.qr_codes.models import OfficeQrPoint
from humotech.questions.models import EmployeeQuestion
from humotech.regions.models import Region
from humotech.schedules.models import (
    EmployeeScheduleAssignment,
    ScheduleBreak,
    ScheduleDay,
    WorkSchedule,
)
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation

try:  # zoneinfo есть в стандартной библиотеке с 3.9
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

# Настройки, на которых команда работать не станет ни при каких ключах.
FORBIDDEN_SETTINGS = "config.settings.production"

# Настройки стендов. На них витрина уместна и без подтверждения: у
# `test` и `e2e` намеренно выключен DEBUG, и требовать там ключ значило
# бы усложнять запуск ровно в тех средах, ради которых команда и нужна.
STAND_SETTINGS = (
    "config.settings.development",
    "config.settings.local",
    "config.settings.test",
    "config.settings.e2e",
)

# Сколько дней истории заводить.
#
# Шестьдесят два, а не тридцать: переключатель «Месяц» показывает месяц
# И предыдущий месяц для сравнения. С тридцатью днями пунктир сравнения
# оказывался пустым ровно в том режиме, ради которого он нужен.
HISTORY_DAYS = 62

#: Сколько человек принято за последний месяц. У них истории отметок
#: меньше — она начинается со дня приёма, а не за два месяца до него.
RECENT_HIRES = 10

#: Переводы между отделами и между офисами внутри последнего месяца.
DEPARTMENT_MOVES = 6
OFFICE_MOVES = 2
#: Назначения, начинающиеся в будущем: перевод уже подписан, но ещё не начался.
FUTURE_MOVES = 3
#: Уволенные и приостановленные — сверх 248 активных.
TERMINATED = 3
SUSPENDED = 2


def stage_day(today: date, working: frozenset[int]) -> date:
    """Опорный день витрины: последний рабочий день графика не позже `today`.

    Витрина — картина одного дня, и этот день обязан быть рабочим.
    Смена в выходной сделала бы выходной «рабочим днём с явкой», и график
    перестал бы отличать выходной от провала явки.

    Правило берётся из графика, а не из календаря: если витрине когда-то
    назначат шестидневку, опорный день поедет за ней сам. Семи шагов
    хватает любому графику, в котором есть хоть один рабочий день;
    пустой набор — отказ, а не «поставим куда-нибудь».

    Шаг только назад. Вперёд нельзя: смена в завтрашнем дне — это смена
    в будущем, и её отвергает та же проверка, что и всю выдуманную явку.
    """
    for back in range(7):
        day = today - timedelta(days=back)
        if day.isoweekday() in working:
            return day
    raise CommandError(
        "В графике витрины нет ни одного рабочего дня — ставить смены некуда."
    )


def demo_rng(*parts) -> random.Random:
    """Случайность, привязанная к предмету, а не к порядку вызовов.

    Один общий генератор дал бы разные числа при малейшей перестановке
    кода. Здесь зерно собирается из табельного номера и даты: у одного и
    того же человека в один и тот же день всегда одно и то же время
    прихода, сколько бы раз команду ни запускали.
    """
    return random.Random(":".join(str(part) for part in parts))


@dataclass
class Person:
    """Один сотрудник витрины со всем, что о нём нужно знать посеву."""

    employee: Employee
    spec: DemoOffice
    index: int
    schedule: DemoSchedule
    schedule_row: WorkSchedule
    habit: Habit
    hire: date
    #: Состояние на опорный день: in_office, left, not_come, vacation, sick.
    state: str
    scenario: Scenario | None
    #: История офисов по возрастанию даты. Перевод между офисами меняет и
    #: офис отметки, и часовой пояс дня — поэтому офис берётся на дату.
    offices: list[tuple[date, Office]] = field(default_factory=list)
    #: День, в который у человека осталась незакрытая смена. Заполняется
    #: только сценарию «незакрытое посещение»: база держит по одной
    #: открытой сессии на человека, и случайная в прошлом отняла бы
    #: место у сегодняшней.
    stale_day: date | None = None

    @property
    def office(self) -> Office:
        return self.offices[-1][1]

    def office_on(self, day: date) -> Office:
        """Офис, в котором человек работал в этот день.

        Не «текущий»: у переведённого отметки до перевода принадлежат
        прежнему офису, и приписать их новому значило бы нарисовать
        явку там, где человека не было.
        """
        chosen = self.offices[0][1]
        for since, office in self.offices:
            if since <= day:
                chosen = office
        return chosen


class Command(BaseCommand):
    help = "Заводит демонстрационную витрину CRM: офисы, людей, месяц отметок и очереди"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--code", default=PREFIX, help="код организации")
        parser.add_argument(
            "--yes", action="store_true",
            help="подтвердить запуск при выключенном DEBUG вне боевых настроек",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        self._guard(options["yes"])

        code = options["code"]
        org = Organization.objects.filter(code=code).first()
        if org is None:
            raise CommandError(
                f"Организация {code} не найдена. Сначала `demo_data --code {code}`: "
                "эта команда наполняет витрину, а не создаёт организацию с нуля."
            )

        tz = self._zone(org.default_timezone)
        now = django_timezone.now().astimezone(tz)
        today = now.date()

        schedules = self._schedules(org)
        working = self._working_weekdays(schedules[MAIN_SCHEDULE.code])
        # Один опорный день на смены, события, отсутствия и очереди:
        # витрина показывает ОДИН день, а не три соседних вперемешку.
        stage = stage_day(today, working)

        regions, offices, points = self._offices(org)
        departments = self._departments(org, offices)
        positions = self._positions(org)
        types = self._absence_types(org)

        people = self._people(org, offices, departments, positions, schedules, stage)
        self._clear_demo_activity(org, people)
        self._assignments(org, people, departments, positions, offices, stage)
        self._retired(org, offices, departments, positions, schedules, stage)
        self._sweep(org, people)

        absences = queues.absences(org, people, types, stage, tz, self._reviewer(org))
        self._attendance(org, people, points, absences, stage, tz)
        queues.corrections(org, people, stage, tz, self._reviewer(org))
        queues.telegram(org, people, now, self._reviewer(org))
        queues.questions(org, people, now, self._reviewer(org))
        queues.notifications(org, people, now)
        papers = queues.papers(org, people, absences, stage, self._reviewer(org))
        portraits = queues.attach_photos(org, people, self._reviewer(org))

        self._report(org, offices, people, today, stage, papers, portraits)

    # --- защита ------------------------------------------------------------

    def _guard(self, confirmed: bool) -> None:
        """Боевые настройки отсекаются до всего остального.

        Отказ на production не снимается ключом намеренно: ключ существует
        для стенда с выключенным DEBUG, а не для того, чтобы витрину можно
        было залить в рабочую базу «если очень надо».
        """
        module = getattr(settings, "SETTINGS_MODULE", "") or ""
        if module == FORBIDDEN_SETTINGS:
            raise CommandError(
                "Боевые настройки. Демонстрационные данные в рабочей базе "
                "не заводятся ни при каких ключах."
            )
        if settings.DEBUG or module in STAND_SETTINGS:
            return
        if not confirmed:
            raise CommandError(
                f"Незнакомые настройки ({module or 'не заданы'}) и DEBUG "
                "выключен. Если это всё-таки стенд, повторите с --yes."
            )

    @staticmethod
    def _zone(name: str):
        if ZoneInfo is None:  # pragma: no cover
            return django_timezone.get_current_timezone()
        try:
            return ZoneInfo(name)
        except Exception:  # pragma: no cover - неизвестный пояс в настройках
            return django_timezone.get_current_timezone()

    # --- справочники -------------------------------------------------------

    def _offices(self, org) -> tuple[dict, dict, dict]:
        regions: dict[str, Region] = {}
        offices: dict[str, Office] = {}
        points: dict[uuid.UUID, list[OfficeQrPoint]] = defaultdict(list)

        for item in OFFICES:
            region, _ = Region.objects.update_or_create(
                organization=org, code=f"{PREFIX}-R-{item.code}",
                defaults={"name": item.region, "status": "ACTIVE"},
            )
            office, _ = Office.objects.update_or_create(
                organization=org, code=f"{PREFIX}-O-{item.code}",
                defaults={
                    "region": region,
                    "name": item.name,
                    "address": f"г. {item.city}, {item.address}",
                    "timezone": org.default_timezone,
                    "status": "ACTIVE",
                    # Координаты центра города: по ним офис стоит на карте
                    # сети. Без них маркер пришлось бы ставить по названию
                    # региона, то есть наугад.
                    "latitude": item.latitude,
                    "longitude": item.longitude,
                    "geofence_radius_m": (
                        GEOFENCE_RADIUS_M if item.latitude is not None else None
                    ),
                },
            )
            # Отметка приходит с точки на двери: событие QR без точки
            # база не принимает, и правильно — «отсканировал, но негде»
            # не бывает. У крупного офиса точек три, у остальных одна.
            wanted = QR_POINTS if item.big else QR_POINTS[:1]
            for suffix, name, direction in wanted:
                point, _ = OfficeQrPoint.objects.update_or_create(
                    organization=org, office=office,
                    code=f"{PREFIX}-Q-{item.code}-{suffix}",
                    defaults={
                        "name": name,
                        "direction_mode": direction,
                        "qr_mode": "ROTATING",
                        "rotation_seconds": 30,
                    },
                )
                points[office.id].append(point)

            # Точки прошлых запусков под старым кодом без суффикса
            # остались бы висеть рядом. Они свои же — переименовываются,
            # а не удаляются: на них могут ссылаться прежние события.
            OfficeQrPoint.objects.filter(
                organization=org, office=office, code=f"{PREFIX}-Q-{item.code}"
            ).update(code=f"{PREFIX}-Q-{item.code}-MAIN-OLD", name="Архивная точка")

            regions[item.code] = region
            offices[item.code] = office

        # Офисы демонстрационной организации, не входящие в витрину,
        # выводятся из состава. Не удаляются: строка остаётся на месте со
        # статусом INACTIVE, и вернуть её — одно поле.
        retired = Office.objects.filter(
            organization=org, status="ACTIVE"
        ).exclude(code__in=[f"{PREFIX}-O-{item.code}" for item in OFFICES])
        names = list(retired.values_list("name", flat=True))
        if names:
            retired.update(status="INACTIVE")
            self.stdout.write(
                "Вне витрины, переведены в INACTIVE: " + ", ".join(names)
            )
        return regions, offices, dict(points)

    def _departments(self, org, offices: dict) -> dict:
        """Отделы по офисам.

        Не все отделы есть везде: юридический, комплаенс и внутренний
        контроль живут только в головном офисе, финансы и маркетинг — в
        крупных. Одинаковый набор в каждом офисе выглядел бы сеткой,
        а не компанией.
        """
        departments: dict[tuple[str, str], Department] = {}
        for item in OFFICES:
            for code, name, scope in DEPARTMENTS:
                if scope == "hq" and item.code != OFFICES[0].code:
                    continue
                if scope == "big" and not item.big:
                    continue
                row, _ = Department.objects.update_or_create(
                    organization=org, code=f"{PREFIX}-D-{item.code}-{code}",
                    defaults={
                        "office": offices[item.code],
                        "name": name,
                        "status": "ACTIVE",
                    },
                )
                departments[(item.code, code)] = row
        return departments

    def _positions(self, org) -> list[Position]:
        rows = []
        for code, name, weight in POSITIONS:
            row, _ = Position.objects.update_or_create(
                organization=org, code=f"{PREFIX}-P-{code}",
                defaults={"name": name, "status": "ACTIVE"},
            )
            # Вес хранится не в базе, а рядом: должность в модели — это
            # просто название, а частота нужна только посеву.
            rows.extend([row] * weight)
        return rows

    def _schedules(self, org) -> dict[str, WorkSchedule]:
        """Шесть графиков витрины вместо одного.

        Разные графики нужны не для разнообразия: аналитика считает
        норму по графику КАЖДОГО сотрудника, и при одном графике на всех
        «ожидалось» было бы одним и тем же числом каждый рабочий день.
        """
        rows: dict[str, WorkSchedule] = {}
        for item in SCHEDULES:
            schedule, _ = WorkSchedule.objects.update_or_create(
                organization=org, name=item.name,
                defaults={
                    "timezone": org.default_timezone,
                    "weekly_minutes": item.weekly_minutes,
                    "late_grace_minutes": item.late_grace,
                    "early_leave_grace_minutes": item.early_leave_grace,
                    "is_flexible": item.flexible,
                    "status": "ACTIVE",
                },
            )
            for weekday in range(1, 8):
                working = weekday in item.weekdays
                day, _ = ScheduleDay.objects.update_or_create(
                    schedule=schedule, weekday=weekday,
                    defaults={
                        "is_working_day": working,
                        "start_time": item.start if working else None,
                        "end_time": item.end if working else None,
                    },
                )
                day.breaks.all().delete()
                if working and item.lunch is not None:
                    ScheduleBreak.objects.create(
                        schedule_day=day,
                        name="Обед",
                        start_time=item.lunch[0],
                        end_time=item.lunch[1],
                        is_paid=False,
                    )
            rows[item.code] = schedule
        return rows

    @staticmethod
    def _working_weekdays(schedule: WorkSchedule) -> frozenset[int]:
        """Рабочие дни графика: 1 — понедельник, 7 — воскресенье.

        Читаются у графика, а не задаются числом рядом: иначе у «пяти
        рабочих дней» появилось бы второе определение, и однажды они
        разошлись бы.
        """
        days = frozenset(
            row.weekday for row in schedule.days.all() if row.is_working_day
        )
        if not days:
            raise CommandError(
                f"В графике «{schedule.name}» нет рабочих дней: витрине "
                "некуда ставить смены."
            )
        return days

    def _absence_types(self, org) -> dict[str, AbsenceType]:
        wanted = (
            ("ANNUAL_LEAVE", "Ежегодный отпуск", True, False, True),
            ("SICK_LEAVE", "Больничный", True, True, False),
            ("UNPAID_LEAVE", "Отпуск за свой счёт", False, False, False),
        )
        rows: dict[str, AbsenceType] = {}
        for code, name, paid, needs_doc, deducts in wanted:
            row, _ = AbsenceType.objects.update_or_create(
                organization=org, code=code,
                defaults={
                    "name": name,
                    "is_paid": paid,
                    "requires_approval": True,
                    "requires_document": needs_doc,
                    "deducts_leave_balance": deducts,
                    "is_active": True,
                },
            )
            # Больничный без требования справки лишил бы смысла очередь
            # «ожидаем справку»: требование — часть витрины.
            if row.requires_document != needs_doc:
                row.requires_document = needs_doc
                row.save(update_fields=["requires_document"])
            rows[code] = row
        return rows

    # --- люди --------------------------------------------------------------

    def _people(self, org, offices, departments, positions, schedules, stage) -> list[Person]:
        """248 человек: анкета, график, привычка и состояние опорного дня.

        Всё собирается детерминированно от табельного номера: повторный
        запуск даёт того же человека под тем же номером — с тем же полом,
        тем же телефоном и тем же графиком.
        """
        by_state = self._states()
        people: list[Person] = []
        number = 0

        for item in OFFICES:
            scenarios = [one for one in SHOWCASE if one.office == item.code]
            queue = dict(by_state[item.code])
            for one in scenarios:
                queue[one.state] -= 1

            # Состояния рядовых слотов раздаются по порядку, а не
            # случайно: при повторном запуске состав каждой группы обязан
            # совпасть, иначе «повтор ничего не меняет» перестанет быть
            # правдой.
            plain_states: list[str] = []
            for state, left in queue.items():
                if left < 0:
                    raise CommandError(
                        f"В офисе {item.code} сценариев в состоянии «{state}» "
                        "больше, чем мест: состав витрины перестал сходиться."
                    )
                plain_states.extend([state] * left)
            slots: list[Scenario | None] = list(scenarios) + [None] * len(plain_states)

            plain_at = 0
            for slot in slots:
                if slot is None:
                    number += 1
                    state = plain_states[plain_at]
                    plain_at += 1
                    employee_number = f"{EMPLOYEE_PREFIX}{number:04d}"
                else:
                    state = slot.state
                    employee_number = slot.number

                person = self._person(
                    org, item, slot, employee_number, state, schedules, stage,
                    offices, len(people),
                )
                people.append(person)

        return people

    @staticmethod
    def _states() -> dict[str, dict[str, int]]:
        return {
            item.code: {
                "vacation": item.vacation,
                "sick": item.sick,
                "not_come": item.not_come,
                "left": item.left,
                "in_office": item.in_office,
            }
            for item in OFFICES
        }

    def _person(
        self, org, item, scenario, employee_number, state, schedules, stage,
        offices, order,
    ) -> Person:
        rng = demo_rng(employee_number)
        female = scenario.female if scenario else rng.random() < 0.45
        first, last, middle = self._name(rng, female)

        hire = self._hire_date(rng, stage, order)
        birth = stage - timedelta(days=rng.randint(21 * 365, 58 * 365))

        schedule_code = scenario.schedule if scenario else self._schedule_code(rng)
        schedule = next(one for one in SCHEDULES if one.code == schedule_code)
        habit = self._habit(rng, scenario)

        employee, _ = Employee.objects.update_or_create(
            organization=org, employee_number=employee_number,
            defaults={
                "first_name": first,
                "last_name": last,
                "middle_name": middle,
                "gender": "FEMALE" if female else "MALE",
                "marital_status": self._marital(rng, birth, stage),
                "birth_date": birth,
                "hire_date": hire,
                "employment_status": "ACTIVE",
                "preferred_language": "ru",
                "phone": self._phone(order),
                "corporate_email": self._email(first, last, order),
                "personal_email": None,
                "telegram_connected": False,
            },
        )

        person = Person(
            employee=employee,
            spec=item,
            index=order,
            schedule=schedule,
            schedule_row=schedules[schedule_code],
            habit=habit,
            hire=hire,
            state=state,
            scenario=scenario,
        )
        person.offices = [(hire, offices[item.code])]
        if scenario is not None and scenario.number.endswith("09"):
            # Шесть дней назад человек ушёл, не отметившись, и смена так
            # и осталась открытой. Этот день выбран, а не выпал: случайная
            # открытая сессия в прошлом заняла бы единственное место,
            # которое база держит под открытую смену.
            person.stale_day = _back_to_working(stage - timedelta(days=6), schedule)
        if scenario is not None and scenario.moved_from:
            moved = stage - timedelta(days=scenario.moved_days_ago)
            person.offices = [
                (hire, offices[scenario.moved_from]),
                (moved, offices[item.code]),
            ]
        return person

    @staticmethod
    def _hire_date(rng, stage: date, order: int) -> date:
        """Когда человека приняли.

        Десять человек приняты за последний месяц: у них история отметок
        начинается со дня приёма, и в списке сотрудников видно, что
        компания растёт. Остальные работают давно.
        """
        if order < RECENT_HIRES:
            return stage - timedelta(days=rng.randint(3, 28))
        return stage - timedelta(days=rng.randint(200, 2600))

    @staticmethod
    def _name(rng, female: bool) -> tuple[str, str, str]:
        first = rng.choice(FEMALE_NAMES if female else MALE_NAMES)
        surname = rng.choice(SURNAMES)[1 if female else 0]
        middle = rng.choice(PATRONYMICS)[1 if female else 0]
        return first, surname, middle

    @staticmethod
    def _marital(rng, birth: date, stage: date) -> str:
        """Семейное положение, согласованное с возрастом.

        У двадцатилетних «вдовец» встречается настолько редко, что в
        витрине это выглядело бы ошибкой данных, а не редкостью.
        """
        years = (stage - birth).days // 365
        if years < 25:
            return rng.choice(["SINGLE", "SINGLE", "MARRIED"])
        if years < 40:
            return rng.choice(["MARRIED", "MARRIED", "MARRIED", "SINGLE", "DIVORCED"])
        return rng.choice(["MARRIED", "MARRIED", "DIVORCED", "SINGLE", "WIDOWED"])

    @staticmethod
    def _schedule_code(rng) -> str:
        """Какой график достался. Большинство — на основной пятидневке."""
        pool: list[str] = []
        for item in SCHEDULES:
            pool.extend([item.code] * item.share)
        return rng.choice(pool)

    @staticmethod
    def _habit(rng, scenario) -> Habit:
        if scenario is not None:
            return next(one for one in HABITS if one.code == scenario.habit)
        pool: list[Habit] = []
        for item in HABITS:
            pool.extend([item] * item.share)
        return rng.choice(pool)

    @staticmethod
    def _phone(order: int) -> str:
        """Телефон из диапазона, зарезервированного под примеры.

        Номера не повторяются по построению: последние шесть цифр — это
        порядковый номер, а не случайное число, которое однажды совпало
        бы дважды.
        """
        return f"+998 90 {900 + order // 100:03d}-{(order % 100):02d}-01"

    @staticmethod
    def _email(first: str, last: str, order: int) -> str:
        return f"{_latin(first)}.{_latin(last)}{order:03d}@humotech.demo"

    # --- назначения и история ----------------------------------------------

    def _assignments(self, org, people, departments, positions, offices, stage) -> None:
        """Назначения: текущее, история переводов и будущие.

        Старое назначение закрывается за день до начала нового. Иначе у
        человека оказалось бы два действующих одновременно, и список
        показывал бы его в одном офисе, а карточка — в другом.
        """
        EmployeeAssignment.objects.filter(
            employee__in=[one.employee for one in people]
        ).delete()

        rows: list[EmployeeAssignment] = []
        for person in people:
            rng = demo_rng("assignment", person.employee.employee_number)
            position = positions[rng.randrange(len(positions))]
            employment = self._employment_type(person)

            periods = list(person.offices)
            for at, (since, office) in enumerate(periods):
                code = self._office_code(office)
                department = self._department_for(departments, code, rng)
                ends = None
                if at + 1 < len(periods):
                    ends = periods[at + 1][0] - timedelta(days=1)
                rows.append(EmployeeAssignment(
                    organization=org,
                    employee=person.employee,
                    office=office,
                    department=department,
                    position=position,
                    employment_type=employment,
                    work_mode="ONSITE",
                    is_primary=True,
                    valid_from=since,
                    valid_to=ends,
                ))

        # Переводы между отделами внутри последнего месяца: назначение
        # рвётся надвое, и в истории карточки видно прежний отдел.
        movable = [
            one for one in people
            if one.scenario is None and len(one.offices) == 1
            and one.hire < stage - timedelta(days=40)
        ]
        for at in range(DEPARTMENT_MOVES):
            person = movable[at * 17 % len(movable)]
            moved = stage - timedelta(days=8 + at * 3)
            code = self._office_code(person.office)
            rng = demo_rng("move", person.employee.employee_number)
            for row in rows:
                if row.employee_id == person.employee.id and row.valid_to is None:
                    row.valid_to = moved - timedelta(days=1)
                    rows.append(EmployeeAssignment(
                        organization=org,
                        employee=person.employee,
                        office=person.office,
                        department=self._department_for(departments, code, rng, skip=row.department_id),
                        position=row.position,
                        employment_type=row.employment_type,
                        work_mode="ONSITE",
                        is_primary=True,
                        valid_from=moved,
                        valid_to=None,
                    ))
                    break

        # Назначения, начинающиеся в будущем: перевод подписан, но ещё не
        # начался. Текущее закрывается накануне — иначе два действующих.
        for at in range(FUTURE_MOVES):
            person = movable[(at * 29 + 7) % len(movable)]
            starts = stage + timedelta(days=7 + at * 4)
            code = self._office_code(person.office)
            rng = demo_rng("future", person.employee.employee_number)
            for row in rows:
                if row.employee_id == person.employee.id and row.valid_to is None:
                    row.valid_to = starts - timedelta(days=1)
                    rows.append(EmployeeAssignment(
                        organization=org,
                        employee=person.employee,
                        office=person.office,
                        department=self._department_for(departments, code, rng, skip=row.department_id),
                        position=row.position,
                        employment_type=row.employment_type,
                        work_mode="ONSITE",
                        is_primary=True,
                        valid_from=starts,
                        valid_to=None,
                    ))
                    break

        EmployeeAssignment.objects.bulk_create(rows, batch_size=500)

        EmployeeScheduleAssignment.objects.filter(
            employee__in=[one.employee for one in people]
        ).delete()
        EmployeeScheduleAssignment.objects.bulk_create(
            [
                EmployeeScheduleAssignment(
                    organization=org,
                    employee=person.employee,
                    schedule=person.schedule_row,
                    valid_from=person.hire,
                    valid_to=None,
                )
                for person in people
            ],
            batch_size=500,
        )

    @staticmethod
    def _employment_type(person: Person) -> str:
        if person.schedule.code == "PART":
            return "PART_TIME"
        return "FULL_TIME"

    @staticmethod
    def _office_code(office: Office) -> str:
        return office.code.rsplit("-", 1)[-1]

    @staticmethod
    def _department_for(departments, code: str, rng, skip=None) -> Department:
        pool = [row for (office_code, _), row in departments.items() if office_code == code]
        if skip is not None and len(pool) > 1:
            pool = [row for row in pool if row.id != skip]
        return pool[rng.randrange(len(pool))]

    def _retired(self, org, offices, departments, positions, schedules, stage) -> None:
        """Уволенные и приостановленные — сверх активного состава.

        Они нужны вкладке «Уволенные» и фильтру статусов, но в 248
        активных не входят: иначе состав на экране перестал бы сходиться
        с таблицей офисов.
        """
        for at in range(TERMINATED + SUSPENDED):
            terminated = at < TERMINATED
            number = f"{EMPLOYEE_PREFIX}9{at:03d}"
            rng = demo_rng(number)
            female = rng.random() < 0.45
            first, last, middle = self._name(rng, female)
            hire = stage - timedelta(days=rng.randint(400, 1800))
            item = OFFICES[at % len(OFFICES)]
            employee, _ = Employee.objects.update_or_create(
                organization=org, employee_number=number,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "middle_name": middle,
                    "gender": "FEMALE" if female else "MALE",
                    "marital_status": self._marital(rng, stage - timedelta(days=30 * 365), stage),
                    "birth_date": stage - timedelta(days=rng.randint(25 * 365, 55 * 365)),
                    "hire_date": hire,
                    "employment_status": "TERMINATED" if terminated else "SUSPENDED",
                    "termination_date": stage - timedelta(days=rng.randint(5, 90)) if terminated else None,
                    "preferred_language": "ru",
                    "phone": f"+998 90 999-{at:02d}-01",
                    "corporate_email": f"{_latin(first)}.{_latin(last)}9{at:02d}@humotech.demo",
                    "telegram_connected": False,
                },
            )
            EmployeeAssignment.objects.filter(employee=employee).delete()
            EmployeeAssignment.objects.create(
                organization=org,
                employee=employee,
                office=offices[item.code],
                department=self._department_for(departments, item.code, rng),
                position=positions[rng.randrange(len(positions))],
                employment_type="FULL_TIME",
                work_mode="ONSITE",
                is_primary=True,
                valid_from=hire,
                valid_to=employee.termination_date,
            )
            EmployeeScheduleAssignment.objects.filter(employee=employee).delete()
            EmployeeScheduleAssignment.objects.create(
                organization=org,
                employee=employee,
                schedule=schedules[MAIN_SCHEDULE.code],
                valid_from=hire,
                valid_to=employee.termination_date,
            )

    def _sweep(self, org, people) -> None:
        """Вывести из штата своих же людей из прошлых запусков.

        Прошлый запуск мог завести другой состав: например, витрина
        уменьшилась или сменилась схема табельных номеров. Лишние — свои
        же — выводятся из штата, а не удаляются: за ними тянутся смены и
        заявки, и история не должна исчезать задним числом.

        Чужие записи не трогаются вовсе: отбор идёт только по нашим
        приставкам. Сотрудник, заведённый до витрины, остаётся активным.
        """
        known = {one.employee.id for one in people}
        known.update(
            Employee.objects.filter(
                organization=org,
                employee_number__startswith=f"{EMPLOYEE_PREFIX}9",
            ).values_list("id", flat=True)
        )
        ours = Employee.objects.none()
        for prefix in EMPLOYEE_PREFIXES:
            ours = ours | Employee.objects.filter(
                organization=org, employee_number__startswith=prefix
            )
        extra = ours.exclude(id__in=known).exclude(employment_status="TERMINATED")
        left = extra.count()
        if left:
            # Назначение закрывается вместе с увольнением: незакрытое
            # оставило бы человека в составе смены навсегда.
            EmployeeAssignment.objects.filter(
                employee__in=extra, valid_to__isnull=True
            ).update(valid_to=django_timezone.now().date())
            extra.update(employment_status="TERMINATED")
            self.stdout.write(f"Лишние из прошлого запуска выведены из штата: {left}")

    # --- очистка своих же следов -------------------------------------------

    def _clear_demo_activity(self, org, people: list[Person]) -> None:
        """Снять прошлый прогон, чтобы второй не удвоил записи.

        Удаляется только активность демонстрационных сотрудников — тех,
        чей табельный номер начинается с `DEMO-E-` или `DEMO-S-`. Чужие
        записи командой не трогаются: у неё нет ни одного удаления без
        этого условия.
        """
        ids = {one.employee.id for one in people}
        for prefix in EMPLOYEE_PREFIXES:
            ids.update(
                Employee.objects.filter(
                    organization=org, employee_number__startswith=prefix
                ).values_list("id", flat=True)
            )
        ids = list(ids)

        # Порядок здесь — не вкусовщина: на смену ссылается заявка на
        # исправление, на событие — сама смена, на файл — документ.
        # Снимать надо с конца цепочки.
        AttendanceCorrectionRequest.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        AttendanceSession.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        AttendanceEvent.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        EmployeeAbsence.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        AbsenceAction.objects.filter(
            absence_request__organization=org,
            absence_request__employee_id__in=ids,
        ).delete()
        AbsenceDocument.objects.filter(
            absence_request__organization=org,
            absence_request__employee_id__in=ids,
        ).delete()
        # Производные заявки — отмена и продление — держат исходную
        # защищённой ссылкой, поэтому снимаются первыми. Обратный
        # порядок упирается в `ProtectedError` при повторном запуске.
        AbsenceRequest.objects.filter(
            organization=org, employee_id__in=ids, parent_request__isnull=False
        ).delete()
        AbsenceRequest.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        EmployeeQuestion.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        # Документы базы знаний витрины: на них ссылались черновики
        # обращений, поэтому снимаются после самих обращений. Чужие
        # документы метки витрины не несут и не трогаются.
        from humotech.core.demo.catalog import PREFIX as DEMO_PREFIX
        from humotech.knowledge.models import KnowledgeSource

        KnowledgeSource.objects.filter(
            organization=org, meta__demo=DEMO_PREFIX
        ).delete()
        Notification.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        TelegramLinkInvitation.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        TelegramAccount.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        EmployeeDocument.objects.filter(
            organization=org, employee_id__in=ids
        ).delete()
        # Фотографии снимаются со ссылок до удаления самих файлов:
        # ключ защищённый, и файл под живой карточкой не удалится.
        Employee.objects.filter(id__in=ids).update(photo=None)
        queues.drop_files(org)

    # --- отметки -----------------------------------------------------------

    def _attendance(self, org, people, points, absences, stage, tz) -> None:
        """Месяц отметок: события входа и выхода, из них — сессии.

        Каждый день строится по графику КОНКРЕТНОГО человека: кто-то
        работает по субботам, кто-то до тринадцати. День, которого нет в
        его графике, пропускается вовсе — нуля там быть не должно, в
        этот день работать никто не был должен.

        Открытых сессий в прошлом нет ни одной, кроме одной нарочной:
        сценарий «незакрытое посещение». База держит по одной открытой
        сессии на человека, и случайная в прошлом отняла бы место у
        сегодняшней.
        """
        events: list[AttendanceEvent] = []
        sessions: list[AttendanceSession] = []
        busy = self._absent_days(absences, tz)

        for person in people:
            first = max(person.hire, stage - timedelta(days=HISTORY_DAYS))
            day = first
            while day <= stage:
                if day.isoweekday() not in person.schedule.weekdays:
                    day += timedelta(days=1)
                    continue
                if (person.employee.id, day) in busy:
                    day += timedelta(days=1)
                    continue
                if day == stage:
                    self._stage_day(org, person, points, day, tz, events, sessions)
                else:
                    self._past_day(org, person, points, day, tz, events, sessions)
                day += timedelta(days=1)

        AttendanceEvent.objects.bulk_create(events, batch_size=1000)
        AttendanceSession.objects.bulk_create(sessions, batch_size=1000)

    @staticmethod
    def _absent_days(absences, tz) -> set[tuple[uuid.UUID, date]]:
        """Дни, в которые человек законно не приходил.

        Отметка в день отпуска — противоречие: система показала бы
        человека и в офисе, и в отпуске одновременно.
        """
        busy: set[tuple[uuid.UUID, date]] = set()
        for row in absences:
            start = row.start_at.astimezone(tz).date()
            end = row.end_at.astimezone(tz).date()
            day = start
            while day <= end:
                busy.add((row.employee_id, day))
                day += timedelta(days=1)
        return busy

    def _shift(self, person: Person, day: date, tz) -> tuple[datetime, datetime]:
        """Плановые начало и конец смены этого дня."""
        return (
            datetime.combine(day, person.schedule.start).replace(tzinfo=tz),
            datetime.combine(day, person.schedule.end).replace(tzinfo=tz),
        )

    def _arrival(self, person: Person, day: date, rng) -> int:
        """Отклонение прихода от начала смены в секундах.

        Складывается из привычки человека, дисциплины офиса и дня
        недели: по понедельникам приходят чуть позже, и это видно на
        графике — ровная линия выглядела бы нарисованной.
        """
        low, high = person.habit.arrival
        minutes = rng.randint(low, high) + person.spec.discipline
        if day.isoweekday() == 1:
            minutes += rng.randint(1, 6)
        # Секунды нужны не для точности: без них у всех отметка ровно в
        # ноль секунд, и таблица выглядит машинной.
        return minutes * 60 + rng.randint(0, 59)

    def _departure(self, person: Person, day: date, rng) -> int:
        low, high = person.habit.departure
        minutes = rng.randint(low, high)
        if day.isoweekday() == 5:
            # В пятницу уходят чуть раньше.
            minutes -= rng.randint(5, 20)
        return minutes * 60 + rng.randint(0, 59)

    def _past_day(self, org, person, points, day, tz, events, sessions) -> None:
        rng = demo_rng(person.employee.employee_number, day.isoformat())
        if rng.random() * 100 < person.habit.absent_pct:
            return  # в этот день отметки нет вовсе

        start, end = self._shift(person, day, tz)
        entry = start + timedelta(seconds=self._arrival(person, day, rng))
        exit_at = end + timedelta(seconds=self._departure(person, day, rng))
        if exit_at <= entry:
            # Выход раньше входа невозможен: у очень раннего ухода
            # оставляем хотя бы час присутствия.
            exit_at = entry + timedelta(hours=1)

        if person.stale_day == day:
            # Единственная нарочно незакрытая сессия в прошлом: человек
            # ушёл, не отметившись, и запись так и осталась открытой.
            self._session(org, person, points, day, entry, None, tz, events, sessions)
            return

        self._session(org, person, points, day, entry, exit_at, tz, events, sessions)

    def _stage_day(self, org, person, points, day, tz, events, sessions) -> None:
        """Что происходит с человеком в опорный день."""
        rng = demo_rng(person.employee.employee_number, day.isoformat(), "stage")
        start, end = self._shift(person, day, tz)
        entry = start + timedelta(seconds=self._arrival(person, day, rng))
        scenario = person.scenario.number[-2:] if person.scenario else None

        if person.state == "not_come":
            return
        if person.state in ("vacation", "sick"):
            return

        if scenario == "10":
            # Два входа и два выхода: ушёл на обед и вернулся.
            self._session(org, person, points, day, entry,
                          entry + timedelta(hours=3, minutes=12), tz, events, sessions)
            back = entry + timedelta(hours=4, minutes=5)
            self._session(org, person, points, day, back,
                          back + timedelta(hours=4, minutes=20), tz, events, sessions)
            return
        if scenario == "11":
            # Три коротких посещения: разъездная работа.
            at = entry
            for span in (95, 70, 110):
                self._session(org, person, points, day, at,
                              at + timedelta(minutes=span), tz, events, sessions)
                at += timedelta(minutes=span + 55)
            return

        if person.state == "in_office":
            # Открытая сессия: человек в офисе прямо сейчас, и правого
            # края у неё нет.
            self._session(org, person, points, day, entry, None, tz, events, sessions)
            return

        exit_at = end + timedelta(seconds=self._departure(person, day, rng))
        if exit_at <= entry:
            exit_at = entry + timedelta(hours=1)
        self._session(org, person, points, day, entry, exit_at, tz, events, sessions)

    def _session(
        self, org, person, points, day, entry, exit_at, tz, events, sessions
    ) -> None:
        office = person.office_on(day)
        entry_event = self._event(org, person, office, points, entry, "ENTRY")
        events.append(entry_event)
        exit_event = None
        if exit_at is not None:
            exit_event = self._event(org, person, office, points, exit_at, "EXIT")
            events.append(exit_event)
        sessions.append(AttendanceSession(
            organization=org,
            employee=person.employee,
            office=office,
            entry_event=entry_event,
            exit_event=exit_event,
            started_at=entry,
            ended_at=exit_at,
            duration_seconds=(
                int((exit_at - entry).total_seconds()) if exit_at is not None else None
            ),
            status="CLOSED" if exit_at is not None else "OPEN",
        ))

    @staticmethod
    def _event(org, person, office, points, moment: datetime, kind: str) -> AttendanceEvent:
        # Ключ задаётся здесь, а не базой: смена ссылается на событие, и
        # при пакетной вставке ссылаться было бы не на что — у ещё не
        # сохранённой строки ключа нет.
        options = points.get(office.id) or []
        rng = demo_rng(person.employee.employee_number, moment.isoformat(), kind)
        point = options[rng.randrange(len(options))] if options else None
        # Пара отметок в день приходит из-за границы геозоны. Это не
        # нарушение: человек мог отметиться у соседнего входа или с
        # неточной геолокацией. Но очередь «требует внимания» показывает
        # именно такие, и без них проверить её нечем.
        inside = rng.random() > 0.004
        return AttendanceEvent(
            id=uuid.uuid4(),
            organization=org,
            employee=person.employee,
            office=office,
            qr_point=point,
            event_type=kind,
            source="QR",
            verification_status="ACCEPTED",
            occurred_at=moment,
            inside_geofence=inside,
            inside_office_network=inside,
        )

    # --- прочее ------------------------------------------------------------

    @staticmethod
    def _reviewer(org) -> User:
        """От чьего имени приняты решения витрины.

        Берётся первый пользователь организации. Если их нет — заводится
        технический, БЕЗ пароля: войти под ним нельзя, он нужен только
        как автор решения, на которое ссылается заявка.
        """
        existing = User.objects.filter(organization=org).order_by("created_at").first()
        if existing is not None:
            return existing
        user = User(
            organization=org,
            email=f"demo-reviewer@{org.code.lower()}.local",
            status="ACTIVE",
        )
        user.set_unusable_password()
        user.save()
        return user

    def _report(self, org, offices, people, today, stage, papers, portraits) -> None:
        self.stdout.write(self.style.SUCCESS(
            f"Витрина {org.code} на {stage.isoformat()}: "
            f"{len(offices)} офисов, {len(people)} сотрудников."
        ))
        if stage != today:
            # Молчать об этом нельзя: иначе кажется, что команда
            # отработала вхолостую.
            self.stdout.write(
                f"{today.isoformat()} — выходной по графику витрины, "
                f"смен в нём нет. Картина дня стоит на {stage.isoformat()}."
            )
        self.stdout.write(
            f"Сессий: {AttendanceSession.objects.filter(organization=org).count()}, "
            f"событий: {AttendanceEvent.objects.filter(organization=org).count()}, "
            f"заявок: {AbsenceRequest.objects.filter(organization=org).count()}, "
            f"документов: {papers}, фотографий: {portraits}."
        )
        self.stdout.write(
            "Числа главной страницы считает backend по этим записям; "
            "команда ни одного агрегата не сохраняет."
        )


def _back_to_working(day: date, schedule: DemoSchedule) -> date:
    """Ближайший рабочий день графика не позже указанного."""
    for _ in range(7):
        if day.isoweekday() in schedule.weekdays:
            return day
        day -= timedelta(days=1)
    raise CommandError("В графике нет рабочих дней")


def _latin(text: str) -> str:
    """Кириллица в латиницу для почтового адреса.

    Таблица короткая нарочно: адреса синтетические, и точность
    транслитерации здесь ничего не решает — важна только устойчивость,
    чтобы повторный запуск дал тот же адрес.
    """
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "қ": "q",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
        "т": "t", "у": "u", "ў": "u", "ф": "f", "х": "h", "ҳ": "h", "ц": "c",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
        "ю": "yu", "я": "ya",
    }
    return "".join(table.get(ch, ch) for ch in text.lower() if ch.isalpha() or ch in table)
