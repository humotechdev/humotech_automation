"""Жизненный путь сотрудника: стажировка → штат или расставание.

**Два слоя статусов, и смешивать их нельзя.** Трудовой статус отвечает на
вопрос «кем человек числится»: стажируется, работает, уволен. Дневной —
на вопрос «где он сегодня»: в офисе, опаздывает, в отпуске, на
больничном. Это разные вопросы с разными ответами, и один не выводится
из другого: стажёр бывает в отпуске, а работающий в штате — не пришёл.

Поэтому здесь только трудовой слой. Дневной живёт в `attendance/hr.py`
и ничего про испытательный срок не знает.

**Почему «Работает», а не «Активен».** В базе значение по-прежнему
`ACTIVE` — его перечисляет ограничение таблицы, и менять набор значений
ради подписи значило бы переписать схему из-за слова. Но человеку
показывается «Работает»: «активен» — это про учётную запись, а не про
человека, и рядом со «Стажировкой» и «Уволен» оно читается как из
другого списка. Перевод живёт в `EMPLOYMENT_TITLES` и применяется на
границе с интерфейсом.

**Уведомления.** Каждый переход человек узнаёт от бота, а не от коллег.
Тексты нейтральны намеренно: «не прошёл стажировку» — это решение
компании, и сообщать его с объяснениями, оценками или сожалениями
значит превращать служебное уведомление в письмо, которое пишут иначе
и не ботом.
"""

from __future__ import annotations

import uuid
from datetime import date

from humotech.core.errors import Conflict, ValidationFailed
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.employees.models import Employee
from humotech.employees.services import (
    CARD_FIELDS,
    EmployeeCard,
    EmployeeService,
)
from humotech.notifications import outbox
from humotech.notifications import messages

#: Как трудовой статус называется для человека.
EMPLOYMENT_TITLES = {
    "PROBATION": "Стажировка",
    "ACTIVE": "Работает",
    "SUSPENDED": "Отстранён",
    "TERMINATED": "Уволен",
    "ARCHIVED": "В архиве",
}

#: Причина, с которой расстаются после испытательного срока.
PROBATION_FAILED = "Не прошёл стажировку"


def title_of(employment_status: str) -> str:
    """Подпись статуса. Неизвестный код возвращается как есть."""
    return EMPLOYMENT_TITLES.get(employment_status, employment_status)


class EmployeeLifecycleService(BaseService):
    """Переходы трудового статуса и уведомления о них."""

    def __init__(self) -> None:
        super().__init__()
        self.employees = EmployeeService()

    def promote_to_staff(
        self,
        actor: Actor,
        employee_id: uuid.UUID,
        *,
        position_id: uuid.UUID | None = None,
        effective_from: date | None = None,
    ) -> EmployeeCard:
        """Принять стажёра в штат.

        `position_id` меняет должность, если по итогам стажировки её
        пересмотрели. Не переданная должность остаётся прежней — перевод
        в штат сам по себе не повод её стирать.

        Должность меняется ДО смены статуса: уведомление должно назвать
        ту должность, на которую человека приняли, а не ту, с которой он
        стажировался.
        """
        self.access.require(actor, "employees.manage")
        employee = self.employees._require_visible_employee(actor, employee_id)

        if employee.employment_status != "PROBATION":
            raise Conflict(
                "В штат принимают со стажировки. "
                f"Сейчас сотрудник в статусе «{title_of(employee.employment_status)}»",
                details={"employment_status": employee.employment_status},
            )

        moment = effective_from or date.today()
        if moment < employee.hire_date:
            raise ValidationFailed(
                "Приём в штат не может быть раньше выхода на стажировку",
                details={"hire_date": str(employee.hire_date),
                         "effective_from": str(moment)},
            )

        if position_id is not None:
            self.employees.change_assignment(
                actor, employee_id,
                effective_from=moment,
                position_id=position_id,
            )

        before = snapshot(employee, CARD_FIELDS)
        with self.atomic():
            employee.employment_status = "ACTIVE"
            employee.save(update_fields=["employment_status", "updated_at"])
            self.audit.record(
                actor,
                action="employee.promote",
                entity_type="employees",
                entity_id=employee.id,
                before=before,
                after=snapshot(employee, CARD_FIELDS),
            )
            self._tell(
                employee,
                notification_type="employee.promoted",
                body=messages.probation_passed(self._facts(employee)),
                # Один переход — одно сообщение, сколько бы раз кнопку
                # ни нажали: ключ включает сотрудника и само событие.
                idempotency_key=f"promote:{employee.id}",
            )
        return self.employees._card(actor, employee_id)

    def end_probation(
        self,
        actor: Actor,
        employee_id: uuid.UUID,
        *,
        last_day: date | None = None,
        reason: str | None = None,
    ) -> EmployeeCard:
        """Расстаться по итогам стажировки.

        Это увольнение, а не отдельный статус: человек перестаёт быть в
        компании, и придумывать для этого случая особое состояние значило
        бы завести второй способ быть уволенным. Отличается только
        причина — она сохраняется в карточке и видна через год.
        """
        self.access.require(actor, "employees.manage")
        employee = self.employees._require_visible_employee(actor, employee_id)

        if employee.employment_status != "PROBATION":
            raise Conflict(
                "Стажировку завершают тому, кто стажируется. "
                f"Сейчас сотрудник в статусе «{title_of(employee.employment_status)}»",
                details={"employment_status": employee.employment_status},
            )

        day = last_day or date.today()
        if day < employee.hire_date:
            day = employee.hire_date

        card = self.employees.terminate(
            actor, employee_id,
            termination_date=day,
            reason=reason or PROBATION_FAILED,
        )
        # Уведомление ПОСЛЕ увольнения и в своей транзакции: человек
        # должен узнать о решении, которое уже принято, а не о том,
        # которое может откатиться.
        with self.atomic():
            self._tell(
                employee,
                notification_type="employee.probation_ended",
                body=messages.probation_ended(day),
                idempotency_key=f"probation-ended:{employee.id}",
            )
        return card

    def announce_hire(self, employee: Employee) -> None:
        """Сообщить принятому, куда его приняли.

        Вызывается сразу после заведения карточки. Если Telegram ещё не
        привязан, сообщение всё равно встаёт в очередь: оно уйдёт, как
        только человек нажмёт «Старт», — и это лучше, чем не отправить
        его вовсе, потому что в момент приёма чата ещё не было.
        """
        with self.atomic():
            self._tell(
                employee,
                notification_type="employee.hired",
                body=messages.hired(self._facts(employee)),
                idempotency_key=f"hired:{employee.id}",
            )

    # ------------------------------------------------------------ внутреннее

    @staticmethod
    def _facts(employee: Employee) -> dict:
        """Где человек работает — одним набором на все три сообщения."""
        from humotech.telegram.services import welcome_facts

        return welcome_facts(employee)

    @staticmethod
    def _tell(
        employee: Employee,
        *,
        notification_type: str,
        body: str,
        idempotency_key: str,
    ) -> None:
        outbox.enqueue(
            organization_id=employee.organization_id,
            employee_id=employee.id,
            notification_type=notification_type,
            body=body,
            idempotency_key=idempotency_key,
            related_entity_type="employees",
            related_entity_id=employee.id,
        )


__all__ = [
    "EMPLOYMENT_TITLES",
    "PROBATION_FAILED",
    "EmployeeLifecycleService",
    "title_of",
]
