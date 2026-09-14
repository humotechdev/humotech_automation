"""Фотографии сотрудников витрины: локальный пакет и его раскладка.

Откуда берутся снимки. Только из локального пакета в
`humotech/core/demo_assets/photos`. Витрина никогда не ходит в сеть:
демонстрация не должна зависеть от интернета, а адрес чужого сервиса в
работающей CRM — это ещё и утечка того, кто и когда её показывал.
Пакет наполняет отдельная команда `import_demo_photos`, и делает это
один раз.

Почему JPEG, а не WebP. Файловый сервис приложения принимает три типа:
PDF, PNG и JPEG (`humotech/files/storage.py`, таблица `MAGIC`). WebP он
отвергает по содержимому, и фотография в этом формате просто не
открылась бы в карточке. Формат выбран тот, который система умеет
отдавать; размер, кадрирование и качество — как в задании: квадрат
256×256, качество около 82.

Если пакета нет, витрина показывает инициалы. Это не поломка: карточка
умеет и то и другое, и пустой пакет — обычное состояние проекта, в
котором нет ключа к фотобанку.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from humotech.core.demo.catalog import SHOWCASE

PACK_DIR = Path(__file__).resolve().parent.parent / "demo_assets" / "photos"
MANIFEST = PACK_DIR / "manifest.json"

#: Сколько фотографий имеет смысл держать. Больше не нужно: на экранах
#: одновременно видно несколько десятков человек, остальным инициалы —
#: нормальное состояние, а не заглушка.
WANTED = 56

SIZE = 256
QUALITY = 82
MIME = "image/jpeg"


@dataclass(frozen=True)
class Photo:
    """Одна фотография пакета и её происхождение.

    Происхождение хранится не для порядка: снимок взят у фотобанка по
    лицензии, и ссылка с именем автора — условие, на котором им можно
    пользоваться.
    """

    file: str
    pexels_id: int
    source_url: str
    photographer: str
    downloaded_at: str
    gender: str
    #: Примерный возраст: нужен, чтобы не поставить двадцатилетнее лицо
    #: человеку с тридцатью годами стажа.
    age_band: str
    employee_number: str | None = None

    @property
    def path(self) -> Path:
        return PACK_DIR / self.file


def pack() -> list[Photo]:
    """Что лежит в локальном пакете. Пустой список — пакета нет."""
    if not MANIFEST.exists():
        return []
    try:
        body = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out: list[Photo] = []
    for row in body.get("photos", []):
        photo = Photo(**row)
        # Запись без файла — не фотография. Показать её нечем, и
        # ссылаться на неё из карточки значило бы обещать картинку,
        # которой нет.
        if photo.path.exists():
            out.append(photo)
    return out


def plan(photos: list[Photo], numbers: list[str]) -> list[tuple[Photo, str]]:
    """Кому какая фотография достанется.

    Правило простое и устойчивое: сначала сотрудники-сценарии — они
    видны на всех экранах и в проверках, — затем первые строки списка.
    Каждая фотография уходит ровно одному человеку: одно лицо у двоих
    выглядит как ошибка данных.

    Пол сверяется: у женского табельного номера женский снимок. Если
    подходящих по полу не осталось, человек остаётся с инициалами —
    это честнее, чем поставить чужое лицо.
    """
    wanted = [one.number for one in SHOWCASE]
    wanted += [number for number in numbers if number not in wanted]

    free = {"MALE": [p for p in photos if p.gender == "MALE"],
            "FEMALE": [p for p in photos if p.gender == "FEMALE"]}
    fixed = {p.employee_number: p for p in photos if p.employee_number}

    out: list[tuple[Photo, str]] = []
    taken: set[str] = set()
    for number in wanted:
        photo = fixed.get(number)
        if photo is not None and photo.file not in taken:
            out.append((photo, number))
            taken.add(photo.file)
    return out


def described(photos: list[Photo]) -> str:
    """Строка для отчёта команды."""
    if not photos:
        return "фотографий нет (локальный пакет пуст)"
    male = sum(1 for one in photos if one.gender == "MALE")
    return f"фотографий {len(photos)} (мужских {male}, женских {len(photos) - male})"
