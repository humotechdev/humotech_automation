"""Кадровые вложения: фотография сотрудника и его документы.

Отдельный модуль, а не часть `services.py`: здесь работа с файлами, а не
с карточкой, и правила у неё свои — размер, формат и то, что содержимое
проверяется по первым байтам, а не по расширению.

Файл принимается ДО создания сотрудника. Иначе кадровику пришлось бы
сначала сохранить человека, а потом отдельно донести его бумаги, и форма
приёма перестала бы быть одной операцией. Строка в `files` при этом
появляется сразу, а привязка к сотруднику — только в момент приёма.

Раздача идёт через view, а не через веб-сервер: паспорт не должен лежать
по угадываемому адресу. Право спрашивается на каждое открытие.
"""

from __future__ import annotations

import uuid

from humotech.core.errors import NotFound, ValidationFailed
from humotech.core.rbac import Actor
from humotech.core.service import BaseService
from humotech.employees.models import Employee, EmployeeDocument
from humotech.files.models import File
from humotech.files.storage import open_stored, store

#: Фотография. JPEG и PNG: снимок с телефона либо выгрузка из пропускной.
PHOTO_TYPES = ("image/jpeg", "image/png")
PHOTO_MAX_BYTES = 5 * 1024 * 1024

#: Документ. Плюс PDF — договор и приказ приходят именно так.
DOCUMENT_TYPES = ("application/pdf", "image/jpeg", "image/png")
DOCUMENT_MAX_BYTES = 10 * 1024 * 1024

#: Что чем ограничено. Ключ приходит из запроса.
PURPOSES = {
    "photo": (PHOTO_TYPES, PHOTO_MAX_BYTES, "employees/photos"),
    "document": (DOCUMENT_TYPES, DOCUMENT_MAX_BYTES, "employees/documents"),
}


class EmployeeAttachmentService(BaseService):
    """Приём и выдача кадровых файлов. Требует `employees.manage` на запись
    и `employees.read` на чтение — те же права, что и у самой карточки."""

    def upload(self, actor: Actor, *, upload, purpose: str) -> File:
        self.access.require(actor, "employees.manage")

        rules = PURPOSES.get(purpose)
        if rules is None:
            raise ValidationFailed(
                "Неизвестное назначение файла",
                details={"field": "purpose", "allowed": sorted(PURPOSES)},
            )
        allowed, max_bytes, prefix = rules

        stored = store(
            upload,
            organization_id=actor.organization_id,
            # Кадровик — пользователь, а не сотрудник: поля с ним у этой
            # загрузки нет вовсе.
            employee=None,
            user_id=actor.user_id,
            allowed_types=allowed,
            max_bytes=max_bytes,
            prefix=prefix,
        )
        return stored.file

    def take(self, actor: Actor, file_ids: list[uuid.UUID]) -> dict[uuid.UUID, File]:
        """Проверить, что файлы существуют и принадлежат этой организации.

        Идентификатор приходит от клиента, и без этой проверки чужой файл
        привязался бы к своему сотруднику по одному только UUID.
        """
        if not file_ids:
            return {}
        rows = File.objects.filter(
            id__in=file_ids,
            organization_id=actor.organization_id,
            deleted_at__isnull=True,
        )
        found = {row.id: row for row in rows}
        missing = [str(one) for one in file_ids if one not in found]
        if missing:
            raise ValidationFailed(
                "Файл не найден — загрузите его заново",
                details={"field": "file_id", "missing": missing},
            )
        return found

    # ------------------------------------------------------------------ выдача

    def open_photo(self, actor: Actor, employee_id: uuid.UUID):
        self.access.require(actor, "employees.read")
        employee = (
            Employee.objects.filter(
                id=employee_id, organization_id=actor.organization_id
            )
            .select_related("photo")
            .first()
        )
        if employee is None or employee.photo is None:
            raise NotFound("Фотографии нет")
        return open_stored(employee.photo), employee.photo

    def open_document(
        self, actor: Actor, employee_id: uuid.UUID, document_id: uuid.UUID
    ):
        self.access.require(actor, "employees.read")
        document = (
            EmployeeDocument.objects.filter(
                id=document_id,
                employee_id=employee_id,
                organization_id=actor.organization_id,
            )
            .select_related("file")
            .first()
        )
        if document is None:
            raise NotFound("Документ не найден")
        if document.file is None:
            raise NotFound("К этому документу файл не приложен")
        return open_stored(document.file), document.file


__all__ = [
    "DOCUMENT_MAX_BYTES",
    "DOCUMENT_TYPES",
    "EmployeeAttachmentService",
    "PHOTO_MAX_BYTES",
    "PHOTO_TYPES",
]
