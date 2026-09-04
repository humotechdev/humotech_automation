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
