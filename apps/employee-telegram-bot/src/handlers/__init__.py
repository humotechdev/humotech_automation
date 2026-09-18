"""Сборка роутеров. Порядок важен: побеждает первый подошедший.

`start` первым — в нём разбор ссылки привязки, и полезная нагрузка `/start`
не должна достаться общему обработчику. `menu` следом. `fallback` последним:
он отвечает на всё, что не разобрали остальные, чтобы человек не получал
молчание.

Прежние разделы — «мои отметки», «статистика», «исправить отметку»,
HR-панель — сняты: часть из них была заглушками без backend, а всё, что
работало, вошло в меню. «Написать в HR» вернулся уже поверх настоящей
очереди обращений: вопрос ложится в CRM, ответ HR приходит в этот чат.
"""

from aiogram import Router

from src.handlers.absence_document import router as absence_document_router
from src.handlers.ask_hr import router as ask_hr_router
from src.handlers.attendance import router as attendance_router
from src.handlers.attendance.sticker import router as sticker_router
from src.handlers.day_start import router as day_start_router
from src.handlers.fallback import router as fallback_router
from src.handlers.menu import router as menu_router
from src.handlers.start import router as start_router


def build_root_router() -> Router:
    root = Router(name="root")
    root.include_router(start_router)
    # Печатный QR ждёт геопозицию в своём состоянии. Раньше меню и
    # `fallback`: иначе присланное место съел бы ответ «не понял».
    root.include_router(sticker_router)
    # Отметка из Mini App приходит служебным сообщением `web_app_data`,
    # у которого НЕТ текста. `fallback` ловит любое сообщение без
    # состояния, поэтому стоять он обязан позже: иначе служебное
    # сообщение съедалось бы ответом «не понял».
    root.include_router(attendance_router)
    # Ответ на напоминание ждёт причину в своём состоянии. Раньше
    # меню и `fallback`: иначе написанная причина уходила бы в HR
    # вопросом или получала бы «не понял».
    root.include_router(day_start_router)
    # Загрузка справки ждёт файл в своём состоянии. Раньше меню и
    # `fallback`: присланный документ иначе получил бы «не понял».
    root.include_router(absence_document_router)
    root.include_router(menu_router)
    # После меню: кнопка меню, нажатая во время ввода вопроса, остаётся
    # кнопкой меню и не уходит в HR текстом. До `fallback`: он ловит
    # любой текст без состояния, в том числе ответ на сообщение HR.
    root.include_router(ask_hr_router)
    root.include_router(fallback_router)   # обязан быть последним
    return root


__all__ = ["build_root_router"]
