"""Ограничения, текст которых должен совпасть с уже существующей схемой.

Django строит `CHECK` из `Q`-выражений и при отрицании добавляет проверку
на NULL: `~Q(a=F("b"))` превращается не в `a <> b`, а в
`NOT (a = b AND a IS NOT NULL)`. Смысл тот же, текст другой — а схема
сверяется с эталоном по тексту ограничения.

Поэтому там, где выражение простое и уже зафиксировано в базе, оно
записывается как есть. Это не обход ORM: `CHECK` всё равно живёт только
в базе, Django его не вычисляет.
"""

from __future__ import annotations

from django.db import models
from django.db.models.expressions import RawSQL


def raw_check(sql: str, name: str) -> models.CheckConstraint:
    """`CHECK (<sql>)` с точным текстом и точным именем."""
    return models.CheckConstraint(
        condition=models.Q(RawSQL(sql, [], output_field=models.BooleanField())),
        name=name,
    )
