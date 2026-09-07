"""Что было в очереди уведомлений до удаления строк — и можно ли вернуть.

Появилась после разбора: с рабочего стенда удалили все строки таблицы
`notifications`, чтобы получить удобное пустое состояние, и вместе с
двенадцатью синтетическими исчезли настоящие. Пустая таблица после
удаления ничего не доказывает: по ней не видно, что в ней было.

Источники событий при этом целы, и это ключ ко всему. У каждого
уведомления, которое заводит система, детерминированный `idempotency_key`,
собранный из идентификатора события. Значит, НАБОР пропавших строк
восстанавливается точно — по журналу аудита и по самим событиям, а не по
догадке.

Чего команда НЕ находит: продление больничного
(`absence.request.extend`). Уведомление там уходит по ДОЧЕРНЕЙ заявке, а
в журнале записан идентификатор родительской, и собрать ключ по одному
журналу нельзя. Такие строки надо искать по самим заявкам вручную.

Чего не восстанавливается ничем:

  * когда именно была отправка (`sent_at`);
  * сколько было попыток и чем каждая кончилась;
  * дошло ли сообщение.

Поэтому по умолчанию команда НИЧЕГО НЕ ПИШЕТ. Она отвечает на вопрос
«что пропало» и показывает, что именно было бы записано. Запись требует
объявить исход доставки — то есть утверждение, которого нет ни в одном
источнике, — и делается только явным `--apply --outcome=...`, чтобы это
утверждение принадлежало человеку, а не команде.

Возвращённые строки не попадают в очередь ни при каком исходе: `PENDING`
среди допустимых значений нет. Восстановление истории не должно
оборачиваться повторной отправкой сообщений людям.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from humotech.audit.models import AuditLog
from humotech.notifications.models import Notification

# Действие журнала -> как собирается ключ пропавшего уведомления.
# Только те действия, рядом с которыми enqueue стоит В ТОЙ ЖЕ транзакции:
# значит, запись в журнале есть ровно тогда, когда была и строка очереди.
FROM_AUDIT = {
    "telegram.invitation.confirm": (
        "telegram-link:{entity}:confirmed", "telegram.link.confirmed",
    ),
    "absence.request.create": ("absence:{entity}:created", "absence.created"),
    "absence.request.cancel": (
        "absence:{entity}:cancelled", "absence.cancelled",
    ),
    "absence.request.approve": (
        "absence:{entity}:approved", "absence.approved",
    ),
    "absence.request.reject": (
        "absence:{entity}:rejected", "absence.rejected",
    ),
    "question.escalation.answer": (
        "question.answered:{entity}", "question.answered",
    ),
}

# Исходы, которыми разрешено объявлять восстановленную строку. PENDING и
# RUNNING отсутствуют намеренно: восстановление не отправляет.
ALLOWED_OUTCOMES = ("SENT", "CANCELLED")

# Тело восстановленной строки. Настоящий текст не сохранился: он
# собирался в момент события из шаблона и данных заявки, и собрать его
# заново сегодняшним шаблоном значило бы выдать нынешнюю формулировку
# за отправленную тогда. Пустая строка тоже не годится — она читалась бы
# как «сообщение было пустым».
RESTORED_BODY = (
    "Текст сообщения не сохранился. Строка восстановлена по журналу "
    "событий: известны адресат, тип события и время создания."
)


@dataclass
class Lost:
    key: str
    notification_type: str
    employee_id: object
    organization_id: object
    created_at: datetime
    source_action: str
    source_entity: object


class Command(BaseCommand):
    help = (
        "Показывает, какие уведомления пропали из очереди, и по явному "
        "требованию возвращает их в терминальном состоянии"
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply", action="store_true",
            help="записать строки; без него команда только показывает",
        )
        parser.add_argument(
            "--outcome", choices=ALLOWED_OUTCOMES,
            help="каким объявить исход восстановленных строк; обязателен "
                 "при --apply. Это утверждение о доставке, и источников "
                 "у него нет — оно принадлежит тому, кто запускает",
        )

    def handle(self, *args, **options) -> None:
        lost = self._missing()
        self._report(lost)
        if not lost:
            return

        if not options["apply"]:
            self.stdout.write(
                "\nНичего не записано. Чтобы вернуть строки, нужен явный "
                "--apply вместе с --outcome: время отправки и результат "
                "доставки не восстанавливаются ниоткуда, и объявить их "
                "должен человек."
            )
            return

        outcome = options["outcome"]
        if not outcome:
            raise CommandError(
                "--apply без --outcome не выполняется: строка без "
                "объявленного исхода была бы записью о доставке, "
                "которой никто не подтверждал."
            )
        self._write(lost, outcome)

    # --- что пропало ------------------------------------------------------

    def _missing(self) -> list[Lost]:
        present = set(
            Notification.objects.exclude(idempotency_key=None).values_list(
                "idempotency_key", flat=True
            )
        )
        lost: list[Lost] = []
        for record in AuditLog.objects.filter(
            action__in=FROM_AUDIT
        ).order_by("occurred_at"):
            template, notification_type = FROM_AUDIT[record.action]
            key = template.format(entity=record.entity_id)
            if key in present:
                continue
            employee_id = self._employee_of(record)
            if employee_id is None:
                self.stderr.write(
                    f"  адресат не определён для {record.action} "
                    f"{record.entity_id}: строка пропущена"
                )
                continue
            lost.append(
                Lost(
                    key=key,
                    notification_type=notification_type,
                    employee_id=employee_id,
                    organization_id=record.organization_id,
                    # Уведомление заводится ТОЙ ЖЕ транзакцией, что и
                    # запись журнала: время создания известно с точностью
                    # до миллисекунд и не выдумано.
                    created_at=record.occurred_at,
                    source_action=record.action,
                    source_entity=record.entity_id,
                )
            )
        return lost

    def _employee_of(self, record) -> object | None:
        """Адресат — из самого события, а не из журнала.

        В журнале лежит тот, кто ДЕЙСТВОВАЛ; уведомление уходит тому, кого
        действие касается. Для привязки это сотрудник приглашения, для
        заявки — её автор, для вопроса — задавший.
        """
        from humotech.absences.models import AbsenceRequest
        from humotech.questions.models import EmployeeQuestion
        from humotech.telegram.models import TelegramLinkInvitation

        if record.action.startswith("telegram.invitation"):
            row = TelegramLinkInvitation.objects.filter(
                id=record.entity_id
            ).first()
        elif record.action.startswith("absence.request"):
            row = AbsenceRequest.objects.filter(id=record.entity_id).first()
        else:
            row = EmployeeQuestion.objects.filter(id=record.entity_id).first()
        return row.employee_id if row is not None else None

    # --- вывод ------------------------------------------------------------

    def _report(self, lost: list[Lost]) -> None:
        if not lost:
            self.stdout.write(
                self.style.SUCCESS(
                    "Пропавших уведомлений не найдено: каждому событию в "
                    "журнале соответствует строка очереди."
                )
            )
            return

        self.stdout.write(
            self.style.WARNING(f"Пропавших уведомлений: {len(lost)}")
        )
        self.stdout.write(
            "  создано (из журнала)      тип                       ключ"
        )
        for row in lost:
            self.stdout.write(
                f"  {row.created_at.isoformat()}  "
                f"{row.notification_type:<24}  {row.key}"
            )
        self.stdout.write(
            "\nВосстановимо: набор строк, адресат, тип события и время "
            "создания.\nНе восстановимо ничем: текст сообщения, время "
            "отправки, число попыток, результат доставки."
        )

    # --- запись -----------------------------------------------------------

    def _write(self, lost: list[Lost], outcome: str) -> None:
        made = 0
        with transaction.atomic():
            for row in lost:
                created = Notification.objects.create(
                    organization_id=row.organization_id,
                    employee_id=row.employee_id,
                    channel="TELEGRAM",
                    notification_type=row.notification_type,
                    title=None,
                    body=RESTORED_BODY,
                    status=outcome,
                    # Время отправки НЕ ставится даже при исходе SENT:
                    # его не знает ни один источник, а `created_at` в этом
                    # поле был бы выдуманной отметкой доставки.
                    sent_at=None,
                    error_message=(
                        None if outcome == "SENT" else "restored_unknown"
                    ),
                    idempotency_key=row.key,
                    attempts=0,
                    # История этой строки не велась и не появится.
                    attempt_history_complete=False,
                )
                Notification.objects.filter(id=created.id).update(
                    created_at=row.created_at
                )
                made += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Записано строк: {made}, исход объявлен как {outcome}, "
                "время отправки оставлено неизвестным, история помечена "
                "неполной."
            )
        )
