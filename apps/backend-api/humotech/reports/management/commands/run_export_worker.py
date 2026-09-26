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

from humotech.reports import heartbeat
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

        logger.info(
            "исполнитель выгрузок запущен: пауза %s с, уборка каждые %s проходов, "
            "признак жизни %s", pause, HOUSEKEEPING_EVERY, heartbeat.heartbeat_path(),
        )
        heartbeat.beat(force=True)
        passes = 0
        while True:
            worked = self.tick(housekeeping=passes % HOUSEKEEPING_EVERY == 0)
            passes += 1
            # Пауза только когда работы нет: очередь из десяти заданий
            # не должна разбираться десять пауз.
            if not worked:
                time.sleep(pause)

    def tick(self, *, housekeeping: bool) -> bool:
        """Один оборот цикла. Исключения наружу не выпускает.

        Обрыв соединения с базой, недоступный каталог выгрузок, ошибка
        в самом захвате задания — раньше любое из них завершало процесс
        целиком, и очередь стояла до перезапуска контейнера. Теперь
        оборот пишет ошибку в лог, закрывает испорченные соединения и
        отдаёт «работы не было», чтобы цикл выждал паузу и попробовал
        снова. Признак жизни обновляется только после удачного оборота:
        исполнитель, который крутится на одних ошибках, healthcheck
        должен видеть мёртвым.
        """
        from django.db import connections

        ok = True
        if housekeeping:
            ok = self._guarded("уборка: возврат зависших", self._reclaim) and ok
            ok = self._guarded("уборка: удаление просроченных", self._purge) and ok
        worked = False
        try:
            worked = bool(run_once())
        except Exception:  # noqa: BLE001 — исполнитель не должен падать
            ok = False
            logger.exception("проход очереди выгрузок завершился ошибкой")
            # Оборванное соединение само не восстановится: без закрытия
            # каждый следующий оборот падал бы на нём же.
            for connection in connections.all():
                if connection.in_atomic_block:
                    continue
                try:
                    connection.close_if_unusable_or_obsolete()
                except Exception:  # noqa: BLE001
                    pass
        if ok:
            heartbeat.beat(force=True)
        return worked

    def _guarded(self, what: str, step) -> bool:
        try:
            step()
            return True
        except Exception:  # noqa: BLE001 — исполнитель не должен падать
            logger.exception("%s завершилась ошибкой", what)
            return False

    def _housekeeping(self) -> None:
        self._reclaim()
        self._purge()

    def _reclaim(self) -> None:
        returned = reclaim_stale()
        if returned:
            logger.warning("возвращено в очередь зависших заданий: %s", returned)

    def _purge(self) -> None:
        removed = purge_expired()
        if removed:
            logger.info("удалено просроченных файлов выгрузок: %s", removed)
