"""Первичное ознакомление: десять карточек и три обязательных документа.

Бизнес-логики здесь нет ни строки. Какой раздел показывать следующим,
можно ли перейти к документам, засчитано ли согласие — решает сервер, а
этот модуль спрашивает и пересказывает. Так и должно быть: кнопка в
Telegram живёт в чате вечно, её нажимают через месяц из прокрученной
вверх переписки, и порядок, державшийся на ней, порядком быть перестаёт.

Три вещи, ради которых написан именно так:

**Одно сообщение вместо десяти.** Карточка показывается правкой того же
сообщения (`edit_message_text`). Десять сообщений подряд превращают чат
в ленту прочитанного, по которой потом никто не находит ни одной кнопки.

**Двойное нажатие — обычное дело.** Telegram показывает часы, человек
жмёт ещё раз. Повтор приходит на сервер, где уникальный ключ решает его
молча, а здесь ловится `TelegramBadRequest` на правке: если текст и
кнопки те же, Telegram отвечает «message is not modified», и без этой
ветки ошибка уходила бы в общий обработчик и пугала человека.

**Ответ на нажатие — раньше работы.** `callback.answer()` вызывается до
обращения к backend, иначе у человека крутится индикатор всё время
запроса.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from src.api.errors import ApiError, Conflict, Forbidden, NotFound, Unauthorized
from src.api.selfservice import SelfServiceClient
from src.keyboards import employee as kb
from src.keyboards import onboarding as ob
from src.messages import employee as base_text
from src.messages import onboarding as text
from src.middlewares.employee import REASON_UNAVAILABLE

logger = logging.getLogger(__name__)

router = Router(name="onboarding")

#: Telegram обрезает сообщение на 4096 символах. Правовой текст бывает
#: длиннее, и режется он по абзацам, а не по середине предложения.
CHUNK = 3500


def enrolled(employee) -> bool:
    """Позвали ли человека проходить ознакомление."""
    return bool((employee or {}).get("onboarding", {}).get("enrolled"))


def finished(employee) -> bool:
    """Открыты ли человеку рабочие функции.

    Для тех, кого в программу не звали, сервер отвечает `completed=true`:
    им важно не то, прошли ли они программу, а то, открыт ли бот.
    """
    return bool((employee or {}).get("onboarding", {}).get("completed", True))


async def _ready(message: Message, employee, denial) -> bool:
    """Есть ли с кем разговаривать.

    Отдельно от `_guard` меню: там проверка «пускать ли к рабочим
    разделам», здесь — «знаем ли мы человека вообще». Ознакомление как
    раз и открыто тому, кого к рабочим разделам ещё не пускают.
    """
    if employee is not None:
        return True
    if denial == REASON_UNAVAILABLE:
        await message.answer(base_text.BACKEND_DOWN)
        return False
    if denial == "pending_confirmation":
        # Человек перешёл по ссылке, но ещё не нажал «Начать
        # ознакомление» — до этого нажатия привязка ждёт согласия.
        # «Вы не связаны с учётной записью» здесь было бы неправдой.
        await message.answer(text.PRESS_START)
        return False
    await message.answer(
        base_text.NOT_LINKED, reply_markup=kb.help_only_menu()
    )
    return False


# --- показ состояния --------------------------------------------------------


async def show(target: Message, state: dict, *, edit: bool = False) -> None:
    """Показать то, что сервер назвал текущим шагом.

    `edit=True` — правка уже показанного сообщения. Именно так чат
    остаётся чистым: карточки сменяют друг друга на месте, а не копятся.
    """
    body, markup = screen(state)
    if edit:
        try:
            await target.edit_text(body, reply_markup=markup)
            return
        except TelegramBadRequest as error:
            if "message is not modified" in str(error).lower():
                # Двойное нажатие: содержимое то же самое. Это не ошибка
                # и не повод писать человеку что-либо.
                return
            # Сообщение слишком старое для правки либо уже удалено —
            # тогда просто присылаем новое.
            logger.info("edit failed, sending a new message: %s", error)
    await target.answer(body, reply_markup=markup)


def screen(state: dict) -> tuple[str, object | None]:
    """Текст и кнопки текущего шага. Решение принято сервером — здесь перевод."""
    stage = state.get("stage")

    if stage == "SECTIONS" and state.get("section"):
        return text.card(state["section"]), ob.card(state["section"])

    if stage == "POLICIES" and state.get("policy"):
        return text.document(state["policy"]), ob.document(state["policy"])

    if stage == "BLOCKED":
        # Отказ: кнопки согласия под ним есть — человек мог передумать, —
        # но ведёт его дальше разговор с кадровиком, а не бот.
        policy = _declined(state)
        if policy is not None:
            return (
                f"{text.document(policy)}\n\n{text.DECLINED}",
                ob.document(policy),
            )
        return text.DECLINED, None

    return text.FINISHED, ob.finished()


def _declined(state: dict) -> dict | None:
    for one in state.get("policies") or []:
        if one.get("decision") == "DECLINED":
            return one
    return None


# --- нижняя клавиатура ------------------------------------------------------


def mid_onboarding(employee) -> bool:
    """Человек известен, но ознакомление не закончил.

    Зовётся из разбора `/start` — не фильтром роутера. Фильтр здесь
    стоять не может: `/start` без полезной нагрузки перехватывает
    роутер `start`, который включён первым, и до этого роутера
    обновление просто не доходит. Такой фильтр выглядел бы рабочим и
    молчал.
    """
    return employee is not None and not finished(employee)


@router.message(F.text == ob.BTN_CONTINUE)
@router.message(Command("onboarding"))
async def continue_onboarding(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    """«Продолжить ознакомление».

    Здесь всегда НОВОЕ сообщение, а не правка: человек вернулся через
    день, старая карточка ушла вверх на сотню сообщений, и правка в ней
    осталась бы незамеченной.
    """
    if not await _ready(message, employee, denial):
        return
    if not enrolled(employee):
        await message.answer(text.NOT_ENROLLED, reply_markup=menu_for(employee, message))
        return

    state = await client.onboarding(message.from_user.id)
    if state["stage"] == "SECTIONS" and state.get("section"):
        # Напоминание, где человек остановился, отдельной строкой перед
        # карточкой: без него непонятно, много ли осталось.
        if state["sections_done"]:
            await message.answer(text.resume(state))
    await show(message, state)


@router.message(F.text == ob.BTN_RULES)
@router.message(Command("policies"))
async def rules_and_documents(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    """«Правила и документы» — открыто и до, и после завершения.

    Человек имеет право перечитать то, с чем согласился, и посмотреть
    то, с чем ещё не согласился.
    """
    if not await _ready(message, employee, denial):
        return
    if not enrolled(employee):
        await message.answer(text.NOT_ENROLLED, reply_markup=menu_for(employee, message))
        return

    state = await client.onboarding(message.from_user.id)
    rows = ob.documents_list(state.get("policies") or [])
    if rows is None:
        await message.answer(text.DOCUMENTS_EMPTY)
        return
    await message.answer("<b>Правила и документы</b>", reply_markup=rows)


@router.message(F.text == ob.BTN_HR)
async def hr_contacts(message: Message, employee, denial) -> None:
    await message.answer(text.HR_CONTACTS, reply_markup=menu_for(employee, message))


# --- шаги -------------------------------------------------------------------


@router.callback_query(F.data == ob.START)
async def begin(callback: CallbackQuery, client: SelfServiceClient) -> None:
    """«Начать ознакомление».

    Здесь же, одним нажатием, принимаются условия работы с ботом. До
    этого момента привязка ждёт согласия (`PENDING`), и человек не может
    даже прочитать первую карточку. Разводить это на две кнопки подряд
    незачем: нажатие «Начать ознакомление» и есть согласие начать, а
    настоящее согласие с правилами берётся дальше — отдельно по каждому
    из обязательных документов.
    """
    await callback.answer()
    if callback.message is None:
        return
    try:
        state = await client.onboarding_start(callback.from_user.id)
    except (Forbidden, Unauthorized) as error:
        reason = (error.details or {}).get("reason") if error.details else None
        if reason != "pending_confirmation":
            raise
        await client.accept_link_terms(telegram_user_id=callback.from_user.id)
        state = await client.onboarding_start(callback.from_user.id)

    # Приветствие остаётся в чате как приветствие, но кнопка с него
    # снимается: нажатая второй раз, она ничего бы не изменила.
    await _drop_keyboard(callback)
    # Единственное «лишнее» сообщение за всё ознакомление. Нужно оно
    # ради нижней клавиатуры: повесить её на карточку нельзя — там
    # inline-кнопки, а два вида клавиатур на одном сообщении Telegram
    # не отдаёт. Зато дальше карточки сменяют друг друга на месте.
    await callback.message.answer(
        text.MENU_INSTALLED, reply_markup=ob.onboarding_menu()
    )
    body, markup = screen(state)
    await callback.message.answer(body, reply_markup=markup)


@router.callback_query(F.data.startswith(ob.ACK))
async def acknowledge(callback: CallbackQuery, client: SelfServiceClient) -> None:
    section_id = callback.data[len(ob.ACK):]
    await callback.answer()
    if callback.message is None:
        return
    try:
        state = await client.onboarding_acknowledge(
            callback.from_user.id, section_id,
            message_id=callback.message.message_id,
        )
    except Conflict as error:
        # Нажали кнопку из старого сообщения и прыгнули вперёд.
        await callback.answer(text.ORDER_BROKEN, show_alert=True)
        logger.info("acknowledge refused: %s", error.details)
        return

    if state["stage"] == "POLICIES" and state["sections_done"] == state["sections_total"]:
        # Карточки кончились. Отдельный экран между ними и документами:
        # это разные вещи, и переход между ними человек должен заметить.
        try:
            await callback.message.edit_text(
                text.info_done(state), reply_markup=ob.to_documents()
            )
        except TelegramBadRequest:
            await callback.message.answer(
                text.info_done(state), reply_markup=ob.to_documents()
            )
        return

    await show(callback.message, state, edit=True)


@router.callback_query(F.data.startswith(ob.BACK))
async def go_back(callback: CallbackQuery, client: SelfServiceClient) -> None:
    """«← Назад»: перечитать пройденный раздел.

    Согласия он не спрашивает — оно уже дано, и предлагать подтвердить
    подтверждённое значит учить нажимать не глядя.
    """
    await callback.answer()
    if callback.message is None:
        return
    try:
        position = int(callback.data[len(ob.BACK):])
    except ValueError:
        return
    section = await client.onboarding_section(callback.from_user.id, position)
    body = (
        text.card_done(section) if section.get("acknowledged_at")
        else text.card(section)
    )
    markup = (
        ob.card_read(section) if section.get("acknowledged_at")
        else ob.card(section)
    )
    try:
        await callback.message.edit_text(body, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            await callback.message.answer(body, reply_markup=markup)


@router.callback_query(F.data == ob.LATER)
async def read_later(callback: CallbackQuery) -> None:
    """«Прочитать позже». Ничего не меняет — прогресс уже сохранён."""
    await callback.answer(text.LATER, show_alert=True)


@router.callback_query(F.data == ob.DOCS)
async def to_documents(callback: CallbackQuery, client: SelfServiceClient) -> None:
    await callback.answer()
    if callback.message is None:
        return
    state = await client.onboarding(callback.from_user.id)
    await show(callback.message, state, edit=True)


@router.callback_query(F.data.startswith(ob.DOC))
async def open_document(callback: CallbackQuery, client: SelfServiceClient) -> None:
    """Карточка документа из списка «Правила и документы»."""
    await callback.answer()
    if callback.message is None:
        return
    version_id = callback.data[len(ob.DOC):]
    state = await client.onboarding(callback.from_user.id)
    found = next(
        (one for one in state.get("policies") or []
         if one.get("version_id") == version_id),
        None,
    )
    if found is None:
        await callback.answer(text.STALE_VERSION, show_alert=True)
        return
    if found.get("decision") == "ACCEPTED":
        await callback.message.answer(
            text.document_done(found), reply_markup=ob.document_read(found)
        )
        return
    await callback.message.answer(
        text.document(found), reply_markup=ob.document(found)
    )


@router.callback_query(F.data.startswith(ob.OPEN))
async def open_full_text(callback: CallbackQuery, client: SelfServiceClient) -> None:
    """«Открыть полный документ»: текст, а при наличии — и утверждённый PDF.

    Новым сообщением, а не правкой: карточка с кнопками согласия должна
    остаться на месте — к ней человек вернётся, дочитав.
    """
    await callback.answer()
    if callback.message is None:
        return
    version_id = callback.data[len(ob.OPEN):]
    try:
        body = await client.policy_text(callback.from_user.id, version_id)
    except NotFound:
        await callback.answer(text.STALE_VERSION, show_alert=True)
        return

    head = f"<b>{text.escape(body['title'])}</b>"
    if body.get("version"):
        head += f"  <i>(версия {text.escape(body['version'])})</i>"
    full = body.get("body")
    if full:
        for number, piece in enumerate(_split(full)):
            await callback.message.answer(
                f"{head}\n\n{text.escape(piece)}" if number == 0
                else text.escape(piece)
            )
    elif not body.get("has_file"):
        await callback.message.answer(f"{head}\n\n{text.TEXT_MISSING}")

    if body.get("has_file"):
        try:
            content, name, _ = await client.policy_file(
                callback.from_user.id, version_id
            )
        except ApiError as error:
            logger.info("policy file unavailable: %s", error)
            await callback.message.answer(text.FILE_MISSING)
            return
        await callback.message.answer_document(
            BufferedInputFile(content, filename=name)
        )


@router.callback_query(F.data.startswith(ob.AGREE))
@router.callback_query(F.data.startswith(ob.REFUSE))
async def decide(callback: CallbackQuery, client: SelfServiceClient) -> None:
    """Согласие или отказ по обязательному документу."""
    agreed = callback.data.startswith(ob.AGREE)
    version_id = callback.data[len(ob.AGREE):]
    await callback.answer()
    if callback.message is None:
        return
    try:
        state = await client.onboarding_decision(
            callback.from_user.id, version_id,
            "ACCEPTED" if agreed else "DECLINED",
        )
    except Conflict as error:
        reason = (error.details or {}).get("reason")
        await callback.answer(
            text.STALE_VERSION if reason == "version_outdated" else error.message,
            show_alert=True,
        )
        return

    if not agreed:
        # Кнопки снимаются: под отказом они предлагали бы передумать
        # молча, а человеку сейчас нужен разговор с кадровиком.
        await _drop_keyboard(callback)
        await callback.message.answer(
            text.DECLINED, reply_markup=ob.onboarding_menu()
        )
        return

    if state["completed"]:
        await _drop_keyboard(callback)
        await callback.message.answer(text.FINISHED, reply_markup=ob.finished())
        return

    await show(callback.message, state, edit=True)


@router.callback_query(F.data == ob.MENU)
async def open_menu(
    callback: CallbackQuery, employee, client: SelfServiceClient
) -> None:
    """«Открыть главное меню» после завершения.

    Профиль перечитывается: у `employee` в этом обновлении состояние
    ДО последнего нажатия, и меню по нему оказалось бы вчерашним.
    """
    await callback.answer()
    if callback.message is None:
        return
    fresh = await client.profile(callback.from_user.id)
    await callback.message.answer(
        base_text.CABINET_HINT, reply_markup=menu_for(fresh, callback.message)
    )


async def _drop_keyboard(callback: CallbackQuery) -> None:
    """Снять кнопки с показанного сообщения, не трогая текст."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


def _split(body: str) -> list[str]:
    """Длинный текст по абзацам, а не по середине предложения."""
    pieces: list[str] = []
    current = ""
    for paragraph in body.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > CHUNK and current:
            pieces.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces or [body]


def menu_for(employee, message: Message | None = None):
    """Нижняя клавиатура по состоянию ознакомления.

    Импорт внутри функции: меню знает про ознакомление, а ознакомление —
    про меню, и на уровне файла это был бы круг.
    """
    from src.handlers.menu.router import build_menu

    return build_menu(employee, message)


__all__ = [
    "enrolled", "finished", "menu_for", "mid_onboarding",
    "router", "screen", "show",
]
