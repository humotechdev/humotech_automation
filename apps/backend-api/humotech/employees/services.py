"""Кадровые операции: приём, перевод, изменение данных, увольнение.

Главное правило этого модуля: сотрудник никогда не удаляется и его история
никогда не переписывается. Офис, отдел и должность лежат не в карточке, а в
`employee_assignments` — периодами. Поэтому перевод — это закрытие текущего
периода и открытие нового, а не UPDATE одной строки: иначе прошлые отметки
о входе оказались бы отнесены к офису, в котором человек тогда не работал.

Периоды защищены EXCLUDE-ограничением по `daterange(valid_from, valid_to, '[]')`
с включительными границами. Отсюда правило, видное в коде: старый период
закрывается днём РАНЬШЕ начала нового, иначе общий день считается пересечением.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import Lower, Replace

from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Cursor, Page, normalize_limit, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import (
    clean_code,
    clean_text,
    require_order,
    validate_email,
    validate_phone,
)
from humotech.departments.models import Department
from humotech.employees.models import (
    Employee,
    EmployeeAssignment,
    EmployeeDocument,
)
from humotech.schedules.models import EmployeeScheduleAssignment
from humotech.telegram.models import TelegramAccount
from humotech.employees.selectors import require_visible_employee
from humotech.offices.models import Office
from humotech.positions.models import Position
from humotech.schedules.models import EmployeeScheduleAssignment

#: Фильтр по справочнику: одно значение или несколько.
#:
#: Кадровик смотрит «два офиса и три отдела», а не по одному, поэтому
#: списки. Одиночное значение осталось допустимым: прежние ссылки на
#: список с одним офисом продолжают работать.
Ids = uuid.UUID | str | Sequence[uuid.UUID | str] | None
from humotech.telegram.models import TelegramAccount

CARD_FIELDS = (
    "employee_number", "first_name", "last_name", "middle_name", "phone",
    "corporate_email", "personal_email", "birth_date", "hire_date",
    "probation_from", "probation_to", "mentor_employee_id",
    "termination_date", "termination_reason", "preferred_language",
    "employment_status",
)
ASSIGNMENT_FIELDS = (
    "office_id", "department_id", "position_id", "manager_employee_id",
    "employment_type", "work_mode", "valid_from", "valid_to",
)

#: Поля карточки, которые правятся её же правкой.
#:
#: Пол и семейное положение здесь по той же причине, что и дата
#: рождения: это анкетные данные человека, а не его назначение. Им
#: незачем период действия — женитьба не создаёт новый период работы.
CONTACT_FIELDS = ("first_name", "last_name", "middle_name", "phone",
                  "corporate_email", "personal_email", "birth_date",
                  "preferred_language", "employee_number",
                  "gender", "marital_status",
                  "probation_from", "probation_to", "mentor_employee_id")

# Статусы, при которых сотрудник считается работающим.
WORKING_STATUSES = ("ACTIVE", "PROBATION")
# Статусы, после которых кадровые операции недоступны.
FINAL_STATUSES = ("TERMINATED", "ARCHIVED")


def _normalize_search_query(value: str | None) -> str:
    """Единая нормализация строки поиска без опасной транслитерации."""
    # NUL PostgreSQL в строке не принимает: без чистки `q=%00` давал 500.
    cleaned = (value or "").replace("\x00", "")[:200]
    return " ".join(cleaned.strip().lower().replace("ё", "е").split())


def current_primary_assignment_filter(at: date) -> Q:
    """Одно определение «текущего основного назначения» на весь модуль.

    Держать его в одном месте важнее, чем кажется: список, карточка и перевод
    обязаны понимать «сейчас» одинаково, иначе сотрудник попадает в список по
    одному офису, а в карточке показывается по другому.
    """
    return (
        Q(is_primary=True)
        & Q(valid_from__lte=at)
        & (Q(valid_to__isnull=True) | Q(valid_to__gte=at))
    )


def roster_assignment_filter(at: date) -> Q:
    """Кто состоит в смене на этот день — одно определение на всю систему.

    К «текущему основному назначению» добавляется работающий статус
    сотрудника. Без этого условия уволенный человек с незакрытым
    назначением продолжает считаться: состав смены его уже не видит
    (`attendance.hr` и `analytics.dashboard` фильтруют по статусу), а
    отчёт `analytics` видел — и знаменатель явки расходился с карточкой
    «по графику» на число таких людей.

    Правило одно и лежит здесь, чтобы разойтись снова не смогло.
    """
    return current_primary_assignment_filter(at) & Q(
        employee__employment_status__in=WORKING_STATUSES
    )


@dataclass(frozen=True)
class TelegramBinding:
    """Состояние привязки Telegram. Сам идентификатор наружу не отдаётся:
    для CRM важен факт привязки, а не номер аккаунта."""

    connected: bool
    status: str | None = None
    username: str | None = None
    connected_at: object | None = None


@dataclass(frozen=True)
class EmployeeCard:
    """Полная карточка: собрана из нескольких таблиц запросами сервиса.

    Отдельный тип, а не модель с довешенными атрибутами: состав карточки
    виден в одном месте, и сериализатору не приходится гадать, что уже
    загружено, а что вызовет ещё один запрос.
    """

    employee: Employee
    current_assignment: EmployeeAssignment | None
    current_schedule: EmployeeScheduleAssignment | None
    telegram: TelegramBinding
    assignment_history: list[EmployeeAssignment] = field(default_factory=list)
    documents: list[EmployeeDocument] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        parts = [self.employee.last_name, self.employee.first_name,
                 self.employee.middle_name]
        return " ".join(part for part in parts if part)


class EmployeeService(BaseService):
    """Требует `employees.read` для чтения, `employees.manage` для изменений,
    `employees.archive` — для увольнения и перевода в архив."""

    # ------------------------------------------------------------------ чтение

    def highlights(
        self,
        actor: Actor,
        *,
        office_id: Ids = None,
        region_id: Ids = None,
        department_id: Ids = None,
        position_id: Ids = None,
        at: date | None = None,
    ) -> dict:
        """Новички, именинники и люди без графика.

        Три вопроса про всю организацию сразу. По странице списка на них
        не ответить: там восемь строк из скольких угодно, и «двое
        именинников» превратилось бы в «двое среди показанных».

        Имена возвращаются короткими списками — правая колонка показывает
        несколько лиц и число остальных. Больше пяти оттуда всё равно не
        видно, и отдавать двести строк было бы тратой.
        """
        self.access.require(actor, "employees.read")
        at = at or date.today()
        base = self._visible(
            actor,
            status="ACTIVE",
            office_id=office_id,
            region_id=region_id,
            department_id=department_id,
            position_id=position_id,
            at=at,
        )

        recent = base.filter(hire_date__gte=at - timedelta(days=30))
        # День рождения сравнивается по дню и месяцу, а не по дате: год
        # рождения к сегодняшнему дню отношения не имеет.
        birthdays = base.filter(birth_date__month=at.month, birth_date__day=at.day)

        scheduled = set(
            EmployeeScheduleAssignment.objects.filter(
                Q(valid_from__lte=at) & (Q(valid_to__isnull=True) | Q(valid_to__gte=at)),
                employee__organization_id=actor.organization_id,
            ).values_list("employee_id", flat=True)
        )
        unscheduled = [row for row in base.exclude(id__in=scheduled)]

        def short(rows) -> list[dict]:
            return [
                {
                    "id": str(row.id),
                    "full_name": " ".join(
                        part for part in
                        (row.last_name, row.first_name, row.middle_name) if part
                    ),
                    "employee_number": row.employee_number,
                    "photo": row.photo_id is not None,
                }
                for row in rows[:5]
            ]

        return {
            "recent_hires": recent.count(),
            "recent": short(list(recent.order_by("-hire_date"))),
            "birthdays_today": birthdays.count(),
            "birthdays": short(list(birthdays.order_by("last_name"))),
            "without_schedule": len(unscheduled),
            "unscheduled": short(unscheduled),
        }

    @staticmethod
    def _page_at(queryset, *, limit: int | None, offset: int) -> Page:
        """Страница по сдвигу, с тем же порядком, что и у курсора.

        Порядок обязан совпадать: иначе первая страница, взятая курсором,
        и вторая, взятая сдвигом, окажутся из разных списков.
        """
        size = normalize_limit(limit)
        rows = list(
            queryset.order_by("-created_at", "-id")[offset:offset + size + 1]
        )
        has_more = len(rows) > size
        items = rows[:size]
        return Page(
            items=items,
            next_cursor=(
                Cursor(created_at=items[-1].created_at, id=items[-1].id).encode()
                if has_more and items else None
            ),
            has_more=has_more,
        )

    def counts(
        self,
        actor: Actor,
        *,
        search: str | None = None,
        office_id: Ids = None,
        region_id: Ids = None,
        department_id: Ids = None,
        position_id: Ids = None,
        at: date | None = None,
    ) -> dict[str, int]:
        """Сколько сотрудников в каждом состоянии — по ТЕКУЩИМ фильтрам.

        Нужно вкладкам списка. Считать это на клиенте нельзя: страница
        приходит курсором, и по ней видно только десять строк из скольких
        угодно. Состояние в счёт не входит намеренно — иначе, выбрав
        «Активные», человек видел бы нули у остальных вкладок.
        """
        self.access.require(actor, "employees.read")
        queryset = self._visible(
            actor,
            search=search,
            office_id=office_id,
            region_id=region_id,
            department_id=department_id,
            position_id=position_id,
            at=at or date.today(),
        )
        rows = queryset.values("employment_status").annotate(n=Count("id"))
        by_status = {row["employment_status"]: row["n"] for row in rows}
        return {"total": sum(by_status.values()), **by_status}

    @staticmethod
    def _ids(value: Ids) -> list | None:
        """Одно значение или несколько — всегда список. Пусто — `None`."""
        if value is None:
            return None
        many = list(value) if isinstance(value, (list, tuple, set)) else [value]
        kept = [one for one in many if one]
        return kept or None

    def _visible(
        self,
        actor: Actor,
        *,
        at: date,
        search: str | None = None,
        status: str | None = None,
        office_id: Ids = None,
        region_id: Ids = None,
        department_id: Ids = None,
        position_id: Ids = None,
    ):
        """Набор сотрудников под фильтрами и областью видимости.

        Один источник и для страницы, и для счётчиков: разойдись они —
        число на вкладке перестало бы совпадать со списком под ней.
        """
        queryset = Employee.objects.filter(organization_id=actor.organization_id)

        if status:
            # Вкладка «Уволенные» покрывает два состояния сразу, поэтому
            # список, а не одно значение.
            values = [part for part in str(status).replace("\x00", "").split(",") if part]
            queryset = queryset.filter(employment_status__in=values)

        # NUL PostgreSQL в строке не принимает: `search=%00` давал 500.
        search = (search or "").replace("\x00", "")[:200]
        if search.strip():
            pattern = search.strip()
            queryset = queryset.filter(
                Q(first_name__icontains=pattern)
                | Q(last_name__icontains=pattern)
                | Q(middle_name__icontains=pattern)
                | Q(employee_number__icontains=pattern)
                | Q(corporate_email__icontains=pattern)
                | Q(phone__icontains=pattern)
            )

        condition = self._office_scope_condition(
            actor,
            office_id=self._ids(office_id),
            region_id=self._ids(region_id),
        )
        departments = self._ids(department_id)
        if departments:
            clause = Q(department_id__in=departments)
            condition = clause if condition is None else (condition & clause)
        positions = self._ids(position_id)
        if positions:
            clause = Q(position_id__in=positions)
            condition = clause if condition is None else (condition & clause)

        if condition is not None:
            # Сотрудник виден по офису своего ТЕКУЩЕГО основного назначения.
            # Подзапрос, а не JOIN: соединение размножило бы строки, если
            # у сотрудника найдётся второе назначение, и пагинация поехала бы.
            visible_ids = EmployeeAssignment.objects.filter(
                current_primary_assignment_filter(at) & condition
            ).values_list("employee_id", flat=True)
            queryset = queryset.filter(id__in=visible_ids)

        return queryset

    def list(
        self,
        actor: Actor,
        *,
        search: str | None = None,
        status: str | None = None,
        office_id: Ids = None,
        region_id: Ids = None,
        department_id: Ids = None,
        position_id: Ids = None,
        at: date | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        offset: int | None = None,
    ) -> Page:
        """Страница списка сотрудников.

        `offset` — для перехода на произвольную страницу. Курсор на это
        не способен по устройству: он говорит «дальше этой записи», и
        добраться до тридцать второй страницы им можно только пройдя
        тридцать одну. Экран со списком страниц без сдвига показывал бы
        номера, по которым нельзя нажать.

        Курсор при этом остаётся основным способом: он устойчив к
        вставкам между запросами, а сдвиг — нет. Когда заданы оба,
        выигрывает сдвиг: его попросили явно.
        """
        self.access.require(actor, "employees.read")
        at = at or date.today()

        queryset = self._visible(
            actor,
            search=search,
            status=status,
            office_id=office_id,
            region_id=region_id,
            department_id=department_id,
            position_id=position_id,
            at=at,
        )
        if offset:
            page = self._page_at(queryset, limit=limit, offset=offset)
        else:
            page = paginate(queryset, limit=limit, cursor=cursor)

        # Справочники всей страницы — ОДНИМ запросом. Именно это отделяет
        # список от N+1: один оператор на страницу вместо одного на строку.
        assignments = {
            row.employee_id: row
            for row in EmployeeAssignment.objects.filter(
                current_primary_assignment_filter(at),
                employee_id__in=[e.id for e in page.items],
            ).select_related("office", "office__region", "department", "position")
        }
        # Действующий график — тем же приёмом: один оператор на страницу.
        # В колонке списка он нужен всем строкам, и запрос на строку
        # превратил бы десять строк в одиннадцать обращений к базе.
        schedules = {
            row.employee_id: row.schedule
            for row in EmployeeScheduleAssignment.objects.filter(
                Q(valid_from__lte=at)
                & (Q(valid_to__isnull=True) | Q(valid_to__gte=at)),
                employee_id__in=[e.id for e in page.items],
            ).select_related("schedule").prefetch_related("schedule__days")
        }
        # Состояние Telegram — из самих привязок, а не из флага
        # `telegram_connected`: этот флаг задумывался денормализованным,
        # но не обновляется ни одной операцией и всегда остаётся `false`.
        # Показывать по нему «не привязан» человеку с рабочей привязкой
        # значит врать в списке.
        accounts = {
            row[0]: row[1:]
            for row in TelegramAccount.objects.filter(
                employee_id__in=[e.id for e in page.items]
            ).values_list("employee_id", "status", "telegram_username")
        }
        for employee in page.items:
            employee.current_assignment = assignments.get(employee.id)
            employee.current_schedule = schedules.get(employee.id)
            binding = accounts.get(employee.id)
            employee.telegram_state = binding[0] if binding else None
            employee.telegram_username = binding[1] if binding else None
        return page

    def search(self, actor: Actor, *, query: str, at: date | None = None) -> list[dict]:
        """Короткий поиск для верхней панели CRM.

        Это намеренно не вариант ``list``: верхняя панель не должна получать
        ни контакты, ни историю, ни произвольный размер страницы.  Поиск всегда
        ограничен десятью строками и проходит через ту же область офисов, что
        и карточка сотрудника.
        """
        self.access.require(actor, "employees.read")
        normalized = _normalize_search_query(query)
        if len(normalized) < 2:
            return []

        at = at or date.today()
        telegram_query = normalized.lstrip("@")
        tokens = [part for part in normalized.split(" ") if part]
        queryset = self._visible(actor, at=at).annotate(
            search_first=Replace(Lower("first_name"), Value("ё"), Value("е")),
            search_last=Replace(Lower("last_name"), Value("ё"), Value("е")),
            search_middle=Replace(Lower("middle_name"), Value("ё"), Value("е")),
            search_number=Lower("employee_number"),
            search_email=Lower("corporate_email"),
            search_telegram=Replace(
                Lower("telegram_account__telegram_username"), Value("ё"), Value("е")
            ),
        )

        # Каждое слово ФИО должно найтись хотя бы в одной допустимой части
        # имени. Так «Иван Петр» не превращается в поиск по одному Ивану.
        for token in tokens:
            queryset = queryset.filter(
                Q(search_first__contains=token)
                | Q(search_last__contains=token)
                | Q(search_middle__contains=token)
                | Q(search_number__contains=token)
                | Q(search_telegram__contains=token.lstrip("@"))
                | Q(search_email__contains=token)
            )

        first_name = tokens[0]
        queryset = queryset.annotate(
            search_rank=Case(
                When(search_number=normalized, then=Value(0)),
                When(search_telegram=telegram_query, then=Value(1)),
                When(
                    Q(search_last=normalized)
                    | Q(search_first=normalized)
                    | Q(search_middle=normalized),
                    then=Value(2),
                ),
                When(
                    Q(search_last__startswith=first_name)
                    | Q(search_first__startswith=first_name)
                    | Q(search_middle__startswith=first_name),
                    then=Value(3),
                ),
                default=Value(4),
                output_field=IntegerField(),
            ),
            status_rank=Case(
                When(employment_status__in=WORKING_STATUSES, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        ).distinct().order_by("search_rank", "status_rank", "last_name", "first_name", "id")
        rows = list(queryset[:10])

        assignments = {
            row.employee_id: row
            for row in EmployeeAssignment.objects.filter(
                current_primary_assignment_filter(at), employee_id__in=[row.id for row in rows]
            ).select_related("office", "department", "position")
        }
        accounts = {
            row.employee_id: row.telegram_username
            for row in TelegramAccount.objects.filter(employee_id__in=[row.id for row in rows])
        }
        return [
            {
                "id": str(row.id),
                "employee_number": row.employee_number,
                "full_name": " ".join(
                    part for part in (row.last_name, row.first_name, row.middle_name) if part
                ),
                "employment_status": row.employment_status,
                "photo": row.photo_id is not None,
                "position_name": assignments[row.id].position.name if assignments.get(row.id) and assignments[row.id].position else None,
                "department_name": assignments[row.id].department.name if assignments.get(row.id) and assignments[row.id].department else None,
                "office_name": assignments[row.id].office.name if assignments.get(row.id) else None,
                "telegram_username": accounts.get(row.id),
            }
            for row in rows
        ]

    def get(
        self, actor: Actor, employee_id: uuid.UUID, *, at: date | None = None
    ) -> EmployeeCard:
        """Полная карточка: назначение, график, Telegram и история переводов."""
        self.access.require(actor, "employees.read")
        return self._card(actor, employee_id, at=at)

    def _card(
        self, actor: Actor, employee_id: uuid.UUID, *, at: date | None = None
    ) -> EmployeeCard:
        """Сборка карточки без проверки `employees.read`.

        Операции записи возвращают карточку результата, и требовать для этого
        отдельное право на чтение нельзя: роль с `employees.manage` без
        `employees.read` успешно сохранила бы данные и следом получила отказ —
        то есть исключение при уже применённых изменениях.
        """
        at = at or date.today()
        employee = self._require_visible_employee(actor, employee_id, at=at)

        history = list(
            EmployeeAssignment.objects.filter(employee_id=employee_id)
            .select_related("office", "office__region", "department", "position")
            .order_by("-valid_from", "-created_at")
        )
        current = next(
            (
                row for row in history
                if row.is_primary
                and row.valid_from <= at
                and (row.valid_to is None or row.valid_to >= at)
            ),
            None,
        )

        schedule = (
            EmployeeScheduleAssignment.objects.filter(employee_id=employee_id)
            .filter(valid_from__lte=at)
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=at))
            .select_related("schedule")
            .order_by("-valid_from")
            .first()
        )

        documents = list(
            EmployeeDocument.objects.filter(employee_id=employee_id)
            .select_related("file")
            .order_by("kind", "created_at")
        )

        account = TelegramAccount.objects.filter(employee_id=employee_id).first()
        telegram = TelegramBinding(
            connected=account is not None and account.status == "ACTIVE",
            status=account.status if account else None,
            username=account.telegram_username if account else None,
            connected_at=account.connected_at if account else None,
        )

        return EmployeeCard(
            employee=employee,
            current_assignment=current,
            current_schedule=schedule,
            telegram=telegram,
            assignment_history=history,
            documents=documents,
        )

    def assignment_history(
        self, actor: Actor, employee_id: uuid.UUID
    ) -> list[EmployeeAssignment]:
        self.access.require(actor, "employees.read")
        self._require_visible_employee(actor, employee_id)
        return list(
            EmployeeAssignment.objects.filter(employee_id=employee_id)
            .select_related("office", "office__region", "department", "position")
            .order_by("-valid_from", "-created_at")
        )

    # ------------------------------------------------------------------- приём

    def create(
        self,
        actor: Actor,
        *,
        employee_number: str,
        first_name: str,
        last_name: str,
        hire_date: date,
        office_id: uuid.UUID,
        employment_type: str = "FULL_TIME",
        work_mode: str = "ONSITE",
        region_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        position_id: uuid.UUID | None = None,
        manager_employee_id: uuid.UUID | None = None,
        employment_status: str = "ACTIVE",
        **contacts,
    ) -> EmployeeCard:
        """Сотрудник и его первое назначение создаются одной операцией.

        Порознь нельзя: сотрудник без назначения не привязан ни к одному офису,
        то есть не виден ни одному HR с территориальной областью — и починить
        это можно было бы только напрямую в базе.
        """
        self.access.require(actor, "employees.manage")

        office = self._require_assignable_office(actor, office_id)
        if region_id is not None and region_id != office.region_id:
            raise ValidationFailed(
                "Указанный регион не совпадает с регионом офиса",
                details={"region_id": str(region_id),
                         "office_region_id": str(office.region_id)},
            )
        department_id = self._validate_department(
            actor, department_id, office_id=office.id
        )
        position_id = self._validate_position(actor, position_id)
        manager_id = self._validate_manager(actor, manager_employee_id)

        if employment_status not in (*WORKING_STATUSES, "SUSPENDED"):
            raise ValidationFailed(
                "Нового сотрудника нельзя завести сразу уволенным",
                details={"employment_status": employment_status},
            )

        with self.atomic():
            employee = Employee.objects.create(
                organization_id=actor.organization_id,
                employee_number=clean_code(
                    employee_number, field="employee_number"
                ),
                first_name=clean_text(first_name, field="first_name",
                                      required=True, max_length=100),
                last_name=clean_text(last_name, field="last_name",
                                     required=True, max_length=100),
                middle_name=clean_text(contacts.get("middle_name"),
                                       field="middle_name", max_length=100),
                phone=validate_phone(contacts.get("phone")),
                corporate_email=validate_email(contacts.get("corporate_email"),
                                               field="corporate_email"),
                personal_email=validate_email(contacts.get("personal_email"),
                                              field="personal_email"),
                birth_date=contacts.get("birth_date"),
                hire_date=hire_date,
                probation_from=contacts.get("probation_from"),
                probation_to=contacts.get("probation_to"),
                preferred_language=contacts.get("preferred_language", "ru"),
                employment_status=employment_status,
            )
            EmployeeAssignment.objects.create(
                organization_id=actor.organization_id,
                employee=employee,
                office=office,
                department_id=department_id,
                position_id=position_id,
                manager_employee_id=manager_id,
                employment_type=employment_type,
                work_mode=work_mode,
                is_primary=True,
                valid_from=hire_date,
            )
            self.audit.record(
                actor, action="employee.create", entity_type="employees",
                entity_id=employee.id,
                after=snapshot(employee, CARD_FIELDS) | {
                    "office_id": str(office.id),
                    "region_id": str(office.region_id),
                },
            )
        return self._card(actor, employee.id)

    # -------------------------------------------------------------- изменение

    def update(self, actor: Actor, employee_id: uuid.UUID, **changes) -> EmployeeCard:
        """Только собственные данные сотрудника.

        Офис, отдел, должность и график живут в назначениях и меняются
        отдельными операциями: у них есть период действия, а у поля карточки
        его нет.
        """
        self.access.require(actor, "employees.manage")
        employee = self._require_visible_employee(actor, employee_id)

        unknown = set(changes) - set(CONTACT_FIELDS)
        if unknown:
            raise ValidationFailed(
                "Эти поля меняются отдельными операциями, а не правкой карточки",
                details={"fields": sorted(unknown)},
            )

        before = snapshot(employee, CARD_FIELDS)
        if changes.get("first_name") is not None:
            employee.first_name = clean_text(
                changes["first_name"], field="first_name", required=True,
                max_length=100,
            )
        if changes.get("last_name") is not None:
            employee.last_name = clean_text(
                changes["last_name"], field="last_name", required=True,
                max_length=100,
            )
        if "middle_name" in changes:
            employee.middle_name = clean_text(
                changes["middle_name"], field="middle_name", max_length=100
            )
        if "phone" in changes:
            employee.phone = validate_phone(changes["phone"])
        if "corporate_email" in changes:
            employee.corporate_email = validate_email(
                changes["corporate_email"], field="corporate_email"
            )
        if "personal_email" in changes:
            employee.personal_email = validate_email(
                changes["personal_email"], field="personal_email"
            )
        if "birth_date" in changes:
            employee.birth_date = changes["birth_date"]
        # Пустая строка значит «не указано»: в базе на этих полях стоит
        # проверка допустимых значений, и «» её не пройдёт.
        if "gender" in changes:
            employee.gender = changes["gender"] or None
        if "marital_status" in changes:
            employee.marital_status = changes["marital_status"] or None
        if "probation_from" in changes:
            employee.probation_from = changes["probation_from"]
        if "probation_to" in changes:
            employee.probation_to = changes["probation_to"]
        if "mentor_employee_id" in changes:
            mentor = changes["mentor_employee_id"]
            if mentor is not None:
                if str(mentor) == str(employee.id):
                    raise ValidationFailed("Сотрудник не может быть своим наставником",
                                           details={"field": "mentor_employee_id"})
                if not Employee.objects.filter(id=mentor, organization_id=employee.organization_id).exists():
                    raise ValidationFailed("Наставник не найден", details={"field": "mentor_employee_id"})
            employee.mentor_employee_id = mentor
        # Порядок дат проверяется здесь, чтобы человек увидел причину, а
        # не отказ базы. Правится одна из двух — сравнивать приходится с
        # той, что уже записана.
        require_order(
            employee.probation_from, employee.probation_to,
            message="Стажировка не может кончаться раньше, чем началась",
            details={"probation_from": str(employee.probation_from),
                     "probation_to": str(employee.probation_to)},
        )
        if changes.get("preferred_language"):
            employee.preferred_language = changes["preferred_language"]
        if changes.get("employee_number") is not None:
            employee.employee_number = clean_code(
                changes["employee_number"], field="employee_number"
            )

        with self.atomic():
            employee.save()
            self.audit.record(
                actor, action="employee.update", entity_type="employees",
                entity_id=employee.id, before=before,
                after=snapshot(employee, CARD_FIELDS),
            )
        return self._card(actor, employee_id)

    def change_assignment(
        self,
        actor: Actor,
        employee_id: uuid.UUID,
        *,
        effective_from: date,
        **changes,
    ) -> EmployeeAssignment:
        """Перевод: другой офис, отдел, должность или руководитель.

        Не переданные поля наследуются из действующего назначения — перевод
        в другой офис не должен молча стирать должность.
        """
        self.access.require(actor, "employees.manage")
        employee = self._require_visible_employee(actor, employee_id)

        if employee.employment_status in FINAL_STATUSES:
            raise Conflict(
                "Сотрудник уволен: перевести его нельзя",
                details={"employment_status": employee.employment_status},
            )
        require_order(
            employee.hire_date, effective_from,
            message="Перевод не может быть раньше даты приёма",
            details={"hire_date": str(employee.hire_date),
                     "effective_from": str(effective_from)},
        )

        # Берётся ПОСЛЕДНИЙ период, а не действующий на дату перевода: перевод
        # задним числом не нашёл бы «текущий» период (тот начинается позже),
        # и вместо понятного отказа получилось бы нарушение ограничения базы.
        current = (
            EmployeeAssignment.objects.filter(
                employee_id=employee_id, is_primary=True
            )
            .order_by("-valid_from")
            .first()
        )
        if current is None:
            raise Conflict("У сотрудника нет основного назначения")
        if current.valid_from >= effective_from:
            raise ValidationFailed(
                "Дата перевода должна быть позже начала действующего назначения",
                details={"effective_from": str(effective_from),
                         "current_valid_from": str(current.valid_from)},
            )
        # прошлый период уже закрыт раньше даты перевода — трогать его не нужно
        needs_closing = (
            current.valid_to is None or current.valid_to >= effective_from
        )

        office = self._require_assignable_office(
            actor, changes.get("office_id") or current.office_id
        )

        if "department_id" in changes:
            department_id = self._validate_department(
                actor, changes["department_id"], office_id=office.id
            )
        elif office.id != current.office_id:
            # отдел принадлежит офису: при переводе в другой офис прежний
            # отдел уже не подходит, поэтому он снимается, а не переносится
            department_id = None
        else:
            department_id = current.department_id

        position_id = (
            self._validate_position(actor, changes["position_id"])
            if "position_id" in changes else current.position_id
        )
        manager_id = (
            self._validate_manager(actor, changes["manager_employee_id"],
                                   employee_id=employee_id)
            if "manager_employee_id" in changes else current.manager_employee_id
        )

        before = snapshot(current, ASSIGNMENT_FIELDS)
        with self.atomic():
            if needs_closing:
                current.valid_to = effective_from - timedelta(days=1)
                # закрытие периода обязано дойти до базы ДО вставки нового:
                # ограничение проверяется в момент выполнения оператора
                current.save(update_fields=["valid_to", "updated_at"])
            new_assignment = EmployeeAssignment.objects.create(
                organization_id=actor.organization_id,
                employee_id=employee_id,
                office=office,
                department_id=department_id,
                position_id=position_id,
                manager_employee_id=manager_id,
                employment_type=changes.get("employment_type")
                or current.employment_type,
                work_mode=changes.get("work_mode") or current.work_mode,
                is_primary=True,
                valid_from=effective_from,
            )
            self.audit.record(
                actor, action="employee.assignment.change",
                entity_type="employee_assignments", entity_id=new_assignment.id,
                before=before, after=snapshot(new_assignment, ASSIGNMENT_FIELDS),
            )
        return EmployeeAssignment.objects.select_related(
            "office", "office__region", "department", "position"
        ).get(id=new_assignment.id)

    # --------------------------------------------------------------- статусы

    def deactivate(self, actor: Actor, employee_id: uuid.UUID) -> EmployeeCard:
        """Временная приостановка. Назначения и история остаются как есть."""
        return self._set_status(
            actor, employee_id, "SUSPENDED", "employee.deactivate"
        )

    def reactivate(self, actor: Actor, employee_id: uuid.UUID) -> EmployeeCard:
        return self._set_status(actor, employee_id, "ACTIVE", "employee.reactivate")

    def _set_status(
        self, actor: Actor, employee_id: uuid.UUID, status: str, action: str
    ) -> EmployeeCard:
        self.access.require(actor, "employees.manage")
        employee = self._require_visible_employee(actor, employee_id)

        if employee.employment_status == status:
            raise Conflict(f"Сотрудник уже в статусе {status}",
                           details={"status": status})
        if employee.employment_status in FINAL_STATUSES:
            raise Conflict(
                "Сотрудник уволен: менять его статус нельзя. "
                "Для повторного приёма заведите новое назначение",
                details={"employment_status": employee.employment_status},
            )

        before = snapshot(employee, CARD_FIELDS)
        employee.employment_status = status
        with self.atomic():
            employee.save()
            self.audit.record(
                actor, action=action, entity_type="employees",
                entity_id=employee.id, before=before,
                after=snapshot(employee, CARD_FIELDS),
            )
        return self._card(actor, employee_id)

    def terminate(
        self, actor: Actor, employee_id: uuid.UUID, *, termination_date: date,
        reason: str | None = None,
    ) -> EmployeeCard:
        """Увольнение. Запись сотрудника и вся его история сохраняются.

        Открытые периоды назначения и графика закрываются датой увольнения:
        иначе уволенный человек навсегда остался бы «работающим сейчас» в
        любом отчёте по текущему составу. Закрытие периода — не удаление:
        строка остаётся, у неё появляется дата окончания.
        """
        self.access.require(actor, "employees.archive")
        employee = self._require_visible_employee(actor, employee_id)

        if employee.employment_status in FINAL_STATUSES:
            raise Conflict(
                "Сотрудник уже уволен",
                details={"termination_date": str(employee.termination_date)},
            )
        require_order(
            employee.hire_date, termination_date,
            message="Дата увольнения не может быть раньше даты приёма",
            details={"hire_date": str(employee.hire_date),
                     "termination_date": str(termination_date)},
        )

        before = snapshot(employee, CARD_FIELDS)
        with self.atomic():
            employee.employment_status = "TERMINATED"
            employee.termination_date = termination_date
            if reason:
                employee.termination_reason = clean_text(
                    reason, field="reason", max_length=255
                )
            employee.save()

            # Циклом, а не queryset.update(): у моделей есть `updated_at`,
            # который проставляет `save()`, и терять его на массовом
            # обновлении незачем — строк здесь единицы.
            for assignment in EmployeeAssignment.objects.filter(
                employee_id=employee_id, valid_to__isnull=True,
                valid_from__lte=termination_date,
            ):
                assignment.valid_to = termination_date
                assignment.save(update_fields=["valid_to", "updated_at"])
            for schedule in EmployeeScheduleAssignment.objects.filter(
                employee_id=employee_id, valid_to__isnull=True,
                valid_from__lte=termination_date,
            ):
                schedule.valid_to = termination_date
                schedule.save(update_fields=["valid_to", "updated_at"])

            self.audit.record(
                actor, action="employee.terminate", entity_type="employees",
                entity_id=employee.id, before=before,
                after=snapshot(employee, CARD_FIELDS) | (
                    {"reason": reason} if reason else {}
                ),
            )
        return self._card(actor, employee_id)

    # ------------------------------------------------------ внутренние правила

    def _office_scope_condition(
        self, actor: Actor, *, office_id: list | None,
        region_id: list | None,
    ) -> Q | None:
        """Условие на офис назначения: пересечение области видимости и фильтров.

        Возвращает None, только если ограничивать нечем: у пользователя доступ
        ко всей организации и фильтры не заданы.

        Выбранные офисы и регионы СКЛАДЫВАЮТСЯ, а не пересекаются. Выбрав
        «Головной офис» и «Самаркандскую область», кадровик просит показать
        и тех, и других; пересечение вернуло бы пусто и читалось бы как
        поломка. Область видимости при этом остаётся пересечением: она
        ограничивает, а не расширяет.
        """
        condition: Q | None = None
        visible = self.access.visible_office_ids(actor)
        if visible is not None:
            # пустое множество тоже условие: «не видит ни одного офиса»
            condition = Q(office_id__in=visible)

        picked: Q | None = None
        if office_id:
            # Доступ проверяется у КАЖДОГО: список не повод пропустить
            # офис, к которому пользователя не допускали.
            for one in office_id:
                self.access.require_office(actor, one)
            picked = Q(office_id__in=list(office_id))
        if region_id:
            for one in region_id:
                self.access.require_region(actor, one)
            clause = Q(
                office_id__in=Office.objects.filter(
                    region_id__in=list(region_id)
                ).values_list("id", flat=True)
            )
            picked = clause if picked is None else picked | clause

        if picked is not None:
            condition = picked if condition is None else condition & picked
        return condition

    def _require_visible_employee(
        self, actor: Actor, employee_id: uuid.UUID, *, at: date | None = None
    ) -> Employee:
        # Само правило лежит в selectors: тем же пользуется привязка Telegram,
        # и разойтись эти два ответа не имеют права.
        return require_visible_employee(self.access, actor, employee_id)

    def _require_assignable_office(
        self, actor: Actor, office_id: uuid.UUID
    ) -> Office:
        office = self.access.require_office(actor, office_id)
        if office.status != "ACTIVE":
            raise Conflict(
                "Офис не активен: назначать в него сотрудников нельзя",
                details={"office_id": str(office_id), "status": office.status},
            )
        return office

    def _validate_department(
        self, actor: Actor, department_id: uuid.UUID | None, *, office_id: uuid.UUID
    ) -> uuid.UUID | None:
        if department_id is None:
            return None
        department = Department.objects.filter(
            id=department_id, organization_id=actor.organization_id
        ).first()
        if department is None:
            raise NotFound("Отдел не найден")
        # У общего отдела офиса нет, и сверять его не с чем: «Продажи»
        # одни на всю компанию, и человек из любого офиса в них числится.
        if department.office_id is not None and department.office_id != office_id:
            raise ValidationFailed(
                "Отдел относится к другому офису",
                details={"department_id": str(department_id),
                         "department_office_id": str(department.office_id),
                         "office_id": str(office_id)},
            )
        if department.status != "ACTIVE":
            raise Conflict(
                "Отдел не активен: назначать в него сотрудников нельзя",
                details={"status": department.status},
            )
        return department.id

    def _validate_position(
        self, actor: Actor, position_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        if position_id is None:
            return None
        position = Position.objects.filter(
            id=position_id, organization_id=actor.organization_id
        ).first()
        if position is None:
            raise NotFound("Должность не найдена")
        if position.status != "ACTIVE":
            raise Conflict(
                "Должность не активна: назначать её нельзя",
                details={"status": position.status},
            )
        return position.id

    def _validate_manager(
        self, actor: Actor, manager_id: uuid.UUID | None, *,
        employee_id: uuid.UUID | None = None,
    ) -> uuid.UUID | None:
        if manager_id is None:
            return None
        if employee_id is not None and manager_id == employee_id:
            raise ValidationFailed("Сотрудник не может быть своим руководителем")
        manager = Employee.objects.filter(
            id=manager_id, organization_id=actor.organization_id
        ).first()
        if manager is None:
            raise NotFound("Руководитель не найден")
        return manager.id
