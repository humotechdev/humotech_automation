"""Где лежит готовый файл выгрузки.

Каталог вне зоны раздачи статики — по той же причине, что и у справок
о болезни (`humotech/files/storage.py`): готовая выгрузка это кадровые
данные, и веб-сервер не должен отдавать их по угаданному адресу. Файл
выдаёт view, который сначала спрашивает, кому можно.

Имя файла на диске задаётся сервером и только сервером — это
идентификатор задания плюс расширение. Ничто из присланного клиентом
в путь не попадает: `..` и абсолютный путь в имени превращают выдачу
файла в чтение произвольного файла на машине.

Хранилище берётся абстракцией Django, а не работой с путями напрямую:
перевод на S3 не должен требовать правок в прикладном коде.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage

#: Расширение по формату. Список закрытый: формат приходит снаружи,
#: и подставлять его в имя файла как есть нельзя.
EXTENSIONS = {"csv": "csv", "xlsx": "xlsx"}


def export_storage() -> FileSystemStorage:
    root = Path(settings.EXPORTS["PRIVATE_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    # `base_url=None` — публичного адреса у файла нет вовсе. Адрес,
    # который можно построить, рано или поздно окажется в переписке.
    return FileSystemStorage(location=str(root), base_url=None)


def storage_key_for(job_id: uuid.UUID, fmt: str) -> str:
    """Имя файла на диске: идентификатор задания и расширение.

    Ни имени отчёта, ни фильтров: имя файла видно в списке каталога,
    и «zarplata-direktora.xlsx» рассказывает лишнее ещё до открытия.
    """
    return f"{job_id}.{EXTENSIONS[fmt]}"


def save_chunks(key: str, chunks) -> int:
    """Записать файл по кускам и вернуть его размер.

    Кусками, а не одной строкой: выгрузка CSV приходит генератором
    именно затем, чтобы не собираться в памяти целиком.
    """
    storage = export_storage()
    delete(key)  # повтор задания перезаписывает файл, а не плодит копии

    written = 0
    with storage.open(key, "wb") as target:
        for chunk in chunks:
            data = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            target.write(data)
            written += len(data)
    return written


def save_bytes(key: str, payload: bytes) -> int:
    storage = export_storage()
    delete(key)
    storage.save(key, ContentFile(payload))
    return len(payload)


def open_export(key: str):
    return export_storage().open(key, "rb")


def exists(key: str) -> bool:
    return export_storage().exists(key)


def delete(key: str | None) -> None:
    """Удалить файл, если он есть. Отсутствие файла не ошибка.

    Уборка вызывается и по сроку, и при отмене задания, и повторно:
    падать оттого, что файла уже нет, ей незачем.
    """
    if not key:
        return
    storage = export_storage()
    if storage.exists(key):
        storage.delete(key)


__all__ = [
    "EXTENSIONS",
    "delete",
    "exists",
    "export_storage",
    "open_export",
    "save_bytes",
    "save_chunks",
    "storage_key_for",
]
