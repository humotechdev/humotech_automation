"""Сборка роутеров. Порядок важен: побеждает первый подошедший.

`start` первым — в нём разбор ссылки привязки, и полезная нагрузка `/start`
не должна достаться общему обработчику. `menu` следом. `fallback` последним:
он отвечает на всё, что не разобрали остальные, чтобы человек не получал
молчание.

Роутеров сотрудника здесь ровно два. Прежние разделы — «мои отметки»,
«статистика», «исправить отметку», «вопрос в HR», HR-панель — сняты: часть
из них была заглушками без backend, а всё, что работало, вошло в меню.
Держать рядом две системы меню значило бы иметь два места, где чинить
одну ошибку.
"""

from aiogram import Router

from src.handlers.fallback import router as fallback_router
from src.handlers.menu import router as menu_router
from src.handlers.start import router as start_router


def build_root_router() -> Router:
    root = Router(name="root")
    root.include_router(start_router)
    root.include_router(menu_router)
    root.include_router(fallback_router)   # обязан быть последним
    return root


__all__ = ["build_root_router"]
