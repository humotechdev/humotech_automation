"""Общая основа прикладных сервисов CRM.

Три вещи, одинаковые для всех сервисов и потому вынесенные сюда:

  * доступ (`AccessControl`) и журнал (`AuditTrail`) создаются один раз
    на сервис, а не на каждый вызов — иначе кэш разрешений бесполезен;
  * любая запись в базу заворачивается в перевод `IntegrityError`
    в доменную ошибку: наружу сырое исключение не выходит;
  * многошаговые операции идут внутри транзакции, поэтому либо применяются
    целиком, либо не применяются вовсе.

Про порядок try и atomic отдельно. Перехват обязан стоять СНАРУЖИ блока:
после ошибки Django помечает транзакцию на откат, и любой следующий запрос
внутри блока даёт `TransactionManagementError`. Если бы try стоял внутри,
сам перевод ошибки в понятное сообщение падал бы на первой же выборке.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import IntegrityError, transaction

from humotech.core.errors import Conflict, translate_integrity_error
from humotech.core.rbac import AccessControl, AuditTrail


class BaseService:
    def __init__(self) -> None:
        self.access = AccessControl()
        self.audit = AuditTrail()

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Операция целиком или никак.

        Вложенный вызов создаёт точку отката, поэтому неудача снимает только
        эту операцию и оставляет транзакцию вызывающего рабочей.
        """
        try:
            with transaction.atomic():
                yield
        except IntegrityError as exc:
            raise translate_integrity_error(exc) from exc


def refuse_stale(current, expected, *, enabled: bool = True) -> None:
    """Отказать, если правят не ту редакцию, которую видели.

    Один механизм на все разделы: у роли, у назначения и у настроек
    вопрос один и тот же — «не изменилось ли это, пока я редактировал».
    Второй словарь для того же понятия означал бы два разных ответа на
    один вопрос и два разных повода для 409.

    `enabled` отделяет «не передали редакцию» от «передали пустую».
    У срока назначения `None` — законное значение «бессрочно», и без
    этого признака проверка молча пропускала бы ровно тот случай, ради
    которого её ставили.
    """
    if enabled and current != expected:
        raise Conflict(
            "Запись изменилась, пока вы её редактировали. Откройте её "
            "заново и повторите правку.",
            details={
                "current": current.isoformat() if current else None,
                "expected": expected.isoformat() if expected else None,
            },
        )
