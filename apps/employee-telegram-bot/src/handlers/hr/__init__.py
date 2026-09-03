"""HR-раздел одного бота.

Фильтр роли стоит на уровне роутера — ни один вложенный хендлер не выполнится
для обычного сотрудника. Это удобство и защита от случайной кнопки, но НЕ
граница безопасности: настоящую проверку прав делает backend-api (403).
"""

from aiogram import Router

from src.handlers.hr import panel, requests
from src.utils.filters import HR_ROLES, RoleFilter

hr_router = Router(name="hr")
hr_router.message.filter(RoleFilter(*HR_ROLES))
hr_router.callback_query.filter(RoleFilter(*HR_ROLES))

hr_router.include_router(panel.router)
hr_router.include_router(requests.router)

__all__ = ["hr_router"]
