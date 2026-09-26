"""Выдача приложенного файла: одна проверка и одни заголовки на все view.

Раньше каждый view собирал `FileResponse` сам, и правило «файл со
`scan_status <> CLEAN` не показывать» соблюдали только ознакомления.
Справка, паспорт, фотография и файл из обращения отдавались при любом
статусе — в том числе удалённые и помеченные сканером как заражённые.

Правило теперь одно и живёт здесь:

  * удалённый файл (`deleted_at`) не отдаётся никому;
  * INFECTED и FAILED не отдаются никому;
  * PENDING (сканер включён, проверка ещё не прошла) отдаётся только
    кадровику в CRM и только вложением (`attachment`): он может скачать
    бумагу, если сканер задерживается, но браузер не откроет её сам.
    Сотруднику и боту — «не найдено», как будто файла ещё нет;
  * CLEAN — как раньше.

Заголовки тоже одни:

  * `X-Content-Type-Options: nosniff` — явно, а не только из настроек:
    тип файла берётся из базы и проверен по сигнатуре, но угадывать его
    браузеру не дают ни при каких настройках;
  * тип вне списка PDF/PNG/JPEG отдаётся как `application/octet-stream`
    и только вложением — такой записи быть не должно, но если она
    появится (ручная правка, старый импорт), HTML или SVG не исполнится
    в происхождении API;
  * картинкам — `Content-Security-Policy: default-src 'none'; sandbox`:
    даже если когда-нибудь картинкой окажется SVG, скрипт не выполнится.
    PDF эту политику НЕ получает: встроенный просмотрщик Chrome с ней
    не открывает документ, а открыть справку — основная задача;
  * `Cache-Control: private, no-store` — медицинская бумага не должна
    оседать в общих кэшах по дороге;
  * имя файла очищено от управляющих символов, переводов строки и
    символов направления текста: иначе `CR LF` в имени роняет ответ
    (Django отказывается ставить такой заголовок, и выходит 500), а
    `U+202E` показывает «справка.exe» как «справка.pdf».
"""

from __future__ import annotations

import unicodedata
from urllib.parse import quote

from django.http import FileResponse
from django.utils.http import content_disposition_header

from humotech.core.errors import NotFound
from humotech.files.models import File

#: Что браузеру можно показать самому. Совпадает с тем, что `store`
#: умеет распознать по содержимому.
INLINE_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg"})

#: Кому не страшно отдать непроверенный файл. Не сотруднику: он его и так
#: приложил сам, а бот переслал бы непроверенную бумагу в чат.
STAFF = "staff"
EMPLOYEE = "employee"

#: Эти статусы не отдаются никому и никогда.
BLOCKED_STATUSES = frozenset({"INFECTED", "FAILED"})

FALLBACK_NAME = "документ"
MAX_NAME_LENGTH = 255


def display_name(name: str | None, *, fallback: str = FALLBACK_NAME) -> str:
    """Имя файла, безопасное для заголовка и для показа человеку.

    Убирается всё, что не печатается: управляющие символы (NUL, CR, LF,
    TAB), символы направления текста (U+202A–U+202E, U+2066–U+2069) и
    прочие невидимые форматирующие (категории Unicode Cc, Cf, Cs, Co,
    Cn). Разделители путей заменяются: имя — только подпись, каталогов в
    нём нет. Длинное имя режется, но расширение сохраняется — по нему
    человек узнаёт, чем открыть.
    """
    if not name:
        return fallback
    # Последний компонент пути: и прямой, и обратный слеш. Путь из
    # Windows приходит с `\`, и `Path.name` в Linux его не режет.
    tail = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(
        ch for ch in tail
        if unicodedata.category(ch)[0] not in ("C",) and ch not in "  "
    ).strip()
    # Имя из одних точек — это «текущий» или «родительский каталог», а
    # не имя файла.
    if not cleaned.strip("."):
        return fallback
    if len(cleaned) > MAX_NAME_LENGTH:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and 0 < len(ext) <= 10 and stem:
            cleaned = stem[: MAX_NAME_LENGTH - len(ext) - 1] + "." + ext
        else:
            cleaned = cleaned[:MAX_NAME_LENGTH]
    return cleaned


def viewable_for(record: File, audience: str) -> bool:
    """Можно ли отдать файл этому кругу людей. Без побочных действий."""
    if record is None or record.deleted_at is not None:
        return False
    status = record.scan_status
    if status == "CLEAN":
        return True
    if status in BLOCKED_STATUSES:
        return False
    # PENDING и всё, чего мы не знаем: только кадровику.
    return audience == STAFF and status == "PENDING"


def require_viewable(record: File, audience: str, *, stream=None) -> None:
    """Отказать, если файл отдавать нельзя. Открытый поток закрывается.

    Отказ — «не найдено», а не «запрещено»: по ответу нельзя понять,
    что файл есть, но заражён или удалён.
    """
    if viewable_for(record, audience):
        return
    if stream is not None:
        try:
            stream.close()
        except Exception:  # noqa: BLE001 — закрыть не удалось, но отказ важнее
            pass
    raise NotFound("Файл недоступен")


def file_response(
    stream,
    record: File,
    *,
    audience: str,
    filename: str | None = None,
    inline: bool = True,
    rfc5987: bool = False,
) -> FileResponse:
    """Ответ с файлом после проверки `scan_status` и удаления.

    `filename` — имя в заголовке; по умолчанию исходное имя файла.
    `rfc5987=True` — заголовок только в форме `filename*=UTF-8''…`:
    так его ждёт бот для файлов из обращений (`_file_name` в боте), и
    менять форму значило бы сломать подпись файла в чате.
    """
    require_viewable(record, audience, stream=stream)

    mime = (record.mime_type or "").split(";")[0].strip().lower()
    safe_type = mime in INLINE_TYPES
    # Непроверенный файл кадровику — только вложением: браузер не откроет
    # его сам, открыть его придётся осознанно.
    as_attachment = (not inline) or (not safe_type) or record.scan_status != "CLEAN"

    name = display_name(filename if filename is not None else record.original_filename)
    response = FileResponse(
        stream,
        content_type=mime if safe_type else "application/octet-stream",
    )
    if rfc5987:
        disposition = "attachment" if as_attachment else "inline"
        response["Content-Disposition"] = (
            f"{disposition}; filename*=UTF-8''{quote(name)}"
        )
    else:
        response["Content-Disposition"] = content_disposition_header(
            as_attachment, name
        )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    if not safe_type or mime.startswith("image/"):
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


__all__ = [
    "EMPLOYEE",
    "INLINE_TYPES",
    "STAFF",
    "display_name",
    "file_response",
    "require_viewable",
    "viewable_for",
]
