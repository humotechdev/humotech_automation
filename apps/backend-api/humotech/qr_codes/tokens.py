"""Подписанный QR-код точки: что в нём лежит и почему именно это.

Код на экране меняется каждые несколько десятков секунд и подписан сервером.
Подпись отвечает на один вопрос: этот код выпустили мы, а не нарисовал кто-то
сам. Всё остальное — кто сканирует, можно ли ему, не поздно ли — решается уже
на сервере, при разборе.

**Чего в коде нет.** Ни идентификатора сотрудника, ни имени, ни Telegram ID,
ни credential экрана. Код видят все, кто стоит рядом с дверью, и его
фотографируют; всё, что в него положено, следует считать опубликованным.

**Что в коде есть.** Организация, офис, точка, направление, время выпуска,
срок и `jti` — разовый номер. Организация и офис избыточны: их можно вывести
из точки. Они здесь ради сверки: сервер сравнивает их с настоящими
и отвергает код, если точку успели перенести в другой офис. Подпись такое
не ловит — она подтверждает, что мы это выпускали, а не что оно до сих пор
верно.

**Формат — двоичный, а не JSON.** Не ради экономии как таковой: чем короче
строка, тем крупнее модули QR при том же размере наклейки и тем увереннее
он читается с телефона в плохом свете у двери. 64 байта содержимого плюс
16 байт подписи дают строку в 110 символов.

Подпись — HMAC-SHA256, усечённая до 128 бит. Усечение допускает RFC 2104:
для кода, живущего полминуты, 128 бит с огромным запасом. Ключ — отдельный,
`QR_SIGNING_SECRET`, а не `SECRET_KEY`: у них разные сроки жизни и разные
последствия утечки, и во frontend не уходит ни тот ни другой.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from humotech.core.timeframes import UTC

# Метка формата. Нужна не для красоты: сканер по ней сразу отличает наш код
# от чужого — от ссылки, от кода Wi-Fi, от чьей-то визитки — и не ходит
# на сервер за очевидным отказом.
PREFIX = "HT1"
VERSION = 1

# Разделение областей применения. Без него подпись, выпущенная где-то ещё
# тем же ключом, засчиталась бы здесь.
DOMAIN = b"humotech.qr.point.v1"

SIGNATURE_BYTES = 16

_DIRECTIONS = {"ENTRY": 0, "EXIT": 1, "BOTH": 2}
_DIRECTIONS_BACK = {code: name for name, code in _DIRECTIONS.items()}

# версия | организация | офис | точка | направление | выпуск | срок | jti
_LAYOUT = struct.Struct(">B16s16s16sBIH8s")


class QrTokenError(Exception):
    """Код не разобран. `reason` — для журнала, не для человека."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class QrPayload:
    organization_id: uuid.UUID
    office_id: uuid.UUID
    qr_point_id: uuid.UUID
    direction_mode: str
    issued_at: datetime
    expires_at: datetime
    jti: bytes

    @property
    def nonce_hash(self) -> str:
        """Чем `jti` записывается в журнал отметок.

        Хешем, а не как есть: в `attendance_events` эта колонка живёт годами,
        а разовый номер — часть кода, который мы сами выпустили. Хранить
        выпущенные секреты в открытом виде незачем даже когда они истекли.
        """
        return hashlib.sha256(DOMAIN + self.jti).hexdigest()


def signing_key() -> bytes:
    key = settings.QR["SIGNING_SECRET"]
    if not key:
        # Не настроен — не выпускаем ничего. Молчаливый запасной ключ
        # означал бы, что коды подписаны предсказуемо.
        raise QrTokenError("signing_secret_not_configured")
    return key.encode("utf-8")


def issue(
    *,
    organization_id: uuid.UUID,
    office_id: uuid.UUID,
    qr_point_id: uuid.UUID,
    direction_mode: str,
    issued_at: datetime,
    ttl_seconds: int,
) -> tuple[str, QrPayload]:
    """Выпустить код. Возвращает строку для экрана и её разбор."""
    if direction_mode not in _DIRECTIONS:
        raise QrTokenError("unknown_direction")
    if not 1 <= ttl_seconds <= 0xFFFF:
        # Срок хранится двумя байтами как смещение от выпуска: так «конец
        # позже начала» — свойство формата, а не проверка, которую можно
        # забыть написать.
        raise QrTokenError("ttl_out_of_range")

    moment = issued_at.astimezone(UTC).replace(microsecond=0)
    jti = secrets.token_bytes(8)
    body = _LAYOUT.pack(
        VERSION,
        organization_id.bytes,
        office_id.bytes,
        qr_point_id.bytes,
        _DIRECTIONS[direction_mode],
        int(moment.timestamp()),
        ttl_seconds,
        jti,
    )
    token = PREFIX + _b64(body + _sign(body))
    return token, QrPayload(
        organization_id=organization_id,
        office_id=office_id,
        qr_point_id=qr_point_id,
        direction_mode=direction_mode,
        issued_at=moment,
        expires_at=moment + timedelta(seconds=ttl_seconds),
        jti=jti,
    )


def read(token: str, *, now: datetime, skew_seconds: int | None = None) -> QrPayload:
    """Разобрать код и проверить подпись и срок.

    Порядок здесь важен: подпись проверяется ДО срока и до всего остального.
    Иначе по разнице ответов на подделанный код выясняли бы, какие поля
    сервер вообще разбирает.
    """
    if not isinstance(token, str) or not token.startswith(PREFIX):
        raise QrTokenError("foreign_code")

    try:
        raw = _unb64(token[len(PREFIX):])
    except (ValueError, TypeError):
        raise QrTokenError("malformed") from None

    if len(raw) != _LAYOUT.size + SIGNATURE_BYTES:
        raise QrTokenError("malformed")

    body, signature = raw[: _LAYOUT.size], raw[_LAYOUT.size:]
    if not hmac.compare_digest(signature, _sign(body)):
        raise QrTokenError("bad_signature")

    version, org, office, point, direction, issued, ttl, jti = _LAYOUT.unpack(body)
    if version != VERSION:
        raise QrTokenError("unknown_version")
    if direction not in _DIRECTIONS_BACK:
        raise QrTokenError("unknown_direction")

    issued_at = datetime.fromtimestamp(issued, tz=UTC)
    expires_at = issued_at + timedelta(seconds=ttl)

    skew = timedelta(
        seconds=(
            settings.QR["CLOCK_SKEW_SECONDS"] if skew_seconds is None else skew_seconds
        )
    )
    # Допуск в обе стороны. Часы экрана и часы сервера расходятся на секунды,
    # и без допуска код, выпущенный «в будущем» на две секунды, отвергался бы
    # весь свой срок.
    if now < issued_at - skew:
        raise QrTokenError("issued_in_future")
    if now > expires_at + skew:
        raise QrTokenError("expired")

    return QrPayload(
        organization_id=uuid.UUID(bytes=org),
        office_id=uuid.UUID(bytes=office),
        qr_point_id=uuid.UUID(bytes=point),
        direction_mode=_DIRECTIONS_BACK[direction],
        issued_at=issued_at,
        expires_at=expires_at,
        jti=jti,
    )


def _sign(body: bytes) -> bytes:
    return hmac.new(signing_key(), DOMAIN + body, hashlib.sha256).digest()[
        :SIGNATURE_BYTES
    ]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


__all__ = ["PREFIX", "QrPayload", "QrTokenError", "issue", "read"]
