"""Признак жизни для циклических команд напоминаний — для healthcheck.

Устроено по образцу `humotech/reports/heartbeat.py`: процесс трогает файл,
healthcheck проверяет, что файл свежий. Отличие в одном: у напоминаний
паузы разные (пять минут у начала дня, час у ознакомления), поэтому путь
и допустимый возраст задаются параметрами, а не константами:

    python -m humotech.attendance.heartbeat /tmp/attendance-reminders.heartbeat 900

Файл трогается ТОЛЬКО после удачного оборота. Цикл, который крутится на
одних исключениях или завис внутри прохода, healthcheck должен видеть
мёртвым — проверка «база отвечает» ни того ни другого не замечала.

Без Django и без базы намеренно: проверка раз в минуту не должна сама
быть нагрузкой.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger("humotech.attendance.heartbeat")


def resolve_path(env_name: str, default: str) -> Path:
    return Path(os.environ.get(env_name) or default)


def beat(path: Path) -> None:
    """Отметить удачный оборот. Недоступный файл цикл не роняет."""
    try:
        path.touch()
    except OSError:
        logger.warning("не удалось обновить признак жизни %s", path)


def age_seconds(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def max_age_for(pause: int) -> int:
    """Сколько файлу можно не обновляться: два оборота и запас на сам проход."""
    return 2 * pause + 300


def run_loop(
    step,
    *,
    pause: int,
    path: Path,
    what: str,
    sleep=time.sleep,
    iterations: int | None = None,
) -> None:
    """Цикл «проход — признак жизни — пауза».

    Исключение в проходе пишется в лог и цикл не роняет. Испорченные
    соединения с базой закрываются, иначе каждый следующий проход падал
    бы на том же оборванном соединении. `iterations` — для тестов.
    """
    done = 0
    while iterations is None or done < iterations:
        tick(step, path=path, what=what)
        done += 1
        sleep(pause)


def tick(step, *, path: Path, what: str) -> bool:
    """Один оборот. True — удачный, признак жизни обновлён."""
    try:
        step()
    except Exception:  # noqa: BLE001 — цикл не должен падать
        logger.exception("%s: проход не удался", what)
        _close_broken_connections()
        return False
    beat(path)
    return True


def _close_broken_connections() -> None:
    try:
        from django.db import connections
    except Exception:  # noqa: BLE001  # pragma: no cover
        return
    for connection in connections.all():
        if connection.in_atomic_block:
            continue
        try:
            connection.close_if_unusable_or_obsolete()
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("использование: python -m humotech.attendance.heartbeat ФАЙЛ МАКС_ВОЗРАСТ_С")
        return 2
    path = Path(args[0])
    try:
        limit = float(args[1])
    except ValueError:
        print("максимальный возраст должен быть числом секунд")
        return 2
    age = age_seconds(path)
    if age is None:
        print(f"{path}: признака жизни нет")
        return 1
    if age > limit:
        print(f"{path}: последний удачный проход {age:.0f} с назад")
        return 1
    print(f"{path}: жив, {age:.0f} с назад")
    return 0


if __name__ == "__main__":  # pragma: no cover - вызывается healthcheck
    sys.exit(main())
