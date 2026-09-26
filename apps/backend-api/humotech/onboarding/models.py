"""Первичное ознакомление: что человек прочитал и с чем согласился.

Две половины с разной природой, и смешивать их нельзя.

**Информационные карточки** рассказывают о компании. Их читают, нажимают
«Я ознакомился», и это факт прочтения, а не обязательство. Правка текста
карточки не обязывает никого перечитывать её заново.

**Обязательные документы** — правила, политика, кодекс. Здесь важно не
«прочитал», а «согласился с этой редакцией». Поэтому у документа есть
версии, подтверждение всегда относится к КОНКРЕТНОЙ версии, а публикация
новой редакции возвращает сотрудника к подтверждению — не к чтению
десяти карточек заново.

Отсюда семь таблиц:

  * `onboarding_programs`  — программа: набор карточек организации;
  * `onboarding_sections`  — одна карточка;
  * `employee_onboarding`  — где человек в программе;
  * `employee_onboarding_section_acks` — какую карточку он подтвердил;
  * `policy_documents` — обязательный документ как таковой;
  * `policy_document_versions` — его редакция: текст, файл, дата выпуска;
  * `employee_policy_acceptances` — решение человека по одной редакции.

Про `employee_onboarding.status` отдельно и важно. Он **вычисляемый**:
хранится ради списков и фильтров, но правду о допуске говорит пересчёт
в `humotech/onboarding/progress.py`. Если гейт станет смотреть на
колонку, публикация новой редакции документа перестанет кого-либо
закрывать — у уже «завершивших» там останется COMPLETED.

Приглашения здесь своей таблицы НЕ заводят. Персональная одноразовая
ссылка уже есть — `telegram_link_invitations`: хеш токена вместо токена,
срок, отзыв, привязка к погасившему её Telegram ID, одна живая ссылка на
сотрудника, журнал. Вторая такая таблица означала бы две двери в один
дом, и закрыть за собой обе никто не вспомнит.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from humotech.core.constraints import raw_check
from humotech.core.enums import (
    ONBOARDING_STATUSES,
    POLICY_DECISIONS,
    POLICY_VERSION_STATUSES,
    choices,
    status_check,
)
from humotech.core.models import (
    ArchivableModel,
    CreatedAtModel,
    OrganizationScopedModel,
    TimestampedModel,
    UUIDPrimaryKeyModel,
)


class OnboardingProgram(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    """Набор информационных карточек организации.

    Программ может быть несколько — для разных ролей когда-нибудь
    понадобятся разные вводные, — но действующая в один момент одна:
    частичный уникальный ключ по `is_active` это и держит. Иначе
    «сколько карточек всего» перестало бы иметь однозначный ответ.
    """

    code = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(db_default=True)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="created_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "onboarding_programs"
        verbose_name = "программа ознакомления"
        verbose_name_plural = "программы ознакомления"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="uq_onboarding_programs_code",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=Q(is_active=True, archived_at__isnull=True),
                name="uq_onboarding_programs_active",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"],
                name="ix_onboarding_programs_org_id",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class OnboardingSection(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    """Одна информационная карточка.

    `version` растёт при правке текста и попадает в подтверждение —
    чтобы через год было видно, какую именно редакцию человек читал.
    Перечитывать заново правка НЕ требует: карточка сообщает, а не
    обязывает. Обязывают документы, и версионируются они иначе.

    `button_label` хранится у карточки, потому что в ТЗ подписи разные:
    под правилами распорядка стоит «С правилами ознакомился», под
    последней — «Завершить ознакомление». Собирать их в коде по номеру
    значило бы держать смысл текста в другом файле, чем сам текст.
    """

    program = models.ForeignKey(
        OnboardingProgram,
        on_delete=models.PROTECT,
        db_column="program_id",
        db_index=False,
        related_name="sections",
    )
    position = models.IntegerField()
    title = models.CharField(max_length=255)
    body = models.TextField()
    button_label = models.CharField(max_length=100)
    version = models.IntegerField(db_default=1)

    class Meta:
        db_table = "onboarding_sections"
        verbose_name = "карточка ознакомления"
        verbose_name_plural = "карточки ознакомления"
        constraints = [
            raw_check("position > 0", "ck_onboarding_sections_position"),
            raw_check("version > 0", "ck_onboarding_sections_version"),
            # Номер уникален среди ЖИВЫХ карточек. Архивная сохраняет
            # свой номер, потому что на неё ссылаются подтверждения, но
            # места в программе больше не занимает.
            models.UniqueConstraint(
                fields=["program", "position"],
                condition=Q(archived_at__isnull=True),
                name="uq_onboarding_sections_position",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"],
                name="ix_onboarding_sections_org_id",
            ),
            models.Index(
                fields=["program"], name="ix_onboarding_sections_program_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.position}. {self.title}"


class EmployeeOnboarding(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Где сотрудник в программе. Одна строка на человека.

    Само наличие этой строки означает «человека позвали проходить
    ознакомление». Сотрудники, работавшие до появления раздела, в
    программе не числятся; включить их можно, но это отдельное решение
    кадровика, а не побочный эффект выката.

    Доступа к боту строка не ограничивает ни в каком состоянии.
    Незавершённое ознакомление — повод напомнить и показать кадровику,
    а не повод отобрать отметку присутствия.

    `status` — вычисляемая колонка, см. модуль `progress`. Она нужна
    спискам («покажи всех, кто не начал») и фильтрам; правду говорит
    пересчёт.

    `chat_message_id` — сообщение бота, в котором показана текущая
    карточка. Хранится, чтобы редактировать его, а не слать десять
    сообщений подряд: чат сотрудника не должен превращаться в ленту из
    прочитанного.
    """

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="onboarding",
    )
    program = models.ForeignKey(
        OnboardingProgram,
        on_delete=models.PROTECT,
        db_column="program_id",
        db_index=False,
        related_name="participants",
    )
    status = models.CharField(max_length=30, choices=choices(ONBOARDING_STATUSES))
    invited_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    info_completed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_reminder_at = models.DateTimeField(null=True, blank=True)
    #: До какого дня нужно пройти ознакомление. Ставится при включении в
    #: программу и меняется кадровиком. Пусто — срок не назначен, и
    #: тогда человек не может быть «просрочен»: у тех, кого включили до
    #: появления сроков, дату задним числом никто не выдумывает.
    due_date = models.DateField(null=True, blank=True)
    #: Идентификатор сообщения бота с текущей карточкой — его правят,
    #: а не плодят новые.
    chat_message_id = models.BigIntegerField(null=True, blank=True)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="created_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "employee_onboarding"
        verbose_name = "ознакомление сотрудника"
        verbose_name_plural = "ознакомления сотрудников"
        constraints = [
            models.UniqueConstraint(
                fields=["employee"], name="uq_employee_onboarding_employee"
            ),
            status_check("status", ONBOARDING_STATUSES, "ck_employee_onboarding_status"),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_employee_onboarding_organization_id"
            ),
            models.Index(
                fields=["organization", "status"],
                name="ix_employee_onboarding_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} / {self.status}"


class EmployeeSectionAcknowledgement(
    UUIDPrimaryKeyModel, OrganizationScopedModel, CreatedAtModel
):
    """«Я ознакомился» с конкретной карточкой в конкретной редакции.

    Неизменяемая запись: прочитанное не «перепрочитывают». Повторное
    нажатие на ту же кнопку ничего не добавляет — уникальный ключ
    делает идемпотентность свойством схемы, а не аккуратности
    обработчика. Двойной тап в Telegram — обычное дело.

    `telegram_user_id` записан рядом намеренно: через год вопрос будет
    не «подтверждал ли», а «кто именно подтверждал», и ответ должен
    лежать в той же строке, что и факт.
    """

    onboarding = models.ForeignKey(
        EmployeeOnboarding,
        on_delete=models.PROTECT,
        db_column="onboarding_id",
        db_index=False,
        related_name="acknowledgements",
    )
    section = models.ForeignKey(
        OnboardingSection,
        on_delete=models.PROTECT,
        db_column="section_id",
        db_index=False,
        related_name="acknowledgements",
    )
    section_version = models.IntegerField()
    acknowledged_at = models.DateTimeField()
    telegram_user_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "employee_onboarding_section_acks"
        verbose_name = "подтверждение карточки"
        verbose_name_plural = "подтверждения карточек"
        constraints = [
            models.UniqueConstraint(
                fields=["onboarding", "section", "section_version"],
                name="uq_onboarding_section_acks",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_onboarding_acks_org_id"
            ),
            models.Index(
                fields=["onboarding"], name="ix_onboarding_acks_onboarding_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.onboarding_id} / {self.section_id} v{self.section_version}"


class PolicyCategory(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    """Раздел материалов: «Охрана труда», «HR и культура».

    Группировка для кадровика и для порядка — на то, с чем человек
    согласился, раздел не влияет. Поэтому его можно переименовать или
    убрать в архив в любой момент: история подтверждений привязана к
    редакции документа, а не к разделу.
    """

    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    #: Кто отвечает за материалы раздела — к кому идти с вопросом.
    owner_employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="owner_employee_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    position = models.IntegerField(db_default=1)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="created_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "policy_categories"
        verbose_name = "раздел материалов"
        verbose_name_plural = "разделы материалов"
        indexes = [
            models.Index(
                fields=["organization"], name="ix_policy_categories_org_id"
            ),
        ]

    def __str__(self) -> str:
        return self.title


class PolicyDocument(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel, ArchivableModel
):
    """Обязательный документ как таковой: правила, политика, кодекс.

    Текста здесь нет — он в редакции. Документ отвечает на вопрос «что
    это», редакция — «в какой формулировке». Разделение не формальное:
    подтверждение сотрудника относится к формулировке, и слить их
    значило бы сделать правку опечатки поводом закрыть бота всей
    компании либо, наоборот, молча подменить то, с чем согласились.
    """

    code = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    #: Необязательный документ показывается, но доступа не закрывает.
    is_mandatory = models.BooleanField(db_default=True)
    position = models.IntegerField(db_default=1)
    #: Раздел для порядка и поиска. Пусто — «без раздела».
    category = models.ForeignKey(
        PolicyCategory,
        on_delete=models.PROTECT,
        db_column="category_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="documents",
    )
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="created_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "policy_documents"
        verbose_name = "обязательный документ"
        verbose_name_plural = "обязательные документы"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="uq_policy_documents_code"
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_policy_documents_org_id"
            ),
        ]

    def __str__(self) -> str:
        return self.title


class PolicyDocumentVersion(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Редакция документа: текст, файл и дата публикации.

    Опубликованная редакция неизменяема. Это не удобство реализации:
    под ней стоит имя сотрудника и время, и правка текста задним числом
    означала бы, что человек согласился не с тем, что записано. Нужна
    другая формулировка — выпускается новая редакция, а прежняя
    уходит в ARCHIVED и остаётся в истории навсегда.

    Действующая редакция одна: частичный уникальный ключ по PUBLISHED.
    Черновиков может быть сколько угодно.

    `file` — утверждённый PDF. Необязателен: пока юрист не принёс
    бумагу, документ живёт текстом, и это рабочее состояние, а не
    недоделка.
    """

    document = models.ForeignKey(
        PolicyDocument,
        on_delete=models.PROTECT,
        db_column="document_id",
        db_index=False,
        related_name="versions",
    )
    version = models.CharField(max_length=20)
    #: Короткий текст, который бот показывает перед кнопкой согласия.
    summary = models.TextField()
    #: Полный текст. Именно его открывает «Открыть полный документ».
    body = models.TextField(null=True, blank=True)
    #: Подпись кнопки согласия: «С правилами согласен», «С кодексом согласен».
    agree_label = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20, choices=choices(POLICY_VERSION_STATUSES)
    )
    file = models.ForeignKey(
        "files.File",
        on_delete=models.PROTECT,
        db_column="file_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        db_column="published_by_user_id",
        db_index=False,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "policy_document_versions"
        verbose_name = "редакция документа"
        verbose_name_plural = "редакции документов"
        constraints = [
            status_check(
                "status", POLICY_VERSION_STATUSES, "ck_policy_versions_status"
            ),
            models.UniqueConstraint(
                fields=["document", "version"], name="uq_policy_versions_number"
            ),
            models.UniqueConstraint(
                fields=["document"],
                condition=Q(status="PUBLISHED"),
                name="uq_policy_versions_published",
            ),
            # Опубликованная редакция обязана знать, когда её выпустили:
            # без даты нельзя сказать, что действовало в день согласия.
            raw_check(
                "status <> 'PUBLISHED' OR published_at IS NOT NULL",
                "ck_policy_versions_published_at",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_policy_versions_org_id"
            ),
            models.Index(
                fields=["document"], name="ix_policy_versions_document_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} v{self.version}"


class EmployeePolicyAcceptance(
    UUIDPrimaryKeyModel, OrganizationScopedModel, TimestampedModel
):
    """Решение сотрудника по одной редакции документа.

    Одна строка на пару «сотрудник + редакция». Отказ и последующее
    согласие — это ОДНО решение, которое человек передумал, а не два
    разных факта: строка обновляется, а история изменений остаётся в
    `audit_logs`, где ей и место.

    Прежние редакции при выходе новой не трогаются вовсе. «Согласился с
    версией 1.0 в марте» — состоявшийся факт, и стирать его ради
    аккуратности таблицы нельзя.
    """

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        db_column="employee_id",
        db_index=False,
        related_name="policy_acceptances",
    )
    version = models.ForeignKey(
        PolicyDocumentVersion,
        on_delete=models.PROTECT,
        db_column="version_id",
        db_index=False,
        related_name="acceptances",
    )
    decision = models.CharField(max_length=20, choices=choices(POLICY_DECISIONS))
    decided_at = models.DateTimeField()
    telegram_user_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "employee_policy_acceptances"
        verbose_name = "решение по документу"
        verbose_name_plural = "решения по документам"
        constraints = [
            status_check(
                "decision", POLICY_DECISIONS, "ck_policy_acceptances_decision"
            ),
            models.UniqueConstraint(
                fields=["employee", "version"],
                name="uq_policy_acceptances_employee_version",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization"], name="ix_policy_acceptances_org_id"
            ),
            models.Index(
                fields=["employee"], name="ix_policy_acceptances_employee_id"
            ),
            models.Index(
                fields=["version"], name="ix_policy_acceptances_version_id"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} / {self.version_id} / {self.decision}"


__all__ = [
    "EmployeeOnboarding",
    "EmployeePolicyAcceptance",
    "EmployeeSectionAcknowledgement",
    "OnboardingProgram",
    "OnboardingSection",
    "PolicyDocument",
    "PolicyDocumentVersion",
]
