"""Антивирусная проверка загружаемых файлов через clamd (ClamAV).

Протокол — INSTREAM по TCP: команда `zINSTREAM\\0`, затем куски вида
«длина (4 байта, big-endian) + данные», в конце кусок нулевой длины.
Ответ — `stream: OK` либо `stream: <сигнатура> FOUND`. Сторонняя
библиотека не нужна: протокол в десяток строк, а зависимость ради него —
ещё одна поверхность атаки.

Проверка синхронная, до записи файла на диск. Поэтому решение принимается
сразу и честно:
  * чистый файл сохраняется со статусом CLEAN;
  * заражённый не сохраняется вовсе — загрузка отклоняется;
  * если антивирус недоступен, загрузка отклоняется с 503
    (`ScannerUnavailable`). Принять «на потом» нельзя: перевести такой
    файл в CLEAN позже некому, и он навсегда остался бы PENDING.

Включается `FILES_SCANNER_ENABLED=true` при работающем сервисе `clamav`
(аудит 11.10, M-11). До включения файлы получают UNSCANNED.
"""

from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass

from django.conf import settings

log = logging.getLogger("humotech.files.scanner")

CHUNK = 64 * 1024


class ScanError(Exception):
    """Антивирус не ответил или ответил непонятно."""


@dataclass(frozen=True)
class Verdict:
    clean: bool
    signature: str | None = None


def _config() -> tuple[str, int, float]:
    files = settings.FILES
    return (
        files.get("SCANNER_HOST", "clamav"),
        int(files.get("SCANNER_PORT", 3310)),
        float(files.get("SCANNER_TIMEOUT_SECONDS", 30)),
    )


def scan(upload) -> Verdict:
    """Проверить содержимое загрузки. Указатель файла возвращается в начало."""
    host, port, timeout = _config()
    try:
        with socket.create_connection((host, port), timeout=timeout) as conn:
            conn.sendall(b"zINSTREAM\0")
            upload.seek(0)
            for chunk in upload.chunks(CHUNK):
                conn.sendall(struct.pack("!L", len(chunk)) + chunk)
            conn.sendall(struct.pack("!L", 0))
            reply = b""
            while not reply.endswith(b"\0") and len(reply) < 4096:
                part = conn.recv(4096)
                if not part:
                    break
                reply += part
    except OSError as error:
        raise ScanError(f"clamd недоступен: {error.__class__.__name__}") from error
    finally:
        try:
            upload.seek(0)
        except Exception:  # noqa: BLE001 — указатель вернуть не удалось, проверка важнее
            pass

    text = reply.rstrip(b"\0").decode("utf-8", "replace").strip()
    if text.endswith("OK"):
        return Verdict(clean=True)
    if text.endswith("FOUND"):
        signature = text.removeprefix("stream:").removesuffix("FOUND").strip()
        return Verdict(clean=False, signature=signature[:200] or "unknown")
    # «INSTREAM size limit exceeded», «ERROR» и прочее — не вердикт.
    raise ScanError(f"непонятный ответ clamd: {text[:100]!r}")
