"""Меню сотрудника: десять пунктов и ни одного вычисления.

Ни одна цифра здесь не считается. Бот спрашивает backend и показывает
ответ — те же endpoint'ы, что и у Mini App. Иначе «за неделю» в чате
и «за неделю» на экране разошлись бы, и выяснить, какая цифра верна,
было бы нечем.

Бизнес-логики в хендлерах нет по той же причине: решение о том, можно ли
что-то, принимает сервер, и повторять его здесь значило бы завести второе
место, где это решение принимается.

ИИ здесь нет. «Помощь» — статический текст и указание, куда идти с тем,
что бот не решает.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.config.settings import settings
from src.keyboards import employee as kb
from src.utils.menu_button import drop_chat_override
from src.messages import employee as text
from src.middlewares.employee import REASON_UNAVAILABLE

logger = logging.getLogger(__name__)

router = Router(name="employee-menu")

# Причина отказа -> что показать. Три разных ответа, потому что человеку
# в них надо делать разное: подождать, попросить ссылку, идти в кадры.
DENIAL_TEXT = {
    "pending_confirmation": text.PENDING,
    "not_linked": text.NOT_LINKED,
    REASON_UNAVAILABLE: text.BACKEND_DOWN,
}


def _menu(employee):
    return (
        kb.employee_menu(settings.mini_app_url)
        if employee
        else kb.help_only_menu()
    )


async def _guard(message: Message, employee, denial) -> bool:
    """Есть ли доступ. Если нет — объясняет и возвращает False.

    Неудача backend отделена от отказа в доступе намеренно: сказать
    «нет доступа» из-за упавшего сервера значит отправить человека
    в отдел кадров разбираться с тем, чего не происходило.
    """
    if employee is not None:
        return True
    await message.answer(
        DENIAL_TEXT.get(denial, text.NO_ACCESS),
        reply_markup=kb.help_only_menu(),
    )
    return False


# --- вход ------------------------------------------------------------------

@router.message(CommandStart(deep_link=False))
async def start(message: Message, employee, denial) -> None:
    if not await _guard(message, employee, denial):
        return
    # Персональная кнопка чата перекрывает общую навсегда. Снимается
    # здесь, на действии, которое человек и так делает, когда кнопка
    # выглядит устаревшей — см. `drop_chat_override`.
    #
    # ПОСЛЕ проверки доступа, а не до: иначе любой посторонний, приславший
    # /start, заставлял бы нас писать в Telegram. Непривязанному кнопка
    # всё равно ничего не открывает, а починится она на первом же /start
    # после подтверждения привязки.
    await drop_chat_override(message.bot, message.chat.id)
    await message.answer(
        f"{text.greet(employee)}\n\n{text.CABINET_HINT}",
        reply_markup=_menu(employee),
    )


@router.message(F.text == kb.BTN_CABINET)
@router.message(Command("cabinet"))
async def cabinet(message: Message, employee, denial) -> None:
    """Кабинет открывается ОТДЕЛЬНОЙ inline-кнопкой, а не этой.

    Telegram передаёт подписанные данные о пользователе только
    приложениям, открытым из inline-кнопки, кнопки меню или прямой
    ссылки. Открытое из нижней клавиатуры не получает ни подписи,
    ни имени — и кабинет не смог бы понять, кто пришёл.
    """
    if not await _guard(message, employee, denial):
        return
    if not settings.mini_app_url:
        await message.answer(text.CABINET_UNAVAILABLE)
        return
    await message.answer(
        text.CABINET_OPEN,
        reply_markup=kb.cabinet_button(settings.mini_app_url),
    )


@router.message(Command("menu"))
async def menu(message: Message, employee, denial) -> None:
    if not await _guard(message, employee, denial):
        return
    await message.answer("Меню", reply_markup=_menu(employee))


# --- быстрые ответы --------------------------------------------------------

@router.message(F.text == kb.BTN_WHERE_AM_I)
@router.message(Command("status"))
async def where_am_i(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    if not await _guard(message, employee, denial):
        return
    body = await client.status(message.from_user.id)
    await message.answer(text.presence(body), reply_markup=_menu(employee))


@router.message(F.text == kb.BTN_TODAY)
@router.message(Command("today"))
async def today(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    await _summary(message, employee, denial, client, "today", "Сегодня")


@router.message(F.text == kb.BTN_WEEK)
@router.message(Command("week"))
async def week(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    await _summary(message, employee, denial, client, "week", "За неделю")


@router.message(F.text == kb.BTN_MONTH)
@router.message(Command("month"))
async def month(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    await _summary(message, employee, denial, client, "month", "За месяц")


async def _summary(message, employee, denial, client, period, title) -> None:
    if not await _guard(message, employee, denial):
        return
    body = await client.statistics(message.from_user.id, period)
    await message.answer(text.summary(body, title), reply_markup=_menu(employee))


@router.message(F.text == kb.BTN_HISTORY)
@router.message(Command("history"))
async def history(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    if not await _guard(message, employee, denial):
        return
    body = await client.history(message.from_user.id)
    await message.answer(text.history(body), reply_markup=_menu(employee))


@router.message(F.text == kb.BTN_MY_REQUESTS)
@router.message(Command("requests"))
async def my_requests(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    if not await _guard(message, employee, denial):
        return
    body = await client.absences(message.from_user.id)
    await message.answer(text.requests(body), reply_markup=_menu(employee))


# --- то, что оформляется в кабинете ----------------------------------------

@router.message(F.text == kb.BTN_SICK_LEAVE)
@router.message(Command("sick_leave"))
async def sick_leave(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    """Больничный оформляется в Mini App, а не перепиской.

    Просить даты сообщениями в чат — способ получить «3 сентебря» и три
    уточняющих вопроса. В кабинете есть календарь.
    """
    if not await _guard(message, employee, denial):
        return

    body = await client.absences(message.from_user.id, limit=3)
    open_rows = [
        row for row in (body.get("requests") or [])
        if row["absence_type"]["code"] == "SICK_LEAVE"
        and row["status"] in ("SUBMITTED", "IN_REVIEW", "APPROVED")
    ]
    lines = ["<b>Больничный</b>", "", text.CABINET_HINT]
    if open_rows:
        lines += ["", "Сейчас оформлено:", text.requests({"requests": open_rows})]
    await message.answer("\n".join(lines), reply_markup=_menu(employee))


@router.message(F.text == kb.BTN_VACATION)
@router.message(Command("vacation"))
async def vacation(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    if not await _guard(message, employee, denial):
        return

    lines = ["<b>Отпуск</b>"]
    try:
        balance = await client.leave_balance(message.from_user.id)
        line = text.balance_line(balance)
        if line:
            lines.append(line)
    except ApiError as error:
        # Остаток — приятное дополнение, а не смысл экрана. Его недоступность
        # не повод не показать, куда идти оформлять отпуск.
        logger.info("leave balance unavailable: %s", error)

    lines += ["", text.CABINET_HINT]
    await message.answer("\n".join(lines), reply_markup=_menu(employee))


# --- помощь ----------------------------------------------------------------

@router.message(F.text == kb.BTN_HELP)
@router.message(Command("help"))
async def help_handler(message: Message, employee, denial) -> None:
    """Работает всегда, в том числе без привязки.

    Человек, которому отказали, должен хотя бы понимать, что это за бот
    и к кому идти.
    """
    await message.answer(text.HELP, reply_markup=_menu(employee))


__all__ = ["router"]
