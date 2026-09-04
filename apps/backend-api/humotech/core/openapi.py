"""Правки генератора схемы, общие для всего REST-слоя.

Генератор выводит типы из ORM: смотрит на queryset набора действий и
берёт тип первичного ключа. У `ServiceViewSet` queryset-а нет намеренно —
он ходит в базу только через сервис, — поэтому генератор о типе `id`
ничего сказать не может и подставляет «строку», предупреждая об этом.

Строка вместо UUID в схеме — не косметика. По схеме генерируется
клиентский код, и `id: string` не отличает идентификатор от любого
другого текста: опечатка в переменной становится ошибкой времени
выполнения вместо ошибки компиляции.

Тип известен и без ORM: первичный ключ всех сущностей CRM — UUID
(`UUIDPrimaryKeyModel`), других не бывает. Здесь это и сообщается —
один раз на все наборы действий, а не отдельной пометкой на каждом.
"""

from __future__ import annotations

import uritemplate
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter


class ServiceViewSetAutoSchema(AutoSchema):
    """Схема, знающая, что `id` в пути — это UUID."""

    def get_override_parameters(self):
        inherited = super().get_override_parameters()
        # Только там, где `id` действительно есть в пути. Списку он не
        # принадлежит, и добавленный туда параметр пути дал бы схему,
        # которая не проходит собственную валидацию.
        path = getattr(self, "path", "") or ""
        if "id" not in uritemplate.variables(path):
            return inherited
        # Свой параметр идёт первым: явный `@extend_schema` на методе
        # обязан перекрывать это умолчание, а не наоборот.
        return [
            OpenApiParameter(
                "id",
                OpenApiTypes.UUID,
                OpenApiParameter.PATH,
                description="Идентификатор записи",
            ),
            *inherited,
        ]


__all__ = ["ServiceViewSetAutoSchema"]
