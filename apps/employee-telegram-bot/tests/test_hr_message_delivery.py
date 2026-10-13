"""Сообщение HR в чате: отдельным сообщением, без привязки к вопросу.

Telegram-reply на сообщение сотрудника «прикрепил» бы каждое следующее
сообщение HR к первоначальному вопросу — ровно то, от чего заголовки
«HR ответил на ваш вопрос» / «Сообщение от HR» и уводят. Разметки нет:
текст кадровика уходит как есть, и `<a href>` в нём ссылкой не станет.
"""

from __future__ import annotations

import asyncio

from src.notifications.sender import TelegramSender


class Bot:
    def __init__(self):
        self.calls: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append({"chat_id": chat_id, "text": text, **kwargs})


def _deliver(body: str) -> dict:
    bot = Bot()
    outcome = asyncio.run(TelegramSender(bot).deliver(
        chat_id=77, text=body, notification_type="question.reply",
        entity_id="00000000-0000-0000-0000-000000000001",
    ))
    assert outcome.sent is True
    return bot.calls[-1]


def test_hr_messages_are_not_telegram_replies_to_the_question():
    for body in ("HR ответил на ваш вопрос\n\nУкажите даты.",
                 "Сообщение от HR\n\nНа какие даты?"):
        call = _deliver(body)

        assert call["text"] == body
        assert "reply_to_message_id" not in call
        assert "reply_parameters" not in call


def test_hr_text_goes_without_markup_so_tags_stay_text():
    call = _deliver('Сообщение от HR\n\n<a href="https://evil.example">портал</a>')

    assert call["parse_mode"] is None
