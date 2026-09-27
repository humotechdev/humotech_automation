"""Отправщик просыпается по сигналу backend, а не по таймеру.

Что закреплено:

  * пустой проход ждёт пробуждения от backend, а не спит пять секунд;
  * проход, который что-то отправил, сразу смотрит следующую пачку;
  * backend не умеет будить (старая версия, процесс событий упал) —
    прежняя пауза: доставка работает в любом сочетании версий;
  * «пора», за которым раз за разом нечего забрать, не крутит цикл
    вхолостую.
"""

from __future__ import annotations

import asyncio

import pytest

from src.api.errors import ApiError
from src.notifications import worker
from src.notifications.sender import StubSender


class FakeClient:
    def __init__(self, batches, wait_answers=None, wait_error=None):
        self.batches = list(batches)
        self.wait_answers = list(wait_answers or [])
        self.wait_error = wait_error
        self.calls: list[str] = []

    # Каждый вызов уступает ход, как настоящий HTTP-запрос: иначе цикл
    # отправщика в тесте никогда не отдал бы управление.
    async def claim_notifications(self):
        await asyncio.sleep(0)
        self.calls.append("claim")
        return {"messages": self.batches.pop(0) if self.batches else []}

    async def report_notifications(self, results):
        await asyncio.sleep(0)
        self.calls.append("report")

    async def wait_outbox(self, seconds):
        await asyncio.sleep(0)
        self.calls.append(f"wait:{seconds}")
        if self.wait_error is not None:
            raise self.wait_error
        return self.wait_answers.pop(0) if self.wait_answers else {"ready": False}


def message(number: int) -> dict:
    return {"id": f"id-{number}", "chat_id": 100 + number, "text": "t",
            "type": "question.reply", "attempts": 0}


@pytest.fixture()
def sleeps(monkeypatch):
    """Паузы не ждутся, а записываются."""
    taken: list[float] = []
    original = asyncio.sleep

    async def fake_sleep(seconds):
        # Модуль asyncio общий: паузу нулевой длины (уступить ход) делает
        # и сам тест — её пропускаем, настоящие паузы записываем.
        if seconds:
            taken.append(seconds)
        await original(0)

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    return taken


def run_loop(client, rounds: int) -> None:
    """Прогнать цикл, пока он не спросит очередь `rounds` раз."""

    async def body():
        task = asyncio.ensure_future(worker.run_worker(StubSender(), client))
        while client.calls.count("claim") < rounds:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(body())


class TestWake:
    def test_an_empty_pass_waits_for_the_backend_signal(self, sleeps, monkeypatch):
        monkeypatch.setattr(worker.settings, "notifications_wait_seconds", 25)
        client = FakeClient([[]], wait_answers=[{"ready": True}])

        run_loop(client, rounds=2)

        assert client.calls[:3] == ["claim", "wait:25", "claim"]
        # Никаких слепых пяти секунд между проходами.
        assert 5 not in sleeps

    def test_a_pass_that_sent_something_looks_again_at_once(self, sleeps):
        client = FakeClient([[message(1)], [message(2)], []])

        run_loop(client, rounds=3)

        assert client.calls[:5] == ["claim", "report", "claim", "report", "claim"]
        assert sleeps == []

    def test_no_wake_support_falls_back_to_the_old_interval(self, sleeps, monkeypatch):
        monkeypatch.setattr(worker.settings, "notifications_poll_seconds", 5)
        client = FakeClient([[]], wait_error=ApiError(404, "not_found", "нет такого пути"))

        run_loop(client, rounds=2)

        assert sleeps[0] == 5

    def test_backend_down_during_claim_waits_the_old_interval(self, sleeps, monkeypatch):
        monkeypatch.setattr(worker.settings, "notifications_poll_seconds", 5)

        class Down(FakeClient):
            async def claim_notifications(self):
                await asyncio.sleep(0)
                self.calls.append("claim")
                raise ApiError(502, "bad_gateway", "backend недоступен")

        client = Down([])
        run_loop(client, rounds=2)

        assert sleeps[0] == 5
        assert not any(call.startswith("wait") for call in client.calls)

    def test_ready_with_nothing_to_take_does_not_spin(self, sleeps):
        client = FakeClient([], wait_answers=[{"ready": True}] * 10)

        run_loop(client, rounds=5)

        assert 1 in sleeps
