"""Гейт: рабочие функции закрыты, пока ознакомление не завершено.

Проверка стоит ЗДЕСЬ, на endpoint'ах личного кабинета, а не в
`telegram/identity.py`. Разница принципиальная: `resolve_account` —
общий шлюз бота и Mini App, и отказ в нём означал бы, что человек не
может даже узнать, кто он. Бот спрашивает `/me/profile` на каждом
обновлении, чтобы понять, с кем разговаривает; закрыв профиль, мы
закрыли бы и сам разговор об ознакомлении.

Поэтому гейт — разрешение DRF с одним переключателем на классе view:

    class SomeView(EmployeeSelfView):
        onboarding_gate = False   # экран доступен и до завершения

Открытых экранов ровно два вида: профиль (кто я) и сами шаги
ознакомления. Всё остальное закрыто — включая отметку присутствия.
Это прямо следует из задачи: полный набор функций, и посещаемость в
нём, человек получает после завершения. Решение спорное на практике —
новичок в первый день не отметится, пока не дочитает, — и держится оно
ровно в одном месте, чтобы при необходимости меняться одной строкой.

Кто под гейтом: только те, у кого есть строка `employee_onboarding`.
Сотрудник вне программы работает как прежде — появление раздела не
должно было закрыть бота всем, кто работает давно.
"""

from __future__ import annotations

from rest_framework import permissions

from humotech.core.errors import PermissionDenied
from humotech.onboarding import progress as progress_module
from humotech.telegram.auth import EmployeePrincipal

#: Что ответить закрытому. Причина уходит боту целиком: он предъявил
#: общий секрет, то есть он наша же сторона, и ему нужно выбрать
#: формулировку — «дочитайте» и «вы отказались» требуют разного.
REASON_INCOMPLETE = "onboarding_incomplete"
REASON_DECLINED = "onboarding_declined"


class OnboardingCompleted(permissions.BasePermission):
    """Пускает того, кто завершил ознакомление либо в нём не участвует."""

    message = "Сначала завершите первичное ознакомление"

    def has_permission(self, request, view) -> bool:
        if not getattr(view, "onboarding_gate", True):
            return True
        user = getattr(request, "user", None)
        if not isinstance(user, EmployeePrincipal):
            # Не наш способ входа — решать будет другое разрешение.
            return True

        progress = progress_module.gate(user.employee.id)
        if progress is None or progress.completed:
            return True

        # Не `return False`, а исключение с причиной. Голый отказ DRF
        # несёт только текст, и бот отличал бы «дочитайте» от «вы
        # отказались» по строке сообщения — то есть перестал бы их
        # отличать при первой же правке формулировки.
        declined = progress.has_declined
        raise PermissionDenied(
            "Вы не подтвердили обязательный документ. "
            "Отдел кадров свяжется с вами."
            if declined
            else "Сначала завершите первичное ознакомление",
            details={
                "reason": REASON_DECLINED if declined else REASON_INCOMPLETE,
                "sections_done": progress.sections_done,
                "sections_total": progress.sections_total,
                "policies_done": progress.policies_done,
                "policies_total": progress.policies_total,
            },
        )


__all__ = ["REASON_DECLINED", "REASON_INCOMPLETE", "OnboardingCompleted"]
