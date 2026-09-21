"""Признак жизни исполнителя выгрузок — для healthcheck контейнера.

Исполнитель трогает файл на каждом проходе очереди и на каждом шаге
сборки отчёта. Healthcheck проверяет, что файл свежий:

    python -m humotech.reports.heartbeat

Без Django и без базы намеренно. Проверка раз в полминуты, поднимающая
Django и открывающая соединение, сама была бы нагрузкой, а «база
недоступна» и так роняет процесс, и его перезапускает `restart`.

Порог — две минуты: больше паузы между проходами и больше самого долгого
шага сборки (один офис за один день или запись книги Excel).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PATH_ENV = "EXPORT_WORKER_HEARTBEAT"
DEFAULT_PATH = "/tmp/export-worker.heartbeat"
MAX_AGE_SECONDS = 120
#: Чаще этого файл не трогается: шаги сборки идут десятками в секунду.
MIN_INTERVAL_SECONDS = 5

_last = 0.0


def heartbeat_path() -> Path:
    return Path(os.environ.get(PATH_ENV) or DEFAULT_PATH)


def beat(*, force: bool = False) -> None:
    global _last
    now = time.monotonic()
    if not force and now - _last < MIN_INTERVAL_SECONDS:
        return
    _last = now
    try:
        heartbeat_path().touch()
    except OSError:
        # Недоступный файл не должен ронять сборку отчёта: healthcheck
        # и так покажет, что признака жизни нет.
        pass


def age_seconds() -> float | None:
    try:
        return time.time() - heartbeat_path().stat().st_mtime
    except OSError:
        return None


def main() -> int:
    age = age_seconds()
    if age is None:
        print("исполнитель выгрузок: признака жизни нет")
        return 1
    if age > MAX_AGE_SECONDS:
        print(f"исполнитель выгрузок: последний признак жизни {age:.0f} с назад")
        return 1
    print(f"исполнитель выгрузок жив: {age:.0f} с назад")
    return 0


if __name__ == "__main__":  # pragma: no cover - вызывается healthcheck
    sys.exit(main())
