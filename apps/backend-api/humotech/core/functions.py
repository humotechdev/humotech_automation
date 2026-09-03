"""Функции PostgreSQL, для которых встроенный эквивалент Django не подходит."""

from __future__ import annotations

from django.contrib.postgres.search import SearchQueryField, SearchVectorField
from django.db.models import DateTimeField, FloatField, Func


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


class SimpleTsVector(Func):
    """`to_tsvector('simple', <выражение>)` — БЕЗ `COALESCE`.

    Встроенный `django.contrib.postgres.search.SearchVector` оборачивает
    колонку в `COALESCE(col, '')`. Для NOT NULL-колонки это ничего не меняет
    по смыслу, но планировщик сопоставляет выражение индекса ПОБУКВЕННО:
    GIN-индекс построен по `to_tsvector('simple', chunk_text)`, и версия
    с `COALESCE` под него не подойдёт — вместо индекса будет полный проход
    по таблице.

    Поэтому выражение задаётся точно таким же, каким создан индекс.
    """

    function = "to_tsvector"
    template = "%(function)s('simple', %(expressions)s)"
    output_field = SearchVectorField()


class SimpleTsQuery(Func):
    """`plainto_tsquery('simple', <текст>)` — запрос под тот же словарь.

    Словарь обязан совпадать со словарём индекса: `simple` не приводит слова
    к основам и не отбрасывает стоп-слова, что для смеси русского,
    таджикского и английского честнее, чем словарь одного языка.
    """

    function = "plainto_tsquery"
    template = "%(function)s('simple', %(expressions)s)"
    output_field = SearchQueryField()


class TsRank(Func):
    """`ts_rank(<вектор>, <запрос>)` — насколько документ подходит запросу.

    Значение ни к чему не нормировано и сравнимо только внутри одной выдачи,
    поэтому в гибридном поиске оно делится на максимум по этой же выдаче.
    """

    function = "ts_rank"
    output_field = FloatField()
