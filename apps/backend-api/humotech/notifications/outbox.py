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


@dataclass(frozen=True)
class Outgoing:
    """Сообщение, готовое к отправке."""

    id: str
    chat_id: int
    text: str
    notification_type: str
    attempts: int


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
                )
            )
    return ready


def mark_sent(notification_id, *, now: datetime | None = None) -> None:
    moment = now or timezone.now()
    # Условие в самом UPDATE: из двух одновременных отчётов строку
    # переводит ровно один, и попытку записывает тоже он.
    with transaction.atomic():
        changed = Notification.objects.filter(
            id=notification_id, status="RUNNING"
        ).update(
            status="SENT", sent_at=moment, locked_at=None, error_message=None
        )
        if not changed:
            # Строку уже кто-то перевёл: попытки не было, записывать нечего.
            return
        row = Notification.objects.filter(id=notification_id).first()
        if row is not None:
            _log_attempt(row, outcome="SENT", reason=None, moment=moment)


def mark_failed(
    notification_id, *, error: str, now: datetime | None = None
) -> None:
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
        _fail(notification_id, error=error, moment=moment)


def _fail(notification_id, *, error: str, moment: datetime) -> None:
    row = (
        Notification.objects.select_for_update()
        .filter(id=notification_id)
        .first()
    )
    if row is None or row.status != "RUNNING":
        return

    attempts = row.attempts + 1
    limit = settings.NOTIFICATIONS["MAX_ATTEMPTS"]
    row.attempts = attempts
    row.locked_at = None
    # Только безопасный текст: ответ Telegram может содержать эхо запроса,
    # а в запросе — текст уведомления целиком.
    row.error_message = error[:200]

    if attempts >= limit:
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


def reclaim_stale(*, now: datetime | None = None) -> int:
    """Вернуть в очередь то, что зависло в RUNNING.

    Процесс отправщика мог упасть между захватом и результатом. Блокировка
    строки снимется сама, статус — нет; без этой уборки такое сообщение
    не ушло бы никогда.
    """
    moment = now or timezone.now()
    deadline = moment - timedelta(
        seconds=settings.NOTIFICATIONS["LOCK_TIMEOUT_SECONDS"]
    )
    return Notification.objects.filter(
        status="RUNNING", locked_at__lt=deadline
    ).update(status="PENDING", locked_at=None, next_attempt_at=moment)


def _backoff(attempts: int) -> timedelta:
    base = settings.NOTIFICATIONS["RETRY_BASE_SECONDS"]
    ceiling = settings.NOTIFICATIONS["RETRY_MAX_SECONDS"]
    return timedelta(seconds=min(base * (2 ** (attempts - 1)), ceiling))


__all__ = [
    "NotificationAttempt",
    "Outgoing",
    "claim",
    "enqueue",
    "mark_failed",
    "mark_sent",
    "reclaim_stale",
]
