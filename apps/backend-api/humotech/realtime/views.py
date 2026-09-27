"""Поток событий CRM (SSE) и пробуждение отправщика Telegram.

Оба обработчика асинхронные и работают ТОЛЬКО в ASGI-процессе событий.
Gunicorn с синхронными воркерами держал бы на каждом открытом потоке
целый воркер: трёх вкладок CRM хватило бы, чтобы остальной API встал.
Поэтому под WSGI они сразу отвечают 503, а шлюз направляет эти пути в
отдельный процесс.

**Что в потоке.** Только «что изменилось»: обращение, что с ним, время
сервера. Ни текста, ни имён. CRM, получив сигнал, перечитывает данные
обычным запросом — с обычной проверкой прав. Поэтому поток не может
показать то, чего пользователю видеть нельзя, даже если бы захотел, а
пропущенный сигнал не теряет сообщение: оно в базе.

**Кто что получает.** Сигнал об обращении уходит пользователю, только
если он видит это обращение прямо сейчас: своя организация, право
`questions.read`, сотрудник в области видимости. Права проверяются
заново на каждом сигнале и на каждом пульсе — отозванная роль или выход
из системы закрывают поток, а не ждут перезагрузки страницы.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.db import close_old_connections, connection
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone

from humotech.realtime import broker as broker_module

#: Пульс. Именованное событие, а не комментарий SSE: комментарий не
#: доходит до JavaScript, и полуживое соединение после сна ноутбука
#: CRM не распознала бы никогда.
HEARTBEAT_SECONDS = 15
#: Через сколько клиенту переподключаться после обрыва, мс.
RETRY_MS = 3000
#: Пределы ожидания отправщика, секунд.
WAIT_DEFAULT = 25
WAIT_MAX = 55


def _error(status: int, code: str, message: str) -> JsonResponse:
    return JsonResponse(
        {"error": {"code": code, "message": message, "details": None}},
        status=status,
    )


def _wsgi_refused(request) -> JsonResponse | None:
    if isinstance(request, ASGIRequest):
        return None
    return _error(
        503, "stream_unavailable",
        "Поток событий обслуживает отдельный процесс, а не этот сервер",
    )


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _db(func):
    """Синхронная работа с базой из потока — с уборкой соединений.

    В ASGI обычная уборка соединений Django срабатывает только в конце
    запроса, а поток живёт часами. Без явной уборки одно оборванное
    соединение PostgreSQL ломало бы все последующие проверки прав.
    """

    def run(*args, **kwargs):
        # Внутри атомарного блока (тесты) соединение закрывать нельзя:
        # это оборвало бы саму транзакцию. В процессе событий блока нет.
        tidy = not connection.in_atomic_block
        if tidy:
            close_old_connections()
        try:
            return func(*args, **kwargs)
        finally:
            if tidy:
                close_old_connections()

    return sync_to_async(run)


# --- проверки прав ------------------------------------------------------------


def _may_read(user_id, organization_id) -> bool:
    """Свежая проверка права: свой экземпляр AccessControl без кэша."""
    from humotech.core.rbac import AccessControl, Actor

    actor = Actor(user_id=user_id, organization_id=organization_id)
    return AccessControl().has(actor, "questions.read")


def _still_signed_in(user_id, organization_id, session_key) -> bool:
    """Сессия жива, пользователь активен и право не отозвано."""
    from django.contrib.sessions.models import Session

    from humotech.accounts.models import User

    if not session_key or not Session.objects.filter(
        session_key=session_key, expire_date__gt=timezone.now()
    ).exists():
        return False
    if not User.objects.filter(
        id=user_id, status="ACTIVE", archived_at__isnull=True
    ).exists():
        return False
    return _may_read(user_id, organization_id)


def _sees_question(user_id, organization_id, question_id) -> bool:
    """То же правило видимости, что у списка и карточки обращения."""
    from humotech.core.errors import DomainError
    from humotech.core.rbac import Actor
    from humotech.questions.inbox import InboxService

    try:
        question_uuid = uuid.UUID(str(question_id))
    except ValueError:
        return False
    if not _may_read(user_id, organization_id):
        return False
    actor = Actor(user_id=user_id, organization_id=organization_id)
    try:
        InboxService()._require(actor, question_uuid)
    except DomainError:
        return False
    return True


# --- поток CRM ----------------------------------------------------------------


async def question_events(request):
    """`GET /api/v1/events/stream` — один поток на открытую CRM."""
    refused = _wsgi_refused(request)
    if refused is not None:
        return refused

    user = await request.auser()
    if not user.is_authenticated:
        return _error(401, "not_authenticated", "Учетные данные не были предоставлены.")
    user_id, organization_id = user.id, user.organization_id
    if not await _db(_may_read)(user_id, organization_id):
        return _error(403, "permission_denied", "Нужно разрешение questions.read")
    session_key = request.session.session_key

    broker = broker_module.broker
    queue = broker.subscribe()

    async def stream():
        try:
            yield f"retry: {RETRY_MS}\n\n"
            yield _frame("ready", {"at": round(time.time(), 3)})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except TimeoutError:
                    alive = await _db(_still_signed_in)(
                        user_id, organization_id, session_key
                    )
                    if not alive:
                        # Вышел, сессия истекла или право отозвано:
                        # поток закрывается, а не ждёт перезагрузки.
                        yield _frame("bye", {"reason": "session"})
                        return
                    yield _frame("ping", {"at": round(time.time(), 3)})
                    continue

                kind = event.get("t")
                if kind == "resync":
                    yield _frame("resync", {"at": round(time.time(), 3)})
                    continue
                if kind != "question":
                    continue
                if event.get("org") != str(organization_id):
                    continue
                if not await _db(_sees_question)(
                    user_id, organization_id, event.get("id")
                ):
                    continue
                yield _frame("question", {
                    "id": event.get("id"),
                    "what": event.get("what"),
                    "at": event.get("at"),
                    "sent": round(time.time(), 3),
                })
        finally:
            # Клиент ушёл — Django отменяет генератор, и подписка должна
            # исчезнуть вместе с ним, а не копить сигналы в пустоту.
            broker.unsubscribe(queue)

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache, no-store"
    # Шлюз не должен копить поток в буфер: сигнал нужен сейчас.
    response["X-Accel-Buffering"] = "no"
    return response


# --- пробуждение отправщика ---------------------------------------------------


def _seconds_until_due() -> float | None:
    """0 — есть что отправить сейчас; N — ближайшее через N секунд; None — пусто."""
    from django.db.models import Min, Q

    from humotech.notifications.models import Notification

    moment = timezone.now()
    pending = Notification.objects.filter(channel="TELEGRAM", status="PENDING")
    if pending.filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=moment)
    ).exists():
        return 0.0
    nearest = pending.aggregate(at=Min("next_attempt_at"))["at"]
    if nearest is None:
        return None
    return max(0.0, (nearest - moment).total_seconds())


def _bot_allowed(request) -> bool:
    from humotech.telegram.auth import BOT_SECRET_HEADER
    from humotech.telegram.compare import constant_time_equal

    expected = settings.TELEGRAM["BOT_API_SECRET"]
    if not expected:
        return False
    return constant_time_equal(request.headers.get(BOT_SECRET_HEADER, ""), expected)


async def outbox_wait(request):
    """`GET /api/v1/telegram/bot/outbox/wait` — ждать, пока есть что отправить.

    Ничего не захватывает: только отвечает «пора» (`ready: true`) или
    «пока нечего» по истечении ожидания. Забирает бот прежним запросом.
    Так очередь в базе, повторы и идемпотентность остаются ровно такими,
    как были, а бот по-прежнему не подключается к PostgreSQL.
    """
    refused = _wsgi_refused(request)
    if refused is not None:
        return refused
    if not _bot_allowed(request):
        return _error(403, "permission_denied", "Только для бота")

    try:
        timeout = float(request.GET.get("timeout", WAIT_DEFAULT))
    except ValueError:
        timeout = WAIT_DEFAULT
    timeout = max(1.0, min(timeout, WAIT_MAX))

    broker = broker_module.broker
    # Подписка ДО проверки очереди: иначе ответ HR, зафиксированный
    # между проверкой и подпиской, разбудил бы пустоту, а бот проспал
    # бы до конца ожидания.
    queue = broker.subscribe()
    try:
        due = await _db(_seconds_until_due)()
        if due == 0:
            return JsonResponse({"ready": True, "reason": "due"})
        # Повтор по расписанию (пауза после ошибки) будит сам: ждать
        # дольше, чем до него, значит задержать повтор.
        wait = timeout if due is None else min(timeout, due)
        deadline = time.monotonic() + wait
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                ready = due is not None and due <= timeout
                return JsonResponse(
                    {"ready": ready, "reason": "scheduled" if ready else "timeout"}
                )
            try:
                event = await asyncio.wait_for(queue.get(), left)
            except TimeoutError:
                continue
            if event.get("t") in ("outbox", "resync"):
                return JsonResponse({"ready": True, "reason": event.get("t")})
    finally:
        broker.unsubscribe(queue)


__all__ = ["outbox_wait", "question_events"]
