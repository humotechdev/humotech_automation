"""Выгрузка данных в CSV и XLSX.

Три правила, из которых сделан этот модуль.

**Область видимости не обходится выгрузкой.** Каждый отчёт собирается тем
же сервисом, что и экран: те же проверки прав, та же область. Отдельный
запрос «в обход, зато быстро» — это способ выдать региональному кадровику
всю компанию одним файлом.

**Таблица не грузится в память целиком.** CSV отдаётся потоком:
`StreamingHttpResponse` забирает строки по одной, и полумиллионная
выгрузка занимает столько же памяти, сколько десять строк. XLSX так не
умеет — формат требует собрать книгу целиком, — поэтому у него жёсткий
предел строк, и при превышении честно предлагается CSV, а не молча
обрезается хвост.

**Файл объясняет сам себя.** Первые строки любого отчёта — это период,
часовой пояс, фильтры, момент формирования, автор и формулы процентов.
Через месяц никто не вспомнит, за какие даты выгружен файл `report.csv`,
а решения по нему принимать будут.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Iterator

from humotech.core.errors import ValidationFailed

#: Больше этого числа строк XLSX не собирает: книга целиком лежит
#: в памяти, и на выгрузке за год это десятки мегабайт на один запрос.
XLSX_MAX_ROWS = 50_000


@dataclass(frozen=True)
class Sheet:
    """Готовая к выгрузке таблица вместе со своим происхождением."""

    title: str
    columns: list[str]
    rows: Iterable[list]
    #: Пары «что» → «значение»: период, пояс, фильтры, автор, формулы.
    meta: list[tuple[str, str]]


class Echo:
    """Приёмник, который ничего не хранит.

    `csv.writer` умеет писать только в объект с `write`. Этот возвращает
    строку вызывающему вместо накопления — без него генератор пришлось бы
    заменить на список, то есть на ту самую загрузку таблицы в память,
    от которой мы уходим.
    """

    def write(self, value: str) -> str:
        return value


def to_csv(sheet: Sheet) -> Iterator[str]:
    """Строки CSV по одной, без сборки файла целиком."""
    writer = csv.writer(Echo(), delimiter=";", lineterminator="\r\n")

    # BOM: Excel по-русски без него читает UTF-8 как cp1251 и показывает
    # «ÐŸÐµÑ€Ð¸Ð¾Ð´» вместо «Период».
    yield "﻿"

    for key, value in sheet.meta:
        yield writer.writerow([key, value])
    yield writer.writerow([])
    yield writer.writerow(sheet.columns)
    for row in sheet.rows:
        yield writer.writerow([_cell(value) for value in row])


def to_xlsx(sheet: Sheet) -> bytes:
    """Книга XLSX целиком.

    Потоковой записи у формата нет, поэтому есть предел строк: молча
    обрезать хвост нельзя — файл выглядел бы полным, а решения по нему
    принимали бы неверные.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    rows = list(sheet.rows)
    if len(rows) > XLSX_MAX_ROWS:
        raise ValidationFailed(
            f"В XLSX помещается не более {XLSX_MAX_ROWS} строк; "
            f"в выгрузке их {len(rows)}. Возьмите период короче "
            f"или формат CSV — он отдаётся потоком без ограничения.",
            details={"rows": len(rows), "limit": XLSX_MAX_ROWS,
                     "suggested_format": "csv"},
        )

    book = Workbook()
    page = book.active
    page.title = sheet.title[:31] or "Отчёт"  # Excel не берёт длиннее

    for key, value in sheet.meta:
        page.append([key, value])
    page.append([])

    header_at = page.max_row + 1
    page.append(sheet.columns)
    for cell in page[header_at]:
        cell.font = Font(bold=True)

    for row in rows:
        page.append([_cell(value) for value in row])

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def base_meta(
    *,
    title: str,
    author: str,
    filters: dict,
    period: tuple[date, date] | None = None,
    timezone: str | None = None,
    generated_at: datetime | None = None,
) -> list[tuple[str, str]]:
    """Шапка отчёта: за что, за когда, по чьему запросу и по каким правилам.

    Автор указывается всегда. Выгрузка персональных данных — действие,
    у которого должен быть человек: файл уходит из системы и живёт
    дальше своей жизнью в почте и мессенджерах.
    """
    meta: list[tuple[str, str]] = [("Отчёт", title)]
    if period:
        meta.append(("Период", f"{period[0].isoformat()} — {period[1].isoformat()}"))
    if timezone:
        meta.append(("Часовой пояс", timezone))
    meta.append(
        (
            "Сформирован",
            (generated_at or _now()).isoformat(timespec="seconds"),
        )
    )
    meta.append(("Автор выгрузки", author))
    for key, value in filters.items():
        if value not in (None, ""):
            meta.append((f"Фильтр: {key}", str(value)))
    return meta


def formula_meta(ratios) -> list[tuple[str, str]]:
    """Определения процентов — в самом файле, а не в переписке.

    Через месяц спор «а как считалась эта цифра» разрешается открытием
    файла, а не поиском того, кто его делал.
    """
    return [
        (
            f"Формула: {ratio.title}",
            f"{ratio.formula} = {ratio.numerator} / {ratio.denominator}"
            + (f" = {ratio.percent}%" if ratio.percent is not None else " = нет данных"),
        )
        for ratio in ratios
    ]


def _cell(value):
    """Как значение выглядит в файле.

    `None` становится пустой клеткой, а не строкой «None»: пустая клетка
    читается как «нет данных», а «None» — как чей-то недосмотр, и оба
    раза это разные выводы.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _now() -> datetime:
    from django.utils import timezone as django_timezone

    return django_timezone.now()


__all__ = [
    "Sheet",
    "XLSX_MAX_ROWS",
    "base_meta",
    "formula_meta",
    "to_csv",
    "to_xlsx",
]
