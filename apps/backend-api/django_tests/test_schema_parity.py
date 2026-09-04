"""Схема, построенная Django-миграциями, обязана совпадать с эталоном Alembic.

Разовая сверка при переносе ничего не гарантирует дальше: следующий
`makemigrations` может тихо изменить тип колонки или потерять ограничение.
Этот тест превращает разовый результат в постоянное условие.

Эталон — снимок схемы на момент перехода на Django, и развитие проекта от него
отходит: это нормально. Ненормально — отойти незаметно. Поэтому сравнение
не ослабляется, а каждое расхождение перечисляется поимённо ниже. Список,
который можно прочитать, — документация; сравнение, которое всё прощает, —
мёртвый тест.

Эталонная база задаётся `ALEMBIC_BASELINE_URL`. Без неё сверка пропускается —
на машине, где старой базы нет, набор не должен падать по этой причине.
Опорные числа ниже работают и без эталона.
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


# Всё, чем схема отошла от эталона Alembic ПОСЛЕ перехода, — поимённо.
#
# Этап «безопасная привязка Telegram»:
#   * новая таблица одноразовых ссылок привязки;
#   * у привязки появился статус PENDING — переход по ссылке больше не даёт
#     доступа сам по себе, доступ открывает подтверждение HR.
#
# Ничего, кроме перечисленного, разойтись не имеет права: любая другая
# строка в выводе `diff` роняет тест.
KNOWN_DIVERGENCES = {
    "ЛИШНЯЯ ТАБЛИЦА: telegram_link_invitations",
    "telegram_accounts: ОГРАНИЧЕНИЕ ОТСУТСТВУЕТ: "
    "check (((status) = any ((array['active', 'revoked', 'blocked'])[])))",
    "telegram_accounts: лишнее ограничение: "
    "check (((status) = any "
    "((array['pending', 'active', 'revoked', 'blocked'])[])))",
}


def test_django_schema_matches_alembic_baseline():
    baseline = _baseline_url()
    if not baseline:
        pytest.skip(
            "Не задан ALEMBIC_BASELINE_URL — сверка схемы с эталоном пропущена"
        )

    problems = diff(dump(baseline), dump(_django_url()))
    unexpected = [item for item in problems if item not in KNOWN_DIVERGENCES]
    assert not unexpected, (
        "Схема разошлась с эталоном сверх объявленного:\n  "
        + "\n  ".join(unexpected)
    )

    # Список расхождений не должен пережить свою причину: если объявленное
    # изменение откатили, строку надо убрать отсюда, а не оставлять
    # молчаливое разрешение на будущее.
    stale = KNOWN_DIVERGENCES - set(problems)
    assert not stale, (
        "Эти расхождения объявлены, но их больше нет — удалите их из списка:"
        "\n  " + "\n  ".join(sorted(stale))
    )


def test_business_schema_has_expected_shape():
    """Опорные числа схемы. Работает и без эталонной базы.

    Не заменяет полную сверку, но ловит грубую потерю: исчезнувшую таблицу,
    снятое ограничение, пропавший индекс.
    """
    snapshot = dump(_django_url())
    # 44 таблицы перенесены с Alembic + telegram_link_invitations.
    assert len(snapshot["tables"]) == 45, (
        f"бизнес-таблиц {len(snapshot['tables'])}, ожидалось 45"
    )

    counts = {"c": 0, "f": 0, "u": 0, "x": 0}
    for table in snapshot["tables"]:
        for item in snapshot["constraints"].get(table, {}).values():
            if item["kind"] in counts:
                counts[item["kind"]] += 1

    # Прибавка этапа привязки Telegram к числам перехода:
    #   CHECK  92 + 1  — статусы приглашения;
    #   FK    127 + 4  — организация, сотрудник, автор, принявший решение;
    #   UNIQUE 20 + 1  — хеш токена. Второй уникальный ключ приглашений
    #                    частичный, а частичный Django строит ИНДЕКСОМ,
    #                    и в pg_constraint он не попадает.
    assert counts["c"] == 93, f"CHECK: {counts['c']}, ожидалось 93"
    assert counts["f"] == 131, f"FOREIGN KEY: {counts['f']}, ожидалось 131"
    assert counts["u"] == 21, f"UNIQUE: {counts['u']}, ожидалось 21"
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

    assert len(rows) == 131, f"внешних ключей {len(rows)}, ожидалось 131"

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
    # +3 RESTRICT и +1 SET NULL — ключи telegram_link_invitations.
    # SET NULL ровно один: учётную запись HR можно заблокировать, но запись
    # о принятом им решении обязана остаться.
    assert actions["r"] == 110, f"RESTRICT: {actions['r']}, ожидалось 110"
    assert actions["n"] == 15, f"SET NULL: {actions['n']}, ожидалось 15"
    assert actions["c"] == 6, f"CASCADE: {actions['c']}, ожидалось 6"
