"""Трудовой статус сотрудника и уведомления о приёме.

**Два слоя статусов, и смешивать их нельзя.** Трудовой статус отвечает на
вопрос «кем человек числится»: работает, отстранён, уволен. Дневной — на
вопрос «где он сегодня»: в офисе, опаздывает, в отпуске, на больничном. Это
разные вопросы с разными ответами, и один не выводится из другого.

Поэтому здесь только трудовой слой. Дневной живёт в `attendance/hr.py`.

**Почему «Работает», а не «Активен».** В базе значение `ACTIVE` — его
перечисляет ограничение таблицы. Но человеку показывается «Работает»:
«активен» — это про учётную запись, а не про человека. Перевод живёт в
`EMPLOYMENT_TITLES` и применяется на границе с интерфейсом.

**Уведомление о приёме.** Принятый узнаёт от бота, куда его приняли.
"""

from __future__ import annotations

from humotech.core.service import BaseService
from humotech.employees.models import Employee
from humotech.employees.services import EmployeeService
from humotech.notifications import outbox
from humotech.notifications import messages

#: Как трудовой статус называется для человека.
EMPLOYMENT_TITLES = {
    "ACTIVE": "Работает",
    "SUSPENDED": "Отстранён",
    "TERMINATED": "Уволен",
    "ARCHIVED": "В архиве",
}


def title_of(employment_status: str) -> str:
    """Подпись статуса. Неизвестный код возвращается как есть."""
    return EMPLOYMENT_TITLES.get(employment_status, employment_status)


class EmployeeLifecycleService(BaseService):
    """Уведомление о приёме. Переходы статуса — в `EmployeeService`."""

    def __init__(self) -> None:
        super().__init__()
        self.employees = EmployeeService()

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
        """Где человек работает — для сообщения самому принятому в его чат.

        Берёт ПОЛНЫЙ набор (`employment_facts`), а не урезанный ответ бота
        на узнавание по `@username` (`welcome_facts`).
        """
        from humotech.telegram.services import employment_facts

        return employment_facts(employee)

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
    "EmployeeLifecycleService",
    "title_of",
]
