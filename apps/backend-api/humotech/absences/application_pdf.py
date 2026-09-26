"""Печатное заявление на отпуск или больничный.

Человек оформляет отсутствие в телефоне, но подписывает бумагу: заявление
с подписью — это то, что остаётся в кадровом деле и на что ссылаются в
споре. Набирать его заново в Word после того, как все данные уже введены
в заявке, — лишняя работа, в которой к тому же ошибаются.

Поэтому бланк собирается из самой заявки и отдаётся готовым: распечатать,
подписать, принести.

**Это не документ сотрудника.** Заявление порождает система, и считать
его приложенной справкой нельзя: иначе больничный, к которому не принесли
ни одной бумаги, выглядел бы подтверждённым — он сам себя и подтвердил
бы. Отсюда отдельный тип `APPLICATION` и правило: требование справки
смотрит только на документы, загруженные человеком.

**Про шрифт.** Во встроенных шрифтах PDF кириллицы нет: базовый набор
Type1 кодирует WinAnsi, и русский текст вышел бы пустыми прямоугольниками.
Поэтому нужен TTF с кириллицей. Он берётся из системы, а не кладётся в
репозиторий: полтора мегабайта бинарника в истории git — плата ни за что,
когда пакет ставится одной строкой в образе.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

logger = logging.getLogger("humotech.absences")

#: Вид документа для системного бланка. Требование справки смотрит
#: только на бумаги, принесённые человеком, и этот тип пропускает.
APPLICATION_DOCUMENT = "APPLICATION"

#: Внутреннее имя шрифта в документе.
FONT = "HumotechSans"
FONT_BOLD = "HumotechSans-Bold"

#: Где искать TTF с кириллицей. Порядок — от контейнера к машине
#: разработчика: в бою файл лежит в первом пути, локально — в одном из
#: последних. Список, а не одна константа: тест обязан проходить и на
#: Windows, где никакого DejaVu нет.
FONT_CANDIDATES = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
)

#: Поля страницы. Левое шире правого: подшитый в дело лист прокалывают
#: слева, и текст не должен уходить под дырокол.
LEFT = 25 * mm
RIGHT = 15 * mm
TOP = 20 * mm
BOTTOM = 20 * mm

#: Как называется заявление по виду отсутствия.
TITLES = {
    "SICK_LEAVE": "Заявление о нетрудоспособности",
    "ANNUAL_LEAVE": "Заявление о предоставлении ежегодного отпуска",
    "UNPAID_LEAVE": "Заявление о предоставлении отпуска без сохранения заработной платы",
}
DEFAULT_TITLE = "Заявление об отсутствии"

MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


@dataclass(frozen=True)
class Application:
    """Всё, что попадает в бланк. Ничего сверх этого бланк не знает."""

    organization: str
    employee_name: str
    position: str | None
    department: str | None
    office: str | None
    absence_name: str
    absence_code: str
    #: Период. `None` — человек подал заявление, не зная дат: он
    #: впишет их от руки, когда получит выписку.
    first_day: date | None
    last_day: date | None
    days: int | None
    comment: str | None
    #: Номер заявки для ссылки в кадровом деле.
    number: str


class FontMissing(RuntimeError):
    """В системе нет шрифта с кириллицей."""


def register_font() -> None:
    """Найти и зарегистрировать шрифт. Повторный вызов ничего не делает."""
    if FONT in pdfmetrics.getRegisteredFontNames():
        return

    for regular, bold in FONT_CANDIDATES:
        if not Path(regular).exists():
            continue
        pdfmetrics.registerFont(TTFont(FONT, regular))
        # Жирное начертание необязательно: без него заголовок просто
        # будет обычным, и это лучше, чем отказ собрать заявление.
        pdfmetrics.registerFont(
            TTFont(FONT_BOLD, bold if Path(bold).exists() else regular)
        )
        return

    raise FontMissing(
        "Не найден шрифт с кириллицей. В образе его ставит пакет "
        "fonts-dejavu-core; проверьте " + FONT_CANDIDATES[0][0]
    )


def human_date(value: date) -> str:
    """«14 марта 2026 г.» — как пишут в заявлении, а не «2026-03-14»."""
    return f"{value.day} {MONTHS[value.month - 1]} {value.year} г."


def plural_days(count: int) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} календарных дней"
    match count % 10:
        case 1:
            return f"{count} календарный день"
        case 2 | 3 | 4:
            return f"{count} календарных дня"
        case _:
            return f"{count} календарных дней"


def build(application: Application, *, today: date | None = None) -> bytes:
    """Собрать PDF. Возвращает готовый файл байтами."""
    register_font()

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    page.setTitle(f"{TITLES.get(application.absence_code, DEFAULT_TITLE)} "
                  f"№{application.number}")
    width, height = A4
    right_edge = width - RIGHT

    # Шапка справа: кому и от кого. Так пишут заявления на бумаге, и
    # отступление от этой формы сразу видно кадровику.
    y = height - TOP
    page.setFont(FONT, 11)
    for line in _header_lines(application):
        page.drawRightString(right_edge, y, line)
        y -= 6 * mm

    y -= 8 * mm
    page.setFont(FONT_BOLD, 14)
    title = TITLES.get(application.absence_code, DEFAULT_TITLE)
    page.drawCentredString(width / 2, y, title)

    y -= 12 * mm
    page.setFont(FONT, 11)
    for line in _body_lines(application):
        if not line:
            y -= 4 * mm
            continue
        for part in _wrap(page, line, right_edge - LEFT, FONT, 11):
            # Длинная причина (до 2000 знаков от сотрудника) раньше
            # уходила ниже края листа и наезжала на строку подписи. Не
            # влезает над местом подписи — новая страница.
            if y < BOTTOM + 45 * mm:
                page.showPage()
                page.setFont(FONT, 11)
                y = height - TOP
            page.drawString(LEFT, y, part)
            y -= 6 * mm

    # Подпись прижата к низу: между текстом и подписью на бумаге всегда
    # остаётся место, и заявление на треть листа выглядит черновиком.
    y = max(y - 10 * mm, BOTTOM + 30 * mm)
    page.setFont(FONT, 11)
    page.drawString(LEFT, y, f"Дата: {human_date(today or date.today())}")
    page.drawRightString(right_edge, y, "Подпись: ____________________")

    y -= 10 * mm
    page.setFont(FONT, 8)
    # Служебная строка внизу: по ней заявление на столе связывают с
    # заявкой в системе, не спрашивая сотрудника.
    page.drawString(LEFT, y, f"Заявка № {application.number}. "
                             f"Сформировано системой HUMOTECH.")

    page.showPage()
    page.save()
    return buffer.getvalue()


def _header_lines(application: Application) -> list[str]:
    lines = [f"В {application.organization}"]
    if application.office:
        lines.append(application.office)
    lines.append(f"от {application.employee_name}")
    if application.position:
        lines.append(application.position)
    if application.department:
        lines.append(application.department)
    return lines


#: Место под дату, которую впишут от руки. Подчёркивание, а не пропуск:
#: пустое место в бумаге читается как забытое поле, а линия — как то,
#: что заполняют ручкой.
BLANK_DATE = "«____» ______________ 20___ г."


def _body_lines(application: Application) -> list[str]:
    known = application.first_day is not None and application.last_day is not None
    if known:
        same_day = application.first_day == application.last_day
        when = (
            f"{human_date(application.first_day)}"
            if same_day
            else f"с {human_date(application.first_day)} "
                 f"по {human_date(application.last_day)}"
        )
        how_long = f" ({plural_days(application.days)})"
    else:
        # Даты вписывают от руки. Заявление всё равно нужно сейчас:
        # его несут в отдел кадров вместе со справкой, а справку
        # выдают в день выписки.
        when = f"с {BLANK_DATE} по {BLANK_DATE}"
        how_long = ""

    if application.absence_code == "SICK_LEAVE":
        first = (
            f"Прошу считать период {when}"
            f"{how_long} периодом временной "
            "нетрудоспособности."
        )
        second = (
            "Листок нетрудоспособности обязуюсь предоставить в отдел "
            "кадров."
        )
    else:
        first = (
            f"Прошу предоставить мне {application.absence_name.lower()} "
            f"{when}{how_long}."
        )
        second = ""

    lines = ["", first, ""]
    if second:
        lines += [second, ""]
    if application.comment:
        lines += [f"Причина: {application.comment}", ""]
    return lines


def _wrap(page, text: str, width: float, font: str, size: float) -> list[str]:
    """Перенос по словам. Длинное слово остаётся как есть.

    Резать слово посередине хуже, чем выпустить его за поле: в заявлении
    это фамилия или название отдела, и разорванное надвое оно читается
    как опечатка.
    """
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if page.stringWidth(candidate, font, size) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


__all__ = [
    "APPLICATION_DOCUMENT",
    "Application",
    "FONT",
    "FontMissing",
    "build",
    "human_date",
    "plural_days",
    "register_font",
]
