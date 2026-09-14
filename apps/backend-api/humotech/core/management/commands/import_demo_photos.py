"""Наполнение локального пакета фотографий витрины.

Отдельная команда, а не часть посева, по двум причинам. Первая: она
ходит в сеть, а посев не должен — демонстрация обязана работать без
интернета. Вторая: она нужна один раз, а посев запускают часто.

Источник — официальный API Pexels. Ни поиска по картинкам, ни
соцсетей, ни фотографий настоящих сотрудников: снимки берутся у
фотобанка по лицензии, и в пакет вместе с файлом кладётся ссылка и имя
автора.

Ключ читается из окружения (`PEXELS_API_KEY`). В репозиторий он не
кладётся и не записывается никуда: без ключа команда честно говорит,
чего ей не хватает, и предлагает второй путь — `--from-dir`, импорт уже
скачанных снимков из локальной папки.

Обработка требует `Pillow`. В боевые зависимости он не входит и не
должен: это инструмент разработчика, который запускают руками. Если его
нет, команда скажет об этом и не станет писать в пакет половину.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from humotech.core.demo import photos
from humotech.core.demo.catalog import SHOWCASE

API = "https://api.pexels.com/v1/search"

#: Запросы подобраны под ОДИН вид кадра: голова и плечи, взгляд в
#: камеру, деловая одежда, светлый фон. «Портрет в офисе» без этого
#: уточнения приносит людей в полный рост и снимки с телефоном в руке —
#: в списке сотрудников они выглядят случайным набором, а не карточками
#: одной компании.
QUERIES = (
    ("MALE", "young", "headshot young man business suit white background"),
    ("MALE", "middle", "corporate headshot businessman suit studio"),
    ("MALE", "senior", "professional headshot senior businessman studio portrait"),
    ("FEMALE", "young", "headshot young businesswoman white background"),
    ("FEMALE", "middle", "corporate headshot businesswoman studio portrait"),
    ("FEMALE", "senior", "professional headshot senior businesswoman studio"),
)


class Command(BaseCommand):
    help = "Наполняет локальный пакет фотографий витрины из Pexels или из папки"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--from-dir",
            help="взять снимки из локальной папки вместо обращения к Pexels",
        )
        parser.add_argument(
            "--limit", type=int, default=photos.WANTED,
            help=f"сколько фотографий положить в пакет (по умолчанию {photos.WANTED})",
        )

    def handle(self, *args, **options) -> None:
        image = self._pillow()
        photos.PACK_DIR.mkdir(parents=True, exist_ok=True)

        source = options.get("from_dir")
        if source:
            rows = self._from_dir(Path(source), options["limit"], image)
        else:
            rows = self._from_pexels(options["limit"], image)

        photos.MANIFEST.write_text(
            json.dumps(
                {
                    "note": (
                        "Демонстрационные портреты. Взяты по лицензии Pexels; "
                        "ни один снимок не изображает сотрудника HUMOTECH."
                    ),
                    "size": photos.SIZE,
                    "quality": photos.QUALITY,
                    "photos": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.stdout.write(self.style.SUCCESS(
            f"В пакете {len(rows)} фотографий: {photos.PACK_DIR}"
        ))

    # --- источники ----------------------------------------------------------

    def _from_pexels(self, limit: int, image) -> list[dict]:
        key = os.environ.get("PEXELS_API_KEY", "").strip()
        if not key:
            raise CommandError(
                "Ключ PEXELS_API_KEY не задан. Он не хранится в репозитории "
                "намеренно: это секрет.\n"
                "Либо задайте его в окружении и повторите, либо соберите "
                "пакет из уже скачанных снимков: "
                "`import_demo_photos --from-dir <папка>`.\n"
                "Пока пакет пуст, витрина показывает инициалы — карточка "
                "умеет и так."
            )

        rows: list[dict] = []
        per_query = max(1, limit // len(QUERIES) + 1)
        for gender, band, query in QUERIES:
            for row in self._search(key, query, per_query):
                if len(rows) >= limit:
                    break
                name = f"{gender.lower()}-{band}-{row['id']}.jpg"
                body = self._fetch(row["src"]["large"])
                self._save(image, body, photos.PACK_DIR / name)
                rows.append({
                    "file": name,
                    "pexels_id": row["id"],
                    "source_url": row["url"],
                    "photographer": row["photographer"],
                    "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "gender": gender,
                    "age_band": band,
                    "employee_number": None,
                })
        return self._assign(rows)

    def _from_dir(self, folder: Path, limit: int, image) -> list[dict]:
        """Импорт локального пакета: снимки уже скачаны кем-то заранее.

        Пол и возраст берутся из имени файла — `male-middle-01.jpg`. Имя
        здесь единственный источник: угадывать пол по изображению
        нечем, а поставить женское лицо мужчине хуже, чем оставить
        инициалы.
        """
        if not folder.is_dir():
            raise CommandError(f"Папки {folder} нет")
        rows: list[dict] = []
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            parts = path.stem.split("-")
            if len(parts) < 2 or parts[0].upper() not in ("MALE", "FEMALE"):
                self.stdout.write(
                    f"Пропущен {path.name}: имя должно начинаться с male- или female-"
                )
                continue
            name = f"{parts[0].lower()}-{parts[1]}-{len(rows):02d}.jpg"
            self._save(image, path.read_bytes(), photos.PACK_DIR / name)
            rows.append({
                "file": name,
                "pexels_id": 0,
                "source_url": "local",
                "photographer": "local pack",
                "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "gender": parts[0].upper(),
                "age_band": parts[1],
                "employee_number": None,
            })
            if len(rows) >= limit:
                break
        return self._assign(rows)

    # --- обработка ----------------------------------------------------------

    @staticmethod
    def _pillow():
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - зависит от окружения
            raise CommandError(
                "Для обработки снимков нужен Pillow: `pip install pillow`. "
                "В боевые зависимости он не входит намеренно — это "
                "инструмент разработчика, а не часть приложения."
            ) from exc
        return Image

    @staticmethod
    def _save(image, body: bytes, path: Path) -> None:
        """Квадрат вокруг лица, 256×256, JPEG.

        Распознавания лиц здесь нет и заводить его ради витрины незачем.
        У портретного кадра лицо стоит в верхней трети, поэтому квадрат
        берётся не по центру, а со смещением вверх на пятую часть
        оставшейся высоты: по центру в кадр попадали бы плечи и грудь,
        а лицо уезжало за верхнюю границу.

        Пропорции не искажаются: берётся квадрат из исходника и
        уменьшается целиком, без растяжения.
        """
        picture = image.open(BytesIO(body)).convert("RGB")
        width, height = picture.size
        side = min(width, height)
        left = (width - side) // 2
        top = max(0, int((height - side) * 0.18))
        picture = picture.crop((left, top, left + side, top + side))
        picture = picture.resize((photos.SIZE, photos.SIZE), image.LANCZOS)
        picture.save(path, "JPEG", quality=photos.QUALITY, optimize=True)

    @staticmethod
    def _assign(rows: list[dict]) -> list[dict]:
        """Закрепить снимки за сотрудниками-сценариями.

        Закрепление хранится в пакете, а не вычисляется посевом: иначе
        одно и то же лицо доставалось бы разным людям при малейшем
        изменении порядка, и «фотография этого человека» перестало бы
        что-то значить.
        """
        free = {"MALE": [], "FEMALE": []}
        for row in rows:
            free[row["gender"]].append(row)
        for one in SHOWCASE:
            pool = free["FEMALE" if one.female else "MALE"]
            if pool:
                pool.pop(0)["employee_number"] = one.number
        return rows

    # --- сеть ---------------------------------------------------------------

    #: Фотобанк отвечает 403 на запрос без опознавательной строки клиента:
    #: `Python-urllib` он считает роботом. Строка честная — это и есть
    #: наш инструмент, а не попытка притвориться браузером.
    AGENT = "HUMOTECH-demo-photo-import/1.0 (+internal tooling)"

    def _search(self, key: str, query: str, count: int) -> list[dict]:
        url = f"{API}?query={urllib.parse.quote(query)}&per_page={count}&orientation=portrait"
        request = urllib.request.Request(
            url, headers={"Authorization": key, "User-Agent": self.AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as answer:
                return json.loads(answer.read()).get("photos", [])
        except urllib.error.HTTPError as exc:
            # Тело ответа содержит причину отказа и не содержит ключа:
            # без него отличить «неверный ключ» от «исчерпан лимит»
            # можно было бы только гаданием.
            body = exc.read().decode("utf-8", "replace")[:200]
            raise CommandError(
                f"Pexels ответил {exc.code}: {body}"
            ) from exc

    def _fetch(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": self.AGENT})
        with urllib.request.urlopen(request, timeout=30) as answer:
            return answer.read()
