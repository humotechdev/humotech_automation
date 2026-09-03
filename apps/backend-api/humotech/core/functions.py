"""Функции PostgreSQL, для которых встроенный эквивалент Django не подходит."""

from __future__ import annotations

from django.db.models import DateTimeField, Func


class TransactionNow(Func):
    """`now()` — метка времени ТРАНЗАКЦИИ.

    Встроенный `django.db.models.functions.Now` на PostgreSQL превращается
    в `statement_timestamp()`, а это другое значение: оно меняется на каждом
    операторе внутри транзакции. Разница видна сразу:

      * `now()` — все строки, вставленные одной транзакцией, получают
        одинаковый `created_at`;
      * `statement_timestamp()` — у каждой строки своё время.

    В прежней схеме везде стоял `now()`, и на это опирается постраничный вывод:
    ключ пагинации — пара `(created_at, id)` именно потому, что время у пачки
    строк совпадает. Замена на `statement_timestamp()` тихо изменила бы данные,
    а не только текст DDL.
    """

    function = "now"
    template = "%(function)s()"
    arity = 0
    output_field = DateTimeField()
