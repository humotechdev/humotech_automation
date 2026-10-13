"""Сообщения HR в Telegram: ответ на вопрос — один, дальше — сообщения.

Что здесь закреплено:

  * первое сообщение HR после вопроса — «HR ответил на ваш вопрос»,
    следующие — «Сообщение от HR», без цитаты старого вопроса;
  * новый вопрос («Написать HR», новое обращение, переоткрытие
    закрытого сообщением сотрудника) снова получает ответ; дописка к
    ещё не отвеченному вопросу и ответ на сообщение HR — нет;
  * повторная доставка и повтор запроса тип не меняют и дублей не дают;
  * два кадровика одновременно — первым ответом считается один;
  * очередь не отдаёт следующее сообщение HR раньше предыдущего;
  * повтор того же сообщения Telegram — одна строка, даже одновременно;
  * сотрудник — только по привязке; чужая организация обращения не видит.
"""

from __future__ import annotations

import threading
import traceback
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from humotech.core.rbac import Actor
from humotech.notifications import outbox
from humotech.notifications.models import Notification
from humotech.questions.inbox import InboxService
from humotech.questions.models import EmployeeQuestion, QuestionMessage

from .conftest import bot_headers, link_telegram
from .test_questions_inbox import other_employee  # noqa: F401 — фикстура

API = "/api/v1"
URL = f"{API}/knowledge/escalations"
ANSWER = ("questions.read", "questions.answer", "employees.read")

ANSWER_HEAD = "HR ответил на ваш вопрос"
MESSAGE_HEAD = "Сообщение от HR"


@pytest.fixture()
def hr(api_client, make_user, organization):
    user = make_user(organization, permissions=ANSWER)
    api_client.force_authenticate(user=user)
    api_client.user = user
    return api_client


def employee_writes(bot_client, text, *, message_id, starts_question=True):
    """Сообщение сотрудника так, как его шлёт бот: по привязке Telegram."""
    response = bot_client.post(
        f"{API}/me/ask/escalate",
        {"text": text, "telegram_message_id": message_id,
         "starts_question": starts_question},
        format="json", **bot_headers(),
    )
    assert response.status_code in (200, 201), response.content
    return response.json()


def hr_writes(hr, question_id, text, **extra):
    response = hr.post(
        f"{URL}/{question_id}/reply/", {"text": text, **extra}, format="json",
    )
    assert response.status_code == 200, response.content
    return response.json()


def chat(question_id) -> list[str]:
    """Что ушло человеку в чат по этому обращению — по порядку."""
    ids = QuestionMessage.objects.filter(
        question_id=question_id, kind="HR"
    ).values_list("notification_id", flat=True)
    return list(
        Notification.objects.filter(id__in=list(ids))
        .order_by("created_at").values_list("body", flat=True)
    )


@pytest.mark.django_db
class TestHeadings:
    def test_first_message_is_the_answer_the_rest_are_messages(
        self, hr, bot_client, linked_account,
    ):
        asked = employee_writes(bot_client, "Как оформить отпуск?", message_id=1)
        qid = asked["question_id"]

        hr_writes(hr, qid, "Укажите даты и отправьте заявку в разделе отпусков.")
        hr_writes(hr, qid, "На какие даты вы планируете отпуск?")
        hr_writes(hr, qid, "Если нужна помощь с заявкой, напишите здесь.")

        assert chat(qid) == [
            f"{ANSWER_HEAD}\n\nУкажите даты и отправьте заявку в разделе отпусков.",
            f"{MESSAGE_HEAD}\n\nНа какие даты вы планируете отпуск?",
            f"{MESSAGE_HEAD}\n\nЕсли нужна помощь с заявкой, напишите здесь.",
        ]
        # Старый вопрос в уведомления не попадает ни разу.
        assert not any("Как оформить отпуск?" in body for body in chat(qid))
        headings = list(
            QuestionMessage.objects.filter(question_id=qid, kind="HR")
            .order_by("created_at").values_list("details", flat=True)
        )
        assert headings == [{"heading": "ANSWER"}, {"heading": "MESSAGE"},
                            {"heading": "MESSAGE"}]
        # В ленте CRM — чистый текст кадровика, без заголовка.
        bodies = [m["body"] for m in hr.get(f"{URL}/{qid}/").json()["messages"]
                  if m["kind"] == "HR"]
        assert "Укажите даты и отправьте заявку в разделе отпусков." in bodies
        assert not any(body.startswith(ANSWER_HEAD) for body in bodies)

    def test_a_reply_to_hr_continues_and_does_not_restart_the_question(
        self, hr, bot_client, linked_account,
    ):
        qid = employee_writes(bot_client, "Как оформить отпуск?", message_id=1)["question_id"]
        hr_writes(hr, qid, "На какие даты?")

        employee_writes(bot_client, "С 3 по 7 ноября", message_id=2, starts_question=False)
        hr_writes(hr, qid, "Оформила, проверьте кабинет.")

        assert [body.split("\n")[0] for body in chat(qid)] == [ANSWER_HEAD, MESSAGE_HEAD]

    def test_an_addition_to_an_unanswered_question_gives_one_answer(
        self, hr, bot_client, linked_account,
    ):
        qid = employee_writes(bot_client, "Нужна справка", message_id=1)["question_id"]
        employee_writes(bot_client, "С места работы, для визы", message_id=2)

        hr_writes(hr, qid, "Готовим, завтра будет.")
        hr_writes(hr, qid, "Справка готова.")

        assert [body.split("\n")[0] for body in chat(qid)] == [ANSWER_HEAD, MESSAGE_HEAD]

    def test_a_new_question_gets_its_own_answer(self, hr, bot_client, linked_account):
        qid = employee_writes(bot_client, "Как оформить отпуск?", message_id=1)["question_id"]
        hr_writes(hr, qid, "Через кабинет.")
        hr_writes(hr, qid, "Есть вопросы?")

        # Человек снова нажал «Написать HR» — новый вопрос в том же разговоре.
        again = employee_writes(bot_client, "А где взять расчётный лист?", message_id=2)
        assert again["question_id"] == qid
        hr_writes(hr, qid, "В разделе «Документы».")
        hr_writes(hr, qid, "Нашли?")

        assert [body.split("\n")[0] for body in chat(qid)] == [
            ANSWER_HEAD, MESSAGE_HEAD, ANSWER_HEAD, MESSAGE_HEAD,
        ]

    def test_reopening_by_the_employee_is_a_new_question_by_hr_is_not(
        self, hr, bot_client, linked_account,
    ):
        qid = employee_writes(bot_client, "Вопрос", message_id=1)["question_id"]
        hr_writes(hr, qid, "Ответ", after="CLOSE")
        # Кадровик сам переоткрыл и дописал — сотрудник ничего не спрашивал.
        assert hr.post(f"{URL}/{qid}/reopen/").status_code == 200
        hr_writes(hr, qid, "Забыла добавить: бланк в кабинете.", after="CLOSE")

        # Сотрудник пишет в закрытое (в окне переоткрытия) — новый вопрос,
        # даже если бот не пометил его началом вопроса.
        employee_writes(bot_client, "А ещё про отпуск", message_id=2, starts_question=False)
        hr_writes(hr, qid, "Про отпуск — через кабинет.")

        assert [body.split("\n")[0] for body in chat(qid)] == [
            ANSWER_HEAD, MESSAGE_HEAD, ANSWER_HEAD,
        ]

    def test_separate_questions_are_independent(self, hr, bot_client, linked_account):
        first = employee_writes(bot_client, "Первый", message_id=1)["question_id"]
        hr_writes(hr, first, "Ответ на первый", after="CLOSE")
        # Давно закрытое не переоткрывается: заводится новое обращение.
        EmployeeQuestion.objects.filter(id=first).update(
            closed_at=timezone.now() - timedelta(days=30)
        )
        second = employee_writes(bot_client, "Второй", message_id=2)
        assert second["created"] is True and second["question_id"] != first

        hr_writes(hr, second["question_id"], "Ответ на второй")

        assert chat(second["question_id"]) == [f"{ANSWER_HEAD}\n\nОтвет на второй"]
        assert chat(first) == [f"{ANSWER_HEAD}\n\nОтвет на первый"]

    def test_repeating_the_request_or_delivery_keeps_the_heading(
        self, hr, bot_client, linked_account,
    ):
        qid = employee_writes(bot_client, "Вопрос", message_id=1)["question_id"]
        hr_writes(hr, qid, "Ответ", client_request_id="k-1")
        # Повтор того же нажатия — то же сообщение, не второе и не «сообщение».
        hr_writes(hr, qid, "Ответ", client_request_id="k-1")
        row = Notification.objects.get()
        row.status, row.attempts = "PENDING", 1
        row.save(update_fields=["status", "attempts"])
        # Повторная доставка берёт готовое тело из очереди.
        delivered = outbox.claim()

        assert [one.text for one in delivered] == [f"{ANSWER_HEAD}\n\nОтвет"]
        assert QuestionMessage.objects.filter(kind="HR").count() == 1


def _two_replies(hr, bot_client):
    """Два сообщения HR, как два отдельных нажатия «Отправить».

    `created_at` — время транзакции, а весь тест — одна транзакция:
    без сдвига у обеих строк было бы одно время, которого в работе не
    бывает (каждый ответ — своя транзакция).
    """
    qid = employee_writes(bot_client, "Вопрос", message_id=1)["question_id"]
    hr_writes(hr, qid, "Первое")
    first = Notification.objects.get()
    Notification.objects.filter(id=first.id).update(
        created_at=first.created_at - timedelta(seconds=5)
    )
    hr_writes(hr, qid, "Второе")
    return list(Notification.objects.order_by("created_at"))


@pytest.mark.django_db
class TestDeliveryOrder:
    def test_a_later_hr_message_waits_for_the_earlier_one(
        self, hr, bot_client, linked_account,
    ):
        first, second = _two_replies(hr, bot_client)
        # Первое не дошло и ждёт повтора через минуту.
        first.next_attempt_at = timezone.now() + timedelta(minutes=1)
        first.save(update_fields=["next_attempt_at"])

        assert outbox.claim() == []
        second.refresh_from_db()
        assert second.status == "PENDING"
        assert second.next_attempt_at > first.next_attempt_at

        # Срок первого пришёл — уходит оно, второе следом.
        later = second.next_attempt_at + timedelta(seconds=1)
        texts = [one.text for one in outbox.claim(now=later)]
        assert texts == [f"{ANSWER_HEAD}\n\nПервое", f"{MESSAGE_HEAD}\n\nВторое"]

    def test_both_in_one_batch_go_in_order(self, hr, bot_client, linked_account):
        _two_replies(hr, bot_client)

        texts = [one.text for one in outbox.claim()]

        assert texts == [f"{ANSWER_HEAD}\n\nПервое", f"{MESSAGE_HEAD}\n\nВторое"]


@pytest.mark.django_db
class TestEmployeeSide:
    def test_the_same_telegram_message_twice_is_one_row(self, bot_client, linked_account):
        first = employee_writes(bot_client, "Текст", message_id=10)
        again = employee_writes(bot_client, "Текст", message_id=10)

        assert again["question_id"] == first["question_id"] and again["created"] is False
        assert QuestionMessage.objects.filter(kind="EMPLOYEE").count() == 1

    def test_same_words_in_different_messages_are_both_kept(self, bot_client, linked_account):
        employee_writes(bot_client, "Перезвоните", message_id=10)
        employee_writes(bot_client, "Перезвоните", message_id=11)

        assert QuestionMessage.objects.filter(kind="EMPLOYEE").count() == 2

    def test_the_employee_comes_from_the_binding_not_the_body(
        self, bot_client, linked_account, employee, other_organization,
    ):
        response = bot_client.post(
            f"{API}/me/ask/escalate",
            {"text": "Вопрос", "telegram_message_id": 1,
             "employee_id": "00000000-0000-0000-0000-000000000001",
             "organization_id": str(other_organization.id)},
            format="json", **bot_headers(),
        )

        assert response.status_code == 201
        question = EmployeeQuestion.objects.get(id=response.json()["question_id"])
        assert question.employee_id == employee.id
        assert question.organization_id == employee.organization_id

    def test_without_a_binding_nothing_is_created(self, bot_client, telegram_settings):
        response = bot_client.post(
            f"{API}/me/ask/escalate", {"text": "Вопрос"}, format="json",
            **bot_headers(telegram_user_id=123_456),
        )

        assert response.status_code in (401, 403, 404)
        assert EmployeeQuestion.objects.count() == 0

    def test_the_answer_goes_to_the_author_and_nobody_else(
        self, hr, bot_client, linked_account, other_employee,
    ):
        link_telegram(other_employee, telegram_user_id=777_000_222, username="shahnoza")
        mine = employee_writes(bot_client, "Мой вопрос", message_id=1)["question_id"]
        theirs = bot_client.post(
            f"{API}/me/ask/escalate",
            {"text": "Чужой вопрос", "telegram_message_id": 1, "starts_question": True},
            format="json", **bot_headers(telegram_user_id=777_000_222),
        ).json()["question_id"]
        assert theirs != mine

        hr_writes(hr, mine, "Ответ автору")
        delivered = outbox.claim()

        assert [(one.chat_id, one.text) for one in delivered] == [
            (linked_account.telegram_chat_id, f"{ANSWER_HEAD}\n\nОтвет автору"),
        ]

    def test_a_foreign_organization_cannot_answer(
        self, api_client, make_user, other_organization, bot_client, linked_account,
    ):
        qid = employee_writes(bot_client, "Вопрос", message_id=1)["question_id"]
        api_client.force_authenticate(user=make_user(other_organization, permissions=ANSWER))

        response = api_client.post(f"{URL}/{qid}/reply/", {"text": "Чужой"}, format="json")

        assert response.status_code == 404
        assert Notification.objects.count() == 0


def _in_threads(*jobs):
    """Запустить одновременно; каждая работа — в своём соединении с базой."""
    gate = threading.Barrier(len(jobs))
    errors: list[str] = []

    def run(job):
        try:
            gate.wait()
            job()
        except BaseException:  # noqa: BLE001 — вернём в тест с трассировкой
            errors.append(traceback.format_exc())
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(job,)) for job in jobs]
    for one in threads:
        one.start()
    for one in threads:
        one.join()
    return errors


@pytest.mark.django_db(transaction=True)
class TestConcurrency:
    def test_two_hr_at_once_only_one_is_the_answer(
        self, make_user, organization, bot_client, linked_account,
    ):
        qid = employee_writes(bot_client, "Вопрос", message_id=1)["question_id"]
        first = make_user(organization, permissions=ANSWER)
        second = make_user(organization, permissions=ANSWER)

        def answer(user, word):
            return lambda: InboxService().reply(
                Actor(user_id=user.id, organization_id=organization.id),
                qid, text=word,
            )

        errors = _in_threads(answer(first, "Один"), answer(second, "Другой"))

        assert errors == [], "\n".join(errors)
        headings = sorted(
            row["heading"] for row in QuestionMessage.objects.filter(
                question_id=qid, kind="HR"
            ).values_list("details", flat=True)
        )
        assert headings == ["ANSWER", "MESSAGE"]
        assert sorted(body.split("\n")[0] for body in chat(qid)) == sorted(
            [ANSWER_HEAD, MESSAGE_HEAD]
        )

    def test_a_repeated_message_gives_one_live_signal(self, linked_account):
        # CRM обновляется по сигналам SSE: повтор того же сообщения Telegram
        # не должен ни лечь второй строкой, ни разбудить ленту второй раз.
        from humotech.telegram.identity import resolve_account

        from .test_realtime import Listener

        listener = Listener()
        try:
            context = resolve_account(linked_account)
            InboxService().receive(context, text="Раз", telegram_message_id=77)
            InboxService().receive(context, text="Раз", telegram_message_id=77)
            events = [one for one in listener.drain() if one["t"] == "question"]
        finally:
            listener.close()

        assert len(events) == 1
        assert QuestionMessage.objects.filter(kind="EMPLOYEE").count() == 1

    def test_the_same_telegram_message_at_once_is_one_row(self, linked_account):
        from humotech.telegram.identity import resolve_account

        context = resolve_account(linked_account)

        def send():
            InboxService().receive(
                context, text="Одновременно", telegram_message_id=55,
                starts_question=True,
            )

        errors = _in_threads(send, send, send)

        assert errors == []
        assert QuestionMessage.objects.filter(kind="EMPLOYEE").count() == 1
        assert EmployeeQuestion.objects.count() == 1
