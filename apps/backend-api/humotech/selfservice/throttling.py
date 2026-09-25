"""Ограничение частоты для личного кабинета — по сотруднику, а не по адресу.

Готовые классы DRF здесь не годятся, и оба по своей причине.

`UserRateThrottle` берёт `request.user.pk`. У `EmployeePrincipal` его нет
намеренно: именно на отсутствии `pk` держится невозможность выдать сотрудника
за пользователя CRM. Дописать `pk` ради троттлинга значило бы снять ту самую
защиту, ради которой класс и заведён.

`AnonRateThrottle` считает по IP. За одним офисным адресом сидит весь офис:
один человек, обновляющий экран, закрыл бы доступ сотне коллег. А бот вообще
ходит с одного сервера — по IP он выглядит как один клиент независимо от
того, сколько людей им пользуется.

Поэтому счёт идёт по сотруднику. До аутентификации сотрудника ещё нет,
и тогда — по адресу: иначе неавторизованный поток вообще ничем не ограничен.
"""

from __future__ import annotations

from humotech.core.throttling import SharedSimpleRateThrottle as SimpleRateThrottle

from humotech.telegram.auth import EmployeePrincipal


class EmployeeRateThrottle(SimpleRateThrottle):
    scope = "employee_self"

    def get_cache_key(self, request, view) -> str:
        principal = getattr(request, "user", None)
        if isinstance(principal, EmployeePrincipal):
            ident = f"employee:{principal.employee.id}"
        else:
            # Аутентификация не прошла — считаем по адресу. Пропустить этот
            # случай значило бы не ограничивать ровно тот поток, который
            # ограничивать и нужно: перебор чужих Telegram ID.
            ident = f"addr:{self.get_ident(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ScanRateThrottle(EmployeeRateThrottle):
    """Отдельный предел на отметки.

    Строже общего: человек отмечается два-четыре раза в день, а не сто.
    Общий предел личного кабинета здесь не годится — он рассчитан на экран,
    который сам обновляет статус, и под ним перебор кодов прошёл бы
    незамеченным.
    """

    scope = "employee_scan"


__all__ = ["EmployeeRateThrottle", "ScanRateThrottle"]
