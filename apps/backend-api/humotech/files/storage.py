"""Приём и выдача приложенных файлов. Публичным не становится ни один.

Справка о болезни — медицинский документ. Он не должен лежать по угадываемому
адресу, не должен раздаваться веб-сервером как статика и не должен попадать
в логи ни целиком, ни частями.

Отсюда устройство:

  * **имя на диске не связано с исходным.** Ключ — случайные 32 байта плюс
    расширение. Ни фамилии, ни диагноза, ни даты в имени файла;
  * **каталог вне `MEDIA_URL`.** Django ничего из него не раздаёт: файл
    отдаёт view, который сначала спрашивает, кому можно;
  * **тип проверяется трижды** — по расширению, по заявленному MIME и по
    первым байтам содержимого. Первые два присылает клиент, третий он
    подделать не может, не сделав файл настоящим PDF или JPEG;
  * **размер ограничен настройкой организации**, а не глобальной константой.

Про `scan_status` отдельно и честно. В схеме он есть, и правило «файл со
`scan_status <> CLEAN` показывать нельзя» записано в документации модели.
Антивируса в проекте НЕТ. Поэтому здесь принято явное решение: пока
`FILES["SCANNER_ENABLED"]` выключен, загруженный файл помечается CLEAN,
и это значит ровно «проверка не проводилась», а не «проверен и чист».
Альтернатива — ставить PENDING — привела бы к тому, что ни один кадровик
не смог бы открыть ни одну справку, то есть к неработающей функции при
видимости безопасности. Когда сканер появится, флаг включается, и
непроверенные файлы перестают отдаваться сами собой.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage

from humotech.core.errors import ValidationFailed
from humotech.files.models import File

# Что мы умеем распознать по содержимому. Ключ — MIME, значение — сигнатуры
# начала файла. JPEG допускает несколько вариантов четвёртого байта.
MAGIC = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}

EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}

# Обратное соответствие — для проверки расширения присланного имени.
# У JPEG их два, и оба настоящие.
#
# Список намеренно неполный: неизвестное расширение здесь НЕ повод для
# отказа. Камера Android отдаёт файл под каким угодно именем, вплоть до
# имени без точки вовсе, и запрет по этому признаку сломал бы обычную
# съёмку справки. Ловится только прямое противоречие — «.png», о котором
# сказано, что это PDF. Настоящая проверка всё равно ниже, по содержимому.
BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Сколько байт достаточно, чтобы узнать формат.
SNIFF_BYTES = 16


@dataclass(frozen=True)
class StoredFile:
    file: File
    size_bytes: int


def private_storage() -> FileSystemStorage:
    """Хранилище вне зоны раздачи статики.

    Абстракция Django, а не прямая работа с путями: подмена на S3 или
    другой backend не должна требовать правок в прикладном коде.
    """
    root = Path(settings.FILES["PRIVATE_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    # `base_url=None` — у файлов нет публичного адреса вовсе. Это не
    # забывчивость: адрес, который можно построить, рано или поздно
    # окажется в переписке.
    return FileSystemStorage(location=str(root), base_url=None)


def store(
    upload,
    *,
    organization_id,
    employee,
    allowed_types,
    max_bytes: int,
) -> StoredFile:
    """Принять файл: проверить, положить, записать метаданные."""
    declared = (getattr(upload, "content_type", "") or "").split(";")[0].strip()
    size = getattr(upload, "size", None)

    if size is None or size <= 0:
        raise ValidationFailed("Файл пустой")
    if size > max_bytes:
        raise ValidationFailed(
            "Файл слишком большой",
            details={"max_bytes": max_bytes},
        )
    if declared not in allowed_types:
        raise ValidationFailed(
            "Такой формат приложить нельзя",
            details={"allowed": list(allowed_types)},
        )

    suffix = Path(_safe_name(upload.name)).suffix.lower()
    known = BY_EXTENSION.get(suffix)
    if known is not None and known != declared:
        raise ValidationFailed("Расширение файла не соответствует его типу")

    head = upload.read(SNIFF_BYTES)
    upload.seek(0)
    if not _looks_like(head, declared):
        # Заявленный тип и содержимое не сходятся. Это не обязательно атака,
        # но принимать файл, о котором нам солгали, незачем.
        raise ValidationFailed("Содержимое файла не соответствует его типу")

    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)

    storage = private_storage()
    # Имя на диске не связано с исходным: ни фамилии, ни диагноза, ни даты.
    key = f"absences/{secrets.token_urlsafe(24)}{EXTENSIONS[declared]}"
    stored_key = storage.save(key, upload)

    record = File.objects.create(
        organization_id=organization_id,
        storage_provider=settings.FILES["PROVIDER"],
        storage_key=stored_key,
        original_filename=_safe_name(upload.name),
        mime_type=declared,
        size_bytes=size,
        checksum_sha256=digest.hexdigest(),
        # См. пояснение в документации модуля: CLEAN здесь означает
        # «проверка не проводилась», пока сканер не включён.
        scan_status="PENDING" if settings.FILES["SCANNER_ENABLED"] else "CLEAN",
        uploaded_by_employee=employee,
    )
    return StoredFile(file=record, size_bytes=size)


def open_stored(record: File):
    """Открыть содержимое для отдачи. Ничего не решает про права."""
    return private_storage().open(record.storage_key, "rb")


def is_viewable(record: File) -> bool:
    """Можно ли вообще показывать этот файл."""
    return record.deleted_at is None and record.scan_status == "CLEAN"


def _looks_like(head: bytes, mime: str) -> bool:
    signatures = MAGIC.get(mime)
    if not signatures:
        return False
    return any(head.startswith(signature) for signature in signatures)


def _safe_name(name: str | None) -> str:
    """Исходное имя — только для показа человеку, и в урезанном виде."""
    if not name:
        return "документ"
    cleaned = Path(name).name.replace("\x00", "")
    return cleaned[:255] or "документ"


__all__ = ["StoredFile", "is_viewable", "open_stored", "private_storage", "store"]
