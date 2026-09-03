"""Команда фоновой индексации базы знаний.

Раньше это был отдельный модуль с собственным разбором аргументов; в Django
такие вещи живут как management-команда — она получает настроенное окружение,
подключение к базе и логирование, ничего не настраивая заново.
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand, CommandError

from humotech.ai_assistant.config import ai_settings
from humotech.ai_assistant.worker import run_once

logger = logging.getLogger("humotech.ai.worker")


class Command(BaseCommand):
    help = "Обрабатывает очередь индексации knowledge_index_jobs"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--loop", type=int, default=0,
            help="пауза в секундах между проходами; 0 — один проход и выход",
        )

    def handle(self, *args, **options) -> None:
        if not ai_settings.ai_assistant_enabled:
            # Защита от случайного обращения к провайдеру: выключенный модуль
            # не должен запускаться «просто чтобы посмотреть».
            raise CommandError(
                "AI_ASSISTANT_ENABLED=false — воркер не запускается."
            )

        pause = options["loop"]
        if pause <= 0:
            run_once()
            return

        while True:
            try:
                if not run_once():
                    time.sleep(pause)
            except KeyboardInterrupt:
                self.stdout.write("воркер остановлен")
                return
            except Exception:
                logger.exception("непредвиденная ошибка воркера")
                time.sleep(pause)
