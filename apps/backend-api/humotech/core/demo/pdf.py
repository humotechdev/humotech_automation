"""Сборка настоящих PDF для демонстрационных бумаг.

Зачем свой сборщик, а не библиотека. В зависимостях backend нет ни
`reportlab`, ни `fpdf`, и тянуть их в боевую поставку ради стенда
неправильно: это код, который в бою не выполняется ни разу. Здесь
собирается ровно то подмножество PDF, которое нужно витрине, — одна
страница A4, кириллический текст и водяной знак.

Почему это не «текстовый файл с расширением .pdf». Документ собирается
по структуре формата: объекты, таблица перекрёстных ссылок, каталог,
дерево страниц, поток содержимого. Шрифт вкладывается внутрь файла
(`FontFile2`), поэтому кириллица не зависит от того, что установлено у
читателя.

Кодировка `Identity-H`: в потоке содержимого лежат номера глифов, а не
байты текста. Иначе кириллицу пришлось бы укладывать в однобайтовую
кодировку, где её нет, и читалка показала бы пустые прямоугольники.
Рядом кладётся обратная таблица `ToUnicode` — по ней текст извлекается
и копируется, и по ней же проверки читают, что написано в файле.

Ничего настоящего в этих бумагах нет: ни подписей, ни печатей, ни
номеров государственных систем. На каждой странице стоит водяной знак,
прямо говорящий, что документ демонстрационный.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

# Размер листа A4 в пунктах PDF (1/72 дюйма). 210 × 297 мм.
A4_WIDTH = 595.28
A4_HEIGHT = 841.89

#: Что стоит на каждой странице по диагонали.
WATERMARK = "ДЕМО — НЕ ЯВЛЯЕТСЯ ОФИЦИАЛЬНЫМ ДОКУМЕНТОМ"

FONT_PATH = Path(__file__).resolve().parent.parent / "demo_assets" / "fonts" / "DejaVuSans.ttf"


# --- разбор шрифта -----------------------------------------------------------


@dataclass
class TrueTypeFont:
    """Ровно то, что нужно знать о шрифте, чтобы вложить его в PDF.

    Полный разбор TrueType здесь не нужен и не делается: читаются пять
    таблиц. `cmap` даёт соответствие символа номеру глифа, `hmtx` —
    ширины, `head` и `hhea` — метрики для описателя шрифта.
    """

    data: bytes
    units_per_em: int
    num_glyphs: int
    char_to_glyph: dict[int, int]
    widths: dict[int, int]
    bbox: tuple[int, int, int, int]
    ascent: int
    descent: int

    def glyphs(self, text: str) -> list[int]:
        """Номера глифов для строки. Незнакомый символ даёт нулевой глиф."""
        return [self.char_to_glyph.get(ord(ch), 0) for ch in text]

    def width(self, text: str) -> float:
        """Ширина строки в единицах 1/1000 кегля — как их считает PDF."""
        total = 0
        for glyph in self.glyphs(text):
            total += self.widths.get(glyph, self.units_per_em // 2)
        return total * 1000 / self.units_per_em


def load_font(path: Path | None = None) -> TrueTypeFont:
    data = (path or FONT_PATH).read_bytes()
    tables = _table_directory(data)

    head = tables["head"]
    units_per_em = struct.unpack_from(">H", data, head + 18)[0]
    bbox = struct.unpack_from(">hhhh", data, head + 36)

    maxp = tables["maxp"]
    num_glyphs = struct.unpack_from(">H", data, maxp + 4)[0]

    hhea = tables["hhea"]
    ascent, descent = struct.unpack_from(">hh", data, hhea + 4)
    num_h_metrics = struct.unpack_from(">H", data, hhea + 34)[0]

    widths: dict[int, int] = {}
    hmtx = tables["hmtx"]
    last = 0
    for glyph in range(num_glyphs):
        if glyph < num_h_metrics:
            last = struct.unpack_from(">H", data, hmtx + glyph * 4)[0]
        # После `numberOfHMetrics` ширины не хранятся: у оставшихся
        # глифов она та же, что у последнего записанного.
        widths[glyph] = last

    return TrueTypeFont(
        data=data,
        units_per_em=units_per_em,
        num_glyphs=num_glyphs,
        char_to_glyph=_cmap(data, tables["cmap"]),
        widths=widths,
        bbox=bbox,
        ascent=ascent,
        descent=descent,
    )


def _table_directory(data: bytes) -> dict[str, int]:
    count = struct.unpack_from(">H", data, 4)[0]
    tables: dict[str, int] = {}
    for index in range(count):
        at = 12 + index * 16
        tag = data[at:at + 4].decode("latin-1")
        offset = struct.unpack_from(">I", data, at + 8)[0]
        tables[tag] = offset
    missing = {"head", "hhea", "hmtx", "maxp", "cmap"} - set(tables)
    if missing:
        raise ValueError(f"В шрифте нет таблиц: {sorted(missing)}")
    return tables


def _cmap(data: bytes, offset: int) -> dict[int, int]:
    """Соответствие «символ Unicode → номер глифа».

    Берётся подтаблица формата 4 для Unicode. Формат 12 (за пределами
    базовой плоскости) витрине не нужен: ни кириллица, ни латиница,
    ни знаки препинания за её границы не выходят.
    """
    count = struct.unpack_from(">H", data, offset + 2)[0]
    chosen: int | None = None
    for index in range(count):
        at = offset + 4 + index * 8
        platform, encoding, sub = struct.unpack_from(">HHI", data, at)
        if (platform, encoding) in ((3, 1), (0, 3), (0, 4), (3, 10), (0, 6)):
            candidate = offset + sub
            if struct.unpack_from(">H", data, candidate)[0] == 4:
                chosen = candidate
                break
    if chosen is None:
        raise ValueError("В шрифте нет юникодной таблицы символов формата 4")

    seg_x2 = struct.unpack_from(">H", data, chosen + 6)[0]
    segments = seg_x2 // 2
    ends = struct.unpack_from(f">{segments}H", data, chosen + 14)
    starts_at = chosen + 16 + seg_x2
    starts = struct.unpack_from(f">{segments}H", data, starts_at)
    deltas = struct.unpack_from(f">{segments}h", data, starts_at + seg_x2)
    range_at = starts_at + seg_x2 * 2
    offsets = struct.unpack_from(f">{segments}H", data, range_at)

    table: dict[int, int] = {}
    for index in range(segments):
        start, end = starts[index], ends[index]
        if start > end or end == 0xFFFF and start == 0xFFFF:
            continue
        for code in range(start, end + 1):
            if offsets[index] == 0:
                glyph = (code + deltas[index]) & 0xFFFF
            else:
                at = range_at + index * 2 + offsets[index] + (code - start) * 2
                if at + 2 > len(data):
                    continue
                glyph = struct.unpack_from(">H", data, at)[0]
                if glyph:
                    glyph = (glyph + deltas[index]) & 0xFFFF
            if glyph:
                table[code] = glyph
    return table


# --- страница ----------------------------------------------------------------


@dataclass
class Paper:
    """Содержимое одной демонстрационной бумаги.

    Разметка нарочно простая: заголовок, строки и подпись. Ничего
    похожего на настоящий бланк здесь быть не должно — это образец для
    показа интерфейса, а не документ.
    """

    title: str
    lines: list[str] = field(default_factory=list)
    #: Пары «поле — значение». Печатаются в две колонки.
    fields: list[tuple[str, str]] = field(default_factory=list)
    #: Место для подписи от руки: процесс предполагает печать и подпись.
    signature: str = "Подпись: __________________"
    footer: str = "Документ создан для демонстрации интерфейса HUMOTECH."


def build(paper: Paper, font: TrueTypeFont | None = None) -> bytes:
    """Собрать однастраничный PDF формата A4."""
    font = font or load_font()
    content = _content_stream(paper, font)
    return _document(content, font)


def _escape(text: str, font: TrueTypeFont) -> str:
    """Строка в виде шестнадцатеричных номеров глифов — как требует Identity-H."""
    return "".join(f"{glyph:04X}" for glyph in font.glyphs(text))


def _content_stream(paper: Paper, font: TrueTypeFont) -> bytes:
    out: list[str] = []
    left = 60.0
    top = A4_HEIGHT - 80.0

    # Водяной знак кладётся ПЕРВЫМ и под текстом: поверх он мешал бы
    # читать, а смысл его — пометить лист, а не закрыть его.
    out.append("q")
    out.append("/GS0 gs")
    out.append("0.85 0.25 0.20 rg")
    size = 26.0
    width = font.width(WATERMARK) * size / 1000
    # Диагональ листа: поворот на 35 градусов, начало подобрано так,
    # чтобы надпись шла через середину страницы.
    out.append("BT")
    out.append(f"/F1 {size:.2f} Tf")
    out.append(
        "0.81915 0.57358 -0.57358 0.81915 "
        f"{(A4_WIDTH - width * 0.81915) / 2:.2f} {A4_HEIGHT / 2 - width * 0.28:.2f} Tm"
    )
    out.append(f"<{_escape(WATERMARK, font)}> Tj")
    out.append("ET")
    out.append("Q")

    def line(text: str, size: float, y: float, *, gray: float = 0.0) -> None:
        out.append("BT")
        out.append(f"{gray:.2f} g")
        out.append(f"/F1 {size:.2f} Tf")
        out.append(f"1 0 0 1 {left:.2f} {y:.2f} Tm")
        out.append(f"<{_escape(text, font)}> Tj")
        out.append("ET")

    y = top
    line("HUMOTECH · демонстрационный стенд", 9, y, gray=0.45)
    y -= 34
    line(paper.title, 16, y)
    y -= 10
    out.append(f"0.75 g {left:.2f} {y:.2f} m {A4_WIDTH - left:.2f} {y:.2f} l S")
    y -= 28

    for name, value in paper.fields:
        line(name, 10, y, gray=0.45)
        out.append("BT")
        out.append("0 g")
        out.append("/F1 10.50 Tf")
        out.append(f"1 0 0 1 {left + 180:.2f} {y:.2f} Tm")
        out.append(f"<{_escape(value, font)}> Tj")
        out.append("ET")
        y -= 20

    if paper.fields:
        y -= 10
    for text in paper.lines:
        line(text, 10.5, y)
        y -= 18

    y -= 40
    line(paper.signature, 10.5, y)
    line(paper.footer, 8.5, 70, gray=0.5)

    return zlib.compress("\n".join(out).encode("utf-8"))


def _document(content: bytes, font: TrueTypeFont) -> bytes:
    """Сборка объектов, таблицы ссылок и трейлера."""
    used = sorted(set(font.widths) & set(range(font.num_glyphs)))
    scale = 1000 / font.units_per_em
    widths = " ".join(
        f"{glyph} [{round(font.widths[glyph] * scale)}]"
        for glyph in used
        if font.widths[glyph]
    )

    font_data = zlib.compress(font.data)
    to_unicode = zlib.compress(_to_unicode(font).encode("latin-1"))

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"<< /Type /Catalog /Pages 2 0 R >>")
    assert catalog == 1
    add(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add(
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {A4_WIDTH:.2f} {A4_HEIGHT:.2f}] "
        "/Resources << /Font << /F1 5 0 R >> /ExtGState << /GS0 4 0 R >> >> "
        "/Contents 9 0 R >>".encode("latin-1")
    )
    # Полупрозрачность водяного знака задаётся состоянием, а не цветом:
    # цветом её не задать, а рисовать бледным «на глаз» — значит менять
    # оттенок вместе с фоном.
    add(b"<< /Type /ExtGState /ca 0.12 /CA 0.12 >>")
    add(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /DejaVuSans "
        b"/Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 10 0 R >>"
    )
    add(
        f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /DejaVuSans "
        f"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        f"/FontDescriptor 7 0 R /DW 1000 /W [{widths}] /CIDToGIDMap /Identity >>"
        .encode("latin-1")
    )
    add(
        "<< /Type /FontDescriptor /FontName /DejaVuSans /Flags 32 "
        f"/FontBBox [{round(font.bbox[0] * scale)} {round(font.bbox[1] * scale)} "
        f"{round(font.bbox[2] * scale)} {round(font.bbox[3] * scale)}] "
        f"/ItalicAngle 0 /Ascent {round(font.ascent * scale)} "
        f"/Descent {round(font.descent * scale)} /CapHeight {round(font.ascent * scale)} "
        "/StemV 80 /FontFile2 8 0 R >>".encode("latin-1")
    )
    add(
        f"<< /Length {len(font_data)} /Length1 {len(font.data)} /Filter /FlateDecode >>"
        .encode("latin-1") + b"\nstream\n" + font_data + b"\nendstream"
    )
    add(
        f"<< /Length {len(content)} /Filter /FlateDecode >>".encode("latin-1")
        + b"\nstream\n" + content + b"\nendstream"
    )
    add(
        f"<< /Length {len(to_unicode)} /Filter /FlateDecode >>".encode("latin-1")
        + b"\nstream\n" + to_unicode + b"\nendstream"
    )

    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    positions: list[int] = []
    for number, body in enumerate(objects, start=1):
        positions.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for at in positions:
        out += f"{at:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def _to_unicode(font: TrueTypeFont) -> str:
    """Обратная таблица: номер глифа → символ.

    Без неё текст в файле есть, а скопировать его нельзя: читалка видит
    номера глифов и не знает, каким буквам они соответствуют. Проверки
    читают написанное той же таблицей.
    """
    pairs = sorted((glyph, code) for code, glyph in font.char_to_glyph.items())
    chunks = []
    for at in range(0, len(pairs), 100):
        part = pairs[at:at + 100]
        body = "\n".join(f"<{g:04X}> <{c:04X}>" for g, c in part)
        chunks.append(f"{len(part)} beginbfchar\n{body}\nendbfchar")
    body = "\n".join(chunks)
    return (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CMapName /DejaVu-Identity-H def\n/CMapType 2 def\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{body}\n"
        "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    )


# --- чтение обратно ----------------------------------------------------------


def text_of(data: bytes, font: TrueTypeFont | None = None) -> str:
    """Весь текст страницы обратно в строку.

    Нужно проверкам: убедиться, что в бумаге стоит нужный период и
    водяной знак, можно только прочитав написанное. Читается тот же
    поток содержимого, что показывает читалка, а номера глифов
    переводятся обратно тем же шрифтом.
    """
    font = font or load_font()
    back = {glyph: chr(code) for code, glyph in font.char_to_glyph.items()}

    out: list[str] = []
    for stream in _streams(data):
        try:
            body = zlib.decompress(stream).decode("utf-8", "replace")
        except zlib.error:
            continue
        if " Tj" not in body:
            continue
        for piece in body.split("<")[1:]:
            hexed = piece.split(">")[0]
            if len(hexed) % 4 or not all(c in "0123456789ABCDEFabcdef" for c in hexed):
                continue
            out.append("".join(
                back.get(int(hexed[at:at + 4], 16), "�")
                for at in range(0, len(hexed), 4)
            ))
    return "\n".join(out)


def _declared_length(data: bytes, before: int) -> int | None:
    """Значение `/Length` из словаря перед потоком.

    Ищется справа налево и с проверкой следующего символа: у вложенного
    шрифта в том же словаре стоит `/Length1` — размер РАСПАКОВАННОГО
    файла. Взять его за длину потока значит прочитать один байт вместо
    двухсот тысяч.
    """
    at = before
    while True:
        mark = data.rfind(b"/Length", 0, at)
        if mark == -1:
            return None
        tail = data[mark + 7:mark + 27]
        if tail[:1].isdigit():
            # Это `/Length1`, `/Length2` и подобные. Ищем дальше влево.
            at = mark
            continue
        digits = tail.split()
        return int(digits[0]) if digits and digits[0].isdigit() else None


def _streams(data: bytes) -> list[bytes]:
    """Тела всех потоков документа.

    Длина берётся из `/Length` в словаре перед `stream`, а НЕ поиском
    `endstream`. Сжатые байты — произвольный двоичный мусор, и
    последовательность `endstream` встречается в них сама собой: редко,
    но регулярно, потому что содержимое каждой бумаги немного разное.
    Поток тогда обрезался посередине, распаковка падала, и проверка
    бумаги падала вместе с ней — на случайном документе и только иногда.
    Искать конец там, где документ сам его назвал, надёжнее любого
    разделителя.
    """
    out: list[bytes] = []
    at = 0
    while True:
        # Ищется именно начало потока. Простое «stream» находило бы и
        # хвост «endstream», после чего следующий поток начинался бы
        # с середины предыдущего.
        start = data.find(b"\nstream", at)
        if start == -1:
            return out

        length = _declared_length(data, start)
        if length is None:
            return out

        start += len(b"\nstream")
        if data[start:start + 2] == b"\r\n":
            start += 2
        elif data[start:start + 1] in (b"\n", b"\r"):
            start += 1

        out.append(data[start:start + length])
        at = start + length


def page_size(data: bytes) -> tuple[float, float]:
    """Размер первой страницы в пунктах — для проверки, что это A4."""
    at = data.find(b"/MediaBox")
    if at == -1:
        raise ValueError("В документе нет страницы с заданным размером")
    body = data[at + 9:data.find(b"]", at)].strip(b" [").split()
    numbers = [float(part) for part in body]
    return numbers[2] - numbers[0], numbers[3] - numbers[1]


def page_count(data: bytes) -> int:
    at = data.find(b"/Type /Pages")
    if at == -1:
        raise ValueError("В документе нет дерева страниц")
    tail = data[at:at + 120]
    mark = tail.find(b"/Count")
    return int(tail[mark + 6:mark + 12].split()[0])
