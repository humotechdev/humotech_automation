"""Адрес клиента — и почему по умолчанию заголовкам здесь не верят.

`X-Forwarded-For` дописывает кто угодно. Приложение, читающее его без
настроенного прокси, само сообщает тот адрес, который ему прислали, —
а проверка «сотрудник внутри офисной сети» держится ровно на этом адресе.
То есть непроверенное чтение заголовка не ослабляет проверку, а отменяет её.

Поэтому умолчание — ноль прокси: адрес берётся из соединения и ничем
не переопределяется. `TRUSTED_PROXY_COUNT` включает чтение заголовка, и
цифра в нём — это не «доверяем заголовку», а «перед нами ровно столько
прокси, каждый из которых дописывает адрес справа». Берётся адрес,
поставленный самым дальним из НАШИХ прокси: всё, что левее, написал клиент.
"""

from __future__ import annotations

import ipaddress

from django.conf import settings


def client_ip(request) -> str | None:
    """Адрес, которому можно верить настолько, насколько настроено."""
    remote = request.META.get("REMOTE_ADDR") or None
    trusted = int(getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0)
    if trusted <= 0:
        return _valid(remote)

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not chain:
        return _valid(remote)

    # Цепочка: [клиент, прокси1, прокси2, ...]. Наши — последние `trusted`
    # записей; адрес, который они видели, стоит прямо перед ними.
    index = len(chain) - trusted
    if index < 0:
        # Записей меньше, чем прокси: цепочка не та, что мы ожидали.
        # Возвращаемся к адресу соединения вместо того, чтобы гадать.
        return _valid(remote)
    return _valid(chain[index]) or _valid(remote)


def _valid(value: str | None) -> str | None:
    if not value:
        return None
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def address_in_networks(address: str | None, networks) -> bool:
    """Попадает ли адрес хотя бы в одну из сетей офиса."""
    if not address:
        return False
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False

    for network in networks:
        try:
            if parsed in ipaddress.ip_network(str(network), strict=False):
                return True
        except ValueError:
            # Кривая запись в справочнике сетей не должна ронять отметку —
            # она означает «эта сеть не подтверждает», а не «отказать всем».
            continue
    return False


__all__ = ["address_in_networks", "client_ip"]
