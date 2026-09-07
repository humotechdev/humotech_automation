"""Общие предохранители тестов бота.

Главный из них один: из тестов нельзя выйти в сеть. Бот — единственный
процесс проекта, который вообще умеет писать людям, и тест, случайно
дошедший до `api.telegram.org` с настоящим токеном из `.env`, отправит
сообщение по-настоящему.

Заглушка отправщика защищает только до тех пор, пока её не забыли
подставить. Этот предохранитель не зависит от аккуратности вызывающего:
он перехватывает системный вызов.
"""

from __future__ import annotations

import socket

import pytest


class RealNetworkCallBlocked(RuntimeError):
    """Тест попытался выйти в настоящую сеть."""


# Петлевой адрес разрешён: на нём поднимаются локальные заглушки HTTP.
ALLOWED = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(scope="session", autouse=True)
def no_real_network():
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _check(address) -> None:
        if not isinstance(address, tuple) or not address:
            return
        host = str(address[0])
        if host in ALLOWED:
            return
        raise RealNetworkCallBlocked(
            f"Тест попытался открыть соединение с {host}. Из тестов бота "
            "наружу не ходят: настоящая отправка проверяется не тестами."
        )

    def guarded_connect(self, address, *args, **kwargs):
        _check(address)
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        _check(address)
        return real_connect_ex(self, address, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
