"""Автовыход вручную: закрыть вчерашние сессии без выхода (решение Р-1).

Обычно автовыход делает служба `attendance-reminders` на каждом проходе.
Команда нужна для выкатки и разбора: `--dry-run` показывает, сколько
открытых сессий закроется, ничего не меняя. Первый запуск после выкатки
закроет все исторические открытые сессии — сначала посмотрите число.
"""

from django.core.management.base import BaseCommand

from humotech.attendance.auto_close import close_overdue_sessions


class Command(BaseCommand):
    help = "Закрывает автовыходом открытые сессии, чей день уже кончился"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true",
                            help="только посчитать, ничего не менять")

    def handle(self, *args, **options) -> None:
        count = close_overdue_sessions(dry_run=options["dry_run"])
        verb = "будет закрыто" if options["dry_run"] else "закрыто"
        self.stdout.write(f"{verb} автовыходом: {count}")
