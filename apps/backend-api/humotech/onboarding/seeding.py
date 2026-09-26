"""Наполнение организации стартовыми карточками и документами.

Отдельный модуль, потому что вызывают его двое: миграция и команда
обслуживания. Миграция обязана работать с ИСТОРИЧЕСКИМИ моделями —
теми, какими они были в момент её написания, — а команда работает с
текущими. Если бы функция брала модели сама, через год миграция
сломалась бы на первом же новом поле.

Поэтому модели передаются снаружи. Тексты при этом одни и те же: они
в `content.py`, и второго их экземпляра не существует.

Идемпотентно целиком. Повторный запуск ничего не дублирует и — что
важнее — ничего не переписывает: кадровик мог уже поправить формулировку,
и «обновление наполнения» не должно затирать его работу.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from humotech.onboarding.content import (
    DOCUMENTS,
    FIRST_VERSION,
    PROGRAM_CODE,
    PROGRAM_DESCRIPTION,
    PROGRAM_TITLE,
    SECTIONS,
)


def seed(
    organization_id: uuid.UUID,
    *,
    Program,
    Section,
    Document,
    Version,
    now: datetime,
) -> dict:
    """Программа, десять карточек и три документа. Возвращает, что создано."""
    program, _ = Program.objects.get_or_create(
        organization_id=organization_id,
        code=PROGRAM_CODE,
        defaults={
            "title": PROGRAM_TITLE,
            "description": PROGRAM_DESCRIPTION,
            "is_active": True,
        },
    )

    sections = 0
    for position, title, body, button in SECTIONS:
        _, created = Section.objects.get_or_create(
            program_id=program.id,
            position=position,
            defaults={
                "organization_id": organization_id,
                "title": title,
                "body": body,
                "button_label": button,
                "version": 1,
            },
        )
        sections += int(created)

    documents = 0
    versions = 0
    for item in DOCUMENTS:
        document, created = Document.objects.get_or_create(
            organization_id=organization_id,
            code=item["code"],
            defaults={
                "title": item["title"],
                "description": item["description"],
                "is_mandatory": True,
                "position": item["position"],
            },
        )
        documents += int(created)
        if Version.objects.filter(document_id=document.id).exists():
            # У документа уже есть редакция — хоть черновик. Подкладывать
            # к ней ещё одну «первую» нельзя: номера разойдутся, и станет
            # непонятно, с чем именно соглашались люди.
            continue
        # Первая редакция публикуется сразу. Черновик здесь означал бы,
        # что свежая организация выкачена без единого обязательного
        # документа и ознакомление завершается на десятой карточке.
        Version.objects.create(
            organization_id=organization_id,
            document_id=document.id,
            version=FIRST_VERSION,
            summary=item["summary"],
            body=item["body"],
            agree_label=item["agree_label"],
            status="PUBLISHED",
            published_at=now,
        )
        versions += 1

    return {
        "program": program.code,
        "sections": sections,
        "documents": documents,
        "versions": versions,
    }


__all__ = ["seed"]
