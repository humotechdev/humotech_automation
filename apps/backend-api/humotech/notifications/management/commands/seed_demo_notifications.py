"""Синтетические уведомления для проверки раздела «Уведомления».

Эта команда существует потому, что однажды такой посев сделали скриптом
по рабочей базе. Скрипт отработал безупречно: завёл двенадцать строк,
перевёл их по состояниям — и две из них ушли настоящим людям в Telegram,
потому что рядом работал настоящий отправщик. Предупреждения в
документации в тот момент тоже были.

Предохранители собраны в `humotech/notifications/isolation.py`: их три,
и каждого достаточно поодиночке.

Строки заводятся прямо в очереди и переводятся по состояниям ТЕМИ ЖЕ
функциями `outbox`, которыми пользуется воркер: второй очереди и второго
отправщика здесь нет. Отправляет по-прежнему внешний процесс, и в
изолированном стенде это заглушка (`NOTIFICATIONS_SENDER=stub`).
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from humotech.employees.models import Employee
from humotech.notifications import outbox
from humotech.notifications.isolation import (
    E2E_CHAT_ID_FLOOR,
    require_isolated_stand,
)
from humotech.notifications.models import Notification, NotificationAttempt
from humotech.organizations.models import Organization
from humotech.telegram.models import TelegramAccount

# Тексты выдуманы целиком. Ни одно из этих сообщений не описывает
# настоящего события, поэтому попадание такого текста живому человеку —
# это ложь в его чате, а не безобидный тестовый шум.
EVENTS = (
    ("absence.approved", "Отпуск согласован",
     "Проверочное сообщение. Настоящего события за ним нет."),
    ("telegram.link.confirmed", "Привязка подтверждена",
     "Проверочное сообщение. Настоящего события за ним нет."),
    ("question.answered", "Ответ на обращение",
     "Проверочное сообщение. Настоящего события за ним нет."),
    ("absence.reviewed", "Заявка рассмотрена",
     "Проверочное сообщение. Настоящего события за ним нет."),
    ("attendance.correction.applied", "Отметка исправлена",
     "Проверочное сообщение. Настоящего события за ним нет."),
)


class Command(BaseCommand):
    help = (
        "Заводит синтетические уведомления для проверки раздела. "
        "Работает только в изолированном тестовом стенде."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--count", type=int, default=12, help="сколько строк завести"
        )
        parser.add_argument(
            "--organization", help="код организации; по умолчанию первая"
        )
        parser.add_argument(
            "--with-recipients", action="store_true",
            help="завести стендовые привязки Telegram, чтобы очередь "
                 "довела строки до отправщика-заглушки",
        )

    def handle(self, *args, **options) -> None:
        require_isolated_stand()

        org = self._organization(options.get("organization"))
        people = list(Employee.objects.filter(organization=org)[:12])
        if not people:
            raise CommandError("В организации нет сотрудников")

        if options["with_recipients"]:
            self._stand_recipients(org, people)

        count = max(1, options["count"])
        made = self._enqueue(org, people, count)
        self._walk_states(made)

        self.stdout.write(
            self.style.SUCCESS(
                f"Заведено {len(made)}; в таблице уведомлений "
                f"{Notification.objects.filter(organization=org).count()}, "
                f"записей попыток {NotificationAttempt.objects.count()}"
            )
        )

    # --- адресаты стенда --------------------------------------------------

    def _stand_recipients(self, org, people) -> None:
        """Привязки с идентификаторами чатов из диапазона стенда.

        Без адресата очередь снимает строку как `not_linked`, и доставка
        не проверяется вовсе. Идентификаторы берутся из заведомо
        невозможного диапазона: настоящему человеку такое сообщение не
        уйдёт даже при подмене отправщика на настоящий.
        """
        now = timezone.now()
        made = 0
        for index, employee in enumerate(people):
            chat_id = E2E_CHAT_ID_FLOOR + index
            _, created = TelegramAccount.objects.get_or_create(
                employee=employee,
                defaults={
                    "organization_id": org.id,
                    "telegram_user_id": chat_id,
                    "telegram_chat_id": chat_id,
                    "telegram_username": f"stand{chat_id}",
                    "status": "ACTIVE",
                    "connected_at": now,
                },
            )
            made += int(created)
        self.stdout.write(f"Стендовых привязок заведено: {made}")

    def _organization(self, code: str | None) -> Organization:
        query = Organization.objects.all()
        if code:
            query = query.filter(code=code)
        org = query.first()
        if org is None:
            raise CommandError("Организация не найдена")
        return org

    # --- наполнение -------------------------------------------------------

    def _enqueue(self, org, people, count: int) -> list[Notification]:
        now = timezone.now()
        made: list[Notification] = []
        for number in range(count):
            event = EVENTS[number % len(EVENTS)]
            row = outbox.enqueue(
                organization_id=org.id,
                employee_id=people[number % len(people)].id,
                notification_type=event[0],
                title=event[1],
                body=event[2],
                idempotency_key=f"demo-notification:{now.isoformat()}:{number}",
            )
            # Разное время создания: иначе вся страница — одна минута,
            # и ни период, ни сортировка не проверяются.
            Notification.objects.filter(id=row.id).update(
                created_at=now - timedelta(minutes=7 * number)
            )
            row.refresh_from_db()
            made.append(row)
        return made

    def _walk_states(self, made: list[Notification]) -> None:
        """Провести строки по состояниям функциями воркера.

        Своей арифметики над статусами здесь нет намеренно: если очередь
        когда-нибудь начнёт вести их иначе, посев должен разойтись с ней
        сразу и заметно, а не изображать состояния, которых уже не бывает.
        """
        now = timezone.now()
        sent, failing, cancelled, rest = _split(made)

        for index, row in enumerate(sent):
            moment = now - timedelta(minutes=7 * index)
            _run(row, moment)
            outbox.mark_sent(row.id, now=moment)

        for row in failing:
            moment = now - timedelta(minutes=20)
            for step in range(3):
                _run(row, moment + timedelta(minutes=step))
                outbox.mark_failed(
                    row.id,
                    error="TelegramNetworkError",
                    now=moment + timedelta(minutes=step),
                )
            Notification.objects.filter(id=row.id).update(status="FAILED")

        for row in cancelled:
            # Так очередь снимает строку, у адресата которой нет живой
            # привязки: тот же путь, что и в `claim`.
            _run(row, now - timedelta(minutes=15))
            outbox.mark_failed(
                row.id, error="not_linked", now=now - timedelta(minutes=15)
            )
            Notification.objects.filter(id=row.id).update(
                status="CANCELLED", error_message="not_linked"
            )

        # `rest` остаётся в PENDING: очереди нужна и непустая часть.
        del rest


def _split(made: list[Notification]):
    """Половина отправленных, четверть с ошибкой, по одной снятой и в очереди."""
    total = len(made)
    sent_to = max(1, total // 2)
    failing_to = min(total, sent_to + max(1, total // 6))
    cancelled_to = min(total, failing_to + 1)
    return (
        made[:sent_to],
        made[sent_to:failing_to],
        made[failing_to:cancelled_to],
        made[cancelled_to:],
    )


def _run(row: Notification, moment) -> None:
    """Перевести строку в RUNNING так же, как это делает захват."""
    Notification.objects.filter(id=row.id).update(
        status="RUNNING", locked_at=moment
    )
