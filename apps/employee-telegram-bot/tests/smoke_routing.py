"""Маршрутизация через настоящий Dispatcher с подменённой сессией — без сети.

Проверяет то, что прошлый смоук не мог: доходят ли апдейты до хендлеров.
"""
import asyncio
import sys
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from src.api import MemoryTokenStorage, build_client
from src.handlers import build_root_router
from src.middlewares.auth import AuthMiddleware

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {extra}")


class FakeSession(BaseSession):
    """Ловит исходящие вызовы Telegram вместо реальной отправки."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append(method)
        if name == "GetMe":
            return User(id=1, is_bot=True, first_name="bot", username="humotech_hr_bot")
        if name in ("SendMessage", "EditMessageReplyMarkup", "EditMessageText"):
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat=Chat(id=1, type="private"))
        return True

    def sent_texts(self):
        return [m.text for m in self.calls if type(m).__name__ == "SendMessage"]

    def alert_texts(self):
        return [getattr(m, "text", None) for m in self.calls
                if type(m).__name__ == "AnswerCallbackQuery"]

    def reset(self):
        self.calls.clear()


def msg(uid: int, text: str, mid: int = 1) -> Update:
    return Update(update_id=mid, message=Message(
        message_id=mid, date=datetime.now(timezone.utc),
        chat=Chat(id=uid, type="private"),
        from_user=User(id=uid, is_bot=False, first_name="Test"),
        text=text,
    ))


def cb(uid: int, data: str, mid: int = 900) -> Update:
    return Update(update_id=mid, callback_query=CallbackQuery(
        id=str(mid), chat_instance="x",
        from_user=User(id=uid, is_bot=False, first_name="Test"),
        data=data,
        message=Message(message_id=mid, date=datetime.now(timezone.utc),
                        chat=Chat(id=uid, type="private")),
    ))


async def main():
    from src.keyboards import menu as kb

    # --- 1. сколько хендлеров реально зарегистрировали составные декораторы ---
    print("== регистрация составных декораторов ==")
    root = build_root_router()
    by_name = {r.name: r for r in root.sub_routers}
    check("profile: и команда, и кнопка",
          len(by_name["profile"].message.handlers) == 2,
          f"(зарегистрировано {len(by_name['profile'].message.handlers)})")
    stub_counts = {n: len(by_name[n].message.handlers)
                   for n in ("attendance", "statistics", "sick_leave",
                             "corrections", "questions")}
    check("заглушки: по 2 хендлера в каждой",
          all(v == 2 for v in stub_counts.values()), str(stub_counts))
    hr_panel = [r for r in by_name["hr"].sub_routers if r.name == "hr:panel"][0]
    check("hr:panel — сообщения и колбэки зарегистрированы",
          len(hr_panel.message.handlers) >= 2 and len(hr_panel.callback_query.handlers) >= 2,
          f"(msg={len(hr_panel.message.handlers)}, cb={len(hr_panel.callback_query.handlers)})")

    # --- 2. живая маршрутизация ---
    print("== маршрутизация через Dispatcher ==")
    session = FakeSession()
    bot = Bot(token="42:TEST", session=session)
    dp = Dispatcher(storage=MemoryStorage())
    client = build_client()
    dp.update.outer_middleware(AuthMiddleware(client, MemoryTokenStorage()))
    dp.include_router(root)

    async def feed(update):
        session.reset()
        await dp.feed_update(bot, update)
        return session.sent_texts()

    # непривязанный жмёт кнопку из старой клавиатуры
    texts = await feed(msg(100, kb.BTN_PROFILE))
    check("непривязанный получает ответ, а не тишину",
          any("привязать аккаунт" in t for t in texts), str(texts))

    texts = await feed(msg(100, "просто текст"))
    check("непривязанный: любой текст не остаётся без ответа",
          any("привязать аккаунт" in t for t in texts), str(texts))

    session.reset()
    await dp.feed_update(bot, cb(100, "absence:approve:1"))
    check("устаревшая inline-кнопка получает ответ (нет вечного спиннера)",
          any(a and "устарела" in a for a in session.alert_texts()),
          str(session.alert_texts()))

    await feed(msg(100, "/start"))
    texts = await feed(msg(100, kb.BTN_CANCEL))
    check("«Отмена» в привязке не принимается за телефон",
          any("Отменено" in t for t in texts), str(texts))

    # привязка сотрудника
    await feed(msg(100, "/start"))
    await feed(msg(100, "+992900000001"))
    texts = await feed(msg(100, "000000"))
    check("сотрудник привязался", any("привязан" in t for t in texts), str(texts))

    # обе формы вызова профиля
    texts = await feed(msg(100, "/profile"))
    check("/profile отвечает", any("Должность" in t for t in texts), str(texts))
    texts = await feed(msg(100, kb.BTN_PROFILE))
    check("кнопка «Профиль» отвечает", any("Должность" in t for t in texts), str(texts))

    # заглушка обеими формами
    texts = await feed(msg(100, "/statistics"))
    check("/statistics отвечает", bool(texts), str(texts))
    texts = await feed(msg(100, kb.BTN_STATISTICS))
    check("кнопка «Статистика» отвечает", bool(texts), str(texts))

    # сотрудник пытается в HR
    texts = await feed(msg(100, "/requests"))
    check("сотруднику HR-команда недоступна",
          not any("заявка" in t.lower() for t in texts), str(texts))
    texts = await feed(msg(100, kb.BTN_HR_PANEL))
    check("сотруднику HR-кнопка недоступна",
          not any("HR-панель" in t for t in texts), str(texts))

    # FSM отпуска целиком
    await feed(msg(100, kb.BTN_VACATION))
    await feed(msg(100, "01.12.2026"))
    await feed(msg(100, "14.12.2026"))
    texts = await feed(msg(100, "-"))
    check("FSM отпуска дошёл до подтверждения",
          any("Проверьте заявку" in t for t in texts), str(texts))
    texts = await feed(cb(100, "vacation:submit"))
    check("заявка отправлена", any("отправлена на согласование" in t for t in texts),
          str(texts))

    # некорректная дата не ломает сценарий
    await feed(msg(100, kb.BTN_VACATION))
    texts = await feed(msg(100, "вчера"))
    check("кривая дата — понятная ошибка", any("ДД.ММ.ГГГГ" in t for t in texts),
          str(texts))
    await feed(msg(100, kb.BTN_CANCEL))

    # привязка HR
    await feed(msg(200, "/start"))
    await feed(msg(200, "+992900000002"))
    texts = await feed(msg(200, "000000"))
    check("HR привязался", any("привязан" in t for t in texts), str(texts))

    texts = await feed(msg(200, kb.BTN_HR_PANEL))
    check("HR видит панель", any("HR-панель" in t for t in texts), str(texts))
    texts = await feed(cb(200, "hr:requests"))
    check("HR видит очередь заявок",
          any("заявка №" in t for t in texts), str(texts)[:200])
    texts = await feed(cb(200, "hr:today"))
    check("HR видит сводку дня", any("На месте" in t for t in texts), str(texts)[:200])

    texts = await feed(cb(200, "absence:approve:119"))
    check("HR одобрил заявку", any("одобрена" in t for t in texts), str(texts))
    texts = await feed(cb(200, "absence:approve:119"))
    check("повторное одобрение -> «уже обработал другой»",
          any("уже обработал" in t for t in texts), str(texts))

    print("== fallback для привязанных ==")
    texts = await feed(msg(100, "абракадабра"))
    check("привязанный получает подсказку, а не тишину",
          any("Не понял" in t for t in texts), str(texts))
    texts = await feed(msg(200, "абракадабра"))
    check("HR тоже получает подсказку", any("Не понял" in t for t in texts), str(texts))

    print("== нетекстовый ввод внутри сценария ==")
    await feed(msg(100, kb.BTN_VACATION))
    session.reset()
    nontext = Update(update_id=999, message=Message(
        message_id=999, date=datetime.now(timezone.utc),
        chat=Chat(id=100, type="private"),
        from_user=User(id=100, is_bot=False, first_name="Test"),
    ))
    await dp.feed_update(bot, nontext)
    check("не-текст в шаге даты — подсказка, а не тишина",
          any(t and "ДД.ММ.ГГГГ" in t for t in session.sent_texts()),
          str(session.sent_texts()))
    await feed(msg(100, kb.BTN_CANCEL))

    await bot.session.close()
    await client.close()
    print(f"\nИТОГО: {ok} PASS, {fail} FAIL")
    sys.exit(1 if fail else 0)


asyncio.run(main())
