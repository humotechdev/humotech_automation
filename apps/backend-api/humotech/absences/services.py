"""Больничные и отпуска: заявка, решение, продление, отмена.

Своих статусов здесь не заводится — их хватает в схеме, и они уже описывают
нужный жизненный цикл. Соответствие такое:

    «ожидает»           = AbsenceRequest.status SUBMITTED
    «на рассмотрении»   = IN_REVIEW
    «подтверждено»      = APPROVED  (+ EmployeeAbsence PLANNED/ACTIVE)
    «отклонено»         = REJECTED
    «отменено»          = CANCELLED
    «продление ждёт»    = ДОЧЕРНЯЯ заявка: request_kind EXTEND,
                          parent_request заполнен, status SUBMITTED
    «завершено»         = EmployeeAbsence.status COMPLETED

Продление намеренно отдельная заявка, а не правка дат в существующей.
Подтверждённые даты — это документ: по ним посчитана статистика, на них
могли сослаться. Молча сдвинуть их значит переписать прошлое.

Разделены две сущности, и путать их нельзя:

  * `AbsenceRequest` — просьба. Она может быть отклонена;
  * `EmployeeAbsence` — подтверждённый период. Только он попадает
    в статистику и только он существует после согласования.

Все правила, по которым что-то разрешено или запрещено, берутся из
`policy.py`, то есть из настроек организации. В коде их нет.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from humotech.absences.models import (
    AbsenceAction,
    AbsenceDocument,
    AbsenceRequest,
    AbsenceType,
    EmployeeAbsence,
    LeaveBalance,
)
from humotech.absences.application_pdf import (
    APPLICATION_DOCUMENT,
    Application,
    build as build_application,
)
from humotech.absences.policy import AbsencePolicy, policy_for
from humotech.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
    constraint_name_of,
)
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.timeframes import (
    closed_range_bounds,
    days_in,
    local_date,
    range_bounds,
)
from humotech.files.storage import open_stored, store
from humotech.notifications import messages
from humotech.notifications.outbox import enqueue
from humotech.schedules.models import CalendarException, EmployeeScheduleAssignment

ENTITY_REQUEST = "absence_requests"
ENTITY_ABSENCE = "employee_absences"

# Что видно в журнале. Ни комментария сотрудника, ни имени файла: в первом
# бывает диагноз, во втором — фамилия и диагноз разом.
REQUEST_AUDIT_FIELDS = (
    "employee_id", "absence_type_id", "request_kind", "status",
    "requested_start_at", "requested_end_at", "parent_request_id",
    "submitted_at", "reviewed_at",
)

# Заявка, которую ещё не рассмотрели.
OPEN_STATUSES = ("DRAFT", "SUBMITTED", "IN_REVIEW")
# Отсутствие, которое считается существующим.
LIVE_ABSENCE_STATUSES = ("PLANNED", "ACTIVE", "COMPLETED")

MINUTES_PER_WORKING_DAY = 8 * 60

# Чего не хватает, чтобы подтвердить больничный. Три пункта, и все три
# делает кадровик: принять справку, подтвердить, что подписанное
# заявление пришло по почте, и проставить фактические даты по справке.
MISSING_NAMES = {
    "certificate": "принятой справки",
    "application": "отметки, что подписанное заявление пришло",
    "period": "фактических дат по справке",
}


def certificate_note(request: AbsenceRequest) -> str | None:
    """Что кадровик сказал о справке — человеку, дословно.

    Без этого «нужны исправления» на карточке означает «что-то не так,
    догадайся сам»: причина уходит в чат отдельным сообщением, а
    сообщение теряется в переписке к следующему дню.

    Берётся у отклонённой бумаги: у принятой объяснять нечего.
    """
    rows = [
        one for one in request.documents.all()
        if one.document_type != APPLICATION_DOCUMENT
        and one.verification_status == "REJECTED"
        and one.verification_comment
    ]
    if not rows:
        return None
    return max(rows, key=lambda one: one.verified_at or one.created_at).verification_comment


def current_certificate(request: AbsenceRequest) -> AbsenceDocument | None:
    """Справка, по которой сейчас идёт разбор.

    Правило то же, что у `certificate_state`: принятая перевешивает всё,
    непроверенная — отклонённую. Возвращается сама бумага, а не её
    состояние: файл нужен там, где его отдают человеку.
    """
    rows = [
        one for one in request.documents.all()
        if one.document_type != APPLICATION_DOCUMENT
    ]
    if not rows:
        return None
    for state in ("VERIFIED", "PENDING", "REJECTED"):
        found = [one for one in rows if one.verification_status == state]
        if found:
            # Среди равных — свежая: справку меняют не по одному разу.
            return max(found, key=lambda one: one.file.created_at)
    return None


def certificate_state(request: AbsenceRequest) -> str | None:
    """Судьба справки: `PENDING`, `VERIFIED`, `REJECTED` или `None`.

    Системный бланк заявления лежит в той же таблице и справкой не
    считается: иначе больничный подтверждал бы сам себя.

    Бумаг бывает несколько, и отвечают они на разные вопросы, поэтому
    выбирается не последняя, а самая сильная:

      * принятая перевешивает всё — вопрос закрыт;
      * непроверенная перевешивает отклонённую: человек уже принёс
        замену, и ждут теперь кадровика, а не его;
      * отклонённая остаётся, только если другой бумаги нет.

    По времени загрузки сравнивать нельзя: `created_at` ставит база
    временем НАЧАЛА транзакции, и две бумаги, созданные в одном
    запросе, получают одинаковую метку.
    """
    rows = [
        one for one in request.documents.all()
        if one.document_type != APPLICATION_DOCUMENT
    ]
    if not rows:
        return None
    seen = {one.verification_status for one in rows}
    for state in ("VERIFIED", "PENDING", "REJECTED"):
        if state in seen:
            return state
    return None


def approval_blockers(request: AbsenceRequest) -> tuple[str, ...]:
    """Что мешает перевести заявку в `APPROVED`.

    `APPROVED` — это финальное подтверждение и строка в табеле, а не
    отметка «кадровик увидел». Пока список не пуст, период отсутствия
    не существует: ни в расчётах, ни в статистике.

    Справку и заявление спрашиваем только у исходной заявки. У продления
    своего комплекта бумаг нет — оно подтверждается по исходной, которая
    все три пункта уже прошла.
    """
    missing = []
    if request.absence_type.requires_document and request.request_kind == "CREATE":
        if certificate_state(request) != "VERIFIED":
            missing.append("certificate")
        if request.application_received_at is None:
            missing.append("application")
        # Даты, введённые самим человеком, — ориентир, а не факт: он
        # подавал заявку в первый день болезни и не знал, когда выйдет.
        # Поэтому спрашивается не «есть ли период», а «переносил ли его
        # кадровик из справки»: непроверенный период и отсутствующий
        # для табеля одинаково ничем не подтверждены.
        #
        # Признак — запись в истории заявки, а не отдельное поле: она и
        # так пишется, и по ней видно, кто и когда период поставил.
        # `actions` у очереди кадровика предзагружены, поэтому перебор,
        # а не `filter`: он не пойдёт в базу лишний раз.
        if not any(one.action == "PERIOD_SET" for one in request.actions.all()):
            missing.append("period")
    elif request.requested_start_at is None or request.requested_end_at is None:
        # У остального период берётся как есть — но он обязан быть:
        # отсутствие это строка в табеле, и записать её «примерно»
        # нельзя.
        #
        # Требовать здесь ещё и подтверждения кадровиком нельзя:
        # организация может обходиться без согласования вовсе
        # (`require_hr_approval`), и тогда ставить `PERIOD_SET` было бы
        # некому — отпуск не подтвердился бы ни одним путём.
        missing.append("period")
    return tuple(missing)


def stage_of(request: AbsenceRequest) -> str:
    """Состояние заявки словами человека, а не кодом таблицы.

    Статус в базе отвечает на вопрос «рассмотрена ли», а человек
    спрашивает другое: чего ждут и от кого. Поэтому стадия собирается
    из статуса и судьбы справки вместе и нигде не хранится — хранимое
    поле разошлось бы с документами на первом же отказе.

    Словарь стадий — про больничный: у отпуска бумаг нет, и он остаётся
    на согласовании до решения.
    """
    if request.status in ("APPROVED", "CANCELLED", "REJECTED"):
        return request.status
    # Словарь стадий — про исходную заявку на больничный. У продления и
    # отмены своего комплекта бумаг нет: они подтверждаются по исходной,
    # которая все три пункта уже прошла. Сказать им «ожидаем документы»
    # значило бы попросить справку, которой никто не ждёт и которая
    # ничего не откроет.
    if not (
        request.absence_type.requires_document
        and request.request_kind == "CREATE"
    ):
        return "PENDING"

    paper = certificate_state(request)
    if paper == "REJECTED":
        return "NEEDS_FIX"
    if paper is None:
        return "WAITING_DOCUMENTS"
    # Справка у кадровика. Дальше ждут только его: принять бумагу,
    # отметить заявление, проставить даты.
    return "HR_REVIEW"


#: Ограничения базы, у которых есть человеческий смысл.
#:
#: Сервис проверяет то же самое и раньше — и почти всегда отвечает сам.
#: Сюда доходит только проигранная гонка: между проверкой и записью
#: успел вклиниться другой запрос. Для человека это ровно тот же случай,
#: и ответ он должен получить тот же, а не «внутреннюю ошибку».
DB_GUARDS = {
    "uq_absence_requests_open_sick_leave": "sick_leave_in_progress",
    "ex_employee_absences_overlap": "overlap",
}


def guard_of(error: Exception) -> str | None:
    """Какое из наших ограничений базы сработало. `None` — не наше.

    Имя берётся из диагностики драйвера, а не из текста ошибки: текст
    зависит от языка и версии сервера, имя — нет. Принимается и сырая
    `IntegrityError`, и уже переведённый отказ: `BaseService.atomic()`
    превращает первую во вторую раньше, чем она дойдёт сюда, а прямая
    вставка мимо сервиса приходит как есть.
    """
    name = None
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        name = details.get("constraint")
    return DB_GUARDS.get(name or constraint_name_of(error) or "")


def lock_employee(employee_id) -> None:
    """Занять сотрудника до конца транзакции.

    Все правила отсутствий — «посмотрел и записал»: проверил пересечение,
    остаток, единственный незакрытый больничный, — и между чтением и
    записью успевает вклиниться второй запрос. Два касания кнопки на
    плохой связи, повтор Telegram, две вкладки CRM: этого достаточно,
    чтобы в табеле оказались два отсутствия на одни сутки.

    Блокировка советующая (`pg_advisory_xact_lock`), а не `SELECT … FOR
    UPDATE` по карточке сотрудника: карточку правит отдел кадров, и
    занимать её на время оформления больничного значило бы связать две
    несвязанные операции. Снимается сама, вместе с транзакцией, —
    забыть её отпустить нельзя.

    Номер блокировки живёт в самой базе — в функции
    `absences_lock_employee`. Ту же функцию зовёт триггер, стоящий на
    заявках, и его правильность держится на том, что номер один и тот
    же: два одинаковых числа в двух языках однажды разойдутся.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT absences_lock_employee(%s)", [str(employee_id)])


@dataclass(frozen=True)
class Overlap:
    """Чужое отсутствие на тех же днях."""

    #: `absence` — подтверждённый период, `request` — поданная заявка.
    kind: str
    request_id: uuid.UUID
    absence_type_name: str
    first_day: date
    last_day: date

    @property
    def human(self) -> str:
        """«5 — 12 октября 2026» или «28 сентября — 3 октября 2026»."""
        year = self.last_day.year
        if self.first_day == self.last_day:
            return f"{_day_ru(self.first_day)} {year}"
        if (self.first_day.year, self.first_day.month) == (year, self.last_day.month):
            return f"{self.first_day.day} — {_day_ru(self.last_day)} {year}"
        return f"{_day_ru(self.first_day)} — {_day_ru(self.last_day)} {year}"


_MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _day_ru(value: date) -> str:
    return f"{value.day} {_MONTHS_RU[value.month - 1]}"


def find_overlap(
    employee_id,
    first_day: date,
    last_day: date,
    tz,
    *,
    ignore_request_ids: tuple = (),
) -> Overlap | None:
    """Первое отсутствие сотрудника, задевающее эти дни.

    Один календарный день пересечения — уже конфликт: 1–5 и 5–10 октября
    спорят за пятое. Поэтому сравниваются полуинтервалы суток в поясе
    офиса, а не сами моменты: у отсутствия конец хранится последней
    микросекундой дня, и «позже ли он» без приведения к суткам врёт.

    Смотрим и подтверждённые периоды, и поданные заявки с датами.
    Заявка — это забронированные дни: пока по ней не решили, ставить на
    них второе отсутствие значит готовить конфликт заранее.

    `COMPLETED` считается наравне с `PLANNED` и `ACTIVE`: закрытое
    отсутствие никуда не делось из табеля, и класть поверх него второе
    нельзя. Раньше проверка его пропускала.
    """
    skip = tuple(one for one in ignore_request_ids if one is not None)

    # Подтверждённое отсутствие сравнивается по календарным дням: часы
    # начала и конца к спору за сутки отношения не имеют, а перевод
    # момента в дату ещё и зависит от пояса. Те же дни лежат в самом
    # ограничении базы — проверка и ограничение обязаны спрашивать одно
    # и то же, иначе одна из них однажды промолчит.
    absences = EmployeeAbsence.objects.filter(
        employee_id=employee_id,
        status__in=LIVE_ABSENCE_STATUSES,
        start_date__lte=last_day,
        end_date__gte=first_day,
    ).select_related("absence_type").order_by("start_date")
    if skip:
        absences = absences.exclude(origin_request_id__in=skip)
    row = absences.first()
    if row is not None:
        return Overlap(
            kind="absence",
            request_id=row.origin_request_id,
            absence_type_name=row.absence_type.name,
            first_day=row.start_date,
            last_day=row.end_date,
        )

    # У заявки календарных дат нет: она хранит просьбу моментами, и
    # менять это ради проверки значило бы переписать половину API. Её
    # период приводится к суткам здесь.
    start, end = range_bounds(first_day, last_day, tz)

    requests = AbsenceRequest.objects.filter(
        employee_id=employee_id,
        request_kind__in=("CREATE", "EXTEND"),
        status__in=OPEN_STATUSES,
        requested_start_at__lt=end,
        requested_end_at__gt=start,
    ).select_related("absence_type").order_by("requested_start_at")
    if skip:
        requests = requests.exclude(id__in=skip)
    pending = requests.first()
    if pending is not None:
        return Overlap(
            kind="request",
            request_id=pending.id,
            absence_type_name=pending.absence_type.name,
            first_day=local_date(pending.requested_start_at, tz),
            last_day=local_date(pending.requested_end_at, tz),
        )
    return None


def raise_on_overlap(
    employee_id, first_day: date, last_day: date, tz, *,
    ignore_request_ids: tuple = (),
) -> None:
    """Отказ с названием и периодом того, с чем спорим.

    «На эти даты уже оформлено отсутствие» заставляет человека искать
    самому. Кадровик при этом обязан знать, ЧТО он сейчас перекрывает:
    решение — сдвинуть период, отменить ошибочную заявку или принять
    отдельное кадровое решение — зависит именно от этого.
    """
    clash = find_overlap(
        employee_id, first_day, last_day, tz,
        ignore_request_ids=ignore_request_ids,
    )
    if clash is None:
        return
    what = (
        "подтверждённым отсутствием"
        if clash.kind == "absence"
        else "поданной заявкой"
    )
    raise Conflict(
        f"Период пересекается с {what} "
        f"«{clash.absence_type_name}»: {clash.human}.",
        details={
            "reason": "overlap",
            "conflict_kind": clash.kind,
            "request_id": str(clash.request_id),
            "first_day": clash.first_day.isoformat(),
            "last_day": clash.last_day.isoformat(),
        },
    )


def _period_words(request, tz) -> str:
    """Период заявки строкой для истории. «не указан» — тоже значение."""
    if request.requested_start_at is None or request.requested_end_at is None:
        return "период не указан"
    return (
        f"{local_date(request.requested_start_at, tz).isoformat()} — "
        f"{local_date(request.requested_end_at, tz).isoformat()}"
    )


def attendance_days(employee_id, first_day: date, last_day: date, tz) -> list[date]:
    """Дни периода, в которые сотрудник реально отмечался.

    Берутся сырые события входа-выхода, а не собранные из них сессии:
    сессия — это вывод, а спорить надо с фактом. Отклонённые отметки не
    в счёт — они уже признаны недействительными.

    Нужно затем, чтобы кадровик не списал отработанный день в
    больничный молча. Событие не удаляется и не правится: отметка —
    исторический факт, а кадровый статус дня считается поверх неё.
    """
    from humotech.attendance.models import AttendanceEvent

    start, end = range_bounds(first_day, last_day, tz)
    moments = (
        AttendanceEvent.objects
        .filter(
            employee_id=employee_id,
            occurred_at__gte=start,
            occurred_at__lt=end,
        )
        .exclude(verification_status="REJECTED")
        .values_list("occurred_at", flat=True)
    )
    return sorted({local_date(one, tz) for one in moments})


@dataclass(frozen=True)
class RequestView:
    """Заявка глазами сотрудника, вместе с производными состояниями."""

    request: AbsenceRequest
    working_days: int
    documents: int
    extension_pending: bool
    absence: EmployeeAbsence | None
    certificate_status: str | None = None
    certificate_comment: str | None = None

    @property
    def can_cancel(self) -> bool:
        return self.request.status in OPEN_STATUSES

    @property
    def stage(self) -> str:
        return stage_of(self.request)


@dataclass(frozen=True)
class BalanceView:
    absence_type: AbsenceType
    year: int
    allocated_minutes: int
    used_minutes: int
    reserved_minutes: int
    adjustment_minutes: int

    @property
    def available_minutes(self) -> int:
        return (
            self.allocated_minutes
            + self.adjustment_minutes
            - self.used_minutes
            - self.reserved_minutes
        )


class AbsenceService(BaseService):
    """Заявки сотрудника. Actor здесь не участвует: действует сам человек."""

    # ------------------------------------------------------------ чтение

    def policy(self, organization_id) -> AbsencePolicy:
        return policy_for(organization_id)

    def types(self, context):
        return AbsenceType.objects.filter(
            organization_id=context.organization_id, is_active=True
        ).order_by("name")

    def requests(self, context, *, limit: int = 50, offset: int = 0):
        queryset = (
            AbsenceRequest.objects.filter(employee_id=context.employee.id)
            .select_related("absence_type", "parent_request")
            .prefetch_related("documents", "absences")
            .order_by("-created_at", "-id")
        )
        total = queryset.count()
        rows = list(queryset[offset:offset + limit])
        return [self._view(context, row) for row in rows], total

    def request(self, context, request_id: uuid.UUID) -> RequestView:
        row = (
            AbsenceRequest.objects.filter(
                id=request_id, employee_id=context.employee.id
            )
            .select_related("absence_type", "parent_request")
            .prefetch_related("documents", "absences")
            .first()
        )
        if row is None:
            # Чужая заявка отвечает так же, как отсутствие записи.
            raise NotFound("Заявка не найдена")
        return self._view(context, row)

    def application(self, context, request_id: uuid.UUID) -> bytes:
        """Печатное заявление по заявке — готовым PDF.

        Собирается заново на каждое обращение, а не хранится файлом.
        Заявка живёт: даты продлевают, статус меняется, — и бланк,
        сохранённый однажды, разошёлся бы с ней молча. Сборка стоит
        миллисекунды, рассинхрон стоит спора в кадровом деле.
        """
        view = self.request(context, request_id)
        self._forbid_closed_paperwork(view.request)
        return build_application(
            _application_of(view.request, context.employee, context.timezone)
        )

    @staticmethod
    def _forbid_closed_paperwork(request) -> None:
        """У закрытой заявки бумаг больше нет.

        Подписанное заявление по отклонённой заявке потом всплывает в
        переписке как действующее, а справка к ней просто исчезает из
        виду вместе со временем, которое человек на неё потратил.
        Интерфейсы это и так прячут — но прячет интерфейс, а отвечает
        сервер.
        """
        if request.status in ("REJECTED", "CANCELLED"):
            raise Conflict(
                "Заявка закрыта: бланк заявления по ней больше не выдаётся",
                details={"reason": "request_closed", "status": request.status},
            )

    def balances(self, context, *, year: int | None = None) -> list[BalanceView]:
        target = year or timezone.now().year
        rows = (
            LeaveBalance.objects.filter(
                employee_id=context.employee.id, year=target
            )
            .select_related("absence_type")
            .order_by("absence_type__name")
        )
        return [
            BalanceView(
                absence_type=row.absence_type,
                year=row.year,
                allocated_minutes=row.allocated_minutes,
                used_minutes=row.used_minutes,
                reserved_minutes=row.reserved_minutes,
                adjustment_minutes=row.adjustment_minutes,
            )
            for row in rows
        ]

    # ----------------------------------------------------------- создание

    def create(
        self,
        context,
        *,
        absence_type_code: str,
        first_day: date | None = None,
        last_day: date | None = None,
        comment: str | None = None,
        document=None,
        now: datetime | None = None,
    ) -> RequestView:
        """Новая заявка на отсутствие — больничный или отпуск.

        **Даты необязательны у того, что не списывает остаток.** Человек
        заболел в пятницу вечером и не знает, выйдет ли он во вторник
        или в четверг; требовать от него число — значит получить
        выдуманное, которое потом всё равно исправят по справке.
        Фактический период проставляет кадровик, когда справка на руках
        (`set_period`).

        У отпуска даты обязательны, и это не симметрия ради симметрии:
        отпуск резервирует дни из остатка, а резерв без периода
        невозможно ни посчитать, ни снять.
        """
        moment = now or timezone.now()
        policy = policy_for(context.organization_id)
        absence_type = self._require_type(context, absence_type_code)

        # Одна дата из двух — это не «период неизвестен», а недописанная
        # форма: принять её значило бы сохранить половину ответа и
        # притвориться, что вопрос закрыт.
        if (first_day is None) != (last_day is None):
            raise ValidationFailed(
                "Укажите обе даты или оставьте оба поля пустыми",
                details={"reason": "half_period"},
            )
        if first_day is None and absence_type.deducts_leave_balance:
            raise ValidationFailed(
                "Для отпуска нужны даты: из них считается остаток",
                details={"reason": "period_required"},
            )

        # Проверки и запись — одной транзакцией и под одной блокировкой.
        # Раньше они стояли до `atomic()`, и между «посмотрел» и
        # «записал» успевал вклиниться второй запрос: два касания на
        # плохой связи давали два отсутствия на одни сутки.
        #
        # `try` снаружи транзакции, а не внутри: после отказа базы
        # транзакция уже сломана, и любой следующий запрос в ней —
        # ещё одна ошибка. Разбирать причину нужно на откатившейся.
        try:
            return self._create(
                context, policy=policy, absence_type=absence_type,
                first_day=first_day, last_day=last_day, comment=comment,
                document=document, moment=moment,
            )
        except Conflict as error:
            reason = guard_of(error)
            if reason is None:
                # Отказал не барьер базы, а проверка в коде — у неё
                # объяснение уже готово.
                raise
            raise self._guard_failed(context, absence_type, reason) from error

    def _guard_failed(self, context, absence_type, reason: str) -> Conflict:
        """Отказ базы словами сервиса.

        Ответ собирается тот же, что и у проверки в коде: человеку
        безразлично, кто именно его остановил, и два разных текста на
        один случай — это два разных представления о правилах.
        """
        if reason == "sick_leave_in_progress":
            try:
                self._check_single_sick_leave(context, absence_type)
            except Conflict as same:
                return same
        return Conflict(
            "На эти даты уже оформлено отсутствие",
            details={"reason": reason},
        )

    def _create(
        self, context, *, policy, absence_type, first_day, last_day,
        comment, document, moment,
    ) -> RequestView:
        """Само создание. Вынесено, чтобы отказ базы ловить снаружи."""
        with self.atomic():
            lock_employee(context.employee.id)

            self._check_single_sick_leave(context, absence_type)

            start_at = end_at = None
            working_days = 0
            if first_day is not None and last_day is not None:
                self._check_period(context, first_day, last_day)
                self._check_backdating(context, first_day, policy, moment)
                self._check_lead_time(
                    context, absence_type, first_day, policy, moment
                )
                raise_on_overlap(
                    context.employee.id, first_day, last_day, context.timezone
                )

                # Конец ВКЛЮЧЁН: `end_at` читают через `local_date(end_at)`, и
                # полуинтервал дал бы начало следующих суток — лишний день
                # в каждом больничном и в каждом отпуске.
                start_at, end_at = closed_range_bounds(
                    first_day, last_day, context.timezone
                )
                working_days = self._working_days(context, first_day, last_day)
                if absence_type.deducts_leave_balance:
                    self._check_balance(
                        context, absence_type, working_days, policy, start_at
                    )

            if self._document_needed(policy, working_days) and document is None:
                raise ValidationFailed(
                    "К заявке нужно приложить справку",
                    details={
                        "reason": "document_required",
                        "from_day": policy.document_required_from_day,
                        "days": working_days,
                    },
                )

            request = AbsenceRequest.objects.create(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                absence_type=absence_type,
                request_kind="CREATE",
                status="SUBMITTED",
                requested_start_at=start_at,
                requested_end_at=end_at,
                employee_comment=comment or None,
                submitted_at=moment,
            )
            self._act(context, request, "SUBMITTED", None, "SUBMITTED")
            if document is not None:
                self._attach(context, request, document, policy)

            if absence_type.deducts_leave_balance:
                # Резерв, а не списание: заявка ещё может быть отклонена.
                # Списывать до решения значило бы удерживать остаток за то,
                # чего не случилось.
                self._reserve(context, absence_type, working_days, start_at)

            if not policy.require_hr_approval and not approval_blockers(request):
                # Организация решила обходиться без согласования: заявка
                # подтверждается сразу и становится отсутствием.
                #
                # Больничный так подтвердиться не может, и настройка тут
                # ни при чём: справку принимает человек, а не флаг. Он
                # ждёт кадровика — и заявку это не ломает, она просто
                # остаётся поданной.
                self._approve(context, request, actor=None, comment=None, now=moment)

            self._notify(request, "created")
            self.audit.record_by_employee(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                action="absence.request.create",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        return self.request(context, request.id)

    def extend(
        self,
        context,
        request_id: uuid.UUID,
        *,
        new_last_day: date,
        comment: str | None = None,
        now: datetime | None = None,
    ) -> RequestView:
        """Продление: ОТДЕЛЬНАЯ заявка, ссылающаяся на исходную.

        Подтверждённые даты не правятся: по ним уже посчитана статистика,
        и молча сдвинуть их значит переписать прошлое.
        """
        moment = now or timezone.now()
        policy = policy_for(context.organization_id)
        if not policy.extensions_allowed:
            raise Conflict(
                "Продление в этой организации не предусмотрено",
                details={"reason": "extensions_disabled"},
            )

        parent = self._require_own(context, request_id)
        if parent.status != "APPROVED":
            raise Conflict(
                "Продлить можно только подтверждённое отсутствие",
                details={"reason": "not_approved"},
            )
        if parent.request_kind != "CREATE":
            raise Conflict("Продлевают исходную заявку, а не продление")
        if self._pending_extension(parent) is not None:
            raise Conflict(
                "Продление уже отправлено и ждёт решения",
                details={"reason": "extension_pending"},
            )

        current_end = parent.requested_end_at
        _, new_end = closed_range_bounds(
            new_last_day, new_last_day, context.timezone
        )
        if current_end is not None and new_end <= current_end:
            raise ValidationFailed(
                "Новая дата окончания должна быть позже текущей",
                details={"reason": "not_longer"},
            )

        # Период продления — только ДОБАВЛЕННЫЕ дни, со следующего дня после
        # прежнего конца. Начать его прежним концом значило бы посчитать
        # последний день исходного отсутствия дважды: и в нём, и в продлении.
        first_extra_day = current_end.astimezone(context.timezone).date() + (
            timedelta(days=1)
        )
        extra_start, _ = closed_range_bounds(
            first_extra_day, new_last_day, context.timezone
        )
        extra_days = self._working_days(context, first_extra_day, new_last_day)
        if parent.absence_type.deducts_leave_balance:
            # Продление отпуска стоит остатка ровно так же, как сам отпуск.
            # Без этой проверки запрет уходить в минус обходился бы одним
            # продлением, а добавленные дни не списывались бы вовсе.
            self._check_balance(
                context, parent.absence_type, extra_days, policy, extra_start
            )

        with self.atomic():
            lock_employee(context.employee.id)
            # Продление задевает новые сутки, и на них может стоять уже
            # оформленный отпуск. Исходная заявка и её отсутствие из
            # проверки исключены: продлевают именно их.
            raise_on_overlap(
                context.employee.id, first_extra_day, new_last_day,
                context.timezone,
                ignore_request_ids=(parent.id, request_id),
            )

            child = AbsenceRequest.objects.create(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                absence_type=parent.absence_type,
                request_kind="EXTEND",
                parent_request=parent,
                status="SUBMITTED",
                requested_start_at=extra_start,
                requested_end_at=new_end,
                employee_comment=comment or None,
                submitted_at=moment,
            )
            self._act(context, child, "SUBMITTED", None, "SUBMITTED")
            if parent.absence_type.deducts_leave_balance:
                self._reserve(
                    context, parent.absence_type, extra_days, extra_start
                )
            self._notify(child, "created")
            self.audit.record_by_employee(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                action="absence.request.extend",
                entity_type=ENTITY_REQUEST,
                entity_id=child.id,
                after=snapshot(child, REQUEST_AUDIT_FIELDS),
            )
        return self.request(context, child.id)

    def cancel(
        self, context, request_id: uuid.UUID, *, now: datetime | None = None
    ) -> RequestView:
        """Отмена своей заявки — в пределах того, что разрешила организация."""
        moment = now or timezone.now()
        policy = policy_for(context.organization_id)
        request = self._require_own(context, request_id)

        if request.status in ("CANCELLED", "REJECTED"):
            raise Conflict("Заявка уже закрыта")
        if request.status == "APPROVED":
            if policy.cancelling_approved_requires_hr:
                raise PermissionDenied(
                    "Подтверждённое отсутствие отменяет отдел кадров",
                    details={"reason": "hr_required"},
                )
        elif not policy.employee_may_cancel_pending:
            raise PermissionDenied(
                "Отменить заявку может отдел кадров",
                details={"reason": "hr_required"},
            )

        with self.atomic():
            before = snapshot(request, REQUEST_AUDIT_FIELDS)
            previous = request.status
            request.status = "CANCELLED"
            request.save(update_fields=["status", "updated_at"])
            self._act(context, request, "CANCELLED", previous, "CANCELLED")
            self._release(
                context, request, moment, was_approved=previous == "APPROVED"
            )
            EmployeeAbsence.objects.filter(
                origin_request=request, status__in=("PLANNED", "ACTIVE")
            ).update(status="CANCELLED", cancelled_at=moment)
            self._notify(request, "cancelled")
            self.audit.record_by_employee(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                action="absence.request.cancel",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                before=before,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        return self.request(context, request.id)

    def attach_document(
        self, context, request_id: uuid.UUID, document, *,
        now: datetime | None = None,
    ) -> RequestView:
        """Донести справку позже — если организация это разрешает."""
        policy = policy_for(context.organization_id)
        request = self._require_own(context, request_id)

        if request.status in ("CANCELLED", "REJECTED"):
            raise Conflict("Заявка закрыта")
        if request.status == "APPROVED" and not policy.document_can_be_added_later:
            raise Conflict(
                "Справку нужно было приложить при подаче",
                details={"reason": "too_late"},
            )

        with self.atomic():
            self._attach(context, request, document, policy)
        return self.request(context, request.id)

    # ------------------------------------------------------ решение отдела

    def decide(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        *,
        approve: bool,
        comment: str | None = None,
        override_marks: bool = False,
        now: datetime | None = None,
    ) -> AbsenceRequest:
        """Решение HR по заявке. Требует права и своей организации.

        `override_marks` — согласие записать больничным дни, в которые
        человек отмечался. Причина при этом обязательна: решение видно
        в истории заявки, и оно должно быть объяснено.
        """
        self.access.require(actor, "absences.approve")
        moment = now or timezone.now()

        request = (
            AbsenceRequest.objects.filter(
                id=request_id, organization_id=actor.organization_id
            )
            .select_related("absence_type", "employee", "parent_request")
            .first()
        )
        if request is None:
            raise NotFound("Заявка не найдена")
        if request.status not in OPEN_STATUSES:
            raise Conflict(
                "Заявка уже рассмотрена", details={"status": request.status}
            )
        self._forbid_self_approval(actor, request)

        try:
            return self._decide(
                actor, request, approve=approve, comment=comment,
                override_marks=override_marks, moment=moment,
            )
        except Conflict as error:
            if guard_of(error) is None:
                raise
            # Период заняли, пока кадровик решал. Сервис проверяет это
            # вплотную к записи, но «вплотную» — не «одновременно».
            raise Conflict(
                "Период заняли, пока заявка была открыта. "
                "Обновите страницу и посмотрите, что изменилось.",
                details={"reason": "overlap"},
            ) from error

    def _decide(
        self, actor, request, *, approve, comment, override_marks, moment,
    ) -> AbsenceRequest:
        """Само решение. Вынесено, чтобы отказ базы ловить снаружи."""
        with self.atomic():
            before = snapshot(request, REQUEST_AUDIT_FIELDS)
            previous = request.status
            if approve:
                if override_marks and not (comment or "").strip():
                    raise ValidationFailed(
                        "Объясните, почему больничный утверждается поверх "
                        "отметок: это останется в истории заявки",
                        details={"field": "comment"},
                    )
                self._approve(
                    _ContextFromRequest(request), request, actor=actor,
                    comment=comment, now=moment, override_marks=override_marks,
                )
            else:
                request.status = "REJECTED"
                request.reviewed_by_user_id = actor.user_id
                request.reviewed_at = moment
                request.review_comment = comment or None
                request.save(
                    update_fields=[
                        "status", "reviewed_by_user", "reviewed_at",
                        "review_comment", "updated_at",
                    ]
                )
                self._act(
                    _ContextFromRequest(request), request, "REJECTED",
                    previous, "REJECTED", actor=actor, comment=comment,
                )
                self._release(_ContextFromRequest(request), request, moment)

            self._notify(request, "approved" if approve else "rejected")
            self.audit.record(
                actor,
                action="absence.request.approve" if approve else
                       "absence.request.reject",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                before=before,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        request.refresh_from_db()
        return request

    def set_period(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        *,
        first_day: date,
        last_day: date,
        comment: str | None = None,
        now: datetime | None = None,
    ) -> AbsenceRequest:
        """Проставить фактические даты больничного по справке.

        Нужно ровно затем, ради чего даты в заявке сделаны
        необязательными: человек подал её, не зная, когда выйдет, а в
        справке стоит точный период. Кадровик переносит его в заявку —
        и только после этого её можно подтвердить.

        Правится ТОЛЬКО нерассмотренная заявка. У подтверждённой период
        уже превратился в строку табеля, и менять его задним числом —
        это отмена и новая заявка, а не правка поля.
        """
        self.access.require(actor, "absences.approve")
        moment = now or timezone.now()
        request = (
            AbsenceRequest.objects.filter(
                id=request_id, organization_id=actor.organization_id
            )
            .select_related("absence_type", "employee")
            .first()
        )
        if request is None:
            raise NotFound("Заявка не найдена")
        if request.status not in OPEN_STATUSES:
            raise Conflict(
                "Период правится до решения по заявке",
                details={"status": request.status},
            )

        context = _ContextFromRequest(request)
        self._check_period(context, first_day, last_day)
        start_at, end_at = closed_range_bounds(
            first_day, last_day, context.timezone
        )

        with self.atomic():
            lock_employee(request.employee_id)
            # Период из справки может лечь поверх чужого отсутствия:
            # человек оформил отпуск на осень, а больничный оказался
            # длиннее, чем думал. Молча наложить одно на другое нельзя —
            # кадровик должен решить, что из двух неверно.
            #
            # Сама заявка из проверки исключена: её собственный прежний
            # период — не препятствие собственному новому.
            raise_on_overlap(
                request.employee_id, first_day, last_day, context.timezone,
                ignore_request_ids=(request.id,),
            )

            before = snapshot(request, REQUEST_AUDIT_FIELDS)
            was = _period_words(request, context.timezone)
            # Резерв держится за конкретные дни. Переписать период, не
            # тронув его, значило бы держать в остатке старую неделю и
            # не держать новую: у отпуска это прямая недостача.
            self._release(context, request, moment)
            request.requested_start_at = start_at
            request.requested_end_at = end_at
            request.save(
                update_fields=[
                    "requested_start_at", "requested_end_at", "updated_at",
                ]
            )
            if request.absence_type.deducts_leave_balance:
                self._reserve(
                    context,
                    request.absence_type,
                    self._working_days(context, first_day, last_day),
                    start_at,
                )
            # В истории заявки видно не только «период поставлен», но и
            # что именно изменилось: спор о больничном — это всегда спор
            # о датах, и «было — стало» в нём главное.
            became = _period_words(request, context.timezone)
            note = f"{was} → {became}"
            self._act(
                context, request, "PERIOD_SET", request.status, request.status,
                actor=actor, comment=f"{note}. {comment}" if comment else note,
            )
            self.audit.record(
                actor,
                action="absence.request.set_period",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                before=before,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        request.refresh_from_db()
        return request

    def mark_application_received(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        *,
        received: bool = True,
        now: datetime | None = None,
    ) -> AbsenceRequest:
        """Отметить, что подписанное заявление дошло по почте.

        Второй, независимый от справки пункт проверки. Их намеренно два:
        подписанная бумага подтверждает намерение человека, справка —
        факт болезни, и приходят они разными дорогами. Одна отметка на
        оба означала бы, что половину работы кадровик подтверждает не
        глядя.
        """
        self.access.require(actor, "absences.documents")
        moment = now or timezone.now()
        request = AbsenceRequest.objects.filter(
            id=request_id, organization_id=actor.organization_id
        ).select_related("absence_type", "employee").first()
        if request is None:
            raise NotFound("Заявка не найдена")

        with self.atomic():
            before = snapshot(request, REQUEST_AUDIT_FIELDS)
            request.application_received_at = moment if received else None
            request.save(
                update_fields=["application_received_at", "updated_at"]
            )
            self.audit.record(
                actor,
                action="absence.request.application_received",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                before=before,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        request.refresh_from_db()
        return request

    def cancel_approved(
        self, actor: Actor, request_id: uuid.UUID, *,
        comment: str | None = None, now: datetime | None = None,
    ) -> AbsenceRequest:
        """Отмена уже подтверждённого отсутствия — только отделом кадров."""
        self.access.require(actor, "absences.approve")
        moment = now or timezone.now()
        request = AbsenceRequest.objects.filter(
            id=request_id, organization_id=actor.organization_id
        ).select_related("absence_type", "employee").first()
        if request is None:
            raise NotFound("Заявка не найдена")
        if request.status != "APPROVED":
            raise Conflict("Отменять нечего: заявка не подтверждена")

        context = _ContextFromRequest(request)
        with self.atomic():
            before = snapshot(request, REQUEST_AUDIT_FIELDS)
            request.status = "CANCELLED"
            request.reviewed_by_user_id = actor.user_id
            request.reviewed_at = moment
            request.review_comment = comment or None
            request.save(
                update_fields=[
                    "status", "reviewed_by_user", "reviewed_at",
                    "review_comment", "updated_at",
                ]
            )
            self._act(context, request, "CANCELLED", "APPROVED", "CANCELLED",
                      actor=actor, comment=comment)
            EmployeeAbsence.objects.filter(
                origin_request=request, status__in=("PLANNED", "ACTIVE")
            ).update(status="CANCELLED", cancelled_at=moment)
            self._release(context, request, moment, was_approved=True)
            self._notify(request, "cancelled")
            self.audit.record(
                actor,
                action="absence.request.cancel",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                before=before,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        request.refresh_from_db()
        return request

    def queue(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        type_code: str | None = None,
        employee_id=None,
        office_id=None,
        region_id=None,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        request_kind: str | None = None,
    ):
        """Заявки на отсутствие под фильтрами очереди HR.

        `request_kind` — вид заявки: `CREATE`, `EXTEND` или `CANCEL`. Отмена
        подтверждённого отпуска — тоже заявка, и у неё своя вкладка.

        Область видимости — та же, что у списка сотрудников: заявка видна,
        если виден сам сотрудник. Фильтр не расширяет доступ: он сужает
        уже разрешённое.

        Период фильтруется по датам САМОГО ОТСУТСТВИЯ, а не по дате
        подачи: кадровик ищет «кто отсутствует в сентябре», а не «кто
        подал заявление в сентябре». Смешивать эти два значения нельзя,
        и в интерфейсе подпись говорит, какое из них выбрано.
        """
        self.access.require(actor, "absences.read")

        queryset = AbsenceRequest.objects.filter(
            organization_id=actor.organization_id
        ).select_related("absence_type", "employee", "parent_request").prefetch_related(
            # Документы и история страницей, а не по запросу на строку.
            "documents__file",
            "actions__actor_user",
            "actions__actor_employee",
        )

        if status:
            queryset = queryset.filter(status__in=[s for s in status.split(",") if s])
        if request_kind:
            kinds = [k for k in request_kind.split(",") if k]
            queryset = queryset.filter(request_kind__in=kinds)
        if employee_id:
            # Отбор по человеку не расширяет доступ: область ниже
            # по-прежнему применяется, и чужой сотрудник даст пустой
            # набор, а не чужие заявки.
            queryset = queryset.filter(employee_id=employee_id)
        if type_code:
            codes = [c for c in type_code.split(",") if c]
            queryset = queryset.filter(absence_type__code__in=codes)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(
                Q(employee__first_name__icontains=pattern)
                | Q(employee__last_name__icontains=pattern)
                | Q(employee__employee_number__icontains=pattern)
            )
        if date_from:
            queryset = queryset.filter(requested_end_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(requested_start_at__date__lte=date_to)

        visible = self._scope_ids(actor, office_id=office_id, region_id=region_id)
        if visible is not None:
            queryset = queryset.filter(employee_id__in=visible)
        return queryset

    def _scope_ids(self, actor: Actor, *, office_id=None, region_id=None):
        """Сотрудники в области видимости. `None` — вся организация.

        Именно `None` для «всех» и ПУСТОЙ набор для «ничего не видно»:
        слить эти случаи проверкой `if ids:` значит молча показать всю
        организацию тому, у кого прав нет.
        """
        from datetime import date

        from humotech.employees.models import EmployeeAssignment
        from humotech.employees.services import current_primary_assignment_filter

        condition = None
        if office_id:
            self.access.require_office(actor, office_id)
            condition = Q(office_id=office_id)
        elif region_id:
            self.access.require_region(actor, region_id)
            condition = Q(office__region_id=region_id)
        else:
            visible = self.access.visible_office_ids(actor)
            if visible is not None:
                condition = Q(office_id__in=visible)

        if condition is None:
            return None
        return EmployeeAssignment.objects.filter(
            current_primary_assignment_filter(date.today()) & condition
        ).values_list("employee_id", flat=True)

    def own_document(self, context, request_id: uuid.UUID):
        """Справка этой заявки — самому человеку.

        Он её и приносил. Не дать ему потом на неё посмотреть значит
        отправить искать файл в собственной переписке — и принести
        кадровику не ту бумагу во второй раз.

        Отдаётся та, по которой идёт разбор: ровно то, что видит
        кадровик. Отклонённая остаётся в заявке, но человеку нужна не
        она, а та, что сейчас в деле.
        """
        request = self._require_own(context, request_id)
        document = current_certificate(request)
        if document is None:
            raise NotFound("Справка не приложена")
        return open_stored(document.file), document.file

    def send_paper(self, context, request_id: uuid.UUID, *, what: str) -> None:
        """Прислать человеку бумагу по заявке в чат.

        Вебвью Telegram не даёт сохранить файл: нажатие «скачать»
        заканчивается ничем. Поэтому кабинет не отдаёт бумагу сам, а
        просит бота прислать её сообщением — оттуда её и пересылают, и
        печатают, и она остаётся в переписке.

        Ключа повтора нет намеренно: это не событие, а просьба. Нажал
        дважды — получил дважды, и это ровно то, чего человек ждёт.
        """
        request = self._require_own(context, request_id)
        if what == "certificate":
            if current_certificate(request) is None:
                raise NotFound("Справка не приложена")
            kind, body = (
                "absence.certificate.copy",
                messages.CERTIFICATE_COPY,
            )
        else:
            kind, body = (
                "absence.application.copy",
                messages.APPLICATION_COPY,
            )
        with self.atomic():
            enqueue(
                organization_id=request.organization_id,
                employee_id=request.employee_id,
                notification_type=kind,
                body=body,
                related_entity_type=ENTITY_REQUEST,
                related_entity_id=request.id,
            )

    def open_document(self, actor: Actor, request_id, document_id):
        """Файл справки к заявке — тем, кто видит саму заявку.

        Право то же, что на очередь, и область та же: справка чужого офиса
        не открывается по угаданному адресу. Отказ по области отвечает «не
        найдено», а не «запрещено»: иначе по ответу можно было бы
        перебирать, какие заявки существуют.
        """
        self.access.require(actor, "absences.read")
        document = (
            AbsenceDocument.objects.filter(
                id=document_id,
                absence_request_id=request_id,
                organization_id=actor.organization_id,
            )
            .select_related("file", "absence_request")
            .first()
        )
        if document is None:
            raise NotFound("Документ не найден")
        visible = self._scope_ids(actor)
        if visible is not None and not visible.filter(
            employee_id=document.absence_request.employee_id
        ).exists():
            raise NotFound("Документ не найден")
        return open_stored(document.file), document.file

    def verify_document(
        self,
        actor: Actor,
        request_id,
        document_id,
        *,
        accept: bool,
        comment: str | None = None,
    ) -> AbsenceDocument:
        """Принять справку или отклонить её с причиной.

        Отклонение без объяснения — тупик: человек приносит ту же бумагу
        второй раз и не понимает, почему её опять не берут. Поэтому
        причина обязательна, и она уходит человеку дословно: пересказ
        своими словами однажды смягчит «нечитаемое фото» до «нужен
        другой документ».

        Решение по бумаге не меняет решения по заявке. Одобренный
        больничный с отклонённой справкой — законное состояние: HR
        ждёт правильный документ, а человек всё это время болеет, а
        не числится прогулявшим.
        """
        self.access.require(actor, "absences.documents")

        document = (
            AbsenceDocument.objects.filter(
                id=document_id,
                absence_request_id=request_id,
                organization_id=actor.organization_id,
            )
            .select_related("absence_request", "absence_request__absence_type")
            .first()
        )
        if document is None:
            raise NotFound("Документ не найден")

        visible = self._scope_ids(actor)
        if visible is not None and not visible.filter(
            employee_id=document.absence_request.employee_id
        ).exists():
            raise NotFound("Документ не найден")

        if document.document_type == APPLICATION_DOCUMENT:
            raise Conflict(
                "Это системный бланк заявления, а не справка сотрудника",
                details={"document_type": document.document_type},
            )

        text = (comment or "").strip() or None
        if not accept and not text:
            raise ValidationFailed(
                "Укажите причину: человек должен понять, что принести взамен",
                details={"field": "comment"},
            )

        request = document.absence_request
        with self.atomic():
            document.verification_status = "VERIFIED" if accept else "REJECTED"
            document.verified_by_user_id = actor.user_id
            document.verified_at = timezone.now()
            document.verification_comment = text
            document.save(
                update_fields=[
                    "verification_status", "verified_by_user_id",
                    "verified_at", "verification_comment", "updated_at",
                ]
            )
            self._act(
                None, request,
                "DOCUMENT_VERIFIED" if accept else "DOCUMENT_REJECTED",
                None, None, actor=actor, comment=text,
            )
            self._notify_document(request, document, accept=accept, reason=text)
        return document

    def _notify_document(self, request, document, *, accept: bool, reason) -> None:
        """Сообщение о судьбе справки.

        Ключ повтора включает документ: у заявки их бывает несколько, и
        общий ключ на заявку проглотил бы решение по второй бумаге.
        """
        if accept:
            body = messages.DOCUMENT_ACCEPTED
        elif reason:
            body = messages.DOCUMENT_REJECTED.format(reason=reason)
        else:
            body = messages.DOCUMENT_REJECTED_NO_REASON

        enqueue(
            organization_id=request.organization_id,
            employee_id=request.employee_id,
            notification_type=(
                "absence.document_accepted" if accept
                else "absence.document_rejected"
            ),
            body=body,
            idempotency_key=(
                f"absence-doc:{document.id}:"
                f"{'accepted' if accept else 'rejected'}"
            ),
            related_entity_type=ENTITY_REQUEST,
            related_entity_id=request.id,
        )

    def hr_application(self, actor: Actor, request_id) -> bytes:
        """То же заявление, но глазами кадровика.

        Нужно, когда человек принёс не ту бумагу или не принёс вовсе:
        кадровик печатает бланк сам и не заставляет сотрудника искать
        телефон. Сборщик тот же — два способа собрать один бланк
        однажды разойдутся.
        """
        self.access.require(actor, "absences.read")
        request = (
            AbsenceRequest.objects.filter(
                id=request_id, organization_id=actor.organization_id
            )
            .select_related("absence_type", "employee")
            .first()
        )
        if request is None:
            raise NotFound("Заявка не найдена")

        # Область видимости: чужой офис отвечает так же, как отсутствие
        # записи, — иначе перебором идентификаторов читают чужие заявки.
        # `_scope_ids` отдаёт назначения, а не идентификаторы людей,
        # поэтому проверка идёт запросом, а не вхождением в множество.
        visible = self._scope_ids(actor)
        if visible is not None and not visible.filter(
            employee_id=request.employee_id
        ).exists():
            raise NotFound("Заявка не найдена")

        self._forbid_closed_paperwork(request)
        return build_application(
            _application_of(request, request.employee, _zone_of(request.employee))
        )

    def for_hr(self, actor: Actor, request_id: uuid.UUID) -> AbsenceRequest:
        """Одна заявка глазами кадровика.

        Нужна отдельной странице заявки: очередь отдаёт страницу строк,
        а по ссылке открывают одну, и искать её перебором очереди —
        значит зависеть от того, попала ли она в текущую выборку.

        Область видимости та же, что у очереди: заявка видна, если виден
        сам сотрудник. Чужая отвечает так же, как несуществующая, —
        иначе по ответу можно перебрать чужие.
        """
        self.access.require(actor, "absences.read")
        row = (
            AbsenceRequest.objects.filter(
                id=request_id, organization_id=actor.organization_id
            )
            .select_related("absence_type", "employee", "parent_request")
            .prefetch_related(
                "documents__file",
                "actions__actor_user",
                "actions__actor_employee",
            )
            .first()
        )
        if row is None:
            raise NotFound("Заявка не найдена")

        visible = self._scope_ids(actor)
        if visible is not None and not visible.filter(
            employee_id=row.employee_id
        ).exists():
            raise NotFound("Заявка не найдена")
        return row

    def pending(self, actor: Actor):
        """Заявки, ждущие решения. Для будущего интерфейса HR."""
        self.access.require(actor, "absences.read")
        return (
            AbsenceRequest.objects.filter(
                organization_id=actor.organization_id, status__in=OPEN_STATUSES
            )
            .select_related("absence_type", "employee", "parent_request")
            .order_by("submitted_at")
        )

    # ------------------------------------------------------------- внутри

    def _approve(
        self, context, request, *, actor, comment, now, override_marks=False
    ) -> None:
        """Подтверждение заявки: сама заявка плюс период отсутствия.

        Замок стоит здесь, а не в `decide`: подтвердить заявку можно ещё
        и автоматически, когда организация обходится без согласования, и
        проверка в одном из двух путей — это дыра во втором.

        `override_marks` — осознанное решение кадровика записать
        больничным дни, в которые человек отмечался. Без него такой
        период не подтверждается вовсе.
        """
        missing = approval_blockers(request)
        if missing:
            raise Conflict(
                "Нельзя подтвердить: не хватает "
                + ", ".join(MISSING_NAMES[one] for one in missing),
                details={"missing": list(missing)},
            )

        tz = context.timezone
        first_day = local_date(request.requested_start_at, tz)
        last_day = local_date(request.requested_end_at, tz)
        sick = (
            request.absence_type.requires_document
            and request.request_kind == "CREATE"
        )

        # Конец периода в будущем — обычное дело, а не ошибка.
        #
        # Справку выписывают на закрытый срок: «нетрудоспособен с 21 по
        # 28». Ждать 28-го, чтобы подтвердить заявку, значит все эти дни
        # держать человека в табеле неоправданно отсутствующим и потом
        # вспомнить вернуться. Отпуск так и оформляется — заранее, и
        # отсутствие до своего начала лежит в `PLANNED`.
        #
        # Вышел раньше срока — заявка отменяется кадровиком, продлился
        # больничный — оформляется продление. Оба пути уже есть, и ни
        # один не требует, чтобы период успел кончиться.

        # Последняя проверка пересечения — вплотную к записи в табель.
        # Между решением кадровика и этой строкой могло появиться другое
        # отсутствие: заявка, поданная сотрудником минуту назад, или
        # период, проставленный вторым кадровиком.
        #
        # У продления исключается и родитель: `_approve` раздвигает
        # ЕГО отсутствие, и новый конец неизбежно перекрывает старый.
        lock_employee(request.employee_id)
        raise_on_overlap(
            request.employee_id, first_day, last_day, tz,
            ignore_request_ids=(request.id, request.parent_request_id),
        )

        if sick and not override_marks:
            marks = attendance_days(request.employee_id, first_day, last_day, tz)
            if marks:
                # Молча списать отработанный день в больничный нельзя:
                # это и потерянная оплата, и испорченная статистика.
                # Отметки при этом не трогаются — решение принимает
                # человек, и оно записывается в историю заявки.
                raise Conflict(
                    "В этом периоде есть отметки входа и выхода: "
                    + ", ".join(_day_ru(one) for one in marks[:5])
                    + (" и другие" if len(marks) > 5 else "")
                    + ". Решите, что с ними делать.",
                    details={
                        "reason": "attendance_conflict",
                        "days": [one.isoformat() for one in marks],
                    },
                )

        previous = request.status
        request.status = "APPROVED"
        request.reviewed_at = now
        request.review_comment = comment or None
        fields = ["status", "reviewed_at", "review_comment", "updated_at"]
        if actor is not None:
            request.reviewed_by_user_id = actor.user_id
            fields.append("reviewed_by_user")
        request.save(update_fields=fields)
        self._act(context, request, "APPROVED", previous, "APPROVED",
                  actor=actor, comment=comment)
        if override_marks:
            # Отдельная строка истории, а не примечание к подтверждению:
            # «утвердил больничный» и «утвердил его поверх отметок» —
            # разные решения, и второе должно быть видно отдельно.
            self._act(
                context, request, "MARKS_OVERRIDDEN", previous, "APPROVED",
                actor=actor, comment=comment,
            )

        if request.request_kind == "EXTEND":
            # Продление сдвигает конец ИСХОДНОГО отсутствия, а не создаёт
            # второе: у человека один непрерывный больничный, а не два.
            parent = request.parent_request
            parent.requested_end_at = request.requested_end_at
            parent.save(update_fields=["requested_end_at", "updated_at"])
            EmployeeAbsence.objects.filter(
                origin_request=parent, status__in=("PLANNED", "ACTIVE")
            ).update(end_at=request.requested_end_at, end_date=last_day)
            if request.absence_type.deducts_leave_balance:
                # Резерв под добавленные дни превращается в списание —
                # ровно как у исходной заявки. Без этого продлением можно
                # было бы отгулять сколько угодно бесплатно.
                self._consume_reservation(request, now)
            return

        absence = EmployeeAbsence.objects.create(
            organization_id=request.organization_id,
            employee_id=request.employee_id,
            absence_type=request.absence_type,
            origin_request=request,
            start_at=request.requested_start_at,
            end_at=request.requested_end_at,
            start_date=first_day,
            end_date=last_day,
            # `PLANNED` бывает только у отпуска: его планируют заранее.
            # У больничного конец не может быть в будущем, значит и
            # начало уже прошло — эта ветка для него недостижима.
            status="ACTIVE" if request.requested_start_at <= now else "PLANNED",
        )

        if request.absence_type.deducts_leave_balance:
            # Резерв превращается в списание: отсутствие подтверждено.
            self._consume_reservation(request, now)
        return absence

    def _view(self, context, request: AbsenceRequest) -> RequestView:
        absence = next(
            (a for a in request.absences.all()
             if a.status in LIVE_ABSENCE_STATUSES),
            None,
        )
        working = 0
        if request.requested_start_at and request.requested_end_at:
            tz = context.timezone
            working = self._working_days(
                context,
                request.requested_start_at.astimezone(tz).date(),
                request.requested_end_at.astimezone(tz).date(),
            )
        return RequestView(
            request=request,
            working_days=working,
            # Считаются только бумаги, принесённые человеком. Системное
            # заявление тоже лежит в этой таблице, и посчитать его
            # справкой значило бы, что больничный подтверждает сам себя.
            documents=sum(
                1 for one in request.documents.all()
                if one.document_type != APPLICATION_DOCUMENT
            ),
            extension_pending=self._pending_extension(request) is not None,
            absence=absence,
            certificate_status=certificate_state(request),
            certificate_comment=certificate_note(request),
        )

    def _pending_extension(self, request: AbsenceRequest):
        return AbsenceRequest.objects.filter(
            parent_request_id=request.id,
            request_kind="EXTEND",
            status__in=OPEN_STATUSES,
        ).first()

    def _require_type(self, context, code: str) -> AbsenceType:
        absence_type = AbsenceType.objects.filter(
            organization_id=context.organization_id, code=code, is_active=True
        ).first()
        if absence_type is None:
            raise NotFound("Такой вид отсутствия недоступен")
        return absence_type

    def _require_own(self, context, request_id: uuid.UUID) -> AbsenceRequest:
        request = (
            AbsenceRequest.objects.filter(
                id=request_id, employee_id=context.employee.id
            )
            .select_related("absence_type", "parent_request")
            .first()
        )
        if request is None:
            raise NotFound("Заявка не найдена")
        return request

    def _check_single_sick_leave(self, context, absence_type) -> None:
        """Незакрытый больничный у человека может быть только один.

        Второй — это всегда ошибка: либо забыл, что уже оформил, либо
        нажал дважды. Разбирать потом два комплекта справок на один
        период дороже, чем не дать создать второй.

        Правило только для того, к чему нужны бумаги: отпусков на разные
        даты человек вправе запланировать сколько угодно, и мешает им
        друг другу только пересечение.

        Вызывается под блокировкой сотрудника — иначе два одновременных
        запроса оба увидели бы «незакрытого нет».
        """
        if not absence_type.requires_document:
            return
        open_row = (
            AbsenceRequest.objects.filter(
                employee_id=context.employee.id,
                absence_type_id=absence_type.id,
                request_kind="CREATE",
                status__in=OPEN_STATUSES,
            )
            .order_by("created_at")
            .first()
        )
        if open_row is None:
            return
        raise Conflict(
            f"{absence_type.name} уже оформлен и ещё не закрыт. "
            "Откройте текущую заявку: приложите справку или отмените её.",
            details={
                "reason": "sick_leave_in_progress",
                "request_id": str(open_row.id),
                "stage": stage_of(open_row),
            },
        )

    def _check_period(self, context, first_day: date, last_day: date) -> None:
        if last_day < first_day:
            raise ValidationFailed(
                "Конец периода раньше начала", details={"reason": "bad_range"}
            )
        if (last_day - first_day).days > 365:
            raise ValidationFailed(
                "Период длиннее года", details={"reason": "too_long"}
            )

    @staticmethod
    def _document_needed(policy, days: int) -> bool:
        """Нужна ли справка к заявке такой длины.

        `document_required_from_day` = 0 означает «с первого дня», то есть
        требование действует всегда. Значение 4 означает «справка нужна,
        если отсутствие длиннее трёх дней» — короткие больничные многие
        организации принимают без неё.
        """
        if not policy.document_required:
            return False
        return days >= max(policy.document_required_from_day, 1)

    def _check_backdating(self, context, first_day: date, policy, now) -> None:
        """Насколько глубоко в прошлое можно оформить отсутствие.

        Ноль означает «без ограничения»: больничный по своей природе
        оформляется задним числом — человек заболел, вышел и принёс
        справку. Организация может поставить границу, но её отсутствие
        не должно ломать главный сценарий.
        """
        allowed = policy.backdating_days_allowed
        if not allowed:
            return
        today = local_date(now, context.timezone)
        if first_day >= today:
            return
        behind = (today - first_day).days
        if behind > allowed:
            raise ValidationFailed(
                "Задним числом отсутствие так далеко не оформляется",
                details={
                    "reason": "backdating_not_allowed",
                    "days_back": behind,
                    "allowed": allowed,
                },
            )

    def _check_lead_time(
        self, context, absence_type, first_day: date, policy, now
    ) -> None:
        """За сколько дней подаётся заявка на отпуск.

        Правило применяется только к типам, которые списывают остаток
        отпуска: больничный по определению не планируется заранее, и
        требовать срок подачи от него было бы бессмыслицей.
        """
        required = policy.vacation_min_days_ahead
        if not required or not absence_type.deducts_leave_balance:
            return
        today = local_date(now, context.timezone)
        ahead = (first_day - today).days
        if ahead < required:
            raise ValidationFailed(
                f"Заявка на отпуск подаётся не позднее чем за {required} дн.",
                details={
                    "reason": "lead_time_required",
                    "days_ahead": ahead,
                    "required": required,
                },
            )

    def leave_check(self, request: AbsenceRequest) -> dict | None:
        """Хватает ли остатка отпуска на период этой заявки.

        Тот же расчёт, что и при подаче, но без отказа: кадровику
        показывают число, решение он принимает сам. Считается на месте,
        а не берётся из заявки: период с тех пор мог измениться, а
        остаток — уйти на другую заявку.

        Дни самой заявки возвращаются в остаток перед сравнением. Они
        уже забронированы при подаче, и без этого собственный резерв
        читался бы как чужой расход — «не хватает» стояло бы всегда.
        """
        if not request.absence_type.deducts_leave_balance:
            return None
        if request.requested_start_at is None or request.requested_end_at is None:
            return None

        context = _ContextFromRequest(request)
        first = local_date(request.requested_start_at, context.timezone)
        last = local_date(request.requested_end_at, context.timezone)
        needed = self._working_days(context, first, last)

        row = LeaveBalance.objects.filter(
            employee_id=request.employee_id,
            absence_type_id=request.absence_type_id,
            year=first.year,
        ).first()
        if row is None:
            # Остаток не начислен — это не «не хватает», а «не о чем
            # говорить»: сравнивать не с чем, и кадровик должен увидеть
            # именно это.
            return {
                "year": first.year,
                "needed_days": needed,
                "available_days": None,
                "enough": False,
            }

        available = (
            row.allocated_minutes
            + row.adjustment_minutes
            - row.used_minutes
            - row.reserved_minutes
        )
        if request.status in OPEN_STATUSES:
            available += needed * MINUTES_PER_WORKING_DAY
        return {
            "year": first.year,
            "needed_days": needed,
            "available_days": available // MINUTES_PER_WORKING_DAY,
            "enough": needed * MINUTES_PER_WORKING_DAY <= available,
        }

    def overlap_check(self, request: AbsenceRequest) -> Overlap | None:
        """С чем период этой заявки спорит у того же человека.

        Та же проверка, что не даёт записать период поверх чужого, но
        показанная заранее: кадровик видит конфликт до решения, а не
        отказом в момент подтверждения.
        """
        if request.requested_start_at is None or request.requested_end_at is None:
            return None
        context = _ContextFromRequest(request)
        return find_overlap(
            request.employee_id,
            local_date(request.requested_start_at, context.timezone),
            local_date(request.requested_end_at, context.timezone),
            context.timezone,
            ignore_request_ids=(request.id,),
        )

    def _working_days(self, context, first_day: date, last_day: date) -> int:
        """Сколько рабочих дней в периоде — по графику и календарю.

        Отпуск считается рабочими днями: суббота, попавшая в отпуск,
        не тратит остаток. Без этого две недели отпуска стоили бы
        четырнадцать дней вместо десяти.
        """
        assignments = list(
            EmployeeScheduleAssignment.objects.filter(
                employee_id=context.employee.id, valid_from__lte=last_day
            )
            .select_related("schedule")
            .prefetch_related("schedule__days")
            .order_by("valid_from")
        )
        exceptions = {
            row.date: (row.is_working_day, row.office_id)
            for row in sorted(
                CalendarException.objects.filter(
                    organization_id=context.organization_id,
                    date__gte=first_day,
                    date__lte=last_day,
                    is_active=True,
                ),
                key=lambda r: r.office_id is not None,
            )
            if row.office_id in (None, getattr(context.office, "id", None))
        }

        if not assignments:
            # Графика нет — считаем все дни периода. Занижать нельзя:
            # заниженный отпуск списал бы меньше, чем человек отгулял.
            return (last_day - first_day).days + 1

        total = 0
        for day in days_in(first_day, last_day):
            schedule = next(
                (
                    a.schedule for a in assignments
                    if a.valid_from <= day
                    and (a.valid_to is None or a.valid_to >= day)
                ),
                None,
            )
            if schedule is None:
                total += 1
                continue
            override = exceptions.get(day)
            if override is not None:
                total += 1 if override[0] else 0
                continue
            match = next(
                (d for d in schedule.days.all() if d.weekday == day.isoweekday()),
                None,
            )
            total += 1 if (match and match.is_working_day) else 0
        return total

    def _check_balance(
        self, context, absence_type, working_days: int, policy, start_at
    ) -> None:
        """Хватает ли остатка ТОГО года, на который просят отпуск.

        Не года подачи заявки: в декабре просят январь, и остаток за январь
        лежит в другой строке. Проверить одну, а списать с другой значило бы
        и пропустить перерасход, и оставить в старом году вечный резерв.
        """
        balance = LeaveBalance.objects.filter(
            employee_id=context.employee.id,
            absence_type=absence_type,
            year=start_at.astimezone(context.timezone).year,
        ).first()
        if balance is None:
            raise Conflict(
                "Остаток отпуска не начислен — обратитесь в отдел кадров",
                details={"reason": "no_balance"},
            )
        available = (
            balance.allocated_minutes
            + balance.adjustment_minutes
            - balance.used_minutes
            - balance.reserved_minutes
        )
        needed = working_days * MINUTES_PER_WORKING_DAY
        if needed > available and not policy.allow_negative_leave_balance:
            raise Conflict(
                "Не хватает остатка отпуска",
                details={
                    "reason": "insufficient_balance",
                    "available_minutes": max(available, 0),
                    "requested_minutes": needed,
                },
            )

    def _reserve(
        self, context, absence_type, working_days: int, start_at
    ) -> None:
        """Отложить минуты под неподтверждённую заявку.

        Резерв, а не списание: заявку ещё могут отклонить, и списывать
        остаток за то, чего не случилось, нельзя. Но и не резервировать
        тоже нельзя — иначе на один остаток подадут пять заявок.

        Год — тот, на который просят отпуск: та же строка, из которой
        потом спишут и в которую вернут.
        """
        year = start_at.astimezone(context.timezone).year
        row = (
            LeaveBalance.objects.select_for_update()
            .filter(
                employee_id=context.employee.id,
                absence_type=absence_type,
                year=year,
            )
            .first()
        )
        if row is None:
            return
        row.reserved_minutes += working_days * MINUTES_PER_WORKING_DAY
        row.save(update_fields=["reserved_minutes", "updated_at"])

    def _release(
        self, context, request: AbsenceRequest, now, *,
        was_approved: bool = False,
    ) -> None:
        """Вернуть дни в остаток: заявка отклонена или отменена.

        Куда возвращать — зависит от того, где эти дни лежат сейчас.
        Пока заявку не подтвердили, они в резерве. После подтверждения
        резерв уже переехал в израсходованное (`_consume_reservation`),
        и вычитать отмену из резерва нельзя: он ноль, вычитание из нуля
        ничего не меняет, а израсходованное остаётся начисленным —
        сотрудник насовсем теряет отпуск, которого не было.
        """
        if not request.absence_type.deducts_leave_balance:
            return
        if not (request.requested_start_at and request.requested_end_at):
            return
        tz = context.timezone
        working = self._working_days(
            context,
            request.requested_start_at.astimezone(tz).date(),
            request.requested_end_at.astimezone(tz).date(),
        )
        row = (
            LeaveBalance.objects.select_for_update()
            .filter(
                employee_id=request.employee_id,
                absence_type=request.absence_type,
                year=request.requested_start_at.astimezone(tz).year,
            )
            .first()
        )
        if row is None:
            return
        field = "used_minutes" if was_approved else "reserved_minutes"
        # Ниже нуля не опускаем: ограничение в базе это запрещает, а
        # расхождение лучше оставить нулём, чем уронить отмену заявки.
        setattr(
            row,
            field,
            max(getattr(row, field) - working * MINUTES_PER_WORKING_DAY, 0),
        )
        row.save(update_fields=[field, "updated_at"])

    def _consume_reservation(self, request: AbsenceRequest, now) -> None:
        context = _ContextFromRequest(request)
        tz = context.timezone
        working = self._working_days(
            context,
            request.requested_start_at.astimezone(tz).date(),
            request.requested_end_at.astimezone(tz).date(),
        )
        minutes = working * MINUTES_PER_WORKING_DAY
        row = (
            LeaveBalance.objects.select_for_update()
            .filter(
                employee_id=request.employee_id,
                absence_type=request.absence_type,
                year=request.requested_start_at.astimezone(tz).year,
            )
            .first()
        )
        if row is None:
            return
        row.reserved_minutes = max(row.reserved_minutes - minutes, 0)
        row.used_minutes += minutes
        row.save(update_fields=["reserved_minutes", "used_minutes", "updated_at"])

    def _attach(self, context, request, document, policy) -> AbsenceDocument:
        stored = store(
            document,
            organization_id=context.organization_id,
            employee=context.employee,
            allowed_types=policy.allowed_document_types,
            max_bytes=policy.max_document_bytes,
        )
        record = AbsenceDocument.objects.create(
            organization_id=context.organization_id,
            absence_request=request,
            file=stored.file,
            document_type="MEDICAL_CERTIFICATE",
            verification_status="PENDING",
        )
        self._act(context, request, "DOCUMENT_ATTACHED", None, None)
        return record

    def _notify(self, request, event: str) -> None:
        """Уведомление о судьбе заявки — той же транзакцией, что и она сама.

        Отдельная транзакция означала бы, что сообщение может уцелеть при
        откате заявки: человек получил бы «отпуск подтверждён» про отпуск,
        которого нет.

        Ключ повтора собран из заявки и события: обработчик, сработавший
        дважды, даёт одну строку, а не два одинаковых сообщения в чате.
        """
        tz = _ContextFromRequest(request).timezone
        first = (
            request.requested_start_at.astimezone(tz).date()
            if request.requested_start_at else None
        )
        last = (
            request.requested_end_at.astimezone(tz).date()
            if request.requested_end_at else None
        )
        if event == "cancelled":
            body = messages.REQUEST_CANCELLED.format(
                first=messages.human_date(first), last=messages.human_date(last)
            )
        elif request.request_kind == "EXTEND" and event == "approved":
            body = messages.SICK_EXTENSION_APPROVED.format(
                last=messages.human_date(last)
            )
        elif request.request_kind == "EXTEND" and event == "rejected":
            body = messages.SICK_EXTENSION_REJECTED.format(
                last=messages.human_date(last)
            )
        else:
            body = messages.for_request(
                request.absence_type.code, event, first, last
            )

        enqueue(
            organization_id=request.organization_id,
            employee_id=request.employee_id,
            notification_type=f"absence.{event}",
            body=body,
            idempotency_key=f"absence:{request.id}:{event}",
            related_entity_type=ENTITY_REQUEST,
            related_entity_id=request.id,
        )

    def _act(
        self, context, request, action, previous, new, *, actor=None, comment=None
    ) -> None:
        """Строка в неизменяемой истории заявки.

        Комментарий сотрудника сюда НЕ попадает: в нём бывает диагноз,
        а история заявки видна шире, чем сама заявка.
        """
        AbsenceAction.objects.create(
            organization_id=request.organization_id,
            absence_request=request,
            action=action,
            actor_user_id=actor.user_id if actor else None,
            actor_employee_id=None if actor else request.employee_id,
            previous_status=previous,
            new_status=new,
            comment=comment or None,
        )

    def _forbid_self_approval(self, actor: Actor, request) -> None:
        """Своё отсутствие не согласовывают.

        Проверка по связи пользователь -> сотрудник: кадровик, у которого
        есть и учётная запись, и карточка сотрудника, не должен подтверждать
        сам себе отпуск.
        """
        from humotech.accounts.models import User

        # Связь идёт от учётной записи к карточке (`User.employee`),
        # а не наоборот: у большинства сотрудников учётной записи нет вовсе.
        own = (
            User.objects.filter(id=actor.user_id)
            .values_list("employee_id", flat=True)
            .first()
        )
        if own is not None and own == request.employee_id:
            raise PermissionDenied(
                "Своё отсутствие согласовывает кто-то другой",
                details={"reason": "self_approval"},
            )


class _ContextFromRequest:
    """Минимальный контекст, собранный из заявки.

    Нужен там, где действует HR: у него нет `EmployeeContext` сотрудника,
    а расчёт рабочих дней и часовой пояс требуются те же самые. Собирается
    из самой заявки, а не из запроса, — подменить нечем.
    """

    def __init__(self, request: AbsenceRequest) -> None:
        self._request = request

    @property
    def employee(self):
        return self._request.employee

    @property
    def organization_id(self):
        return self._request.organization_id

    @property
    def office(self):
        from humotech.telegram.identity import current_assignment

        assignment = current_assignment(self._request.employee_id)
        return assignment.office if assignment else None

    @property
    def timezone(self):
        from humotech.core.timeframes import office_zone

        return office_zone(self.office)


#: Заявка, которая ещё держит минуты в остатке: резерв (до решения) или
#: израсходованное (после одобрения). Отклонённая и отменённая их вернула.
HOLDING_STATUSES = ("DRAFT", "SUBMITTED", "IN_REVIEW", "APPROVED")


def hr_period_summary(request: AbsenceRequest) -> dict:
    """Дни заявки, пересечение и остаток отпуска до и после — для кадровика.

    Живёт здесь, а не у вызывающего: и рабочие дни, и остаток считаются
    по правилам отсутствий, и второй расчёт рядом с ними означал бы два
    разных ответа на один вопрос.

    Остаток берётся из текущего состояния баланса, а не пересчитывается
    заново. Минуты нерассмотренной заявки уже лежат в резерве
    (`_reserve`), одобрение переносит их в израсходованное —
    ДОСТУПНЫЙ остаток при этом не меняется. Поэтому «до заявки» — это
    доступно плюс запрошено, «после» — доступно, и обе величины верны
    и для поданной заявки, и для одобренной. У отклонённой и отменённой
    минуты уже возвращены, и «до» равно доступному.
    """
    first = request.requested_start_at.date() if request.requested_start_at else None
    last = request.requested_end_at.date() if request.requested_end_at else None
    if first is None or last is None:
        return {
            "calendar_days": None,
            "working_days": None,
            "balance_before_days": None,
            "balance_after_days": None,
            "overlaps": False,
        }

    context = _ContextFromRequest(request)
    working = AbsenceService()._working_days(context, first, last)

    overlaps = (
        EmployeeAbsence.objects.filter(
            employee_id=request.employee_id,
            status__in=("PLANNED", "ACTIVE"),
            start_at__lt=request.requested_end_at,
            end_at__gt=request.requested_start_at,
        )
        .exclude(origin_request_id=request.id)
        .exists()
        or AbsenceRequest.objects.filter(
            employee_id=request.employee_id,
            request_kind__in=("CREATE", "EXTEND"),
            status__in=OPEN_STATUSES,
            requested_start_at__lt=request.requested_end_at,
            requested_end_at__gt=request.requested_start_at,
        )
        .exclude(id=request.id)
        .exists()
    )

    before = after = None
    if request.absence_type.deducts_leave_balance:
        balance = LeaveBalance.objects.filter(
            employee_id=request.employee_id,
            absence_type_id=request.absence_type_id,
            year=first.year,
        ).first()
        if balance is not None:
            available = (
                balance.allocated_minutes
                + balance.adjustment_minutes
                - balance.used_minutes
                - balance.reserved_minutes
            )
            needed = working * MINUTES_PER_WORKING_DAY
            held = available + needed if request.status in HOLDING_STATUSES else available
            before = round(held / MINUTES_PER_WORKING_DAY, 1)
            after = round((held - needed) / MINUTES_PER_WORKING_DAY, 1)

    return {
        "calendar_days": (last - first).days + 1,
        "working_days": working,
        "balance_before_days": before,
        "balance_after_days": after,
        "overlaps": overlaps,
    }


__all__ = [
    "AbsenceService",
    "BalanceView",
    "Overlap",
    "RequestView",
    "approval_blockers",
    "attendance_days",
    "certificate_note",
    "certificate_state",
    "find_overlap",
    "guard_of",
    "hr_period_summary",
    "lock_employee",
    "raise_on_overlap",
    "stage_of",
]


def _zone_of(employee):
    """Пояс офиса, в котором человек числится."""
    from humotech.core.timeframes import office_zone
    from humotech.employees.models import EmployeeAssignment

    place = (
        EmployeeAssignment.objects.filter(employee_id=employee.id, is_primary=True)
        .select_related("office", "office__organization")
        .order_by("-valid_from")
        .first()
    )
    return office_zone(place.office if place else None)


def _application_of(request, employee, tz) -> Application:
    """Заявка и человек — в поля бланка.

    Один сборщик на сотрудника и на кадровика: два одинаковых бланка,
    собранных по-разному, однажды разойдутся, и спорить придётся уже
    о том, какой из них настоящий.
    """
    from humotech.employees.models import EmployeeAssignment
    from humotech.organizations.models import Organization

    # Дат может не быть вовсе: больничный оформляют, не зная, когда
    # выйдешь. Бланк при этом нужен сразу — его как раз и несут в отдел
    # кадров, — поэтому период в нём остаётся пустым местом, которое
    # заполняют от руки, когда выписка на руках.
    first_day = (
        request.requested_start_at.astimezone(tz).date()
        if request.requested_start_at else None
    )
    last_day = (
        request.requested_end_at.astimezone(tz).date()
        if request.requested_end_at else None
    )
    place = (
        EmployeeAssignment.objects.filter(employee_id=employee.id, is_primary=True)
        .select_related("office", "department", "position")
        .order_by("-valid_from")
        .first()
    )
    organization = Organization.objects.filter(
        id=request.organization_id
    ).first()

    return Application(
        organization=organization.name if organization else "HUMOTECH",
        employee_name=" ".join(
            one for one in
            [employee.last_name, employee.first_name, employee.middle_name]
            if one
        ),
        position=place.position.name if place and place.position else None,
        department=place.department.name if place and place.department else None,
        office=place.office.name if place and place.office else None,
        absence_name=request.absence_type.name,
        absence_code=request.absence_type.code,
        first_day=first_day,
        last_day=last_day,
        days=(
            (last_day - first_day).days + 1
            if first_day and last_day else None
        ),
        comment=request.employee_comment,
        # Восемь знаков идентификатора: достаточно, чтобы найти заявку,
        # и коротко настолько, чтобы переписать с бумаги от руки.
        number=str(request.id)[:8].upper(),
    )
