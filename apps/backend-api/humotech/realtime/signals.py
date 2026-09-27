"""Сигналы изменений: `pg_notify` в той же транзакции, что и изменение.

Почему `pg_notify`, а не отдельный брокер. PostgreSQL доставляет
уведомление слушателям ТОЛЬКО после фиксации транзакции и выбрасывает
его при откате. Это ровно нужное правило «сигнал — только о том, что
действительно произошло», и получается оно само, без `on_commit` и без
второго места, где сигнал может потеряться или прийти раньше данных.

Что в сигнале: идентификаторы и время сервера. Ни текста сообщения, ни
имени, ни телефона — канал слушает любой процесс с доступом к базе, а
журналы PostgreSQL могут сохранить полезную нагрузку. Кто что увидит,
решает поток по правам конкретного пользователя, а сами данные CRM
забирает обычным запросом с обычной проверкой доступа.

Вне транзакции (автофиксация) уведомление уходит сразу после оператора —
то есть тоже после записи, которая к этому моменту уже зафиксирована.
"""

from __future__ import annotations

import json
import logging
import time

from django.db import connection, transaction

logger = logging.getLogger("humotech.realtime")

#: Канал LISTEN/NOTIFY. Один на всё: событий немного, а разбирает их
#: получатель по полю `t`.
CHANNEL = "humotech_events"

#: Что случилось с обращением. Слова для CRM, а не для человека.
MESSAGE = "message"      # сотрудник написал
REPLY = "reply"          # HR ответил
UPDATED = "updated"      # состояние, ответственный, приоритет, категория
READ = "read"            # прочитано в CRM
DRAFT = "draft"          # готова подсказка ассистента
DELIVERY = "delivery"    # изменилась доставка ответа HR в Telegram


def emit(payload: dict) -> None:
    """Отправить сигнал. Ошибка сигнала не ломает изменение.

    Сигнал — ускоритель, а не гарантия: CRM всё равно перечитывает
    данные при переподключении и по резервному таймеру. Поэтому сбой
    здесь пишется в журнал и не откатывает ответ сотруднику.
    """
    body = dict(payload, at=round(time.time(), 3))
    # Диагностика цепочки (временно): когда вызван NOTIFY и когда
    # зафиксирована транзакция — именно тогда PostgreSQL его доставляет.
    if payload.get("t") == "question":
        called = time.time()
        logger.info(
            "diag notify called question=%s what=%s",
            str(payload.get("id"))[:8], payload.get("what"),
        )
        transaction.on_commit(lambda: logger.info(
            "diag commit question=%s what=%s after_notify_ms=%d",
            str(payload.get("id"))[:8], payload.get("what"),
            (time.time() - called) * 1000,
        ))
    try:
        # Внутри чужой транзакции `atomic` — точка сохранения: упавший
        # NOTIFY откатывается до неё и не помечает всю транзакцию
        # прерванной. Вне транзакции — своя короткая транзакция.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT pg_notify(%s, %s)", [CHANNEL, json.dumps(body)])
    except Exception:  # noqa: BLE001 — сигнал необязателен
        logger.exception("сигнал %s не отправлен", body.get("t"))


def question_changed(question, what: str) -> None:
    """Обращение изменилось. Только идентификаторы."""
    emit({
        "t": "question",
        "org": str(question.organization_id),
        "id": str(question.id),
        "what": what,
    })


def outbox_ready() -> None:
    """В очереди Telegram появилось что отправить — будит отправщика."""
    emit({"t": "outbox"})


def deliveries_changed(notification_ids) -> None:
    """Сменилась доставка уведомлений. Сигналит обращениям, к которым они
    относятся; прочие уведомления CRM-чату неинтересны."""
    from humotech.questions.models import QuestionMessage

    ids = [one for one in notification_ids if one]
    if not ids:
        return
    try:
        rows = list(
            QuestionMessage.objects.filter(notification_id__in=ids)
            .values_list("organization_id", "question_id")
            .distinct()
        )
    except Exception:  # noqa: BLE001 — сигнал необязателен
        logger.exception("не удалось найти обращения для сигнала доставки")
        return
    for organization_id, question_id in rows:
        emit({
            "t": "question",
            "org": str(organization_id),
            "id": str(question_id),
            "what": DELIVERY,
        })


__all__ = [
    "CHANNEL",
    "DELIVERY",
    "DRAFT",
    "MESSAGE",
    "READ",
    "REPLY",
    "UPDATED",
    "deliveries_changed",
    "emit",
    "outbox_ready",
    "question_changed",
]
