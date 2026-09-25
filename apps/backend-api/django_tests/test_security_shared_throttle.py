"""Пределы DRF считаются одним счётчиком на все воркеры.

Раньше `ScopedRateThrottle` и `EmployeeRateThrottle` хранили историю
в LocMem — своём у каждого процесса gunicorn, и предел «10 в минуту»
при трёх воркерах пропускал 30. Здесь «воркеры» — независимые экземпляры
класса: общего у них только база.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from humotech.core.throttling import SharedScopedRateThrottle

pytestmark = pytest.mark.django_db


def _request(ip: str = "203.0.113.7"):
    request = Request(APIRequestFactory().post("/x", REMOTE_ADDR=ip))
    request.user = AnonymousUser()
    return request


def _worker():
    # Каждый воркер — свой экземпляр, как в отдельном процессе.
    return SharedScopedRateThrottle()


def test_limit_is_shared_between_workers(settings):
    view = SimpleNamespace(throttle_scope="qr_display_pair")
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
                                   "qr_display_pair": "3/min"},
    }
    workers = [_worker() for _ in range(3)]
    allowed = []
    for attempt in range(9):
        throttle = workers[attempt % 3]
        # ScopedRateThrottle читает предел при allow_request: переустановим.
        throttle.THROTTLE_RATES = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        allowed.append(throttle.allow_request(_request(), view))
    assert allowed.count(True) == 3
    assert workers[0].wait() and workers[0].wait() > 0


def test_other_address_has_its_own_counter(settings):
    view = SimpleNamespace(throttle_scope="qr_display_pair")
    rates = {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "qr_display_pair": "1/min"}
    first, second = _worker(), _worker()
    first.THROTTLE_RATES = second.THROTTLE_RATES = rates
    assert first.allow_request(_request("203.0.113.7"), view)
    assert not first.allow_request(_request("203.0.113.7"), view)
    assert second.allow_request(_request("198.51.100.9"), view)
