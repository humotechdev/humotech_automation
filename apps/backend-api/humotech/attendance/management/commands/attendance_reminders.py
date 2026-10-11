"""Проверка «рабочий день начался, а отметки нет».

Отдельный процесс, а не поток внутри backend: проверка идёт по всем
сотрудникам организации и ходит в базу, и делать это в процессе,
который одновременно отвечает на запросы, значит однажды придержать
интерфейс ради рассылки.

Без `--loop` делает один проход и выходит — так её удобно вызывать из
внешнего планировщика и так же её вызывает тест. С `--loop` живёт сама
и спит между проходами.

Частота не влияет на то, сколько сообщений получит человек: повтор
закрыт ключом идемпотентности очереди. Слишком редкий опрос лишь
задержит напоминание, слишком частый — потратит запросы впустую.
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from humotech.attendance import heartbeat
from humotech.attendance.reminders import run_once

logger = logging.getLogger("humotech.attendance.reminders")

#: Файл признака жизни. Healthcheck:
#: `python -m humotech.attendance.heartbeat <файл> <2*пауза+300>`.
HEARTBEAT_ENV = "ATTENDANCE_REMINDERS_HEARTBEAT"
HEARTBEAT_DEFAULT = "/tmp/attendance-reminders.heartbeat"

#: Пауза по умолчанию. Пять минут — компромисс: напоминание приходит
#: не позже чем через пять минут после допуска опоздания, а база
#: опрашивается двенадцать раз в час, а не шестьдесят.
DEFAULT_PAUSE = 300


def _pass() -> int:
    """Один проход: сначала автовыход вчерашних сессий (решение владельца
    Р-1), затем напоминания. Автовыход живёт в этой же службе, чтобы не
    заводить ещё один контейнер: она и так просыпается каждые пять минут."""
    from humotech.attendance.auto_close import close_overdue_sessions

    close_overdue_sessions()
    return run_once()


class Command(BaseCommand):
    help = "Напоминает о начале рабочего дня тем, кто не отметился; закрывает автовыходом вчерашние сессии"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--loop", type=int, nargs="?", const=DEFAULT_PAUSE, default=0,
            help="пауза в секундах между проходами; 0 — один проход и выход",
        )

    def handle(self, *args, **options) -> None:
        pause = options["loop"]
        if pause <= 0:
            self.stdout.write(f"поставлено напоминаний: {_pass()}")
            return

        path = heartbeat.resolve_path(HEARTBEAT_ENV, HEARTBEAT_DEFAULT)
        self.stdout.write(
            f"проверка каждые {pause} с; признак жизни {path} "
            f"(допустимый возраст {heartbeat.max_age_for(pause)} с); выход по Ctrl+C"
        )
        # Упавший проход не уносит с собой процесс, но и признак жизни
        # не обновляет: цикл на одних ошибках healthcheck видит мёртвым.
        heartbeat.run_loop(
            _pass, pause=pause, path=path, what="напоминания о начале дня",
            sleep=time.sleep,
        )
