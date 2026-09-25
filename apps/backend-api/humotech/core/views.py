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


# --- ответы на ошибки вне DRF --------------------------------------------
#
# Django по умолчанию отвечает на 400/403/404/500 HTML-страницей. Клиенты
# проекта — CRM, бот, Mini App — разбирают только JSON и ждут единого
# конверта `{"error": {"code", "message", "details"}}`. К тому же HTML-500
# при случайно включённом DEBUG — это трейсбек с настройками наружу.
# Поэтому здесь свои обработчики, и никаких подробностей в них нет: ни
# текста исключения, ни пути к файлу, ни запроса к базе. Подробности —
# в журнале сервера (`django.request` пишет их сам).


def _error_json(code: str, message: str, status: int) -> JsonResponse:
    return JsonResponse(
        {"error": {"code": code, "message": message, "details": None}},
        status=status,
        json_dumps_params={"ensure_ascii": False},
    )


def bad_request(request, exception=None) -> JsonResponse:
    return _error_json("bad_request", "Некорректный запрос", 400)


def permission_denied(request, exception=None) -> JsonResponse:
    return _error_json("forbidden", "Недостаточно прав", 403)


def page_not_found(request, exception=None) -> JsonResponse:
    return _error_json("not_found", "Не найдено", 404)


def server_error(request) -> JsonResponse:
    return _error_json("server_error", "Внутренняя ошибка сервера", 500)
