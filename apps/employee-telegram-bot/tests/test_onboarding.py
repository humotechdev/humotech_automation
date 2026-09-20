"""Первичное ознакомление со стороны бота.

Проверяется ровно то, за что отвечает бот: разбор ссылки, показ того,
что назвал сервер, и правка одного сообщения вместо десяти. Решения —
какой раздел следующий, засчитано ли согласие, можно ли перейти к
документам — принимает backend, и здесь их нет.

Ни одного обращения в Telegram и ни одного в сеть: клиент подменён
дублёром, `message.answer` и `edit_text` пишут в список.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.api.errors import Conflict, Forbidden
from src.handlers.menu.router import build_menu, _guard, onboarding_done
from src.handlers.onboarding.router import (
    acknowledge,
    begin,
    continue_onboarding,
    decide,
    mid_onboarding,
    open_full_text,
    rules_and_documents,
    _split,
)
from src.handlers.start.router import is_onboarding_link, parse_link_payload, start_with_link
from src.keyboards import employee as kb
from src.keyboards import onboarding as ob
from src.messages import onboarding as text


# --- разбор ссылки ----------------------------------------------------------

@pytest.mark.parametrize(
    "payload, token, onboarding",
    [
        ("onboarding_AbCd-123_x", "AbCd-123_x", True),
        ("link_AbCd-123_x", "AbCd-123_x", False),
        ("  onboarding_token  ", "token", True),
        ("onboarding_", None, True),
        # Посторонняя нагрузка не должна оказаться «почти токеном».
        ("onboardingAbCd", None, False),
        ("ONBOARDING_AbCd", None, False),
        (None, None, False),
    ],
)
def test_both_prefixes_lead_to_the_same_token(payload, token, onboarding):
    assert parse_link_payload(payload) == token
    assert is_onboarding_link(payload) is onboarding


def test_callback_data_fits_telegram_limit():
    """Telegram отводит под нажатие 64 байта.

    Префикс плюс UUID — сорок с небольшим. Проверка стоит здесь, чтобы
    попытка положить в кнопку что-то ещё падала тестом, а не молчанием
    Telegram у живого человека.
    """
    version = "0f8fad5b-d9cb-469f-a165-70867728950e"
    section = {"id": version, "position": 2, "button_label": "Я ознакомился"}
    policy = {
        "version_id": version, "agree_label": "Согласен",
        "has_body": True, "has_file": False,
    }
    codes = [
        button.callback_data
        for markup in (ob.card(section), ob.document(policy), ob.welcome())
        for row in markup.inline_keyboard
        for button in row
    ]
    assert codes
    for code in codes:
        assert len(code.encode("utf-8")) <= 64, code


# --- дублёры ----------------------------------------------------------------

SECTION = {
    "id": "0f8fad5b-d9cb-469f-a165-70867728950e",
    "position": 1, "total": 10,
    "title": "Добро пожаловать в HUMOTECH",
    "body": "Текст карточки",
    "button_label": "Я ознакомился",
    "version": 1,
    "acknowledged_at": None,
    "acknowledged_version": None,
}

POLICY = {
    "document_id": "11111111-1111-1111-1111-111111111111",
    "code": "LABOUR_RULES",
    "title": "Правила внутреннего трудового распорядка",
    "description": None,
    "index": 1, "total": 3,
    "version_id": "22222222-2222-2222-2222-222222222222",
    "version": "1.0",
    "summary": "Краткое описание",
    "agree_label": "С правилами согласен",
    "has_body": True, "has_file": False,
    "published_at": None,
    "decision": None, "decided_at": None,
}


def state(**changes) -> dict:
    base = {
        "status": "IN_PROGRESS", "stage": "SECTIONS",
        "completed": False, "info_completed": False, "has_declined": False,
        "sections_done": 0, "sections_total": 10,
        "policies_done": 0, "policies_total": 3,
        "started_at": None, "completed_at": None,
        "section": SECTION, "policy": None,
        "policies": [POLICY], "sections": [SECTION],
    }
    base.update(changes)
    return base


class FakeMessage:
    def __init__(self, user_id=555, message_id=42):
        self.from_user = SimpleNamespace(
            id=user_id, username="ivan", language_code="ru", first_name="Иван"
        )
        self.chat = SimpleNamespace(id=user_id, type="private")
        self.message_id = message_id
        self.answers: list[tuple[str, object]] = []
        self.edits: list[tuple[str, object]] = []
        self.markup_edits: list[object] = []
        self.documents: list[object] = []
        #: Что вернуть на следующую правку: None — успех, иначе бросить.
        self.edit_raises: Exception | None = None

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return SimpleNamespace(message_id=self.message_id + 1)

    async def edit_text(self, text, reply_markup=None, **kwargs):
        if self.edit_raises is not None:
            raise self.edit_raises
        self.edits.append((text, reply_markup))

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.markup_edits.append(reply_markup)

    async def answer_document(self, document, **kwargs):
        self.documents.append(document)


class FakeCallback:
    def __init__(self, data: str, message: FakeMessage | None = None):
        self.data = data
        self.message = message or FakeMessage()
        self.from_user = self.message.from_user
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))


class FakeClient:
    """Клиент backend: отдаёт заготовленные ответы и помнит вызовы."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls: list[tuple[str, tuple, dict]] = []
        self.accepted_terms = 0

    def _reply(self, name, default=None):
        value = self.answers.get(name, default)
        if isinstance(value, list):
            # Последовательность ответов: первый вызов — первый ответ.
            # Разбирается ДО проверки на исключение: иначе отказ,
            # положенный в список, вернулся бы значением, а не отказом.
            value = value.pop(0) if value else default
        if isinstance(value, Exception):
            raise value
        return value

    async def onboarding(self, telegram_id):
        self.calls.append(("onboarding", (telegram_id,), {}))
        return self._reply("onboarding", state())

    async def onboarding_start(self, telegram_id, message_id=None):
        self.calls.append(("start", (telegram_id,), {"message_id": message_id}))
        return self._reply("onboarding_start", state())

    async def onboarding_acknowledge(self, telegram_id, section_id, message_id=None):
        self.calls.append(("ack", (telegram_id, section_id), {}))
        return self._reply("onboarding_acknowledge", state())

    async def onboarding_decision(self, telegram_id, version_id, decision):
        self.calls.append(("decision", (telegram_id, version_id, decision), {}))
        return self._reply("onboarding_decision", state())

    async def onboarding_section(self, telegram_id, position):
        self.calls.append(("section", (telegram_id, position), {}))
        return self._reply("onboarding_section", SECTION)

    async def policy_text(self, telegram_id, version_id):
        self.calls.append(("policy_text", (telegram_id, version_id), {}))
        return self._reply("policy_text", {
            "version_id": version_id, "title": POLICY["title"],
            "version": "1.0", "body": "Полный текст", "has_file": False,
            "published_at": None,
        })

    async def policy_file(self, telegram_id, version_id):
        self.calls.append(("policy_file", (telegram_id, version_id), {}))
        return self._reply("policy_file", (b"%PDF-1.4", "rules.pdf", "application/pdf"))

    async def accept_link_terms(self, *, telegram_user_id):
        self.accepted_terms += 1
        return {"status": "ACTIVE"}

    async def consume_link_token(self, **kwargs):
        self.calls.append(("consume", (), kwargs))
        return self._reply("consume", {
            "status": "PENDING", "employee_known": True,
            "onboarding_required": True,
        })

    async def profile(self, telegram_id):
        return self._reply("profile", {"onboarding": {"completed": True}})


def profile(**onboarding) -> dict:
    block = {
        "enrolled": True, "required": True, "completed": False,
        "status": "IN_PROGRESS", "stage": "SECTIONS",
        "sections_done": 0, "sections_total": 10,
        "policies_done": 0, "policies_total": 3,
    }
    block.update(onboarding)
    return {"employee": {"full_name": "Иванов Иван"}, "onboarding": block}


# --- переход по ссылке ------------------------------------------------------

def test_onboarding_link_greets_and_offers_to_begin():
    client = FakeClient()
    message = FakeMessage()

    asyncio.run(start_with_link(
        message,
        SimpleNamespace(command="start", args="onboarding_the-token", mention=None),
        SimpleNamespace(clear=_noop),
        client=client, employee=None, denial="not_linked",
    ))

    body, markup = message.answers[-1]
    assert "Добро пожаловать в HUMOTECH, Иван!" in body
    assert "10–15 минут" in body
    assert markup.inline_keyboard[0][0].callback_data == ob.START


def test_plain_link_still_shows_the_old_consent():
    """Обычная привязка не должна измениться от появления раздела."""
    client = FakeClient(consume={
        "status": "PENDING", "employee_known": True,
        "onboarding_required": False,
    })
    message = FakeMessage()

    asyncio.run(start_with_link(
        message,
        SimpleNamespace(command="start", args="link_the-token", mention=None),
        SimpleNamespace(clear=_noop),
        client=client, employee=None, denial="not_linked",
    ))

    _, markup = message.answers[-1]
    assert markup.inline_keyboard[0][0].callback_data == kb.LINK_ACCEPT


# --- начало -----------------------------------------------------------------

def test_begin_accepts_the_terms_when_the_link_is_still_pending():
    """Нажатие «Начать ознакомление» и есть согласие начать.

    До него привязка ждёт согласия, и первая карточка не открылась бы.
    Разводить это на две кнопки подряд незачем — настоящее согласие с
    правилами берётся дальше, отдельно по каждому документу.
    """
    refused = Forbidden(403, "forbidden", "закрыто", {"reason": "pending_confirmation"})
    client = FakeClient(onboarding_start=[refused, state()])
    callback = FakeCallback(ob.START)

    asyncio.run(begin(callback, client))

    assert client.accepted_terms == 1
    # Кнопка с приветствия снята, нижнее меню установлено, показана карточка.
    assert callback.message.markup_edits == [None]
    hint, menu = callback.message.answers[0]
    assert hint == text.MENU_INSTALLED
    assert [b.text for row in menu.keyboard for b in row] == list(ob.ONBOARDING_BUTTONS)
    card, markup = callback.message.answers[1]
    assert "Ознакомление · 1 из 10" in card
    assert markup.inline_keyboard[0][0].callback_data.startswith(ob.ACK)


def test_begin_does_not_accept_terms_when_it_is_not_needed():
    client = FakeClient()
    asyncio.run(begin(FakeCallback(ob.START), client))
    assert client.accepted_terms == 0


# --- карточки ---------------------------------------------------------------

def test_first_card_has_no_back_button():
    markup = ob.card(SECTION)
    codes = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert not any(code.startswith(ob.BACK) for code in codes)


def test_second_card_can_go_back():
    markup = ob.card({**SECTION, "position": 2})
    codes = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"{ob.BACK}1" in codes


def test_acknowledge_edits_the_same_message_instead_of_sending_a_new_one():
    """Десять сообщений подряд превращают чат в ленту прочитанного."""
    following = {**SECTION, "position": 2, "title": "О компании"}
    client = FakeClient(onboarding_acknowledge=state(
        sections_done=1, section=following
    ))
    callback = FakeCallback(f"{ob.ACK}{SECTION['id']}")

    asyncio.run(acknowledge(callback, client))

    assert callback.message.answers == []
    body, _ = callback.message.edits[-1]
    assert "Ознакомление · 2 из 10" in body


def test_double_tap_on_the_same_button_is_silent():
    """Telegram отвечает «message is not modified» — это не ошибка."""
    client = FakeClient(onboarding_acknowledge=state())
    callback = FakeCallback(f"{ob.ACK}{SECTION['id']}")
    callback.message.edit_raises = _not_modified()

    asyncio.run(acknowledge(callback, client))

    # Ни нового сообщения, ни предупреждения человеку.
    assert callback.message.answers == []


def test_jumping_ahead_is_explained_not_silently_ignored():
    client = FakeClient(onboarding_acknowledge=Conflict(
        409, "conflict", "рано", {"expected_position": 1}
    ))
    callback = FakeCallback(f"{ob.ACK}{SECTION['id']}")

    asyncio.run(acknowledge(callback, client))

    assert (text.ORDER_BROKEN, True) in callback.answers


def test_last_card_leads_to_the_documents_screen():
    client = FakeClient(onboarding_acknowledge=state(
        stage="POLICIES", status="INFO_COMPLETED", info_completed=True,
        sections_done=10, section=None, policy=POLICY,
    ))
    callback = FakeCallback(f"{ob.ACK}{SECTION['id']}")

    asyncio.run(acknowledge(callback, client))

    body, markup = callback.message.edits[-1]
    assert "Ознакомление с компанией завершено" in body
    assert "3 обязательных документа" in body
    assert markup.inline_keyboard[0][0].callback_data == ob.DOCS


# --- документы --------------------------------------------------------------

def test_document_screen_offers_reading_before_agreeing():
    markup = ob.document(POLICY)
    first = markup.inline_keyboard[0][0]
    assert first.text == "Открыть полный документ"
    agree, refuse = markup.inline_keyboard[1]
    assert agree.text == "С правилами согласен"
    assert refuse.text == "Не согласен"


def test_full_text_arrives_as_a_new_message_and_keeps_the_buttons():
    """Карточка с согласием должна остаться — к ней человек вернётся."""
    client = FakeClient()
    callback = FakeCallback(f"{ob.OPEN}{POLICY['version_id']}")

    asyncio.run(open_full_text(callback, client))

    assert callback.message.edits == []
    body, _ = callback.message.answers[-1]
    assert "Полный текст" in body


def test_attached_pdf_is_sent_as_a_document():
    client = FakeClient(policy_text={
        "version_id": POLICY["version_id"], "title": POLICY["title"],
        "version": "1.0", "body": None, "has_file": True, "published_at": None,
    })
    callback = FakeCallback(f"{ob.OPEN}{POLICY['version_id']}")

    asyncio.run(open_full_text(callback, client))

    assert len(callback.message.documents) == 1
    assert callback.message.documents[0].filename == "rules.pdf"


def test_long_text_is_split_by_paragraphs_not_mid_sentence():
    body = "\n\n".join(f"Абзац номер {number}. " + "х" * 400 for number in range(30))
    pieces = _split(body)

    assert len(pieces) > 1
    assert all(len(one) <= 3600 for one in pieces)
    # Склеенные обратно, куски дают исходный текст без потерь.
    assert "\n\n".join(pieces) == body


def test_refusal_drops_the_buttons_and_says_hr_was_told():
    client = FakeClient(onboarding_decision=state(
        stage="BLOCKED", status="BLOCKED_BY_DECLINED_POLICY", has_declined=True,
        info_completed=True, sections_done=10, section=None,
        policies=[{**POLICY, "decision": "DECLINED"}],
    ))
    callback = FakeCallback(f"{ob.REFUSE}{POLICY['version_id']}")

    asyncio.run(decide(callback, client))

    assert callback.message.markup_edits == [None]
    body, markup = callback.message.answers[-1]
    assert "HR получил уведомление" in body
    assert [b.text for row in markup.keyboard for b in row] == list(ob.ONBOARDING_BUTTONS)


def test_stale_version_button_is_explained():
    client = FakeClient(onboarding_decision=Conflict(
        409, "conflict", "устарело", {"reason": "version_outdated"}
    ))
    callback = FakeCallback(f"{ob.AGREE}{POLICY['version_id']}")

    asyncio.run(decide(callback, client))

    assert (text.STALE_VERSION, True) in callback.answers


def test_last_agreement_finishes_and_offers_the_main_menu():
    client = FakeClient(onboarding_decision=state(
        stage="DONE", status="COMPLETED", completed=True, info_completed=True,
        sections_done=10, policies_done=3, section=None, policy=None,
    ))
    callback = FakeCallback(f"{ob.AGREE}{POLICY['version_id']}")

    asyncio.run(decide(callback, client))

    body, markup = callback.message.answers[-1]
    assert "Первичное ознакомление завершено" in body
    assert markup.inline_keyboard[0][0].callback_data == ob.MENU


# --- возвращение ------------------------------------------------------------

def test_coming_back_says_where_the_person_stopped():
    client = FakeClient(onboarding=state(
        sections_done=4, section={**SECTION, "position": 5,
                                  "title": "Как устроена компания"},
    ))
    message = FakeMessage()

    asyncio.run(continue_onboarding(message, profile(), None, client))

    assert "Вы остановились на разделе 5 из 10" in message.answers[0][0]
    assert "Как устроена компания" in message.answers[0][0]


def test_documents_section_lists_what_is_done_and_what_is_left():
    client = FakeClient(onboarding=state(policies=[
        {**POLICY, "decision": "ACCEPTED"},
        {**POLICY, "version_id": "33333333-3333-3333-3333-333333333333",
         "title": "Кодекс", "decision": None},
    ]))
    message = FakeMessage()

    asyncio.run(rules_and_documents(message, profile(), None, client))

    _, markup = message.answers[-1]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels[0].startswith("✓ ")
    assert not labels[1].startswith("✓ ")


# --- меню -------------------------------------------------------------------

def test_menu_is_three_buttons_until_onboarding_is_done():
    markup = build_menu(profile())
    assert [b.text for row in markup.keyboard for b in row] == list(
        ob.ONBOARDING_BUTTONS
    )


def test_menu_is_full_once_onboarding_is_done():
    markup = build_menu(profile(completed=True), launch_apps=False)
    labels = [b.text for row in markup.keyboard for b in row]
    assert ob.BTN_CONTINUE not in labels
    assert kb.BTN_WHERE_AM_I in labels
    # «Правила и документы» остаются: перечитать согласованное можно всегда.
    assert kb.BTN_RULES in labels


def test_profile_without_the_block_keeps_the_old_behaviour():
    """Рассогласование версий не должно отобрать бота у всех."""
    assert onboarding_done({"employee": {}}) is True
    labels = [
        b.text for row in build_menu({"employee": {}}, launch_apps=False).keyboard
        for b in row
    ]
    assert kb.BTN_WHERE_AM_I in labels


def test_work_button_explains_why_it_is_closed():
    message = FakeMessage()

    allowed = asyncio.run(_guard(message, profile(), None))

    assert allowed is False
    body, markup = message.answers[-1]
    assert "Продолжить ознакомление" in body
    assert [b.text for row in markup.keyboard for b in row] == list(
        ob.ONBOARDING_BUTTONS
    )


def test_refusal_gets_its_own_explanation():
    message = FakeMessage()

    asyncio.run(_guard(message, profile(status="BLOCKED_BY_DECLINED_POLICY"), None))

    assert "не подтвердили обязательный документ" in message.answers[-1][0]


def test_start_during_onboarding_is_routed_to_the_cards():
    assert mid_onboarding(FakeMessage(), profile()) is True
    assert mid_onboarding(FakeMessage(), profile(completed=True)) is False
    assert mid_onboarding(FakeMessage(), None) is False


# --- мелочи -----------------------------------------------------------------

def test_texts_from_crm_cannot_inject_markup():
    """Карточку правит кадровик; знак «<» в его тексте — обычный знак."""
    body = text.card({**SECTION, "body": "Условие: a < b и <b>жирный</b>"})
    assert "&lt;b&gt;" in body
    assert "<b>жирный</b>" not in body


@pytest.mark.parametrize(
    "count, word",
    [(1, "обязательный документ"), (3, "обязательных документа"),
     (5, "обязательных документов"), (0, "обязательных документов")],
)
def test_documents_are_counted_in_russian(count, word):
    assert text._documents_word(count) == word


async def _noop(*args, **kwargs):
    return None


def _not_modified() -> Exception:
    from aiogram.exceptions import TelegramBadRequest

    return TelegramBadRequest(
        method=SimpleNamespace(),
        message="Bad Request: message is not modified",
    )
