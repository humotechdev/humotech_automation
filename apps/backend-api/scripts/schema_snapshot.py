"""Снимок схемы PostgreSQL и сравнение двух баз.

Нужен для переноса на Django ORM: схема, созданная Alembic-миграциями, —
эталон, а схема, созданную Django-миграциями, надо с ним сверить. Сравнение
делается машиной, а не глазами: расхождение в nullability одной колонки
глазами не находится, а в проде обходится дорого.

Читается всё из системных каталогов, а не из ORM: проверяется то, что
действительно лежит в базе.

    python -m scripts.schema_snapshot dump  <url> <файл.json>
    python -m scripts.schema_snapshot diff  <эталон.json> <новый.json>
    python -m scripts.schema_snapshot compare <url-эталон> <url-новый>
"""

from __future__ import annotations

import json
import sys
from typing import Any

import psycopg

# Служебные таблицы Django и Alembic в сравнение не входят: они не бизнес-схема,
# и их наличие с обеих сторон ничего не доказывает.
IGNORED_TABLES = {
    "alembic_version",
    "django_migrations",
    "django_content_type",
    "django_admin_log",
    "django_session",
    "auth_permission",
    "auth_group",
    "auth_group_permissions",
}
IGNORED_PREFIXES = ("django_", "auth_")


def _is_business_table(name: str) -> bool:
    return name not in IGNORED_TABLES and not name.startswith(IGNORED_PREFIXES)


COLUMNS_SQL = """
SELECT c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       a.attnotnull AS not_null,
       pg_get_expr(d.adbin, d.adrelid) AS default_expr
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attname
"""

CONSTRAINTS_SQL = """
SELECT t.relname AS table_name,
       c.conname AS name,
       c.contype AS kind,
       pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
ORDER BY t.relname, c.conname
"""

INDEXES_SQL = """
SELECT tablename AS table_name, indexname AS name, indexdef AS definition
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname
"""

EXTENSIONS_SQL = "SELECT extname FROM pg_extension ORDER BY extname"


def dump(url: str) -> dict[str, Any]:
    """Полный снимок бизнес-схемы."""
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(COLUMNS_SQL)
        columns: dict[str, dict[str, dict]] = {}
        for table, column, data_type, not_null, default in cur.fetchall():
            if not _is_business_table(table):
                continue
            columns.setdefault(table, {})[column] = {
                "type": data_type,
                "not_null": bool(not_null),
                "default": default,
            }

        cur.execute(CONSTRAINTS_SQL)
        constraints: dict[str, dict[str, dict]] = {}
        for table, name, kind, definition in cur.fetchall():
            if not _is_business_table(table):
                continue
            constraints.setdefault(table, {})[name] = {
                "kind": kind, "definition": definition,
            }

        cur.execute(INDEXES_SQL)
        indexes: dict[str, dict[str, str]] = {}
        for table, name, definition in cur.fetchall():
            if not _is_business_table(table):
                continue
            indexes.setdefault(table, {})[name] = definition

        cur.execute(EXTENSIONS_SQL)
        extensions = sorted(row[0] for row in cur.fetchall())

    return {
        "tables": sorted(columns),
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "extensions": extensions,
    }


def _normalize_definition(text: str) -> str:
    """Убирает различия записи, не меняющие смысл.

    Django и Alembic пишут одно и то же по-разному: кавычки вокруг имён,
    `::text` у литералов, разные пробелы. Сравнивать надо смысл, иначе весь
    вывод утонет в шуме и настоящее расхождение в нём потеряется.
    """
    result = text.replace('"', "").replace("::text", "").replace("::character varying", "")
    result = " ".join(result.split())
    return result.lower()


def _index_body(definition: str) -> str:
    """Тело индекса без его имени: имена Django и Alembic назначают по-своему."""
    _, _, body = definition.partition(" ON ")
    return _normalize_definition(body)


def diff(expected: dict, actual: dict) -> list[str]:
    """Чем `actual` отличается от эталона `expected`."""
    problems: list[str] = []

    missing_tables = sorted(set(expected["tables"]) - set(actual["tables"]))
    extra_tables = sorted(set(actual["tables"]) - set(expected["tables"]))
    for table in missing_tables:
        problems.append(f"ТАБЛИЦА ОТСУТСТВУЕТ: {table}")
    for table in extra_tables:
        problems.append(f"ЛИШНЯЯ ТАБЛИЦА: {table}")

    for table in sorted(set(expected["tables"]) & set(actual["tables"])):
        want = expected["columns"].get(table, {})
        have = actual["columns"].get(table, {})
        for column in sorted(set(want) - set(have)):
            problems.append(f"{table}.{column}: колонка отсутствует")
        for column in sorted(set(have) - set(want)):
            problems.append(f"{table}.{column}: лишняя колонка")
        for column in sorted(set(want) & set(have)):
            a, b = want[column], have[column]
            if a["type"] != b["type"]:
                problems.append(
                    f"{table}.{column}: тип {a['type']} -> {b['type']}"
                )
            if a["not_null"] != b["not_null"]:
                problems.append(
                    f"{table}.{column}: NOT NULL {a['not_null']} -> {b['not_null']}"
                )
            if _normalize_definition(a["default"] or "") != _normalize_definition(
                b["default"] or ""
            ):
                problems.append(
                    f"{table}.{column}: DEFAULT {a['default']!r} -> {b['default']!r}"
                )

        # Ограничения сравниваются по ОПРЕДЕЛЕНИЮ, а не по имени: имя может
        # отличаться (у Django своя схема именования), а смысл — не должен.
        want_c = {
            _normalize_definition(item["definition"])
            for item in expected["constraints"].get(table, {}).values()
        }
        have_c = {
            _normalize_definition(item["definition"])
            for item in actual["constraints"].get(table, {}).values()
        }
        for definition in sorted(want_c - have_c):
            problems.append(f"{table}: ОГРАНИЧЕНИЕ ОТСУТСТВУЕТ: {definition}")
        for definition in sorted(have_c - want_c):
            problems.append(f"{table}: лишнее ограничение: {definition}")

        want_i = {
            _index_body(d) for d in expected["indexes"].get(table, {}).values()
        }
        have_i = {_index_body(d) for d in actual["indexes"].get(table, {}).values()}
        for definition in sorted(want_i - have_i):
            problems.append(f"{table}: ИНДЕКС ОТСУТСТВУЕТ: {definition}")
        for definition in sorted(have_i - want_i):
            problems.append(f"{table}: лишний индекс: {definition}")

    for extension in sorted(set(expected["extensions"]) - set(actual["extensions"])):
        problems.append(f"РАСШИРЕНИЕ ОТСУТСТВУЕТ: {extension}")

    return problems


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    command = argv[1]
    if command == "dump":
        snapshot = dump(argv[2])
        with open(argv[3], "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        print(f"таблиц: {len(snapshot['tables'])}, файл: {argv[3]}")
        return 0

    if command in ("diff", "compare"):
        if command == "diff":
            with open(argv[2], encoding="utf-8") as handle:
                expected = json.load(handle)
            with open(argv[3], encoding="utf-8") as handle:
                actual = json.load(handle)
        else:
            expected = dump(argv[2])
            actual = dump(argv[3])

        problems = diff(expected, actual)
        if not problems:
            print(
                f"СХЕМЫ СОВПАДАЮТ: {len(expected['tables'])} бизнес-таблиц, "
                "расхождений нет"
            )
            return 0
        print(f"РАСХОЖДЕНИЙ: {len(problems)}")
        for problem in problems:
            print("  ", problem)
        return 1

    print(f"неизвестная команда: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
