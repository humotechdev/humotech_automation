"""Безопасность бота: разметка Telegram, callback_data, deep-link, файлы.

Бот шлёт сообщения с `parse_mode=HTML`. Всё, что приходит с сервера или
от человека, обязано экранироваться; разметку пишет только код бота.
Идентификаторы из `callback_data` и очереди уходят в путь запроса к
backend с общим секретом — туда пускается только UUID.

Данные вымышленные.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from src.api.errors import ApiError, Conflict
from src.messages import attendance as attendance_text
from src.messages import employee as text
from src.messages import link as link_text
from src.messages import onboarding as onboarding_text
from src.utils.safe import escape, is_uuid, safe_filename

EVIL = '<a href="https://phish.example/login">Портал HR</a> 5 < 6 & <tg-spoiler>x'
UUID = "11111111-2222-4333-8444-555555555555"
TRAVERSAL_ID = "../../telegram/bot/outbox"

#: Теги, которые бот ставит сам. Любой другой в готовом тексте — инъекция.
OWN_TAGS = {"b", "i"}


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)


def tags_of(body: str) -> set[str]:
    parser = _Tags()
    parser.feed(body)
    return set(parser.tags)


def assert_no_injection(body: str) -> None:
    assert tags_of(body) <= OWN_TAGS, body
    assert "phish.example" not in body or "&lt;a href" in body
    assert " < 6" not in body  # сырой «<» ломает разбор Telegram


# --- 1. Экранирование в сообщениях -------------------------------------------


class TestEscaping:
    def test_greet(self):
        body = text.greet({
            "employee": {"full_name": EVIL},
            "position": {"name": EVIL},
            "office": {"name": EVIL},
        })
        assert_no_injection(body)

    def test_presence(self):
        body = text.presence({
            "state": "SICK_LEAVE", "absence_name": EVIL, "timezone": "UTC",
            "seconds_today": 0,
        })
        assert_no_injection(body)

    def test_history(self):
        body = text.history({
            "period": {"timezone": "UTC"},
            "days": [{
                "day": "2026-09-01", "seconds": 60, "absence_name": EVIL,
                "sessions": [{
                    "started_at": "2026-09-01T09:00:00+00:00",
                    "ended_at": "2026-09-01T10:00:00+00:00", "is_open": False,
                    "office_name": EVIL, "entry_point_name": EVIL,
                }],
            }],
        })
        assert_no_injection(body)

    def test_requests_with_hr_comment(self):
        """(было: комментарий кадровика уходил в HTML как есть.)"""
        body = text.requests({"requests": [{
            "absence_type": {"name": EVIL, "code": "X"},
            "status": "REJECTED", "stage": "REJECTED",
            "first_day": "2026-09-01", "last_day": "2026-09-02",
            "working_days": 2, "review_comment": EVIL,
        }]})
        assert_no_injection(body)
        assert "&lt;a href" in body

    def test_balance(self):
        body = text.balance_line({"balances": [
            {"absence_type": {"name": EVIL}, "available_days": 3.0}
        ]})
        assert_no_injection(body)

    def test_assistant_answer_cannot_inject_links(self):
        """(было: ответ ассистента — prompt-инъекция или документ базы знаний
        с `<a href>` — становился кликабельной подменённой ссылкой.)"""
        body = text.ask_answered(EVIL, [EVIL, "Правила <внутренние>"])
        assert_no_injection(body)

    def test_ask_hr_number(self):
        assert_no_injection(text.ask_hr_sent({"created": True, "number": EVIL}))

    def test_welcome_after_recognize(self):
        body = link_text.welcome({
            "full_name": EVIL, "employment_status": "ACTIVE", "hire_date": EVIL,
            "office_name": EVIL, "department_name": EVIL, "position_name": EVIL,
            "schedule_name": EVIL, "manager_name": EVIL,
        })
        assert_no_injection(body)

    @pytest.mark.parametrize("status", ["ENTERED", "OUTSIDE_GEOFENCE", "QR_EXPIRED"])
    def test_scan_outcome(self, status):
        body = attendance_text.outcome({
            "status": status, "office_name": EVIL, "point_name": EVIL,
            "occurred_at_local": EVIL,
        })
        assert_no_injection(body)

    def test_onboarding_screens(self):
        section = {
            "position": EVIL, "total": EVIL, "title": EVIL, "body": EVIL,
            "acknowledged_at": "2026-09-01T09:00:00", "acknowledged_version": EVIL,
        }
        assert_no_injection(onboarding_text.card_done(section))
        policy = {"index": EVIL, "total": EVIL, "title": EVIL, "version": EVIL,
                  "summary": EVIL}
        assert_no_injection(onboarding_text.document(policy))
        assert_no_injection(onboarding_text.resume(
            {"section": {"position": EVIL, "title": EVIL}, "sections_total": EVIL}
        ))
        assert_no_injection(onboarding_text.greeting(EVIL))

    def test_escape_is_idempotent_on_plain_text(self):
        assert escape("Иванов Иван") == "Иванов Иван"
        assert escape(None) == ""
        assert escape(5) == "5"


# --- 2. Очередь уведомлений: обычный текст ----------------------------------


class _Bot:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_message(self, chat_id, body, **kwargs):
        self.calls.append(("message", {"text": body, **kwargs}))

    async def send_document(self, chat_id, document, **kwargs):
        self.calls.append(("document", kwargs))


class _Files:
    def __init__(self):
        self.asked: list[tuple] = []

    async def absence_application(self, telegram_id, entity_id):
        self.asked.append(("application", entity_id))
        return b"%PDF", "z.pdf"

    async def absence_certificate(self, telegram_id, entity_id):
        self.asked.append(("certificate", entity_id))
        return b"%PDF", "s.pdf"

    async def question_reply_file(self, telegram_id, entity_id):
        self.asked.append(("question", entity_id))
        return b"%PDF", "q.pdf"


def _deliver(bot, files=None, **kwargs):
    from src.notifications.sender import TelegramSender

    base = {"chat_id": 1, "text": EVIL, "notification_type": "absence.rejected"}
    base.update(kwargs)
    return asyncio.run(TelegramSender(bot, files).deliver(**base))


class TestOutboxIsPlainText:
    def test_message_goes_without_markup(self):
        """(было: текст очереди уходил с HTML по умолчанию — «<» срывал
        доставку, `<a href>` в ответе HR становился ссылкой.)"""
        bot = _Bot()
        outcome = _deliver(bot)
        assert outcome.sent
        kind, sent = bot.calls[0]
        assert kind == "message"
        assert "parse_mode" in sent and sent["parse_mode"] is None
        # Текст не искажён: в обычном тексте экранировать нечего.
        assert sent["text"] == EVIL

    def test_document_caption_goes_without_markup(self):
        bot, files = _Bot(), _Files()
        _deliver(bot, files, attachment="absence_application", entity_id=UUID,
                 telegram_user_id=7)
        assert bot.calls and all(
            "parse_mode" in sent and sent["parse_mode"] is None
            for _, sent in bot.calls
        )

    @pytest.mark.parametrize(
        "entity", ["../../telegram/bot/outbox", "x/../../y", "", "12345", UUID + "/x"]
    )
    def test_attachment_needs_uuid(self, entity):
        bot, files = _Bot(), _Files()
        _deliver(bot, files, attachment="absence_certificate", entity_id=entity,
                 telegram_user_id=7)
        assert files.asked == []

    def _failing_files(self, error):
        class Files(_Files):
            async def question_reply_file(self, telegram_id, entity_id):
                raise error

            async def absence_certificate(self, telegram_id, entity_id):
                raise error

        return Files()

    def test_missing_reply_file_is_final(self):
        """Файл не прошёл проверку (404): отказ окончательный, без повторов."""
        from src.api.errors import NotFound

        bot = _Bot()
        outcome = _deliver(
            bot, self._failing_files(NotFound(404, "not_found", "нет")),
            attachment="question_file", entity_id=UUID, telegram_user_id=7,
        )
        assert (outcome.sent, outcome.error) == (False, "attachment_not_found")
        assert bot.calls == []

    def test_temporary_failure_is_retried(self):
        from src.api.errors import ServerError

        bot = _Bot()
        outcome = _deliver(
            bot, self._failing_files(ServerError(502, "bad_gateway", "x")),
            attachment="question_file", entity_id=UUID, telegram_user_id=7,
        )
        assert (outcome.sent, outcome.error) == (False, "attachment_unavailable")

    def test_bad_reply_file_id_is_final(self):
        bot = _Bot()
        outcome = _deliver(bot, _Files(), attachment="question_file",
                           entity_id=TRAVERSAL_ID, telegram_user_id=7)
        assert (outcome.sent, outcome.error) == (False, "attachment_rejected")

    def test_missing_certificate_still_sends_text(self):
        from src.api.errors import NotFound

        bot = _Bot()
        outcome = _deliver(
            bot, self._failing_files(NotFound(404, "not_found", "нет")),
            attachment="absence_certificate", entity_id=UUID, telegram_user_id=7,
        )
        assert outcome.sent and bot.calls[0][0] == "message"

    def test_upload_button_needs_uuid(self):
        from src.notifications.buttons import document_markup

        assert document_markup("../../me/profile") is None
        assert document_markup(UUID) is not None


# --- 3. callback_data: только UUID в пути запроса ---------------------------


class _Msg:
    def __init__(self):
        self.from_user = SimpleNamespace(id=555, username="ivan",
                                         language_code="ru", first_name="Иван")
        self.chat = SimpleNamespace(id=555, type="private")
        self.message_id = 1
        self.answers: list = []
        self.document = None
        self.photo = []
        self.text = None
        self.bot = None

    async def answer(self, body, reply_markup=None, **kwargs):
        self.answers.append(body)

    async def edit_text(self, body, reply_markup=None, **kwargs):
        self.answers.append(body)

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        pass


class _Call:
    def __init__(self, data):
        self.data = data
        self.message = _Msg()
        self.from_user = self.message.from_user
        self.alerts: list = []

    async def answer(self, body=None, show_alert=False, **kwargs):
        self.alerts.append((body, show_alert))


class _State:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.value = None

    async def set_state(self, value):
        self.value = value

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.data, self.value = {}, None


class _Recorder:
    """Клиент backend, который только записывает, куда его позвали."""

    def __init__(self, raise_on=None):
        self.calls: list[tuple] = []
        self.raise_on = raise_on or {}

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name in self.raise_on:
                raise self.raise_on[name]
            return {"title": "t", "completed": False, "stage": "SECTIONS"}
        return call


TRAVERSAL = "../../telegram/bot/outbox"


class TestCallbackIds:
    @pytest.mark.parametrize("data", [f"doc:{TRAVERSAL}", "doc:", "doc:1 OR 1=1"])
    def test_upload_button_with_bad_id_does_nothing(self, data):
        from src.handlers.absence_document.router import ask_file

        state = _State()
        asyncio.run(ask_file(_Call(data), state))
        assert state.value is None and state.data == {}

    def test_state_with_bad_id_is_not_sent(self):
        from src.handlers.absence_document.router import receive

        message = _Msg()
        message.document = SimpleNamespace(
            file_id="f", file_name="a.pdf", mime_type="application/pdf", file_size=10
        )
        client = _Recorder()
        asyncio.run(receive(message, _State({"absence_request_id": TRAVERSAL}), client))
        assert client.calls == []

    @pytest.mark.parametrize("prefix", ["op:", "oy:", "on:", "oa:"])
    def test_onboarding_ids(self, prefix):
        import importlib

        onboarding = importlib.import_module("src.handlers.onboarding.router")
        handler = {
            "op:": onboarding.open_full_text,
            "oy:": onboarding.decide,
            "on:": onboarding.decide,
            "oa:": onboarding.acknowledge,
        }[prefix]
        client = _Recorder()
        asyncio.run(handler(_Call(prefix + TRAVERSAL), client))
        assert client.calls == []

    @pytest.mark.parametrize("raw", ["-1", "0", "１", "1e3", "99999", " 2"])
    def test_onboarding_back_number(self, raw):
        from src.handlers.onboarding.router import go_back

        client = _Recorder()
        asyncio.run(go_back(_Call("os:" + raw), client))
        assert client.calls == []


# --- 4. deep-link и привязка -------------------------------------------------


class TestDeepLink:
    @pytest.mark.parametrize(
        "payload",
        ["link_abc' OR 1=1", "link_" + "a" * 65, "link_токен", "link_a b",
         "onboarding_../../x", "link_<b>"],
    )
    def test_foreign_payload_is_not_a_token(self, payload):
        from src.handlers.start.router import parse_link_payload

        assert parse_link_payload(payload) is None

    def test_real_token_passes(self):
        from src.handlers.start.router import parse_link_payload

        token = "Ab-_" + "x" * 39
        assert parse_link_payload("link_" + token) == token

    def test_accept_refused_for_recognized_says_hr(self):
        from src.handlers.start.router import accept_link_terms

        refusal = Conflict(409, "conflict", "нет",
                           {"reason": "hr_confirmation_required"})
        call = _Call("link:accept")
        asyncio.run(accept_link_terms(call, _Recorder({"accept_link_terms": refusal})))
        assert call.alerts == [(link_text.LINK_NEEDS_HR, True)]


# --- 5. Файлы ----------------------------------------------------------------


class TestFiles:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("../../etc/passwd", "passwd"),
            ("..\\..\\boot.ini", "boot.ini"),
            ("spravka‮gpj.exe", "spravkagpj.exe"),
            ("a\r\nContent-Type: text/html.pdf", "aContent-Type: text/html.pdf".split("/")[-1]),
            ("", "spravka"),
            ("...", "spravka"),
            (None, "spravka"),
        ],
    )
    def test_safe_filename(self, raw, expected):
        assert safe_filename(raw, "spravka") == expected

    def test_long_name_keeps_extension(self):
        name = safe_filename("а" * 500 + ".pdf")
        assert len(name) <= 120 and name.endswith(".pdf")

    def test_upload_sends_sanitized_name_and_escapes_server_message(self):
        from src.handlers.absence_document.router import receive

        class Stream:
            def read(self):
                return b"%PDF-1.4"

        class Bot:
            async def download(self, file_id):
                return Stream()

        message = _Msg()
        message.bot = Bot()
        message.document = SimpleNamespace(
            file_id="f", file_name="../x‮.pdf",
            mime_type="application/pdf", file_size=10,
        )
        refusal = ApiError(400, "validation_failed", "Файл <b>x</b> не подходит")
        client = _Recorder({"upload_absence_document": refusal})
        asyncio.run(receive(message, _State({"absence_request_id": UUID}), client))

        name, _, kwargs = client.calls[0]
        assert kwargs["filename"] == "x.pdf"
        assert message.answers == ["Файл &lt;b&gt;x&lt;/b&gt; не подходит"]

    def test_empty_download_is_not_sent(self):
        from src.handlers.absence_document.router import receive

        class Stream:
            def read(self):
                return b""

        class Bot:
            async def download(self, file_id):
                return Stream()

        message = _Msg()
        message.bot = Bot()
        message.document = SimpleNamespace(
            file_id="f", file_name="a.pdf", mime_type="application/pdf",
            file_size=None,
        )
        client = _Recorder()
        asyncio.run(receive(message, _State({"absence_request_id": UUID}), client))
        assert client.calls == []


def test_is_uuid():
    assert is_uuid(UUID)
    assert not is_uuid(UUID + "\n")
    assert not is_uuid(None)
    assert not is_uuid("../" + UUID)
