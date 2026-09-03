"""Сборка всех роутеров. Порядок важен: побеждает первый подошедший.

start идёт первым (в нём /start, отмена и FSM привязки), hr — предпоследним,
потому что его фильтр роли самый узкий, а fallback — последним: он отвечает на
всё, что не разобрали остальные, чтобы пользователь не получал молчание.
"""

from aiogram import Router

from src.handlers.attendance import router as attendance_router
from src.handlers.corrections import router as corrections_router
from src.handlers.fallback import router as fallback_router
from src.handlers.hr import hr_router
from src.handlers.profile import router as profile_router
from src.handlers.questions import router as questions_router
from src.handlers.sick_leave import router as sick_leave_router
from src.handlers.start import router as start_router
from src.handlers.statistics import router as statistics_router
from src.handlers.vacation import router as vacation_router


def build_root_router() -> Router:
    root = Router(name="root")
    root.include_router(start_router)
    root.include_router(profile_router)
    root.include_router(vacation_router)
    root.include_router(attendance_router)
    root.include_router(statistics_router)
    root.include_router(sick_leave_router)
    root.include_router(corrections_router)
    root.include_router(questions_router)
    root.include_router(hr_router)
    root.include_router(fallback_router)   # обязан быть последним
    return root


__all__ = ["build_root_router"]
