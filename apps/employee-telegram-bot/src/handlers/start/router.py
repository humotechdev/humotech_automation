"""/start и привязка по одноразовой ссылке.

Привязку начинает HR: он выдаёт сотруднику персональную ссылку вида
`https://t.me/<бот>?start=link_<ТОКЕН>`. Бот участвует ровно одним вызовом —
сообщает backend токен и подтверждённый Telegram-аккаунт. Решение принимает
backend, бот только пересказывает ответ человеку.

Бизнес-логики привязки здесь нет и быть не должно. Handler разбирает
полезную нагрузку `/start`, вызывает клиента и подбирает текст под код
ответа. Всё остальное — срок, одноразовость, отзыв, чей это сотрудник —
решается там, где есть база и права.

Порядок регистрации внутри роутера имеет значение: обработчик ссылки идёт
ПЕРЕД обычным `/start`, иначе полезная нагрузка потеряется.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.api.errors import ApiError
from src.api.selfservice import SelfServiceClient
from src.handlers.attendance.sticker import begin as begin_sticker, sticker_payload
from src.keyboards import employee as kb
from src.keyboards import onboarding as ob
from src.messages import link as text
from src.messages import onboarding as onboarding_text

logger = logging.getLogger(__name__)

router = Router(name="start")

# Префикс полезной нагрузки `/start`. Telegram отдаёт в ней до 64 символов
# из [A-Za-z0-9_-], чего хватает на «link_» плюс токен в 43 символа.
LINK_PREFIX = "link_"

# Ссылка на первичное ознакомление. Токен и приглашение те же самые:
# разное у них только то, что человека ждёт после привязки. Второй
# механизм ссылок означал бы две двери в один дом, и закрыть за собой
# обе никто не вспомнит.
ONBOARDING_PREFIX = "onboarding_"

#: Оба префикса разом. Порядок значения не имеет — они не пересекаются.
LINK_PREFIXES = (LINK_PREFIX, ONBOARDING_PREFIX)


def parse_link_payload(payload: str | None) -> str | None:
    """Токен из полезной нагрузки `/start`, либо None.

    Разбор строгий: посторонняя нагрузка не должна случайно оказаться
    «почти токеном» и уйти на backend.
    """
    if not payload:
        return None
    payload = payload.strip()
    for prefix in LINK_PREFIXES:
        if payload.startswith(prefix):
            return payload[len(prefix):] or None
    return None


def is_onboarding_link(payload: str | None) -> bool:
    """Ведёт ли ссылка на ознакомление, а не на обычную привязку."""
    return bool(payload) and payload.strip().startswith(ONBOARDING_PREFIX)


@router.message(CommandStart(deep_link=True))
async def start_with_link(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    client: SelfServiceClient,
    employee,
    denial,
) -> None:
    # Печатный QR-код офиса ведёт сюда же, через `/start qr_…`. Его
    # разбирает отметка, а не привязка: у него свой разговор — геопозиция.
    sticker = sticker_payload(command.args)
    if sticker is not None:
        await begin_sticker(message, sticker, state, employee, denial)
        return

    token = parse_link_payload(command.args)
    if token is None:
        # Нагрузка есть, но не наша: ведём себя как при обычном /start.
        # Причину отказа передаём дальше — без неё человек без привязки
        # получил бы «доступ закрыт» вместо «попросите ссылку».
        await plain_or_recognize(message, employee, denial, client)
        return

    await state.clear()
    user = message.from_user
    onboarding_link = is_onboarding_link(command.args)
    try:
        result = await client.consume_link_token(
            token=token,
            telegram_user_id=user.id,
            telegram_chat_id=message.chat.id,
            telegram_username=user.username,
            language_code=user.language_code,
        )
    except ApiError as error:
        reason = (error.details or {}).get("reason") if error.details else None
        # Токена в журнале нет: он рабочий секрет, пока ссылка жива.
        logger.info("link attempt refused for %s: %s", user.id, reason or error.code)
        # Ссылка могла быть открыта за минуту до обновления бота: тогда
        # аккаунт уже PENDING, но человек ещё не видел условия. Повторный
        # переход продолжает тот же сценарий. Принять условия сможет
        # только тот Telegram ID, который погасил ссылку.
        if reason == "pending":
            # Ссылку уже открывали: привязка ждёт согласия. Продолжаем
            # тот же сценарий — и именно тот, на который указывает
            # префикс ссылки, а не какой-нибудь другой.
            if onboarding_link:
                await message.answer(
                    onboarding_text.greeting(user.first_name),
                    reply_markup=ob.welcome(),
                )
                return
            await message.answer(text.LINK_PENDING, reply_markup=kb.link_consent())
            return
        await message.answer(
            text.LINK_MESSAGES.get(reason, text.LINK_ERROR),
            reply_markup=kb.help_only_menu(),
        )
        return

    # Что показать после привязки, решает СЕРВЕР, а не префикс ссылки:
    # человека могли позвать на ознакомление и обычной ссылкой, и
    # наоборот. Префикс — подсказка для того случая, когда сервер уже
    # ничего не отвечает (ссылка открыта повторно).
    if result.get("onboarding_required") or onboarding_link:
        await message.answer(
            onboarding_text.greeting(user.first_name), reply_markup=ob.welcome()
        )
        return
    await message.answer(text.LINK_PENDING, reply_markup=kb.link_consent())


@router.callback_query(F.data == kb.LINK_ACCEPT)
async def accept_link_terms(callback: CallbackQuery, client: SelfServiceClient) -> None:
    try:
        await client.accept_link_terms(telegram_user_id=callback.from_user.id)
    except ApiError:
        await callback.answer("Не получилось подтвердить условия. Попробуйте ещё раз.", show_alert=True)
        return
    await callback.answer("Условия приняты")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        from src.handlers.menu.router import build_menu

        # Профиль перечитывается, а не подставляется заглушкой: меню
        # зависит от того, ждёт ли человека ознакомление, и собранное
        # по `True` обещало бы рабочие разделы тому, кому они закрыты.
        try:
            profile = await client.profile(callback.from_user.id)
        except ApiError:
            profile = None
        await callback.message.answer(
            text.LINK_CONNECTED,
            reply_markup=build_menu(profile, callback.message),
        )


@router.message(CommandStart(deep_link=False))
async def start_plain(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    """Обычный /start — без ссылки.

    Бот не может написать первым: правило Telegram. Но человек, открывший
    бота сам, приносит своё имя в Telegram, и если кадровик указал его в
    карточке — узнать пришедшего можно без всякой ссылки.
    """
    await plain_or_recognize(message, employee, denial, client)


async def plain_or_recognize(
    message: Message, employee, denial, client: SelfServiceClient
) -> None:
    """Привязанного ведём в меню, незнакомого — пробуем узнать.

    Попытка узнавания делается ровно один раз и только для тех, у кого
    привязки нет: у остальных она ничего не изменила бы, а лишний запрос
    на каждый /start — это запрос на каждый /start.
    """
    from src.handlers.menu.router import start as plain_start

    if employee is not None:
        await plain_start(message, employee, denial)
        return

    user = message.from_user
    try:
        facts = await client.recognize(
            telegram_user_id=user.id,
            telegram_chat_id=message.chat.id,
            telegram_username=user.username,
            language_code=user.language_code,
        )
    except ApiError as error:
        reason = (error.details or {}).get("reason") if error.details else None
        logger.info("recognize refused for %s: %s", user.id, reason or error.code)
        await message.answer(
            text.RECOGNIZE_MESSAGES.get(reason)
            or text.LINK_MESSAGES.get(reason, text.LINK_ERROR),
            reply_markup=kb.help_only_menu(),
        )
        return

    # Узнали. Доступа это не даёт — привязка ждёт кадровика, — но
    # поздороваться по имени и рассказать условия можно уже сейчас.
    await message.answer(text.welcome(facts), reply_markup=kb.help_only_menu())


__all__ = [
    "LINK_PREFIX",
    "LINK_PREFIXES",
    "ONBOARDING_PREFIX",
    "is_onboarding_link",
    "parse_link_payload",
    "plain_or_recognize",
    "router",
    "start_plain",
]
