"""Проверки живости и готовности.

`healthz` отвечает, что процесс жив. `readyz` — что он способен обслуживать
запросы: есть база и установлены расширения, без которых схема не работает.
Разделение нужно оркестратору: перезапускать процесс из-за недоступной базы
бессмысленно, а выводить его из балансировки — осмысленно.
"""

from __future__ import annotations

from django.db import connection
from django.http import JsonResponse

REQUIRED_EXTENSIONS = ("btree_gist", "vector")


def healthz(request) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def readyz(request) -> JsonResponse:
    issues: list[str] = []
    extensions: list[str] = []
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT extname FROM pg_extension")
            extensions = sorted(row[0] for row in cursor.fetchall())
    except Exception as exc:  # noqa: BLE001 — наружу уходит только категория
        issues.append(f"база недоступна: {type(exc).__name__}")

    for name in REQUIRED_EXTENSIONS:
        if extensions and name not in extensions:
            issues.append(f"нет расширения {name}")

    return JsonResponse(
        {
            "status": "ok" if not issues else "degraded",
            "database": not issues or "база недоступна" not in issues[0],
            "extensions": extensions,
            "issues": issues,
        },
        status=200 if not issues else 503,
    )
