"""Общая основа прикладных сервисов CRM.

Три вещи, одинаковые для всех сервисов и потому вынесенные сюда:

  * доступ (`AccessControl`) и журнал (`AuditTrail`) создаются один раз
    на сервис, а не на каждый вызов — иначе кэш разрешений бесполезен;
  * любая запись в базу заворачивается в перевод `IntegrityError`
    в доменную ошибку: наружу сырое исключение SQLAlchemy не выходит;
  * многошаговые операции идут внутри SAVEPOINT, поэтому либо применяются
    целиком, либо не применяются вовсе.

Про SAVEPOINT отдельно. Смена офиса — это закрытие старой строки назначения
и вставка новой. Если вторая часть нарушит ограничение, первая уже выполнена.
Без SAVEPOINT пришлось бы откатывать всю транзакцию вызывающего, а он мог
делать что-то ещё. Внутренняя точка отката снимает ровно эту операцию
и оставляет сессию рабочей.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.errors import translate_integrity_error
from src.core.rbac import AccessControl, AuditTrail


class BaseService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.access = AccessControl(session)
        self.audit = AuditTrail(session)

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Операция целиком или никак."""
        try:
            with self.session.begin_nested():
                yield
        except IntegrityError as exc:
            raise translate_integrity_error(exc) from exc

    def flush(self) -> None:
        """Отправить накопленные изменения и перевести нарушение ограничения.

        Нужен именно явный flush, а не ожидание конца транзакции: EXCLUDE- и
        UNIQUE-ограничения проверяются в момент выполнения оператора, и порядок
        операторов имеет значение — закрытие старого периода обязано дойти
        до базы раньше вставки нового.
        """
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(exc) from exc
