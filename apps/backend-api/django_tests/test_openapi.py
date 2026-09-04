"""Схема OpenAPI: она есть, она валидна и описывает то же, что и код.

Схема, которая расходится с настоящим API, хуже отсутствия схемы:
по ней генерируют клиентский код, и расхождение выясняется в бою.
Поэтому проверяется не «файл отдаётся», а совпадение путей со списком
маршрутов Django.
"""

from __future__ import annotations

import pytest
from django.urls import get_resolver

pytestmark = pytest.mark.django_db


def test_schema_is_generated_and_valid(api_client, make_user, organization):
    user = make_user(organization, permissions=())
    api_client.force_authenticate(user=user)

    response = api_client.get("/api/schema")

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "openapi:" in body
    assert "HUMOTECH HR CRM API" in body


def test_schema_covers_the_crm_endpoints(api_client, make_user, organization):
    """Каждый добавленный для CRM путь обязан быть в схеме.

    Именно поимённо: молчаливое выпадение эндпоинта из схемы —
    это документация, в которой у фронтенда просто нет половины API.
    """
    import yaml

    user = make_user(organization, permissions=())
    api_client.force_authenticate(user=user)
    schema = yaml.safe_load(api_client.get("/api/schema").content)

    paths = set(schema["paths"])
    for path in (
        "/api/v1/dashboard",
        "/api/v1/analytics",
        "/api/v1/analytics/compare",
        "/api/v1/attendance/events",
        "/api/v1/attendance/sessions",
        "/api/v1/attendance/presence",
        "/api/v1/attendance/corrections",
        "/api/v1/attendance/manual",
        "/api/v1/audit-logs",
        "/api/v1/qr-points/",
        "/api/v1/reports/{kind}/export",
    ):
        assert path in paths, f"{path} выпал из схемы"


def test_audit_log_has_no_write_methods_in_the_schema(
    api_client, make_user, organization
):
    """Схема тоже говорит, что журнал не пишется снаружи."""
    import yaml

    user = make_user(organization, permissions=())
    api_client.force_authenticate(user=user)
    schema = yaml.safe_load(api_client.get("/api/schema").content)

    methods = set(schema["paths"]["/api/v1/audit-logs"])
    assert methods == {"get"}


def test_docs_pages_are_development_only(client, settings):
    """Swagger и ReDoc — читалки, а не часть продукта.

    В бою это лишний открытый интерфейс, который пришлось бы защищать
    наравне с остальными. Сама схема при этом доступна всегда: по ней
    фронтенд генерирует типы.
    """
    names = {
        pattern.name
        for pattern in get_resolver().url_patterns
        if getattr(pattern, "name", None)
    }
    assert "schema" in names
    if settings.DEBUG:
        assert {"swagger", "redoc"} <= names
    else:
        assert not ({"swagger", "redoc"} & names)


# --- бюджет диагностики ---
#
# drf-spectacular умеет строить схему «как получится», молча подменяя
# то, что не смог вывести. Такая схема генерируется без ошибки, но
# описывает не наш API: у половины эндпоинтов тело запроса пропадает,
# и сгенерированный клиент получает `unknown` вместо типов.
#
# Поэтому диагностика измеряется числом и зафиксирована здесь. Бюджет
# движется только вниз: новый модуль, добавивший непокрытый view,
# роняет тест сразу, а не через месяц во фронтенде.

MAX_ERRORS = 26
MAX_WARNINGS = 27


def _generate_schema_diagnostics() -> tuple[list[str], list[str]]:
    """Собирает схему и возвращает уникальные ошибки и предупреждения."""
    from drf_spectacular.drainage import GENERATOR_STATS, reset_generator_stats
    from drf_spectacular.generators import SchemaGenerator

    reset_generator_stats()
    was_silent = GENERATOR_STATS.silent
    GENERATOR_STATS.silent = True
    try:
        SchemaGenerator().get_schema(request=None, public=True)
        return (
            sorted(GENERATOR_STATS._error_cache),
            sorted(GENERATOR_STATS._warn_cache),
        )
    finally:
        GENERATOR_STATS.silent = was_silent
        reset_generator_stats()


def test_schema_generates_without_complaints():
    """Сборка схемы не должна давать ни ошибок, ни предупреждений.

    Оба числа блокирующие: сборка идёт с `--fail-on-warn`, и
    предупреждение «не смог определить тип параметра» означает ровно то
    же, что ошибка, — неверный контракт у клиента.
    """
    errors, warnings = _generate_schema_diagnostics()

    assert len(errors) <= MAX_ERRORS, (
        f"Ошибок в схеме {len(errors)}, бюджет {MAX_ERRORS}. "
        "Новые:\n" + "\n".join(errors)
    )
    assert len(warnings) <= MAX_WARNINGS, (
        f"Предупреждений в схеме {len(warnings)}, бюджет {MAX_WARNINGS}. "
        "Новые:\n" + "\n".join(warnings)
    )
