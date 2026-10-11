"""Применить увольнения, чей последний день кончился (решение В1).

Обычно это делает служба `attendance-reminders` на каждом проходе. Команда —
для выкатки и разбора: `--dry-run` показывает, скольких уволит, ничего не
меняя.
"""

from django.core.management.base import BaseCommand

from humotech.employees.offboarding import apply_due_terminations


class Command(BaseCommand):
    help = "Переводит в «уволен» сотрудников, чей последний рабочий день кончился"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true",
                            help="только посчитать, ничего не менять")

    def handle(self, *args, **options) -> None:
        count = apply_due_terminations(dry_run=options["dry_run"])
        verb = "будет уволено" if options["dry_run"] else "уволено"
        self.stdout.write(f"{verb}: {count}")
