"""Кадровые операции: приём, перевод, изменение данных, увольнение.

Главное правило этого модуля: сотрудник никогда не удаляется и его история
никогда не переписывается. Офис, отдел и должность лежат не в карточке, а в
`employee_assignments` — периодами. Поэтому перевод — это закрытие текущего
периода и открытие нового, а не UPDATE одной строки: иначе прошлые отметки
о входе оказались бы отнесены к офису, в котором человек тогда не работал.

Периоды защищены EXCLUDE-ограничением по `daterange(valid_from, valid_to, '[]')`
с включительными границами. Отсюда два следствия, которые видны в коде:

  * старый период закрывается днём РАНЬШЕ начала нового, иначе общий день
    считается пересечением;
  * закрытие обязано дойти до базы до вставки нового периода, поэтому между
    ними стоит явный flush.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import Select, select
from src.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from src.core.pagination import Page, paginate
from src.core.rbac import Actor, snapshot
from src.core.service import BaseService
from src.core.validation import (
    clean_code,
    clean_text,
    validate_email,
    validate_phone,
    require_order,
)
from src.modules.departments.models import Department
from src.modules.employees.models import Employee, EmployeeAssignment
from src.modules.employees.schemas import (
    AssignmentChangeRequest,
    AssignmentView,
    EmployeeCard,
    EmployeeCreateRequest,
    EmployeeListItem,
    EmployeeUpdateRequest,
    ScheduleBriefView,
    TelegramBindingView,
)
from src.modules.offices.models import Office
from src.modules.positions.models import Position
from src.modules.regions.models import Region
from src.modules.schedules.models import EmployeeScheduleAssignment, WorkSchedule
from src.modules.telegram.models import TelegramAccount

CARD_FIELDS = (
    "employee_number", "first_name", "last_name", "middle_name", "phone",
    "corporate_email", "personal_email", "birth_date", "hire_date",
    "termination_date", "preferred_language", "employment_status",
)
ASSIGNMENT_FIELDS = (
    "office_id", "department_id", "position_id", "manager_employee_id",
    "employment_type", "work_mode", "valid_from", "valid_to",
)

# Статусы, при которых сотрудник считается работающим.
WORKING_STATUSES = ("ACTIVE", "PROBATION")


def current_primary_assignment_predicate(at: date):
    """Одно определение «текущего основного назначения» на весь модуль.

    Держать его в одном месте важнее, чем кажется: список, карточка и перевод
    обязаны понимать «сейчас» одинаково, иначе сотрудник попадает в список по
    одному офису, а в карточке показывается по другому.
    """
    return (
        EmployeeAssignment.is_primary.is_(True),
        EmployeeAssignment.valid_from <= at,
        (EmployeeAssignment.valid_to.is_(None))
        | (EmployeeAssignment.valid_to >= at),
    )


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

        statement: Select = select(Employee).where(
            Employee.organization_id == actor.organization_id
        )
        if status:
            statement = statement.where(Employee.employment_status == status)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                Employee.first_name.ilike(pattern)
                | Employee.last_name.ilike(pattern)
                | Employee.middle_name.ilike(pattern)
                | Employee.employee_number.ilike(pattern)
                | Employee.corporate_email.ilike(pattern)
                | Employee.phone.ilike(pattern)
            )

        office_condition = self._office_scope_condition(
            actor, office_id=office_id, region_id=region_id
        )
        if office_condition is not None:
            # Сотрудник виден по офису своего ТЕКУЩЕГО основного назначения.
            # EXISTS, а не JOIN: соединение размножило бы строки, если у
            # сотрудника найдётся второе назначение, и пагинация поехала бы.
            statement = statement.where(
                select(EmployeeAssignment.id)
                .where(
                    EmployeeAssignment.employee_id == Employee.id,
                    *current_primary_assignment_predicate(at),
                    office_condition,
                )
                .exists()
            )

        page = paginate(self.session, statement, Employee, limit=limit, cursor=cursor)

        assignments = self._assignments_for(
            [employee.id for employee in page.items], at=at
        )
        items = []
        for employee in page.items:
            row = assignments.get(employee.id)
            item = EmployeeListItem.model_validate(employee)
            item.full_name = self._full_name(employee)
            if row is not None:
                item.office_id = row.office_id
                item.office_name = row.office_name
                item.region_id = row.region_id
                item.region_name = row.region_name
                item.department_name = row.department_name
                item.position_name = row.position_name
            items.append(item)

        return Page(items=items, next_cursor=page.next_cursor,
                    has_more=page.has_more)

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

        card = EmployeeCard.model_validate(employee)
        card.full_name = self._full_name(employee)
        card.assignment_history = self._assignment_history(employee_id)
        card.current_assignment = next(
            (
                row for row in card.assignment_history
                if row.is_primary
                and row.valid_from <= at
                and (row.valid_to is None or row.valid_to >= at)
            ),
            None,
        )

        schedule_row = self.session.execute(
            select(EmployeeScheduleAssignment, WorkSchedule)
            .join(WorkSchedule, WorkSchedule.id == EmployeeScheduleAssignment.schedule_id)
            .where(
                EmployeeScheduleAssignment.employee_id == employee_id,
                EmployeeScheduleAssignment.valid_from <= at,
                (EmployeeScheduleAssignment.valid_to.is_(None))
                | (EmployeeScheduleAssignment.valid_to >= at),
            )
            .order_by(EmployeeScheduleAssignment.valid_from.desc())
            .limit(1)
        ).first()
        if schedule_row is not None:
            assignment, schedule = schedule_row
            card.current_schedule = ScheduleBriefView(
                schedule_id=schedule.id, name=schedule.name,
                timezone=schedule.timezone, status=schedule.status,
                valid_from=assignment.valid_from, valid_to=assignment.valid_to,
            )

        account = self.session.scalar(
            select(TelegramAccount).where(
                TelegramAccount.employee_id == employee_id
            )
        )
        card.telegram = TelegramBindingView(
            connected=account is not None and account.status == "ACTIVE",
            status=account.status if account else None,
            username=account.telegram_username if account else None,
            connected_at=account.connected_at if account else None,
        )
        return card

    def assignment_history(
        self, actor: Actor, employee_id: uuid.UUID
    ) -> list[AssignmentView]:
        self.access.require(actor, "employees.read")
        self._require_visible_employee(actor, employee_id)
        return self._assignment_history(employee_id)

    # ------------------------------------------------------------------- приём

    def create(self, actor: Actor, request: EmployeeCreateRequest) -> EmployeeCard:
        """Сотрудник и его первое назначение создаются одной операцией.

        Порознь нельзя: сотрудник без назначения не привязан ни к одному офису,
        то есть не виден ни одному HR с территориальной областью — и починить
        это можно было бы только напрямую в базе.
        """
        self.access.require(actor, "employees.manage")

        office = self._require_assignable_office(actor, request.office_id)
        if request.region_id is not None and request.region_id != office.region_id:
            raise ValidationFailed(
                "Указанный регион не совпадает с регионом офиса",
                details={"region_id": str(request.region_id),
                         "office_region_id": str(office.region_id)},
            )
        department_id = self._validate_department(
            actor, request.department_id, office_id=office.id
        )
        position_id = self._validate_position(actor, request.position_id)
        manager_id = self._validate_manager(actor, request.manager_employee_id)

        if request.employment_status not in (*WORKING_STATUSES, "SUSPENDED"):
            raise ValidationFailed(
                "Нового сотрудника нельзя завести сразу уволенным",
                details={"employment_status": request.employment_status},
            )
        employee = Employee(
            organization_id=actor.organization_id,
            employee_number=clean_code(
                request.employee_number, field="employee_number"
            ),
            first_name=clean_text(request.first_name, field="first_name",
                                  required=True, max_length=100),
            last_name=clean_text(request.last_name, field="last_name",
                                 required=True, max_length=100),
            middle_name=clean_text(request.middle_name, field="middle_name",
                                   max_length=100),
            phone=validate_phone(request.phone),
            corporate_email=validate_email(request.corporate_email,
                                           field="corporate_email"),
            personal_email=validate_email(request.personal_email,
                                          field="personal_email"),
            birth_date=request.birth_date,
            hire_date=request.hire_date,
            preferred_language=request.preferred_language,
            employment_status=request.employment_status,
        )

        with self.atomic():
            self.session.add(employee)
            self.flush()
            assignment = EmployeeAssignment(
                organization_id=actor.organization_id,
                employee_id=employee.id,
                office_id=office.id,
                department_id=department_id,
                position_id=position_id,
                manager_employee_id=manager_id,
                employment_type=request.employment_type,
                work_mode=request.work_mode,
                is_primary=True,
                valid_from=request.hire_date,
            )
            self.session.add(assignment)
            self.flush()
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

    def update(
        self, actor: Actor, employee_id: uuid.UUID, request: EmployeeUpdateRequest
    ) -> EmployeeCard:
        self.access.require(actor, "employees.manage")
        employee = self._require_visible_employee(actor, employee_id)

        before = snapshot(employee, CARD_FIELDS)
        changed = request.model_fields_set

        if "first_name" in changed and request.first_name is not None:
            employee.first_name = clean_text(
                request.first_name, field="first_name", required=True,
                max_length=100,
            )
        if "last_name" in changed and request.last_name is not None:
            employee.last_name = clean_text(
                request.last_name, field="last_name", required=True,
                max_length=100,
            )
        if "middle_name" in changed:
            employee.middle_name = clean_text(
                request.middle_name, field="middle_name", max_length=100
            )
        if "phone" in changed:
            employee.phone = validate_phone(request.phone)
        if "corporate_email" in changed:
            employee.corporate_email = validate_email(
                request.corporate_email, field="corporate_email"
            )
        if "personal_email" in changed:
            employee.personal_email = validate_email(
                request.personal_email, field="personal_email"
            )
        if "birth_date" in changed:
            employee.birth_date = request.birth_date
        if "preferred_language" in changed and request.preferred_language:
            employee.preferred_language = request.preferred_language
        if "employee_number" in changed and request.employee_number is not None:
            employee.employee_number = clean_code(
                request.employee_number, field="employee_number"
            )

        with self.atomic():
            self.flush()
            self.audit.record(
                actor, action="employee.update", entity_type="employees",
                entity_id=employee.id, before=before,
                after=snapshot(employee, CARD_FIELDS),
            )
        return self._card(actor, employee_id)

    def change_assignment(
        self, actor: Actor, employee_id: uuid.UUID,
        request: AssignmentChangeRequest,
    ) -> AssignmentView:
        """Перевод: другой офис, отдел, должность или руководитель.

        Не переданные поля наследуются из действующего назначения — перевод
        в другой офис не должен молча стирать должность.
        """
        self.access.require(actor, "employees.manage")
        employee = self._require_visible_employee(actor, employee_id)

        if employee.employment_status in ("TERMINATED", "ARCHIVED"):
            raise Conflict(
                "Сотрудник уволен: перевести его нельзя",
                details={"employment_status": employee.employment_status},
            )
        require_order(
            employee.hire_date, request.effective_from,
            message="Перевод не может быть раньше даты приёма",
            details={"hire_date": str(employee.hire_date),
                     "effective_from": str(request.effective_from)},
        )

        # Берётся ПОСЛЕДНИЙ период, а не действующий на дату перевода: перевод
        # задним числом не нашёл бы «текущий» период (тот начинается позже),
        # и вместо понятного отказа получилось бы нарушение EXCLUDE-ограничения.
        current = self.session.scalar(
            select(EmployeeAssignment)
            .where(
                EmployeeAssignment.employee_id == employee_id,
                EmployeeAssignment.is_primary.is_(True),
            )
            .order_by(EmployeeAssignment.valid_from.desc())
            .limit(1)
        )
        if current is None:
            raise Conflict("У сотрудника нет основного назначения")
        if current.valid_from >= request.effective_from:
            raise ValidationFailed(
                "Дата перевода должна быть позже начала действующего назначения",
                details={"effective_from": str(request.effective_from),
                         "current_valid_from": str(current.valid_from)},
            )
        # прошлый период уже закрыт раньше даты перевода — трогать его не нужно
        needs_closing = (
            current.valid_to is None or current.valid_to >= request.effective_from
        )

        changed = request.model_fields_set
        office_id = (
            request.office_id if "office_id" in changed
            and request.office_id is not None else current.office_id
        )
        office = self._require_assignable_office(actor, office_id)

        if "department_id" in changed:
            department_id = self._validate_department(
                actor, request.department_id, office_id=office.id
            )
        elif office.id != current.office_id:
            # отдел принадлежит офису: при переводе в другой офис прежний
            # отдел уже не подходит, поэтому он снимается, а не переносится
            department_id = None
        else:
            department_id = current.department_id

        position_id = (
            self._validate_position(actor, request.position_id)
            if "position_id" in changed else current.position_id
        )
        manager_id = (
            self._validate_manager(actor, request.manager_employee_id,
                                   employee_id=employee_id)
            if "manager_employee_id" in changed else current.manager_employee_id
        )

        new_assignment = EmployeeAssignment(
            organization_id=actor.organization_id,
            employee_id=employee_id,
            office_id=office.id,
            department_id=department_id,
            position_id=position_id,
            manager_employee_id=manager_id,
            employment_type=request.employment_type or current.employment_type,
            work_mode=request.work_mode or current.work_mode,
            is_primary=True,
            valid_from=request.effective_from,
        )

        before = snapshot(current, ASSIGNMENT_FIELDS)
        with self.atomic():
            if needs_closing:
                current.valid_to = request.effective_from - timedelta(days=1)
                self.flush()  # закрытие периода обязано дойти до базы первым
            self.session.add(new_assignment)
            self.flush()
            self.audit.record(
                actor, action="employee.assignment.change",
                entity_type="employee_assignments", entity_id=new_assignment.id,
                before=before, after=snapshot(new_assignment, ASSIGNMENT_FIELDS),
            )
        return self._assignment_history(employee_id)[0]

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
        if employee.employment_status in ("TERMINATED", "ARCHIVED"):
            raise Conflict(
                "Сотрудник уволен: менять его статус нельзя. "
                "Для повторного приёма заведите новое назначение",
                details={"employment_status": employee.employment_status},
            )

        before = snapshot(employee, CARD_FIELDS)
        employee.employment_status = status
        with self.atomic():
            self.flush()
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

        if employee.employment_status in ("TERMINATED", "ARCHIVED"):
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

            for assignment in self.session.scalars(
                select(EmployeeAssignment).where(
                    EmployeeAssignment.employee_id == employee_id,
                    EmployeeAssignment.valid_to.is_(None),
                )
            ):
                if assignment.valid_from <= termination_date:
                    assignment.valid_to = termination_date
            for schedule in self.session.scalars(
                select(EmployeeScheduleAssignment).where(
                    EmployeeScheduleAssignment.employee_id == employee_id,
                    EmployeeScheduleAssignment.valid_to.is_(None),
                )
            ):
                if schedule.valid_from <= termination_date:
                    schedule.valid_to = termination_date

            self.flush()
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
    ):
        """Условие на офис назначения: пересечение области видимости и фильтров.

        Возвращает None, только если ограничивать нечем: у пользователя доступ
        ко всей организации и фильтры не заданы.
        """
        conditions = []
        visible = self.access.visible_office_ids(actor)
        if visible is not None:
            # пустое множество тоже условие: «не видит ни одного офиса»
            conditions.append(EmployeeAssignment.office_id.in_(visible))
        if office_id is not None:
            self.access.require_office(actor, office_id)
            conditions.append(EmployeeAssignment.office_id == office_id)
        if region_id is not None:
            self.access.require_region(actor, region_id)
            conditions.append(
                EmployeeAssignment.office_id.in_(
                    select(Office.id).where(Office.region_id == region_id)
                )
            )
        if not conditions:
            return None
        result = conditions[0]
        for extra in conditions[1:]:
            result = result & extra
        return result

    def _require_visible_employee(
        self, actor: Actor, employee_id: uuid.UUID, *, at: date | None = None
    ) -> Employee:
        employee = self.session.get(Employee, employee_id)
        if employee is None or employee.organization_id != actor.organization_id:
            # чужая организация отвечает так же, как отсутствие записи
            raise NotFound("Сотрудник не найден")

        visible = self.access.visible_office_ids(actor)
        if visible is None:
            return employee

        at = at or date.today()
        # у сотрудника мог смениться офис, поэтому проверяется не только
        # текущий период, но и любой, попадающий в область видимости:
        # иначе перевод внутри своего региона делал бы карточку недоступной
        allowed = self.session.scalar(
            select(EmployeeAssignment.id)
            .where(
                EmployeeAssignment.employee_id == employee_id,
                EmployeeAssignment.office_id.in_(visible),
            )
            .limit(1)
        )
        if allowed is None:
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
        department = self.session.get(Department, department_id)
        if (
            department is None
            or department.organization_id != actor.organization_id
        ):
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
        position = self.session.get(Position, position_id)
        if position is None or position.organization_id != actor.organization_id:
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
        manager = self.session.get(Employee, manager_id)
        if manager is None or manager.organization_id != actor.organization_id:
            raise NotFound("Руководитель не найден")
        return manager.id

    def _current_assignment(
        self, employee_id: uuid.UUID, *, at: date
    ) -> EmployeeAssignment | None:
        return self.session.scalar(
            select(EmployeeAssignment)
            .where(
                EmployeeAssignment.employee_id == employee_id,
                *current_primary_assignment_predicate(at),
            )
            .order_by(EmployeeAssignment.valid_from.desc())
            .limit(1)
        )

    def _assignment_history(self, employee_id: uuid.UUID) -> list[AssignmentView]:
        """Вся история переводов одним запросом со всеми справочниками."""
        rows = self.session.execute(
            select(
                EmployeeAssignment, Office, Region, Department, Position,
            )
            .join(Office, Office.id == EmployeeAssignment.office_id)
            .join(Region, Region.id == Office.region_id)
            .outerjoin(Department, Department.id == EmployeeAssignment.department_id)
            .outerjoin(Position, Position.id == EmployeeAssignment.position_id)
            .where(EmployeeAssignment.employee_id == employee_id)
            .order_by(
                EmployeeAssignment.valid_from.desc(),
                EmployeeAssignment.created_at.desc(),
            )
        ).all()

        history: list[AssignmentView] = []
        for assignment, office, region, department, position in rows:
            view = AssignmentView.model_validate(assignment)
            view.office_code = office.code
            view.office_name = office.name
            view.office_timezone = office.timezone
            view.region_id = region.id
            view.region_code = region.code
            view.region_name = region.name
            view.department_name = department.name if department else None
            view.position_name = position.name if position else None
            history.append(view)
        return history

    def _assignments_for(self, employee_ids: list[uuid.UUID], *, at: date) -> dict:
        """Текущие основные назначения сразу для всей страницы списка.

        Именно этот запрос отделяет список от N+1: один оператор на страницу
        вместо одного на каждого сотрудника.
        """
        if not employee_ids:
            return {}

        rows = self.session.execute(
            select(
                EmployeeAssignment.employee_id,
                Office.id, Office.name, Office.timezone,
                Region.id, Region.name,
                Department.name, Position.name,
            )
            .join(Office, Office.id == EmployeeAssignment.office_id)
            .join(Region, Region.id == Office.region_id)
            .outerjoin(Department, Department.id == EmployeeAssignment.department_id)
            .outerjoin(Position, Position.id == EmployeeAssignment.position_id)
            .where(
                EmployeeAssignment.employee_id.in_(employee_ids),
                *current_primary_assignment_predicate(at),
            )
        ).all()

        class _Row:
            __slots__ = ("office_id", "office_name", "office_timezone",
                         "region_id", "region_name", "department_name",
                         "position_name")

        result: dict[uuid.UUID, _Row] = {}
        for (employee_id, office_id, office_name, office_tz, region_id,
             region_name, department_name, position_name) in rows:
            row = _Row()
            row.office_id = office_id
            row.office_name = office_name
            row.office_timezone = office_tz
            row.region_id = region_id
            row.region_name = region_name
            row.department_name = department_name
            row.position_name = position_name
            result[employee_id] = row
        return result

    @staticmethod
    def _full_name(employee: Employee) -> str:
        parts = [employee.last_name, employee.first_name, employee.middle_name]
        return " ".join(part for part in parts if part)
