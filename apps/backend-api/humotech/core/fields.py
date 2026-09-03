"""Типы колонок, которых нет во встроенном наборе Django.

Единственный такой тип — `cidr`. `inet` покрывается `GenericIPAddressField`,
`vector` — полем из `pgvector.django`, JSONB и UUID есть в самом Django.
"""

from __future__ import annotations

import ipaddress

from django.core.exceptions import ValidationError
from django.db import models


class CidrField(models.Field):
    """Сеть в нотации CIDR — тип PostgreSQL `cidr`.

    От `inet` отличается тем, что хранит именно СЕТЬ: PostgreSQL запрещает
    ненулевые биты правее маски, поэтому `192.168.1.5/24` будет отвергнут,
    а `192.168.1.0/24` принят. Для «разрешённых сетей офиса» это ровно то,
    что нужно: адрес одного устройства сюда попасть не должен.

    Встроенного поля для `cidr` в Django нет, а `GenericIPAddressField`
    даёт `inet` и такой проверки не выполняет.
    """

    description = "Подсеть IPv4 или IPv6 (PostgreSQL cidr)"
    empty_strings_allowed = False

    def db_type(self, connection) -> str:
        if connection.vendor != "postgresql":
            raise NotImplementedError(
                "CidrField существует только в PostgreSQL: тип cidr есть "
                "лишь в нём, а подменять его строкой значило бы потерять "
                "проверку корректности сети на уровне базы"
            )
        return "cidr"

    def to_python(self, value):
        if value is None or isinstance(
            value, (ipaddress.IPv4Network, ipaddress.IPv6Network)
        ):
            return value
        try:
            return ipaddress.ip_network(str(value), strict=True)
        except ValueError as exc:
            raise ValidationError(
                f"«{value}» не является корректной сетью CIDR: {exc}",
                code="invalid_cidr",
            ) from exc

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return ipaddress.ip_network(str(value), strict=False)

    def get_prep_value(self, value):
        if value is None:
            return None
        return str(self.to_python(value))
