"""Живые обращения: сигналы изменений, поток SSE и пробуждение отправщика.

Что закреплено:

  * сигнал уходит только после фиксации транзакции и не несёт текста;
  * поток отдаёт пользователю лишь то, что он видит: своя организация,
    право `questions.read`, сотрудник в области видимости;
  * выход из системы закрывает поток на ближайшем пульсе;
  * поток и ожидание очереди не работают под WSGI — не держат воркер;
  * ожидание очереди отвечает «пора» сразу, как появилось что отправить;
  * «прочитано» не снимается с сообщения, которого HR ещё не видел;
  * повтор ответа с тем же ключом не даёт второго сообщения и второго
    уведомления.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta

import psycopg
import pytest
from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection, transaction
from django.test import AsyncRequestFactory, RequestFactory
from django.utils import timezone

from humotech.notifications import outbox
from humotech.notifications.models import Notification
from humotech.questions.inbox import InboxService
from humotech.questions.models import EmployeeQuestion, QuestionMessage
from humotech.realtime import broker as broker_module
from humotech.realtime import signals, views
from humotech.realtime.broker import Broker

from .conftest import TEST_BOT_SECRET
from .test_questions_inbox import ANSWER, READ, ask

API = "/api/v1"
URL = f"{API}/knowledge/escalations"


# --- слушатель базы для проверки фиксации --------------------------------------


class Listener:
    """Отдельное соединение с LISTEN — как у процесса событий."""

    def __init__(self):
        db = settings.DATABASES["default"]
        self.conn = psycopg.connect(
            dbname=connection.settings_dict["NAME"], user=db["USER"],
            password=db["PASSWORD"], host=db["HOST"], port=db["PORT"],
            autocommit=True,
        )
        self.conn.execute(f"LISTEN {signals.CHANNEL}")

    def drain(self, seconds: float = 1.0) -> list[dict]:
        events = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for notice in self.conn.notifies(timeout=0.1, stop_after=50):
                events.append(json.loads(notice.payload))
        return events

    def close(self):
        self.conn.close()


@pytest.fixture()
def listener():
    one = Listener()
    yield one
    one.close()


@pytest.fixture()
def context(linked_account):
    from humotech.telegram.identity import resolve_account

    return resolve_account(linked_account)


# --- сигналы -----------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSignals:
    def test_employee_message_signals_after_commit_without_text(
        self, listener, context
    ):
        question, _ = InboxService().receive(
            context, text="Секретный текст о больничном"
        )

        events = [one for one in listener.drain() if one["t"] == "question"]
        assert events, "сигнал о сообщении не дошёл"
        event = events[-1]
        assert event["id"] == str(question.id)
        assert event["what"] == signals.MESSAGE
        assert event["org"] == str(question.organization_id)
        # Только идентификаторы и время: ни текста, ни имени.
        assert set(event) == {"t", "org", "id", "what", "at"}
        assert "Секретный" not in json.dumps(events)

    def test_rolled_back_change_sends_nothing(self, listener, employee):
        question = ask(employee)
        listener.drain(0.3)

        class Boom(Exception):
            pass

        with pytest.raises(Boom), transaction.atomic():
            signals.question_changed(question, signals.UPDATED)
            raise Boom

        assert listener.drain(0.5) == []

    def test_read_in_one_window_is_signalled_to_the_others(
        self, listener, make_actor, organization, employee
    ):
        """Прочитал в одном окне — у второго окна пропадает отметка."""
        actor = make_actor(organization, permissions=READ)
        question = ask(employee, unread=True)
        listener.drain(0.3)

        InboxService().read(actor, question.id)

        kinds = [(one["t"], one.get("what")) for one in listener.drain()]
        assert ("question", signals.READ) in kinds

    def test_reply_wakes_the_sender_and_delivery_is_signalled(
        self, listener, make_actor, organization, employee, linked_account
    ):
        actor = make_actor(organization, permissions=ANSWER)
        question = ask(employee)
        listener.drain(0.3)

        InboxService().reply(actor, question.id, text="Можно", client_request_id="k1")
        kinds = [(one["t"], one.get("what")) for one in listener.drain()]
        assert ("outbox", None) in kinds
        assert ("question", signals.REPLY) in kinds

        row = QuestionMessage.objects.get(question=question, kind="HR").notification
        Notification.objects.filter(id=row.id).update(
            status="RUNNING", locked_at=timezone.now()
        )
        outbox.mark_sent(row.id)
        kinds = [(one["t"], one.get("what")) for one in listener.drain()]
        assert ("question", signals.DELIVERY) in kinds


# --- поток -------------------------------------------------------------------


class FakeBroker(Broker):
    """Брокер без базы: сигналы кладёт тест."""

    def _ensure_started(self) -> None:  # слушатель не нужен
        return None


@pytest.fixture()
def fake_broker(monkeypatch):
    fake = FakeBroker()
    monkeypatch.setattr(broker_module, "broker", fake)
    return fake


def stream_request(user, *, session_key: str | None = None):
    request = AsyncRequestFactory().get(f"{API}/events/stream")
    store = SessionStore(session_key=session_key)
    if session_key is None:
        store.create()
    request.session = store

    async def auser():
        return user

    request.auser = auser
    return request


def read_stream(response, *, events, frames: int, timeout: float = 5.0) -> list:
    """Прочитать `frames` кадров, подкладывая сигналы после первого кадра."""

    async def run():
        out = []
        iterator = response.streaming_content.__aiter__()
        fed = False
        try:
            while len(out) < frames:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout)
                text = chunk.decode() if isinstance(chunk, bytes) else chunk
                if text.startswith("retry:"):
                    continue
                out.append(text)
                if not fed:
                    fed = True
                    for event in events:
                        broker_module.broker.publish(event)
        finally:
            await iterator.aclose()
        return out

    return async_to_sync(run)()


def names(frames) -> list[str]:
    return [frame.split("\n", 1)[0].removeprefix("event: ") for frame in frames]


def data(frame) -> dict:
    return json.loads(frame.split("data: ", 1)[1])


class TestStream:
    def test_wsgi_refuses_instead_of_holding_a_worker(self, make_user, organization):
        user = make_user(organization, permissions=READ)
        request = RequestFactory().get(f"{API}/events/stream")
        request.user = user

        response = async_to_sync(views.question_events)(request)

        assert response.status_code == 503

    def test_anonymous_gets_401(self, db, fake_broker):
        from django.contrib.auth.models import AnonymousUser

        response = async_to_sync(views.question_events)(stream_request(AnonymousUser()))

        assert response.status_code == 401

    def test_without_questions_read_gets_403(self, fake_broker, make_user, organization):
        user = make_user(organization, permissions=("employees.read",))

        response = async_to_sync(views.question_events)(stream_request(user))

        assert response.status_code == 403
        assert fake_broker.subscribers == 0

    def test_visible_question_is_delivered_with_ids_only(
        self, fake_broker, make_user, organization, employee
    ):
        user = make_user(organization, permissions=READ)
        question = ask(employee)
        response = async_to_sync(views.question_events)(stream_request(user))

        assert response["Content-Type"] == "text/event-stream"
        assert response["X-Accel-Buffering"] == "no"
        frames = read_stream(response, frames=2, events=[{
            "t": "question", "org": str(organization.id),
            "id": str(question.id), "what": "message", "at": time.time(),
        }])

        assert names(frames) == ["ready", "question"]
        assert set(data(frames[1])) == {"id", "what", "at", "sent"}
        assert data(frames[1])["id"] == str(question.id)
        assert fake_broker.subscribers == 0, "подписка пережила поток"

    def test_other_organization_and_out_of_scope_are_filtered(
        self, fake_broker, make_user, organization, other_office, employee,
        foreign_office,
    ):
        # Видит только чужой для сотрудника офис той же организации.
        user = make_user(organization, permissions=READ, office=other_office)
        question = ask(employee)
        response = async_to_sync(views.question_events)(stream_request(user))

        frames = read_stream(response, frames=2, events=[
            # Другая организация.
            {"t": "question", "org": str(foreign_office.organization_id),
             "id": str(question.id), "what": "message", "at": 1},
            # Своя организация, но сотрудник вне области.
            {"t": "question", "org": str(organization.id),
             "id": str(question.id), "what": "message", "at": 1},
            # «Перечитай всё» — доходит до всех.
            {"t": "resync"},
        ])

        assert names(frames) == ["ready", "resync"]

    def test_logout_closes_the_stream_on_the_next_pulse(
        self, fake_broker, make_user, organization, monkeypatch
    ):
        monkeypatch.setattr(views, "HEARTBEAT_SECONDS", 0.05)
        user = make_user(organization, permissions=READ)
        request = stream_request(user)
        response = async_to_sync(views.question_events)(request)
        # Выход в другом окне: строки сессии больше нет.
        request.session.delete()

        frames = read_stream(response, frames=2, events=[])

        assert names(frames) == ["ready", "bye"]

    def test_revoked_permission_closes_the_stream(
        self, fake_broker, make_user, organization, monkeypatch
    ):
        from humotech.accounts.models import UserRoleScope

        monkeypatch.setattr(views, "HEARTBEAT_SECONDS", 0.05)
        user = make_user(organization, permissions=READ)
        response = async_to_sync(views.question_events)(stream_request(user))
        UserRoleScope.objects.filter(user=user).update(
            valid_to=timezone.now() - timedelta(minutes=1)
        )

        frames = read_stream(response, frames=2, events=[])

        assert names(frames)[-1] == "bye"

    def test_pulse_keeps_a_live_session(
        self, fake_broker, make_user, organization, monkeypatch
    ):
        monkeypatch.setattr(views, "HEARTBEAT_SECONDS", 0.05)
        user = make_user(organization, permissions=READ)
        response = async_to_sync(views.question_events)(stream_request(user))

        frames = read_stream(response, frames=3, events=[])

        assert names(frames) == ["ready", "ping", "ping"]


    def test_two_hr_windows_both_get_the_same_signal(
        self, fake_broker, make_user, organization, employee
    ):
        """Два окна CRM — два потока; один сигнал доходит до обоих."""
        first_user = make_user(organization, permissions=READ)
        second_user = make_user(organization, permissions=READ)
        question = ask(employee)
        one = async_to_sync(views.question_events)(stream_request(first_user))
        two = async_to_sync(views.question_events)(stream_request(second_user))

        async def run():
            streams = [one.streaming_content.__aiter__(), two.streaming_content.__aiter__()]

            async def frame(iterator):
                while True:
                    chunk = await asyncio.wait_for(iterator.__anext__(), 5)
                    text = chunk.decode() if isinstance(chunk, bytes) else chunk
                    if not text.startswith("retry:"):
                        return text

            try:
                ready = [await frame(it) for it in streams]
                assert fake_broker.subscribers == 2
                fake_broker.publish({
                    "t": "question", "org": str(organization.id),
                    "id": str(question.id), "what": "read", "at": time.time(),
                })
                got = [await frame(it) for it in streams]
            finally:
                for it in streams:
                    await it.aclose()
            return ready, got

        ready, got = async_to_sync(run)()

        assert names(ready) == ["ready", "ready"]
        assert names(got) == ["question", "question"]
        assert {data(frame)["what"] for frame in got} == {"read"}
        assert fake_broker.subscribers == 0


# --- брокер ------------------------------------------------------------------


class TestBroker:
    def test_a_slow_subscriber_gets_one_resync_instead_of_silent_loss(self):
        async def run():
            fake = FakeBroker()
            queue = fake.subscribe()
            for n in range(broker_module.QUEUE_SIZE + 5):
                fake.publish({"t": "question", "id": str(n)})
            return queue.qsize(), queue.get_nowait()

        size, first = async_to_sync(run)()

        assert first == {"t": "resync"}
        assert size <= broker_module.QUEUE_SIZE

    def test_reconnect_after_losing_the_database_broadcasts_resync(self):
        class Notice:
            def __init__(self, payload):
                self.payload = payload

        class FakeConnection:
            def __init__(self, fail):
                self.fail = fail

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, sql):
                return None

            async def notifies(self):
                if self.fail:
                    raise ConnectionError("database restarted")
                yield Notice(json.dumps({"t": "question", "id": "q"}))
                await asyncio.sleep(3600)

        attempts = {"n": 0}

        async def connect():
            attempts["n"] += 1
            return FakeConnection(fail=attempts["n"] == 1)

        async def run(monkey_sleep):
            broker = Broker(connect=connect)
            queue = broker.subscribe()
            got = [await asyncio.wait_for(queue.get(), 5) for _ in range(2)]
            broker._task.cancel()
            return got

        original = asyncio.sleep

        async def quick(seconds):
            await original(min(seconds, 0.01))

        asyncio.sleep = quick
        try:
            got = async_to_sync(run)(None)
        finally:
            asyncio.sleep = original

        assert got == [{"t": "resync"}, {"t": "question", "id": "q"}]


# --- пробуждение отправщика --------------------------------------------------


def wait_request(secret=TEST_BOT_SECRET, timeout="2"):
    return AsyncRequestFactory().get(
        f"{API}/telegram/bot/outbox/wait",
        {"timeout": timeout},
        headers={"X-Bot-Token": secret},
    )


class TestOutboxWait:
    def test_only_the_bot_may_wait(self, fake_broker, telegram_settings):
        response = async_to_sync(views.outbox_wait)(wait_request(secret="wrong-secret"))
        assert response.status_code == 403

    def test_due_notification_answers_at_once(
        self, fake_broker, telegram_settings, organization, employee
    ):
        outbox.enqueue(
            organization_id=organization.id, employee_id=employee.id,
            notification_type="question.reply", body="x",
        )
        started = time.monotonic()
        response = async_to_sync(views.outbox_wait)(wait_request(timeout="20"))

        assert json.loads(response.content) == {"ready": True, "reason": "due"}
        assert time.monotonic() - started < 2

    def test_a_signal_wakes_the_waiting_bot(self, db, fake_broker, telegram_settings):
        async def run():
            task = asyncio.ensure_future(views.outbox_wait(wait_request(timeout="20")))
            await asyncio.sleep(0.2)
            started = time.monotonic()
            fake_broker.publish({"t": "outbox", "at": time.time()})
            response = await asyncio.wait_for(task, 5)
            return response, time.monotonic() - started

        response, woke_in = async_to_sync(run)()

        assert json.loads(response.content)["ready"] is True
        assert woke_in < 1
        assert fake_broker.subscribers == 0

    def test_nothing_to_send_times_out(self, db, fake_broker, telegram_settings):
        response = async_to_sync(views.outbox_wait)(wait_request(timeout="1"))
        assert json.loads(response.content) == {"ready": False, "reason": "timeout"}

    def test_scheduled_retry_shortens_the_wait(
        self, fake_broker, telegram_settings, organization, employee
    ):
        Notification.objects.create(
            organization=organization, employee=employee, channel="TELEGRAM",
            notification_type="question.reply", body="x", status="PENDING",
            next_attempt_at=timezone.now() + timedelta(seconds=1),
        )
        started = time.monotonic()
        response = async_to_sync(views.outbox_wait)(wait_request(timeout="20"))

        assert json.loads(response.content)["ready"] is True
        assert time.monotonic() - started < 5

    def test_wsgi_refuses(self, telegram_settings):
        request = RequestFactory().get(
            f"{API}/telegram/bot/outbox/wait", headers={"X-Bot-Token": TEST_BOT_SECRET}
        )
        response = async_to_sync(views.outbox_wait)(request)
        assert response.status_code == 503


# --- прочитано и повтор ответа ------------------------------------------------


@pytest.fixture()
def hr(api_client, make_user, organization):
    user = make_user(organization, permissions=ANSWER)
    api_client.force_authenticate(user=user)
    api_client.user = user
    return api_client


class TestReadAndReply:
    def test_a_message_that_arrived_after_the_screen_stays_unread(
        self, hr, employee
    ):
        question = ask(employee, unread=True)
        seen = question.messages.get()
        QuestionMessage.objects.create(
            organization=employee.organization, question=question, kind="EMPLOYEE",
            source="TELEGRAM", body="Ещё вопрос", author_employee=employee,
            created_at=seen.created_at + timedelta(seconds=5),
        )

        response = hr.post(f"{URL}/{question.id}/read/",
                           {"seen_message_id": str(seen.id)}, format="json")

        assert response.status_code == 200
        assert response.json()["unread"] is True
        question.refresh_from_db()
        assert question.unread is True

    def test_the_latest_seen_message_clears_unread_and_can_repeat(self, hr, employee):
        question = ask(employee, unread=True)
        latest = question.messages.get()

        first = hr.post(f"{URL}/{question.id}/read/",
                        {"seen_message_id": str(latest.id)}, format="json")
        assert first.json()["unread"] is False

        # Новое сообщение снова делает обращение непрочитанным, и отметить
        # его прочитанным можно ещё раз.
        EmployeeQuestion.objects.filter(id=question.id).update(unread=True)
        newer = QuestionMessage.objects.create(
            organization=employee.organization, question=question, kind="EMPLOYEE",
            source="TELEGRAM", body="Ещё", author_employee=employee,
            created_at=latest.created_at + timedelta(seconds=1),
        )
        again = hr.post(f"{URL}/{question.id}/read/",
                        {"seen_message_id": str(newer.id)}, format="json")
        assert again.json()["unread"] is False

    def test_a_foreign_message_id_is_rejected(self, hr, employee, other_employee_q):
        question = ask(employee, unread=True)
        response = hr.post(
            f"{URL}/{question.id}/read/",
            {"seen_message_id": str(other_employee_q.messages.get().id)},
            format="json",
        )
        assert response.status_code == 400

    def test_repeated_reply_makes_no_second_message_or_notification(
        self, hr, employee, linked_account
    ):
        question = ask(employee)
        body = {"text": "Ответ", "client_request_id": "same-key"}

        first = hr.post(f"{URL}/{question.id}/reply/", body, format="json")
        second = hr.post(f"{URL}/{question.id}/reply/", body, format="json")

        assert first.status_code == second.status_code == 200
        assert QuestionMessage.objects.filter(question=question, kind="HR").count() == 1
        assert Notification.objects.filter(
            idempotency_key__startswith="question.reply:"
        ).count() == 1
        mine = [one for one in second.json()["messages"] if one["kind"] == "HR"]
        assert mine[0]["client_request_id"] == "same-key"
        assert mine[0]["delivery"]["status"] == "QUEUED"

    def test_resend_is_offered_only_with_notifications_manage(
        self, api_client, make_user, organization, employee, linked_account
    ):
        question = ask(employee)
        writer = make_user(organization, permissions=ANSWER)
        api_client.force_authenticate(user=writer)
        api_client.post(f"{URL}/{question.id}/reply/", {"text": "Ответ"}, format="json")
        Notification.objects.update(status="FAILED", error_message="blocked_by_user")

        plain = api_client.get(f"{URL}/{question.id}/").json()
        delivery = [one for one in plain["messages"] if one["kind"] == "HR"][0]["delivery"]
        assert delivery["status"] == "FAILED"
        assert delivery["error"] == "blocked_by_user"
        assert delivery["notification_id"]
        assert delivery["can_retry"] is False

        manager = make_user(
            organization, permissions=ANSWER + ("notifications.read", "notifications.manage")
        )
        api_client.force_authenticate(user=manager)
        full = api_client.get(f"{URL}/{question.id}/").json()
        delivery = [one for one in full["messages"] if one["kind"] == "HR"][0]["delivery"]
        assert delivery["can_retry"] is True


@pytest.fixture()
def other_employee_q(organization, office):
    from datetime import date

    from humotech.employees.models import Employee, EmployeeAssignment

    person = Employee.objects.create(
        organization=organization, employee_number="EMP-0077",
        first_name="Другой", last_name="Сотрудник",
        hire_date=date(2024, 2, 1), employment_status="ACTIVE",
    )
    EmployeeAssignment.objects.create(
        organization=organization, employee=person, office=office,
        employment_type="FULL_TIME", work_mode="ONSITE",
        is_primary=True, valid_from=date(2024, 2, 1),
    )
    return ask(person)
