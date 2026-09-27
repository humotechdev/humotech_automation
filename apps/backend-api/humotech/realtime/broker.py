"""Один слушатель `LISTEN` на процесс и раздача сигналов подписчикам.

Живёт только в ASGI-процессе событий. Одно подключение к PostgreSQL на
процесс, сколько бы вкладок CRM ни было открыто: каждая подписка — это
очередь в памяти, а не соединение с базой.

Подключение отдельное от Django: `LISTEN` требует автофиксации и живёт
всё время процесса, а соединения Django закрываются и переоткрываются
по своим правилам. Смешивать их значило бы однажды потерять `LISTEN`
вместе с соединением, которое Django счёл устаревшим.

Потеря связи с базой — обычное событие (перезапуск PostgreSQL, сеть).
Слушатель переподключается с растущей паузой, а после восстановления
рассылает всем `resync`: сигналы, отправленные за время разрыва,
потеряны, и подписчики должны перечитать данные целиком.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from humotech.realtime.signals import CHANNEL

logger = logging.getLogger("humotech.realtime")

#: Сколько сигналов держит очередь одного подписчика. Медленный клиент не
#: должен раздувать память процесса: переполнение заменяется одним
#: `resync` — «перечитай всё», и это честнее, чем молча терять сигналы.
QUEUE_SIZE = 200

RESYNC = {"t": "resync"}


class Broker:
    def __init__(self, connect: Callable[[], Awaitable] | None = None) -> None:
        self._connect = connect or _connect
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        #: Слушатель хотя бы раз подключился: следующее подключение —
        #: уже восстановление, и подписчикам нужен `resync`.
        self._connected_once = False
        self.listening = asyncio.Event()

    # ------------------------------------------------------------ подписки

    def subscribe(self) -> asyncio.Queue:
        """Новая очередь сигналов. Слушатель запускается при первой."""
        self._ensure_started()
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    def publish(self, event: dict) -> None:
        """Раздать сигнал всем подписчикам процесса."""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Очередь забита — подписчик не успевает. Вместо потери
                # отдельных сигналов — один «перечитай всё».
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(RESYNC)

    # ------------------------------------------------------------ слушатель

    def _ensure_started(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        pause = 1.0
        while True:
            try:
                connection = await self._connect()
                async with connection:
                    await connection.execute(f"LISTEN {CHANNEL}")
                    if self._connected_once:
                        logger.info("realtime listener reconnected")
                        self.publish(RESYNC)
                    self._connected_once = True
                    self.listening.set()
                    pause = 1.0
                    async for notice in connection.notifies():
                        event = _parse(notice.payload)
                        if event is not None:
                            # Диагностика (временно): сигнал дошёл до
                            # слушателя; задержка — от вызова NOTIFY.
                            if event.get("t") == "question" and isinstance(
                                event.get("at"), (int, float)
                            ):
                                logger.info(
                                    "diag listener got question=%s lag_ms=%d subscribers=%d",
                                    str(event.get("id"))[:8],
                                    (time.time() - event["at"]) * 1000,
                                    len(self._subscribers),
                                )
                            self.publish(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — база вернётся
                logger.warning("realtime listener lost, retry in %.0fs", pause,
                               exc_info=True)
            self.listening.clear()
            await asyncio.sleep(pause)
            pause = min(pause * 2, 30.0)


def _parse(payload: str) -> dict | None:
    try:
        event = json.loads(payload)
    except ValueError:
        return None
    return event if isinstance(event, dict) and "t" in event else None


async def _connect():
    """Отдельное соединение в автофиксации из настроек Django."""
    import psycopg
    from django.conf import settings

    db = settings.DATABASES["default"]
    return await psycopg.AsyncConnection.connect(
        dbname=db["NAME"],
        user=db.get("USER") or None,
        password=db.get("PASSWORD") or None,
        host=db.get("HOST") or None,
        port=db.get("PORT") or None,
        autocommit=True,
        application_name="humotech-realtime",
    )


#: Брокер процесса. Один на ASGI-процесс; в тестах подменяется.
broker = Broker()


__all__ = ["Broker", "RESYNC", "broker"]
