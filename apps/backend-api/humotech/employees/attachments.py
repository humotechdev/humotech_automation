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
from humotech.employees.selectors import require_visible_employee
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
            # Только кадровые загрузки (`upload` выше кладёт их под
            # `employees/`). Иначе по UUID к «документам» сотрудника
            # привязывалась бы чужая справка больничного или файл
            # обращения — и скачивалась бы мимо `absences.read`.
            storage_key__startswith="employees/",
        )
        found = {row.id: row for row in rows}
        missing = [str(one) for one in file_ids if one not in found]
        if missing:
            raise ValidationFailed(
                "Файл не найден — загрузите его заново",
                details={"field": "file_id", "missing": missing},
            )
        return found

    # --------------------------------------------------------------- документы

    def attach_document(
        self,
        actor: Actor,
        employee_id: uuid.UUID,
        *,
        kind: str,
        file_id: uuid.UUID,
        title: str | None = None,
    ) -> EmployeeDocument:
        """Приложить бумагу уже заведённому сотруднику.

        Строка чек-листа ЗАПОЛНЯЕТСЯ, а не дублируется: на сотрудника
        приходится одна бумага каждого вида — это правило базы
        (`uq_employee_documents_kind`), и вторая вставка порвала бы
        запрос на IntegrityError. «Прочее» из правила исключено, и
        каждый такой файл получает собственную строку.

        Замена файла не удаляет прежний из хранилища: на него может
        ссылаться журнал, а история кадровых бумаг важнее места на диске.
        """
        self.access.require(actor, "employees.manage")
        employee = require_visible_employee(self.access, actor, employee_id)
        stored = self.take(actor, [file_id])[file_id]
        # Поле модели — `original_filename`; прежнее `original_name` роняло
        # каждое прикрепление без заголовка в 500.
        name = (title or "").strip() or stored.original_filename or "Документ"

        row = None
        if kind != "OTHER":
            row = EmployeeDocument.objects.filter(
                employee_id=employee.id,
                organization_id=actor.organization_id,
                kind=kind,
            ).first()

        before = None
        if row is None:
            row = EmployeeDocument.objects.create(
                organization_id=actor.organization_id,
                employee_id=employee.id,
                kind=kind,
                title=name,
                status="UPLOADED",
                file=stored,
            )
        else:
            before = {"title": row.title, "status": row.status,
                      "file_id": str(row.file_id) if row.file_id else None}
            row.title = name
            row.status = "UPLOADED"
            row.file = stored
            row.save(update_fields=["title", "status", "file", "updated_at"])

        self.audit.record(
            actor, action="employee.document.attach",
            entity_type="employee_documents", entity_id=row.id,
            before=before,
            after={"title": row.title, "status": row.status,
                   "file_id": str(row.file_id)},
        )
        return row

    def detach_document(
        self, actor: Actor, employee_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """Снять файл с бумаги.

        Строка чек-листа остаётся и возвращается в исходное состояние:
        «паспорта нет» — это факт, который кадровику нужно видеть, а не
        отсутствие строки. Свободная бумага («прочее») исчезает целиком:
        её никто не требовал, и пустая строка была бы мусором.
        """
        self.access.require(actor, "employees.manage")
        employee = require_visible_employee(self.access, actor, employee_id)
        row = EmployeeDocument.objects.filter(
            id=document_id,
            employee_id=employee.id,
            organization_id=actor.organization_id,
        ).first()
        if row is None:
            raise NotFound("Документ не найден")

        before = {"title": row.title, "status": row.status,
                  "file_id": str(row.file_id) if row.file_id else None}
        if row.kind == "OTHER":
            row.delete()
            after = None
        else:
            from humotech.employees.onboarding import REQUIRED_DOCUMENTS

            planned = {kind: (title, status)
                       for kind, title, status in REQUIRED_DOCUMENTS}
            title, status = planned.get(row.kind, (row.title, "MISSING"))
            row.title = title
            row.status = status
            row.file = None
            row.save(update_fields=["title", "status", "file", "updated_at"])
            after = {"title": row.title, "status": row.status, "file_id": None}

        self.audit.record(
            actor, action="employee.document.detach",
            entity_type="employee_documents", entity_id=document_id,
            before=before, after=after,
        )

    def set_photo(self, actor: Actor, employee_id: uuid.UUID, file_id: uuid.UUID):
        """Заменить фотографию в карточке."""
        self.access.require(actor, "employees.manage")
        employee = require_visible_employee(self.access, actor, employee_id)
        stored = self.take(actor, [file_id])[file_id]
        before = {"photo_id": str(employee.photo_id) if employee.photo_id else None}
        employee.photo = stored
        employee.save(update_fields=["photo", "updated_at"])
        self.audit.record(
            actor, action="employee.photo.set", entity_type="employees",
            entity_id=employee.id, before=before,
            after={"photo_id": str(employee.photo_id)},
        )
        return stored

    # ------------------------------------------------------------------ выдача

    def open_photo(self, actor: Actor, employee_id: uuid.UUID):
        self.access.require(actor, "employees.read")
        # Та же проверка, что у карточки: организация И область. Одной
        # организации мало — фото человека чужого региона закрыто так же,
        # как его карточка.
        require_visible_employee(self.access, actor, employee_id)
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
        require_visible_employee(self.access, actor, employee_id)
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
