"""Напоминания о незавершённом ознакомлении.

Ознакомление обязательно, но доступа не отбирает — значит, единственное,
чем система доводит его до конца, это вовремя напомнить. Кто и когда
получит сообщение, решает `humotech/onboarding/reminders.py`; здесь
только запуск.

Без `--loop` делает один проход и выходит — так её удобно вызывать из
внешнего планировщика и так же её вызывает тест. С `--loop` живёт сама
и спит между проходами.

Частота прохода не влияет на то, сколько сообщений получит человек:
пауза между напоминаниями своя, а повтор в один день закрыт ключом
идемпотентности очереди. Слишком редкий опрос лишь задержит
напоминание, слишком частый — потратит запросы впустую.
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from humotech.attendance import heartbeat
from humotech.onboarding.reminders import run_once

logger = logging.getLogger("humotech.onboarding.reminders")

#: Файл признака жизни. Healthcheck:
#: `python -m humotech.attendance.heartbeat <файл> <2*пауза+300>`.
HEARTBEAT_ENV = "ONBOARDING_REMINDERS_HEARTBEAT"
HEARTBEAT_DEFAULT = "/tmp/onboarding-reminders.heartbeat"

#: Пауза по умолчанию — час. Само напоминание уходит раз в несколько
#: дней, поэтому опрашивать чаще незачем: проход нужен лишь затем,
#: чтобы попасть в рабочие часы организации.
DEFAULT_PAUSE = 3600


class Command(BaseCommand):
    help = "Напоминает о незавершённом первичном ознакомлении"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--loop", type=int, nargs="?", const=DEFAULT_PAUSE, default=0,
            help="пауза в секундах между проходами; 0 — один проход и выход",
        )

    def handle(self, *args, **options) -> None:
        pause = options["loop"]
        if pause <= 0:
            self.stdout.write(f"поставлено напоминаний: {run_once()}")
            return

        path = heartbeat.resolve_path(HEARTBEAT_ENV, HEARTBEAT_DEFAULT)
        self.stdout.write(
            f"проверка каждые {pause} с; признак жизни {path} "
            f"(допустимый возраст {heartbeat.max_age_for(pause)} с); выход по Ctrl+C"
        )
        # Упавший проход не уносит с собой процесс, но и признак жизни
        # не обновляет: цикл на одних ошибках healthcheck видит мёртвым.
        heartbeat.run_loop(
            run_once, pause=pause, path=path,
            what="напоминания об ознакомлении", sleep=time.sleep,
        )
