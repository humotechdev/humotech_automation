"""Удаление записей журнала — только для пересоздания демо-стендов.

Журнал на уровне базы защищён триггером `audit_logs_append_only`
(миграция `audit/0002`): DELETE проходит лишь в транзакции с
`humotech.audit_purge = 'on'`. Этим пользуются только сидеры изолированных
стендов, которые и так требуют изолированную базу.
"""

from __future__ import annotations

from contextlib import contextmanager

from django.db import connection, transaction


@contextmanager
def allow_audit_purge():
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL humotech.audit_purge = 'on'")
        yield
