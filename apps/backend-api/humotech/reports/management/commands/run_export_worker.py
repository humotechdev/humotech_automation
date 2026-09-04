"""Исполнитель очереди выгрузок.

Отдельный процесс, а не поток внутри backend: сборка отчёта за год
занимает минуты и ест память, и делать это в процессе, который
одновременно отвечает на запросы, значит подвесить интерфейс ради
одного файла.

Ключа провайдера и выхода в интернет команде не нужно: она читает базу
и пишет файл.
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from humotech.reports.service import purge_expired
from humotech.reports.worker import reclaim_stale, run_once

logger = logging.getLogger("humotech.reports.worker")

#: Как часто выполняется уборка: возврат зависших заданий и удаление
#: просроченных файлов. Не на каждом проходе — очередь опрашивается
#: раз в несколько секунд, а уборке этого не нужно.
HOUSEKEEPING_EVERY = 60


class Command(BaseCommand):
    help = "Обрабатывает очередь фоновых выгрузок export_jobs"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--loop", type=int, default=0,
            help="пауза в секундах между проходами; 0 — один проход и выход",
        )

    def handle(self, *args, **options) -> None:
        pause = options["loop"]
        if pause <= 0:
            self._housekeeping()
            run_once()
            return

        logger.info("исполнитель выгрузок запущен, пауза %s c", pause)
        passes = 0
        while True:
            if passes % HOUSEKEEPING_EVERY == 0:
                self._housekeeping()
            passes += 1
            # Пауза только когда работы нет: очередь из десяти заданий
            # не должна разбираться десять пауз.
            if not run_once():
                time.sleep(pause)

    def _housekeeping(self) -> None:
        returned = reclaim_stale()
        if returned:
            logger.warning("возвращено в очередь зависших заданий: %s", returned)
        removed = purge_expired()
        if removed:
            logger.info("удалено просроченных файлов выгрузок: %s", removed)
