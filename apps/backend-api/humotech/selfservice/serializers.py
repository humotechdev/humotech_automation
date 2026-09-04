"""Разбор входных данных личного кабинета.

Здесь важнее то, чего сериализаторы НЕ принимают. Ни `employee_id`, ни
`organization_id`, ни `office_id`, ни направление отметки, ни её время:
всё это сервер определяет сам, и поле, которого нет в сериализаторе,
невозможно подсунуть даже при ошибке во view.
"""

from __future__ import annotations

from rest_framework import serializers


class ScanRequestSerializer(serializers.Serializer):
    """Один отсканированный код.

    `client_event_id` — идентификатор попытки со стороны клиента. Нужен
    против двойной отправки: телефон в вебвью легко отправляет один и тот
    же скан дважды при подрагивании кнопки или обрыве ответа.
    """

    token = serializers.CharField(max_length=512, trim_whitespace=True)
    client_event_id = serializers.CharField(
        max_length=100, required=False, allow_blank=False
    )
