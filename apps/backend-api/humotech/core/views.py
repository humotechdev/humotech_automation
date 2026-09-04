"""Проверки живости и готовности.

`healthz` отвечает, что процесс жив. `readyz` — что он способен обслуживать
запросы: есть база, установлены расширения, без которых схема не работает,
и применены все миграции. Разделение нужно оркестратору: перезапускать
процесс из-за недоступной базы бессмысленно, а выводить его из
балансировки — осмысленно.

Наружу уходит категория проблемы, но не подробности: имя базы, адрес
сервера и текст исключения драйвера — это разведданные, а страница
готовности по определению открыта тому, кто её опрашивает.
"""

from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse

REQUIRED_EXTENSIONS = ("btree_gist", "vector")


def healthz(request) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def _pending_migrations() -> int:
    """Сколько миграций ещё не применено к этой базе.

    Проверка отдельная от «база доступна» намеренно. Контейнер приложения
    поднимается после разового сервиса миграций, и если тот отработал
    вхолостую или откатился, база будет доступна, а схема — старая.
    Запросы к такой базе падают по одному, и разбирать их приходится по
    логам вместо того, чтобы не выпустить процесс в работу.
    """
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return len(executor.migration_plan(targets))


def readyz(request) -> JsonResponse:
    issues: list[str] = []
    extensions: list[str] = []
    database = True
    migrations_applied = False

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT extname FROM pg_extension")
            extensions = sorted(row[0] for row in cursor.fetchall())
    except Exception as exc:  # noqa: BLE001 — наружу уходит только категория
        database = False
        issues.append(f"база недоступна: {type(exc).__name__}")

    if database:
        for name in REQUIRED_EXTENSIONS:
            if name not in extensions:
                issues.append(f"нет расширения {name}")

        try:
            pending = _pending_migrations()
        except Exception as exc:  # noqa: BLE001
            issues.append(f"состояние миграций не читается: {type(exc).__name__}")
        else:
            migrations_applied = pending == 0
            if pending:
                issues.append(f"не применено миграций: {pending}")

    return JsonResponse(
        {
            "status": "ok" if not issues else "degraded",
            "database": database,
            "migrations_applied": migrations_applied,
            "extensions": extensions,
            "issues": issues,
        },
        status=200 if not issues else 503,
    )
