"""Живость и готовность.

Эти два адреса опрашивает Docker, и от их ответа зависит, запустятся ли
бот и рабочие процессы: `depends_on: service_healthy` не выпустит их,
пока backend не скажет «готов». Поэтому проверяется не только «отвечает
200», но и то, что готовность действительно чему-то соответствует.
"""

from __future__ import annotations

import pytest

from humotech.core import views


def test_liveness_answers_without_authentication(client):
    """Живость отвечает всем: её спрашивает оркестратор, а не человек."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readiness_confirms_database_extensions_and_migrations(client):
    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["migrations_applied"] is True
    assert body["issues"] == []
    for extension in views.REQUIRED_EXTENSIONS:
        assert extension in body["extensions"]


@pytest.mark.django_db
def test_pending_migrations_make_it_not_ready(client, monkeypatch):
    """База доступна, схема старая — это не готовность.

    Ровно так выглядит откатившийся или невыполненный сервис миграций:
    соединение есть, запросы к новым таблицам падают по одному.
    """
    monkeypatch.setattr(views, "_pending_migrations", lambda: 3)

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["migrations_applied"] is False
    assert "не применено миграций: 3" in body["issues"]


@pytest.mark.django_db
def test_readiness_never_tells_where_the_database_lives(client, monkeypatch):
    """Страница готовности открыта. Подробностей о базе в ней быть не должно.

    Имя базы, адрес сервера и текст исключения драйвера — это разведданные:
    по ним видно, что за СУБД, какой версии и под каким пользователем.
    """
    from django.db import connection

    def explode():
        raise RuntimeError(
            "could not connect to server at 10.0.0.7:5432, "
            "database humotech_django, user humotech"
        )

    monkeypatch.setattr(views, "_pending_migrations", explode)

    response = client.get("/readyz")
    text = response.content.decode("utf-8")

    settings_dict = connection.settings_dict
    for leak in (
        "10.0.0.7",
        settings_dict["NAME"],
        settings_dict["USER"],
        str(settings_dict.get("PASSWORD") or "нет-пароля-в-настройках"),
    ):
        assert leak not in text
    assert "RuntimeError" in text
