"""Очередь отправки в Telegram: transactional outbox поверх PostgreSQL.

Брокера в проекте нет и не появляется. Celery потребовал бы Redis или
RabbitMQ, ещё один процесс и ещё одно место, где сообщение может потеряться;
здесь важнее другая гарантия — не отправить того, чего не произошло, и не
потерять того, что произошло. Её даёт транзакция: строка уведомления
создаётся ТОЙ ЖЕ транзакцией, что и сама заявка. Откатилась заявка —
откатилось и уведомление, и наоборот не бывает.

Забирает сообщения бот, но не из базы напрямую. У него нет и не будет
подключения к PostgreSQL: он ходит в backend по HTTP с общим секретом,
и заводить вторую дорогу означало бы раздать боту доступ к схеме целиком
ради двух запросов. Захват строк делает backend — здесь, `SELECT ... FOR
UPDATE SKIP LOCKED`, — а бот получает готовый список и сообщает результат.

Состояния и переходы:

    PENDING --захват--> RUNNING --успех--> SENT
                           |
                           +--ошибка--> PENDING (попытка+1, пауза)
                           |
                           +--попытки кончились--> FAILED

`SKIP LOCKED` нужен ровно затем, чтобы два отправщика не подрались за одну
строку и при этом не выстроились в очередь: второй просто берёт следующую.

Отдельно — почему у RUNNING есть `locked_at`. Блокировка строки снимается
при падении процесса сама, а статус RUNNING — нет. Без отметки времени
такая строка осталась бы RUNNING навсегда, и переотправить её было бы
некому.

Чего в теле уведомления нет и быть не может: диагноза, комментария
сотрудника, содержимого справки. Уведомление сообщает, ЧТО произошло
с заявкой, а не почему человек болеет.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from humotech.audit.redaction import redact_text
from humotech.notifications.isolation import is_demo_chat_id
from humotech.notifications.models import Notification, NotificationAttempt
from humotech.telegram.identity import AccessDenied, resolve_account
from humotech.telegram.models import TelegramAccount

logger = logging.getLogger("humotech.notifications")

# Уведомления О САМОЙ ПРИВЯЗКЕ доставляются, даже если привязка уже не
# ACTIVE. Иначе сообщение «отдел кадров не подтвердил привязку» не дошло бы
# ровно до того человека, которому оно адресовано: к моменту отправки его
# привязка уже отозвана.
#
# Правило «не отправить чужому» при этом сохраняется полностью: чат берётся
# из строки привязки ТОГО ЖЕ сотрудника, а не из уведомления и не из
# запроса. Дойти до постороннего сообщению неоткуда.
BINDING_NOTIFICATION_PREFIX = "telegram.link."


def _log_attempt(
    row: Notification, *, outcome: str, reason: str | None, moment: datetime
) -> None:
    """Записать состоявшуюся попытку в историю.

    Номер сквозной по уведомлению, а не равен `attempts`: ручной повтор
    обнуляет счётчик строки, и без своей нумерации история после повтора
    начиналась бы с единицы поверх старой.
    """
    NotificationAttempt.objects.create(
        notification_id=row.id,
        number=(
            NotificationAttempt.objects.filter(notification_id=row.id).count() + 1
        ),
        attempted_at=moment,
        outcome=outcome,
        # У успеха причины нет: «отправлено, потому что» не бывает.
        reason=None if outcome == "SENT" else (reason or None),
    )


#: Уведомления, к которым полагается вложение, и какое именно.
#:
#: Файл не кладётся в очередь: он собирается из заявки на каждое
#: обращение, и сохранённая копия молча разошлась бы с продлённой
#: заявкой. В очереди только признак — бот по нему скачивает бумагу
#: тем же запросом, что и человек из кабинета.
ATTACHMENTS = {
    "absence.created": "absence_application",
    # То же самое по просьбе человека: он нажал «прислать» в кабинете.
    # Скачать файл прямо в вебвью Telegram нельзя — оно не даёт
    # сохранить blob, — поэтому бумага приходит сообщением в чат,
    # откуда её и пересылают, и печатают.
    "absence.application.copy": "absence_application",
    "absence.certificate.copy": "absence_certificate",
    # Файл, который кадровик приложил к ответу на обращение.
    "question.reply.file": "question_file",
}


@dataclass(frozen=True)
class Outgoing:
    """Сообщение, готовое к отправке."""

    id: str
    chat_id: int
    text: str
    notification_type: str
    attempts: int
    #: На что ссылается уведомление. Нужно там, где к сообщению
    #: прикладывают кнопку: без идентификатора бот знает, что опрос
    #: пришёл, но не знает какой.
    related_entity_id: str | None = None
    #: Что приложить к сообщению. `None` — обычный текст.
    attachment: str | None = None
    #: От чьего имени бот запросит вложение. Совпадает с `chat_id` в
    #: личном чате, но полагаться на это нельзя: они разные величины,
    #: и однажды разойдутся.
    telegram_user_id: int | None = None


def enqueue(
    *,
    organization_id,
    employee_id,
    notification_type: str,
    body: str,
    title: str | None = None,
    idempotency_key: str | None = None,
    related_entity_type: str | None = None,
    related_entity_id=None,
    scheduled_at: datetime | None = None,
) -> Notification | None:
    """Поставить уведомление в очередь.

    Вызывается ВНУТРИ транзакции изменения. Своей транзакции здесь нет
    намеренно: отдельная означала бы, что уведомление может уцелеть при
    откате заявки.

    `idempotency_key` защищает от повтора: одно событие — одна строка,
    сколько бы раз обработчик ни сработал. Уникальный ключ в базе делает
    это свойством схемы, а не аккуратности вызывающего.
    """
    if idempotency_key:
        existing = Notification.objects.filter(
            organization_id=organization_id, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing

    return Notification.objects.create(
        organization_id=organization_id,
        employee_id=employee_id,
        channel="TELEGRAM",
        notification_type=notification_type,
        title=title,
        body=body,
        status="PENDING",
        scheduled_at=scheduled_at,
        next_attempt_at=scheduled_at,
        idempotency_key=idempotency_key,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
    )


def claim(*, limit: int = 20, now: datetime | None = None) -> list[Outgoing]:
    """Захватить пачку сообщений для отправки.

    Возвращает только те, у которых есть живой адресат. Уведомление
    сотруднику с отозванной привязкой не отправляется никому: чужому
    человеку оно не уйдёт по построению, потому что chat_id берётся
    из ТЕКУЩЕЙ привязки, а не из строки уведомления.
    """
    moment = now or timezone.now()
    limit = max(1, min(limit, 100))
    ready: list[Outgoing] = []

    with transaction.atomic():
        rows = list(
            Notification.objects.select_for_update(skip_locked=True)
            .filter(
                Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=moment),
                channel="TELEGRAM",
                status="PENDING",
            )
            .order_by("next_attempt_at", "created_at")[:limit]
        )

        for row in rows:
            account = (
                TelegramAccount.objects.select_related("employee", "organization")
                .filter(employee_id=row.employee_id)
                .first()
            )
            about_binding = row.notification_type.startswith(
                BINDING_NOTIFICATION_PREFIX
            )
            resolved = None if account is None else resolve_account(account)
            blocked = account is None or (
                isinstance(resolved, AccessDenied) and not about_binding
            )
            # Демонстрационная привязка: отправлять некому по построению.
            if account is not None and is_demo_chat_id(account.telegram_chat_id):
                blocked = True
                resolved = AccessDenied("demo_account")
            if blocked:
                # Адресата нет или доступ закрыт. Это не ошибка отправки:
                # повторять нечего, и держать строку в очереди вечно
                # незачем. Причина сохраняется — без неё непонятно, почему
                # человек не получил уведомления.
                row.status = "CANCELLED"
                row.error_message = (
                    resolved.reason if resolved is not None else "not_linked"
                )
                row.save(
                    update_fields=["status", "error_message", "updated_at"]
                )
                # Это состоявшийся исход, а не отсутствие попытки:
                # без записи карточка показывала бы пустую историю
                # и статус «снято» без единого объяснения когда.
                _log_attempt(
                    row,
                    outcome="CANCELLED",
                    reason=row.error_message,
                    moment=moment,
                )
                continue

            row.status = "RUNNING"
            row.locked_at = moment
            row.save(update_fields=["status", "locked_at", "updated_at"])
            ready.append(
                Outgoing(
                    id=str(row.id),
                    chat_id=account.telegram_chat_id,
                    text=row.body,
                    notification_type=row.notification_type,
                    attempts=row.attempts,
                    related_entity_id=(
                        str(row.related_entity_id)
                        if row.related_entity_id else None
                    ),
                    # Вложение только там, где есть на что сослаться:
                    # без записи скачивать нечего.
                    attachment=(
                        ATTACHMENTS.get(row.notification_type)
                        if row.related_entity_id else None
                    ),
                    telegram_user_id=account.telegram_user_id,
                )
            )
    return ready


def mark_sent(notification_id, *, now: datetime | None = None) -> bool:
    """Успех. Возвращает, изменил ли отчёт строку.

    Принимается для RUNNING и для строки, которую уборка вернула в
    очередь как зависшую (`STALE_LOCK`), но ещё никто не взял снова:
    отправщик отправил и отчитался позже срока. Отбросить такой отчёт
    значило бы отправить сообщение второй раз. Строку, которую снял
    или повторил человек, поздний отчёт не трогает — у неё другая причина.
    """
    moment = now or timezone.now()
    # Условие в самом UPDATE: из двух одновременных отчётов строку
    # переводит ровно один, и попытку записывает тоже он.
    with transaction.atomic():
        changed = Notification.objects.filter(
            Q(status="RUNNING")
            | Q(
                status__in=("PENDING", "FAILED"),
                error_message=STALE_LOCK,
                locked_at__isnull=True,
            ),
            id=notification_id,
        ).update(
            status="SENT", sent_at=moment, locked_at=None, error_message=None,
            next_attempt_at=None,
        )
        if not changed:
            # Строку уже кто-то перевёл: попытки не было, записывать нечего.
            return False
        row = Notification.objects.filter(id=notification_id).first()
        if row is not None:
            _log_attempt(row, outcome="SENT", reason=None, moment=moment)
        return True


def mark_failed(
    notification_id, *, error: str, now: datetime | None = None
) -> bool:
    """Неудачная попытка: назад в очередь с паузой либо окончательный отказ.

    Пауза растёт по степеням двойки. Сервер Telegram, ответивший ошибкой,
    редко чинится за секунду, а долбить его каждую секунду — верный способ
    получить ограничение уже за поведение, а не за первую ошибку.
    """
    moment = now or timezone.now()
    # Под блокировкой строки: отчёт может прийти дважды — повторным
    # запросом бота или вторым его экземпляром. Без блокировки оба
    # читают RUNNING, оба пишут попытку, и в истории появляется
    # событие, которого не было.
    with transaction.atomic():
        return _fail(notification_id, error=error, moment=moment)


#: Причины, при которых повтор ничего не изменит: строка сразу FAILED.
#: Вложение, которое backend отказался отдать (файл не прошёл проверку
#: или удалён — ответ 404), не появится от того, что бот спросит ещё раз.
#: Для остальных причин повторы и так конечны (`MAX_ATTEMPTS`), но тут
#: и пяти попыток с растущей паузой незачем. Человек, устранив причину,
#: повторяет вручную из CRM.
PERMANENT_ERRORS = frozenset({"attachment_rejected", "attachment_not_found"})


def _fail(notification_id, *, error: str, moment: datetime) -> bool:
    row = (
        Notification.objects.select_for_update()
        .filter(id=notification_id)
        .first()
    )
    if row is None or row.status != "RUNNING":
        return False

    attempts = row.attempts + 1
    limit = settings.NOTIFICATIONS["MAX_ATTEMPTS"]
    row.attempts = attempts
    row.locked_at = None
    # Только безопасный текст: ответ Telegram может содержать эхо запроса,
    # а в запросе — текст уведомления целиком.
    # Ещё и маскирование: в эхе может оказаться адрес API с токеном бота.
    row.error_message = redact_text(str(error or "unknown"))[:200]

    if attempts >= limit or row.error_message in PERMANENT_ERRORS:
        row.status = "FAILED"
        row.next_attempt_at = None
        logger.warning(
            "notification failed after %s attempts: %s", attempts, row.id
        )
    else:
        row.status = "PENDING"
        row.next_attempt_at = moment + _backoff(attempts)

    row.save(
        update_fields=[
            "status", "attempts", "locked_at", "error_message",
            "next_attempt_at", "updated_at",
        ]
    )
    _log_attempt(row, outcome="FAILED", reason=row.error_message, moment=moment)
    return True


#: Причина, с которой уборка возвращает зависшую строку. По ней же
#: поздний отчёт об успехе узнаёт строку, которую отправщик на самом
#: деле успел отправить (см. `mark_sent`).
STALE_LOCK = "stale_lock"


def reclaim_stale(*, now: datetime | None = None) -> int:
    """Вернуть в очередь то, что зависло в RUNNING.

    Процесс отправщика мог упасть между захватом и результатом. Блокировка
    строки снимется сама, статус — нет; без этой уборки такое сообщение
    не ушло бы никогда.

    Зависание — это ПОПЫТКА: сообщение, возможно, уже ушло в Telegram.
    Поэтому уборка тратит её так же, как `mark_failed`: счётчик растёт,
    пауза растёт, после `MAX_ATTEMPTS` строка становится FAILED. Иначе
    отправщик, который падает на одном и том же сообщении, крутил бы его
    вечно, и человек получал бы его каждые несколько минут.
    """
    moment = now or timezone.now()
    deadline = moment - timedelta(
        seconds=settings.NOTIFICATIONS["LOCK_TIMEOUT_SECONDS"]
    )
    with transaction.atomic():
        ids = list(
            Notification.objects.select_for_update(skip_locked=True)
            .filter(status="RUNNING", locked_at__lt=deadline)
            .values_list("id", flat=True)[:500]
        )
        for notification_id in ids:
            _fail(notification_id, error=STALE_LOCK, moment=moment)
    return len(ids)


def _backoff(attempts: int) -> timedelta:
    base = settings.NOTIFICATIONS["RETRY_BASE_SECONDS"]
    ceiling = settings.NOTIFICATIONS["RETRY_MAX_SECONDS"]
    return timedelta(seconds=min(base * (2 ** (attempts - 1)), ceiling))


__all__ = [
    "NotificationAttempt",
    "Outgoing",
    "PERMANENT_ERRORS",
    "STALE_LOCK",
    "claim",
    "enqueue",
    "mark_failed",
    "mark_sent",
    "reclaim_stale",
]
