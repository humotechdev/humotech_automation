"""Схема, построенная Django-миграциями, обязана совпадать с эталоном Alembic.

Разовая сверка при переносе ничего не гарантирует дальше: следующий
`makemigrations` может тихо изменить тип колонки или потерять ограничение.
Этот тест превращает разовый результат в постоянное условие.

Эталонная база задаётся `ALEMBIC_BASELINE_URL`. Без неё тест пропускается —
на машине, где старой базы нет, набор не должен падать по этой причине.
"""

from __future__ import annotations

import os

import pytest
from django.db import connection

from scripts.schema_snapshot import diff, dump

pytestmark = pytest.mark.django_db


def _baseline_url() -> str | None:
    return os.getenv("ALEMBIC_BASELINE_URL")


def _django_url() -> str:
    params = connection.get_connection_params()
    return (
        f"postgresql://{params['user']}:{params['password']}"
        f"@{params['host']}:{params['port']}/{params['dbname']}"
    )


def test_django_schema_matches_alembic_baseline():
    baseline = _baseline_url()
    if not baseline:
        pytest.skip(
            "Не задан ALEMBIC_BASELINE_URL — сверка схемы с эталоном пропущена"
        )

    problems = diff(dump(baseline), dump(_django_url()))
    assert not problems, "Схема разошлась с эталоном:\n  " + "\n  ".join(problems)


def test_business_schema_has_expected_shape():
    """Опорные числа схемы. Работает и без эталонной базы.

    Не заменяет полную сверку, но ловит грубую потерю: исчезнувшую таблицу,
    снятое ограничение, пропавший индекс.
    """
    snapshot = dump(_django_url())
    assert len(snapshot["tables"]) == 44, (
        f"бизнес-таблиц {len(snapshot['tables'])}, ожидалось 44"
    )

    counts = {"c": 0, "f": 0, "u": 0, "x": 0}
    for table in snapshot["tables"]:
        for item in snapshot["constraints"].get(table, {}).values():
            if item["kind"] in counts:
                counts[item["kind"]] += 1

    assert counts["c"] == 92, f"CHECK: {counts['c']}, ожидалось 92"
    assert counts["f"] == 127, f"FOREIGN KEY: {counts['f']}, ожидалось 127"
    assert counts["u"] == 20, f"UNIQUE: {counts['u']}, ожидалось 20"
    assert counts["x"] == 2, f"EXCLUDE: {counts['x']}, ожидалось 2"
    assert {"btree_gist", "vector"} <= set(snapshot["extensions"])


def test_every_foreign_key_keeps_its_on_delete_action():
    """Django не выводит `ON DELETE` сам — его восстанавливает `core/0002`.

    Без этого прямой `DELETE` мимо ORM снёс бы историю: запрет физического
    удаления объектов со связями держится на действии в самой базе,
    а не на коллекторе Django.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.relname, c.conname, c.confdeltype, c.condeferrable
              FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
              JOIN pg_namespace n ON n.oid = t.relnamespace
             WHERE n.nspname = 'public' AND c.contype = 'f'
               AND t.relname NOT LIKE 'django_%' AND t.relname NOT LIKE 'auth_%'
            """
        )
        rows = cursor.fetchall()

    assert len(rows) == 127, f"внешних ключей {len(rows)}, ожидалось 127"

    # 'a' = NO ACTION: значит, действие не задано
    without_action = [f"{t}.{n}" for t, n, kind, _ in rows if kind == "a"]
    assert not without_action, (
        f"внешние ключи без ON DELETE: {without_action[:5]}"
    )
    deferrable = [f"{t}.{n}" for t, n, _, deferred in rows if deferred]
    assert not deferrable, (
        f"отложенные внешние ключи (в эталоне их нет): {deferrable[:5]}"
    )

    actions = {"r": 0, "n": 0, "c": 0}
    for _, _, kind, _ in rows:
        actions[kind] = actions.get(kind, 0) + 1
    assert actions["r"] == 107, f"RESTRICT: {actions['r']}, ожидалось 107"
    assert actions["n"] == 14, f"SET NULL: {actions['n']}, ожидалось 14"
    assert actions["c"] == 6, f"CASCADE: {actions['c']}, ожидалось 6"
