"""Кнопка под сообщением очереди — по типу уведомления.

Очередь несёт текст и тип; какая кнопка полагается этому типу, решается
здесь и только здесь. Раньше кнопок не было вовсе: сообщение было
сообщением, и этого хватало.

Опрос — первый случай, когда не хватает. Прислать шесть вопросов
отдельными сообщениями значит превратить чат в анкету, которую бросают
на третьем вопросе: в чате нельзя вернуться назад, нельзя увидеть, что
осталось, и нельзя ответить шкалой. Поэтому в чат уходит одно короткое
приглашение, а вопросы показывает Mini App.

Если адрес Mini App не настроен, кнопки не будет — и это правильнее
кнопки, которая ничего не открывает: человек хотя бы поймёт, что надо
спросить у HR, а не будет нажимать в пустоту.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from src.config.settings import settings

logger = logging.getLogger("humotech.notifications")

#: Приглашение пройти опрос. Тот же код стоит на стороне backend.
SURVEY_INVITE = "survey.invite"

#: Напоминание о начале дня. Тот же код стоит на стороне backend.
DAY_START = "attendance.day_start"

#: Справку не приняли. Тот же код стоит на стороне backend.
DOCUMENT_REJECTED = "absence.document_rejected"

#: Коды нажатий. Короткие: Telegram ограничивает `callback_data`
#: шестьюдесятью четырьмя байтами, и длинное имя однажды не влезет.
MARK_NOW = "day:mark"
#: Загрузить справку к заявке. Идентификатор заявки идёт следом:
#: тридцать шесть знаков UUID плюс префикс укладываются в лимит.
UPLOAD_DOCUMENT = "doc:"
SAY_LATE = "day:late"
SAY_ABSENT = "day:absent"


def markup_for(
    notification_type: str, entity_id: str | None
) -> InlineKeyboardMarkup | None:
    """Разметка для сообщения этого типа или `None`, если её не нужно."""
    if notification_type == DAY_START:
        return day_start_markup()
    if notification_type == DOCUMENT_REJECTED:
        return document_markup(entity_id)
    if notification_type != SURVEY_INVITE:
        return None
    if not entity_id:
        # Backend прислал приглашение без ссылки на опрос. Кнопка
        # открыла бы «какой-то» опрос — лучше без неё.
        logger.warning("survey invite without entity id: button skipped")
        return None
    url = survey_url(entity_id)
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Пройти опрос", web_app=WebAppInfo(url=url),
            )
        ]]
    )


def day_start_markup() -> InlineKeyboardMarkup:
    """Три ответа на напоминание о начале дня.

    «Отметиться сейчас» стоит первым: человек чаще всего просто забыл
    приложить телефон, и путь к отметке должен быть короче, чем к
    объяснению. Оно открывает Mini App там, где адрес настроен, и
    остаётся подсказкой, где не настроен, — кнопка, которая ничего не
    открывает, хуже её отсутствия.

    «Не приду» не оформляет ни отпуска, ни больничного: это
    предупреждение, а не заявка, и бот говорит об этом прямо в ответе.
    """
    first: InlineKeyboardButton
    url = scan_url()
    if url:
        first = InlineKeyboardButton(
            text="Отметиться сейчас", web_app=WebAppInfo(url=url)
        )
    else:
        first = InlineKeyboardButton(
            text="Отметиться сейчас", callback_data=MARK_NOW
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [first],
            [
                InlineKeyboardButton(text="Опаздываю", callback_data=SAY_LATE),
                InlineKeyboardButton(text="Не приду", callback_data=SAY_ABSENT),
            ],
        ]
    )


def document_markup(request_id: str | None) -> InlineKeyboardMarkup | None:
    """Кнопка «Загрузить справку» под отказом.

    Без неё человек читает «загрузите корректный документ» и идёт
    искать, где именно, — а заявка, к которой прикладывать, у него не
    одна. Кнопка снимает этот вопрос: она знает свою заявку.
    """
    if not request_id:
        # Backend прислал отказ без ссылки на заявку. Кнопка приложила
        # бы справку «куда-нибудь» — лучше без неё.
        logger.warning("document rejection without request id: button skipped")
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Загрузить справку",
                callback_data=f"{UPLOAD_DOCUMENT}{request_id}",
            )
        ]]
    )


def scan_url() -> str | None:
    """Экран отметки в Mini App."""
    base = (settings.mini_app_url or "").strip()
    if not base:
        logger.warning("MINI_APP_URL пуст: кнопка отметки не показана")
        return None
    return f"{base.rstrip('/')}/scan"


def survey_url(recipient_id: str) -> str | None:
    """Адрес опроса в Mini App.

    Идентификатор экранируется: он приходит из ответа backend, но
    подставлять в адрес что угодно без проверки — привычка, которая
    однажды подставит туда лишнее.
    """
    base = (settings.mini_app_url or "").strip()
    if not base:
        logger.warning("MINI_APP_URL пуст: кнопка опроса не показана")
        return None
    return f"{base.rstrip('/')}/survey/{quote(str(recipient_id), safe='')}"


__all__ = [
    "DAY_START",
    "DOCUMENT_REJECTED",
    "UPLOAD_DOCUMENT",
    "document_markup",
    "MARK_NOW",
    "SAY_ABSENT",
    "SAY_LATE",
    "SURVEY_INVITE",
    "day_start_markup",
    "markup_for",
    "scan_url",
    "survey_url",
]
