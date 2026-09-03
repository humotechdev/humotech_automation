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
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import Q

from humotech.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from humotech.core.pagination import Page, paginate
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
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.offices.models import Office
from humotech.positions.models import Position
from humotech.schedules.models import EmployeeScheduleAssignment
from humotech.telegram.models import TelegramAccount

CARD_FIELDS = (
    "employee_number", "first_name", "last_name", "middle_name", "phone",
    "corporate_email", "personal_email", "birth_date", "hire_date",
    "termination_date", "preferred_language", "employment_status",
)
ASSIGNMENT_FIELDS = (
    "office_id", "department_id", "position_id", "manager_employee_id",
    "employment_type", "work_mode", "valid_from", "valid_to",
)

CONTACT_FIELDS = ("first_name", "last_name", "middle_name", "phone",
                  "corporate_email", "personal_email", "birth_date",
                  "preferred_language", "employee_number")

# Статусы, при которых сотрудник считается работающим.
WORKING_STATUSES = ("ACTIVE", "PROBATION")
# Статусы, после которых кадровые операции недоступны.
FINAL_STATUSES = ("TERMINATED", "ARCHIVED")


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

    @property
    def full_name(self) -> str:
        parts = [self.employee.last_name, self.employee.first_name,
                 self.employee.middle_name]
        return " ".join(part for part in parts if part)


class EmployeeService(BaseService):
    """Требует `employees.read` для чтения, `employees.manage` для изменений,
    `employees.archive` — для увольнения и перевода в архив."""

    # ------------------------------------------------------------------ чтение

    def list(
        self,
        actor: Actor,
        *,
        search: str | None = None,
        status: str | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        at: date | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "employees.read")
        at = at or date.today()

        queryset = Employee.objects.filter(organization_id=actor.organization_id)
        if status:
            queryset = queryset.filter(employment_status=status)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(
                Q(first_name__icontains=pattern)
                | Q(last_name__icontains=pattern)
                | Q(middle_name__icontains=pattern)
                | Q(employee_number__icontains=pattern)
                | Q(corporate_email__icontains=pattern)
                | Q(phone__icontains=pattern)
            )

        office_condition = self._office_scope_condition(
            actor, office_id=office_id, region_id=region_id
        )
        if office_condition is not None:
            # Сотрудник виден по офису своего ТЕКУЩЕГО основного назначения.
            # Подзапрос, а не JOIN: соединение размножило бы строки, если
            # у сотрудника найдётся второе назначение, и пагинация поехала бы.
            visible_ids = EmployeeAssignment.objects.filter(
                current_primary_assignment_filter(at) & office_condition
            ).values_list("employee_id", flat=True)
            queryset = queryset.filter(id__in=visible_ids)

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
        for employee in page.items:
            employee.current_assignment = assignments.get(employee.id)
        return page

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
        self, actor: Actor, *, office_id: uuid.UUID | None,
        region_id: uuid.UUID | None,
    ) -> Q | None:
        """Условие на офис назначения: пересечение области видимости и фильтров.

        Возвращает None, только если ограничивать нечем: у пользователя доступ
        ко всей организации и фильтры не заданы.
        """
        condition: Q | None = None
        visible = self.access.visible_office_ids(actor)
        if visible is not None:
            # пустое множество тоже условие: «не видит ни одного офиса»
            condition = Q(office_id__in=visible)
        if office_id is not None:
            self.access.require_office(actor, office_id)
            clause = Q(office_id=office_id)
            condition = clause if condition is None else condition & clause
        if region_id is not None:
            self.access.require_region(actor, region_id)
            clause = Q(
                office_id__in=Office.objects.filter(
                    region_id=region_id
                ).values_list("id", flat=True)
            )
            condition = clause if condition is None else condition & clause
        return condition

    def _require_visible_employee(
        self, actor: Actor, employee_id: uuid.UUID, *, at: date | None = None
    ) -> Employee:
        employee = Employee.objects.filter(
            id=employee_id, organization_id=actor.organization_id
        ).first()
        if employee is None:
            # чужая организация отвечает так же, как отсутствие записи
            raise NotFound("Сотрудник не найден")

        visible = self.access.visible_office_ids(actor)
        if visible is None:
            return employee

        # У сотрудника мог смениться офис, поэтому проверяется не только
        # текущий период, но и любой, попадающий в область видимости.
        # Сузить до текущего нельзя: увольнение закрывает все периоды,
        # и HR своего же региона потерял бы доступ к карточкам своих уволенных.
        allowed = EmployeeAssignment.objects.filter(
            employee_id=employee_id, office_id__in=visible
        ).exists()
        if not allowed:
            raise PermissionDenied("Сотрудник вне вашей области видимости")
        return employee

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
        if department.office_id != office_id:
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
