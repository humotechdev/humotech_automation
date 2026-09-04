"""Уборка просроченных файлов выгрузок.

Ту же уборку делает сам исполнитель очереди, и обычно этого хватает.
Отдельная команда нужна для двух случаев: разово убрать накопившееся
и поставить уборку в планировщик там, где исполнитель не запущен.

Запись задания при этом остаётся: по ней видно, что выгрузка была и кем
заказана. Уходит только файл — именно он и есть утечка.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from humotech.reports.service import purge_expired


class Command(BaseCommand):
    help = "Удаляет файлы выгрузок, у которых истёк срок хранения"

    def handle(self, *args, **options) -> None:
        removed = purge_expired()
        self.stdout.write(f"удалено файлов: {removed}")
