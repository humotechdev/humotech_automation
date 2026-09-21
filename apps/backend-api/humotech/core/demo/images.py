"""Растровые демонстрационные файлы: скан справки и разбор фотографий.

PNG собирается вручную, без `Pillow`: этой библиотеки в зависимостях
backend нет, и тянуть её в боевую поставку ради одного
демонстрационного скана неправильно. Формат позволяет собрать
корректный файл из четырёх блоков — сигнатуры, заголовка, данных и
конца, — и именно это здесь и делается.

Текста на скане нет намеренно. Нарисовать кириллицу в растре без
шрифтового движка можно только попиксельно, а «скан» с ломаными буквами
хуже, чем скан без букв: он выглядел бы испорченным файлом, а не
образцом. Узнаваемость даёт форма — белый лист, поля, строки и
диагональная полоса водяного знака.
"""

from __future__ import annotations

import struct
import zlib

#: Размер «скана»: пропорции листа, но небольшой файл.
SCAN_WIDTH = 620
SCAN_HEIGHT = 877


def png(width: int, height: int, pixels: bytes) -> bytes:
    """Собрать PNG из готовых пикселей RGB.

    `pixels` — строки по `width * 3` байта БЕЗ байта фильтра: он
    добавляется здесь, потому что это часть формата, а не картинки.
    """
    raw = bytearray()
    for row in range(height):
        raw.append(0)  # фильтр «без фильтра»
        raw += pixels[row * width * 3:(row + 1) * width * 3]

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def scan_png() -> bytes:
    """Скан справки: белый лист с полями, строками и водяной полосой.

    Нужен, чтобы проверить показ не-PDF документа: карточка обязана
    открывать и картинку, а не только PDF.
    """
    width, height = SCAN_WIDTH, SCAN_HEIGHT
    pixels = bytearray(b"\xf6\xf7\xf9" * (width * height))

    def put(x: int, y: int, colour: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            at = (y * width + x) * 3
            pixels[at:at + 3] = bytes(colour)

    # Лист с полями.
    margin = 34
    for y in range(margin, height - margin):
        for x in range(margin, width - margin):
            put(x, y, (255, 255, 255))
    for x in range(margin, width - margin):
        put(x, margin, (196, 206, 220))
        put(x, height - margin - 1, (196, 206, 220))
    for y in range(margin, height - margin):
        put(margin, y, (196, 206, 220))
        put(width - margin - 1, y, (196, 206, 220))

    # Шапка и строки текста — прямоугольниками. Букв нет: без шрифтового
    # движка они получились бы ломаными, а это хуже, чем их отсутствие.
    for y in range(margin + 40, margin + 62):
        for x in range(margin + 34, margin + 300):
            put(x, y, (206, 216, 232))
    top = margin + 110
    for line in range(22):
        y0 = top + line * 26
        length = width - margin * 2 - 68 - (120 if line % 5 == 4 else 0)
        for y in range(y0, y0 + 7):
            for x in range(margin + 34, margin + 34 + length):
                put(x, y, (224, 230, 240))

    # Диагональная полоса водяного знака.
    for y in range(height):
        for width_at in range(-18, 18):
            x = int((y - height * 0.1) * 0.85) + width_at
            if margin < x < width - margin and margin < y < height - margin:
                at = ((y) * width + x) * 3
                red, green, blue = pixels[at], pixels[at + 1], pixels[at + 2]
                put(x, y, (
                    (red + 244 * 3) // 4,
                    (green + 196 * 3) // 4,
                    (blue + 192 * 3) // 4,
                ))

    return png(width, height, bytes(pixels))


def png_size(data: bytes) -> tuple[int, int]:
    """Ширина и высота PNG из заголовка — для проверок."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Это не PNG")
    width, height = struct.unpack_from(">II", data, 16)
    return width, height
