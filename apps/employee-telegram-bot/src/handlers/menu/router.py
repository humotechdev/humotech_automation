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
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.config.settings import settings
from src.keyboards import employee as kb
from src.keyboards import onboarding as ob
from src.utils.menu_button import drop_chat_override
from src.messages import employee as text
from src.middlewares.employee import REASON_UNAVAILABLE

logger = logging.getLogger(__name__)

router = Router(name="employee-menu")

# Причина отказа -> что показать. Три разных ответа, потому что человеку
# в них надо делать разное: подождать, попросить ссылку, идти в кадры.
#: Слово-переключатель у `/keyboard`: прислать меню без кнопок запуска.
PLAIN_WORDS = {"plain", "текст", "text", "без"}

DENIAL_TEXT = {
    "pending_confirmation": text.PENDING,
    "not_linked": text.NOT_LINKED,
    REASON_UNAVAILABLE: text.BACKEND_DOWN,
}


def onboarding_state(employee) -> dict:
    """Блок ознакомления из ответа профиля.

    Терпим к тому, что придёт не словарь: сюда попадает и `True` из
    старого вызова, и профиль без этого блока — например, от backend,
    который ещё не обновили. В обоих случаях правильный ответ —
    «ознакомление ни при чём», а не падение на `.get`.
    """
    if not isinstance(employee, dict):
        return {}
    block = employee.get("onboarding")
    return block if isinstance(block, dict) else {}


def onboarding_done(employee) -> bool:
    """Закончил ли человек ознакомление.

    Доступа это не касается: рабочие разделы открыты в любом случае.
    От ответа зависит одно — показывать ли пункт «Продолжить
    ознакомление» и напоминание под меню.

    По умолчанию — «закончил». Отсутствие блока означает, что сервер
    про ознакомление ничего не сказал, и приставать к человеку на этом
    основании не за что.
    """
    return bool(onboarding_state(employee).get("completed", True))


def build_menu(
    employee, message: Message | None = None, *, launch_apps: bool | None = None
) -> ReplyKeyboardMarkup:
    """Нижняя клавиатура. Один сборщик на бота, здесь только выбор набора.

    Тип чата важен: `web_app` у кнопки нижней клавиатуры Telegram
    разрешает только в личном чате, и в группе такая клавиатура — ошибка
    запроса целиком, то есть человек остался бы вообще без кнопок.

    Набор ПОЛНЫЙ с первого дня. Пока ознакомление не завершено, сверху
    добавляется «Продолжить ознакомление» — но остальное не прячется:
    отметка присутствия нужна человеку в первое же утро, а ознакомление
    лечится напоминанием, а не запертой дверью.
    """
    if not employee:
        return kb.help_only_menu()
    private = message is None or getattr(message.chat, "type", "private") == "private"
    if launch_apps is None:
        launch_apps = settings.keyboard_launch_buttons
    markup = kb.employee_menu(
        settings.mini_app_url, private=private, launch_apps=launch_apps,
        onboarding=not onboarding_done(employee),
    )
    describe(markup, who=message)
    return markup


#: Прежнее имя. Осталось, чтобы не разошлись вызовы внутри модуля.
_menu = build_menu


def describe(markup: ReplyKeyboardMarkup, *, who: Message | None = None) -> None:
    """Что именно уходит в Telegram — в журнал, одной строкой.

    По жалобе «кнопок нет» иначе нечего смотреть: конструктор в коде и
    разметка в запросе — разные вещи, и расходятся они молча. Ни токенов,
    ни строк запуска, ни персональных данных здесь нет — только подписи
    кнопок и флаги разметки.
    """
    labels = [button.text for row in markup.keyboard for button in row]
    web_apps = sum(
        1 for row in markup.keyboard for button in row if button.web_app is not None
    )
    logger.info(
        "keyboard -> %s: rows=%d buttons=%d web_app=%d persistent=%s resize=%s "
        "one_time=%s selective=%s labels=%s",
        getattr(getattr(who, "from_user", None), "id", "?"),
        len(markup.keyboard),
        len(labels),
        web_apps,
        markup.is_persistent,
        markup.resize_keyboard,
        markup.one_time_keyboard,
        markup.selective,
        labels,
    )


async def remind_about_onboarding(message: Message, employee) -> None:
    """Ненавязчивый блок под меню: сколько пройдено и кнопка продолжить.

    Отдельным сообщением, а не строкой в меню: нижняя клавиатура и
    inline-кнопка на одном сообщении Telegram не уживаются, а кнопка
    нужна — без неё человеку пришлось бы искать, куда нажимать.

    Молчит у тех, кого в программу не звали, и у тех, кто закончил:
    напоминание о сделанном — это шум.
    """
    if onboarding_done(employee):
        return
    from src.messages import onboarding as onboarding_text

    state = onboarding_state(employee)
    await message.answer(
        onboarding_text.nudge(state), reply_markup=ob.nudge(state)
    )


async def _guard(message: Message, employee, denial) -> bool:
    """Есть ли доступ. Если нет — объясняет и возвращает False.

    Незавершённое ознакомление доступом НЕ считается: человек
    отмечается и подаёт заявки с первого дня. Проверка «пока не
    дочитал — нельзя» здесь стояла и была снята намеренно.

    Неудача backend отделена от отказа в доступе намеренно: сказать
    «нет доступа» из-за упавшего сервера значит отправить человека
    в отдел кадров разбираться с тем, чего не происходило.

    И клавиатуру в этом случае НЕ трогаем. Прежде здесь уходила
    «Помощь» — одна кнопка вместо одиннадцати, — и сетевой сбой на
    секунду отбирал у человека меню до следующего `/start`. Состояние
    неизвестно: правильный ответ — не менять то, что у него уже есть.
    """
    if employee is not None:
        # Незавершённое ознакомление рабочим разделам не мешает: оно
        # напоминает о себе кнопкой в меню и отдельным сообщением, а не
        # отказом на входе.
        return True
    if denial == REASON_UNAVAILABLE:
        logger.info(
            "menu skipped for %s: backend unavailable, keyboard left as is",
            getattr(message.from_user, "id", "?"),
        )
        await message.answer(text.BACKEND_DOWN)
        return False
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
        reply_markup=_menu(employee, message),
    )
    await remind_about_onboarding(message, employee)


@router.message(F.text == kb.BTN_OPEN)
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
@router.message(Command("keyboard"))
async def menu(
    message: Message, command: CommandObject, employee, denial
) -> None:
    """Вернуть нижнюю клавиатуру, если её свернули или удалили.

    `/keyboard` — то же самое под именем, которое ищут, когда кнопки
    пропали: «меню» в этот момент звучит как список команд.

    `/keyboard текст` — тот же набор без кнопок запуска приложения.
    Нужен, когда кнопок не видно вовсе: если этот вариант появился, а
    обычный нет, дело в клиенте и его отношении к `web_app` в нижней
    клавиатуре, а не в том, что бот ничего не прислал.
    """
    if not await _guard(message, employee, denial):
        return
    plain = (command.args or "").strip().lower() in PLAIN_WORDS
    markup = build_menu(employee, message, launch_apps=not plain)
    body = "Меню без кнопок запуска приложения" if plain else "Меню"
    sent = await message.answer(body, reply_markup=markup)
    logger.info(
        "menu delivered to %s: message_id=%s plain=%s",
        getattr(message.from_user, "id", "?"),
        getattr(sent, "message_id", None),
        plain,
    )
    await remind_about_onboarding(message, employee)


@router.message(F.text == kb.BTN_SCAN)
@router.message(Command("scan"))
async def scan(message: Message, employee, denial) -> None:
    """Отметка inline-кнопкой — запасной путь к тому же экрану.

    Обычно сюда не попадают: кнопка нижней клавиатуры открывает
    приложение сама и боту ничего не шлёт. Обработчик нужен на случай,
    когда она этого не сделала, — тогда человек получает рабочую
    кнопку, а не молчание.
    """
    if not await _guard(message, employee, denial):
        return
    if not settings.mini_app_url:
        await message.answer(text.CABINET_UNAVAILABLE)
        return
    await message.answer(
        text.SCAN_OPEN,
        reply_markup=kb.scan_button(settings.mini_app_url),
    )


# --- быстрые ответы --------------------------------------------------------

@router.message(F.text == kb.BTN_WHERE_AM_I)
@router.message(Command("status"))
async def where_am_i(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    if not await _guard(message, employee, denial):
        return
    body = await client.status(message.from_user.id)
    await message.answer(text.presence(body), reply_markup=_menu(employee, message))


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
    await message.answer(text.summary(body, title), reply_markup=_menu(employee, message))


@router.message(F.text == kb.BTN_HISTORY)
@router.message(Command("history"))
async def history(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    if not await _guard(message, employee, denial):
        return
    body = await client.history(message.from_user.id)
    await message.answer(text.history(body), reply_markup=_menu(employee, message))


@router.message(F.text == kb.BTN_MY_REQUESTS)
@router.message(Command("requests"))
async def my_requests(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    if not await _guard(message, employee, denial):
        return
    body = await client.absences(message.from_user.id)
    await message.answer(text.requests(body), reply_markup=_menu(employee, message))


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
    await message.answer("\n".join(lines), reply_markup=_menu(employee, message))


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
    await message.answer("\n".join(lines), reply_markup=_menu(employee, message))


# --- помощь ----------------------------------------------------------------

@router.message(F.text == kb.BTN_HELP)
@router.message(Command("help"))
async def help_handler(message: Message, employee, denial) -> None:
    """Работает всегда, в том числе без привязки.

    Человек, которому отказали, должен хотя бы понимать, что это за бот
    и к кому идти.
    """
    await message.answer(text.HELP, reply_markup=_menu(employee, message))


__all__ = [
    "build_menu", "onboarding_done", "onboarding_state",
    "remind_about_onboarding", "router",
]
