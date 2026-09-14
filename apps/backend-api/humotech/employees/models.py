"""Сотрудник, история его назначений и доступ к дополнительным офисам."""

from __future__ import annotations

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.db import models
from django.db.models import F, Func, Q, Value

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    EMPLOYEE_DOCUMENT_KINDS,
    EMPLOYEE_DOCUMENT_STATUSES,
    EMPLOYMENT_STATUSES,
    EMPLOYMENT_TYPES,
    GENDERS,
    MARITAL_STATUSES,
    OFFICE_ACCESS_TYPES,
    WORK_MODES,
    choices,
    status_check,
)
from humotech.core.models import (
    ArchivableModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


def validity_daterange() -> Func:
    """`daterange(valid_from, valid_to, '[]')` — период с ВКЛЮЧИТЕЛЬНЫМИ границами.

    Именно `'[]'`, а не значение по умолчанию `'[)'`: день окончания входит
    в период. Отсюда следует правило, на которое опирается весь перенос
    сотрудника: закрывать предыдущий период надо датой на день РАНЬШЕ начала
    нового, иначе общий день считается пересечением и строка не вставится.
    """
    return Func(
        F("valid_from"), F("valid_to"), Value("[]"),
        function="daterange",
        output_field=DateRangeField(),
    )


class Employee(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    """Сотрудник никогда не удаляется физически: у него есть история отметок."""

    employee_number = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    corporate_email = models.CharField(max_length=255, null=True, blank=True)
    personal_email = models.CharField(max_length=255, null=True, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    # Анкетные поля. Оба необязательны по той же причине, что и ПИНФЛ:
    # у заведённых раньше сотрудников их нет, и пустое значение здесь
    # означает «не указано», а не «неизвестного пола».
    gender = models.CharField(
        max_length=10, choices=choices(GENDERS), null=True, blank=True
    )
    marital_status = models.CharField(
        max_length=20, choices=choices(MARITAL_STATUSES), null=True, blank=True
    )
    # Фотография — обычный приложенный файл, а не колонка с путём: она
    # лежит в приватном хранилище и отдаётся view, который сначала
    # спрашивает, кому можно. PROTECT — чтобы файл нельзя было удалить
    # из-под живой карточки.
    photo = models.ForeignKey(
        "files.File",
        on_delete=models.PROTECT,
        db_column="photo_file_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    # ПИНФЛ. Необязателен: у сотрудников, заведённых до появления поля, его
    # нет, и требовать его задним числом означало бы не дать открыть их
    # карточку. У новых он обязателен — это проверяет сервис приёма.
    pinfl = models.CharField(max_length=14, null=True, blank=True)
    hire_date = models.DateField()
    termination_date = models.DateField(null=True, blank=True)
    preferred_language = models.CharField(max_length=10, db_default="ru")
    employment_status = models.CharField(
        max_length=30, choices=choices(EMPLOYMENT_STATUSES)
    )
    # денормализованный флаг для быстрых выборок; источник правды — telegram_accounts
    telegram_connected = models.BooleanField(db_default=False)

    class Meta:
        db_table = "employees"
        verbose_name = "сотрудник"
        verbose_name_plural = "сотрудники"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "employee_number"],
                name="uq_employees_org_number",
            ),
            # ПИНФЛ уникален в организации, а не глобально: одна и та же
            # физическая персона может числиться в двух организациях базы.
            # Условие на NOT NULL обязательно — иначе строки без ПИНФЛ
            # считались бы совпадающими в Postgres по-другому, чем ожидает
            # сервис, и старые записи нельзя было бы хранить рядом.
            models.UniqueConstraint(
                fields=["organization", "pinfl"],
                condition=Q(pinfl__isnull=False),
                name="uq_employees_org_pinfl",
            ),
            status_check(
                "employment_status", EMPLOYMENT_STATUSES,
                "ck_employees_employment_status",
            ),
            status_check("gender", GENDERS, "ck_employees_gender", nullable=True),
            status_check(
                "marital_status", MARITAL_STATUSES,
                "ck_employees_marital_status", nullable=True,
            ),
            raw_check(
                "termination_date IS NULL OR termination_date >= hire_date",
                "ck_employees_termination_after_hire",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"], name="ix_employees_organization_id"),
            models.Index(
                fields=["organization", "employment_status"],
                name="ix_employees_org_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_number} {self.last_name} {self.first_name}"


class EmployeeAssignment(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """История переводов: офис, отдел, должность, руководитель на период.

    Текущее назначение — то, у которого valid_to IS NULL либо период включает
    сегодняшнюю дату. Основное назначение (is_primary) определяет офис сотрудника
    по умолчанию для QR-отметки.
    """

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="assignments",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        related_name="assignments",
    )
    department = models.ForeignKey(
        "departments.Department",
        on_delete=models.PROTECT,
        db_column="department_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="assignments",
    )
    position = models.ForeignKey(
        "positions.Position",
        on_delete=models.PROTECT,
        db_column="position_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="assignments",
    )
    manager_employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        db_column="manager_employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="subordinate_assignments",
    )
    employment_type = models.CharField(
        max_length=30, choices=choices(EMPLOYMENT_TYPES)
    )
    work_mode = models.CharField(max_length=30, choices=choices(WORK_MODES))
    is_primary = models.BooleanField(db_default=True)
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "employee_assignments"
        verbose_name = "назначение сотрудника"
        verbose_name_plural = "назначения сотрудников"
        constraints = [
            status_check(
                "employment_type", EMPLOYMENT_TYPES,
                "ck_employee_assignments_employment_type",
            ),
            status_check(
                "work_mode", WORK_MODES, "ck_employee_assignments_work_mode"
            ),
            raw_check(
                "valid_to IS NULL OR valid_to >= valid_from",
                "ck_employee_assignments_valid_period",
            ),
            raw_check(
                "manager_employee_id IS NULL OR manager_employee_id <> employee_id",
                "ck_employee_assignments_no_self_manager",
            ),
            # У сотрудника не может быть двух пересекающихся ОСНОВНЫХ назначений.
            # Требует расширения btree_gist: без него gist-индекс не примет
            # колонку uuid рядом с диапазоном.
            ExclusionConstraint(
                name="ex_employee_assignments_primary_overlap",
                expressions=[
                    ("employee_id", RangeOperators.EQUAL),
                    (validity_daterange(), RangeOperators.OVERLAPS),
                ],
                condition=Q(is_primary=True),
                index_type="GIST",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_employee_assignments_organization_id"
            ),
            models.Index(
                fields=["employee"], name="ix_employee_assignments_employee_id"
            ),
            models.Index(fields=["office"], name="ix_employee_assignments_office_id"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} @ {self.office_id} с {self.valid_from}"


class EmployeeOfficeAccess(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Право отмечаться в дополнительном офисе, помимо основного назначения."""

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="office_access",
    )
    office = models.ForeignKey(
        "offices.Office",
        on_delete=models.PROTECT,
        db_column="office_id",
        db_index=False,
        related_name="extra_access",
    )
    access_type = models.CharField(
        max_length=30, choices=choices(OFFICE_ACCESS_TYPES)
    )
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    # админа могут удалить — история доступа от этого исчезать не должна
    granted_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="granted_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "employee_office_access"
        verbose_name = "доступ к офису"
        verbose_name_plural = "доступы к офисам"
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "office", "valid_from"],
                name="uq_employee_office_access_period",
            ),
            status_check(
                "access_type", OFFICE_ACCESS_TYPES,
                "ck_employee_office_access_access_type",
            ),
            raw_check(
                "valid_to IS NULL OR valid_to >= valid_from",
                "ck_employee_office_access_valid_period",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"],
                name="ix_employee_office_access_organization_id",
            ),
            models.Index(
                fields=["employee"], name="ix_employee_office_access_employee_id"
            ),
            models.Index(
                fields=["office"], name="ix_employee_office_access_office_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} -> {self.office_id}"


class EmployeeDocument(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Чек-лист бумаг сотрудника: что нужно, что уже есть, чего ждём.

    Отдельная таблица, а не поля в карточке: список требуемых документов
    со временем меняется, и хранить его колонками означало бы миграцию на
    каждую новую бумагу. Файл здесь не обязателен — строка со статусом
    «будет сформирован» существует именно для того, чтобы сказать, что
    документа пока нет и это нормально.
    """

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="documents",
    )
    kind = models.CharField(max_length=30, choices=choices(EMPLOYEE_DOCUMENT_KINDS))
    title = models.CharField(max_length=255)
    status = models.CharField(
        max_length=30, choices=choices(EMPLOYEE_DOCUMENT_STATUSES)
    )
    file = models.ForeignKey(
        "files.File",
        on_delete=models.PROTECT,
        db_column="file_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="employee_documents",
    )

    class Meta:
        db_table = "employee_documents"
        verbose_name = "документ сотрудника"
        verbose_name_plural = "документы сотрудника"
        constraints = [
            status_check(
                "kind", EMPLOYEE_DOCUMENT_KINDS, "ck_employee_documents_kind"
            ),
            status_check(
                "status", EMPLOYEE_DOCUMENT_STATUSES, "ck_employee_documents_status"
            ),
            # Одна бумага одного вида на сотрудника; «прочее» может быть
            # любым числом, поэтому оно из правила исключено.
            models.UniqueConstraint(
                fields=["employee", "kind"],
                condition=~Q(kind="OTHER"),
                name="uq_employee_documents_kind",
            ),
            raw_check(
                "status <> 'UPLOADED' OR file_id IS NOT NULL",
                "ck_employee_documents_uploaded_has_file",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_employee_documents_org_id"
            ),
            models.Index(
                fields=["employee"], name="ix_employee_documents_employee_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} {self.kind} {self.status}"


class EmployeeOnboardingKey(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Ключ одного нажатия кнопки «Добавить сотрудника».

    Двойное нажатие, потерянный ответ и повтор из-за обрыва связи — это
    три разных истории с одним исходом: запрос приходит дважды. Ключ
    придумывает клиент один раз на форму; повтор с тем же ключом отдаёт
    того же созданного сотрудника, а не заводит второго.

    Уникальность на уровне базы, а не проверкой перед вставкой: два
    запроса, пришедшие одновременно, обе проверки прошли бы.
    """

    key = models.CharField(max_length=100)
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="onboarding_keys",
    )
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        db_column="created_by_user_id",
        db_index=False,
        related_name="employee_onboarding_keys",
    )

    class Meta:
        db_table = "employee_onboarding_keys"
        verbose_name = "ключ добавления сотрудника"
        verbose_name_plural = "ключи добавления сотрудников"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "key"], name="uq_employee_onboarding_keys"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_employee_onboarding_org_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key} -> {self.employee_id}"
