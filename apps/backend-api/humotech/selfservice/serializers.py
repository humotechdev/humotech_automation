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


class PeriodSerializer(serializers.Serializer):
    """Период статистики: либо готовое имя, либо две даты.

    Даты, а не моменты времени. «За сентябрь» — вопрос о календаре,
    и в каком поясе начался сентябрь, решает сервер по офису сотрудника,
    а не клиент по часам телефона.
    """

    PERIODS = ("today", "week", "month")
    MAX_DAYS = 366

    period = serializers.ChoiceField(choices=PERIODS, required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        if attrs.get("period"):
            return attrs
        if not (attrs.get("date_from") and attrs.get("date_to")):
            # Умолчание — текущий месяц: самый частый вопрос, и отвечать
            # на пустой запрос ошибкой было бы придирчиво.
            attrs["period"] = "month"
            return attrs
        if attrs["date_to"] < attrs["date_from"]:
            raise serializers.ValidationError(
                {"date_to": "Конец периода раньше начала"}
            )
        if (attrs["date_to"] - attrs["date_from"]).days + 1 > self.MAX_DAYS:
            # Предел не про удобство, а про нагрузку: запрос на десять лет
            # строит десять тысяч дневных записей на каждый вызов.
            raise serializers.ValidationError(
                {"date_to": "Период длиннее года — сузьте запрос"}
            )
        return attrs
