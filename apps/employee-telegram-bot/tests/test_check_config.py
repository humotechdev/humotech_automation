"""Проверка настроек: где http допустим, а где это утечка секрета.

Секрет бота — единственное, чем он доказывает backend, что он наш.
Уехав по открытому http через чужую сеть, он перестаёт быть секретом,
поэтому правило «только https» ослабляется ровно в одном месте: когда
запрос физически не покидает машину.
"""

from __future__ import annotations

import pytest

from src.check_config import _stays_on_this_machine


@pytest.mark.parametrize(
    "hostname",
    ["localhost", "127.0.0.1", "::1", "gateway", "backend", "humotech-backend"],
)
def test_local_and_container_names_are_internal(hostname):
    """Петлевой адрес и имя сервиса Docker — это одна машина.

    Имя без точки не может быть публичным DNS-именем: доменов первого
    уровня без точки в интернете нет, и такое имя резолвится только
    внутри сети контейнеров.
    """
    assert _stays_on_this_machine(hostname) is True


@pytest.mark.parametrize(
    "hostname",
    [
        "api.example.com",
        "example-stand.ngrok-free.dev",
        "10.0.0.7",
        "backend.local",
        "evil.com",
        None,
        "",
    ],
)
def test_everything_with_a_dot_is_external(hostname):
    """Послабление узкое: одна точка в имени — и правило снова строгое.

    Адрес в локальной сети (10.0.0.7) тоже внешний: «внутри офиса» не
    значит «внутри машины», и там секрет уже читается соседом.
    """
    assert _stays_on_this_machine(hostname) is False
