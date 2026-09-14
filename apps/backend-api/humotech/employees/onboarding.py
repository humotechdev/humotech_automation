"""Приём сотрудника одной операцией.

Форма «Новый сотрудник» заполняется один раз, а в базе после неё должно
появиться шесть разных вещей: карточка, табельный номер, назначение,
график, чек-лист документов и заготовка доступа к боту. По отдельности их
заводили руками, и на каждом шаге можно было остановиться — в базе
оставался человек без назначения (его не видит ни один HR с
территориальной областью) или назначение без графика (его не считает
посещаемость).

Поэтому здесь не новая логика, а сборка существующей: карточку и
назначение по-прежнему делает `EmployeeService.create`, график —
`WorkScheduleService.assign_to_employee`, ссылку на бота —
`TelegramLinkService.create_invitation`. Своего здесь только то, чего не
было нигде: табельный номер, чек-лист документов и защита от повторного
нажатия.

Всё это выполняется в одной транзакции. Половина приёма хуже, чем отказ:
отказ виден и его повторяют, а половина тихо живёт в базе.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from django.db.models import Q
from django.utils import timezone

from humotech.core.errors import Conflict, ValidationFailed
from humotech.core.rbac import Actor
from humotech.core.service import BaseService
from humotech.core.validation import (
    clean_text,
    validate_email,
    validate_phone,
    validate_pinfl,
)
from humotech.employees.attachments import EmployeeAttachmentService
from humotech.employees.models import (
    Employee,
    EmployeeDocument,
    EmployeeOnboardingKey,
)
from humotech.employees.services import EmployeeCard, EmployeeService
from humotech.schedules.services import WorkScheduleService
from humotech.telegram.models import TelegramAccount
from humotech.telegram.services import TelegramLinkService

#: Чек-лист, который заводится каждому принятому сотруднику.
#:
#: Договор и приказ печатает кадровик позже, поэтому их начальное состояние
#: — «будет сформирован», а не «не добавлен»: второе означало бы упущение,
#: хотя упущения нет.
REQUIRED_DOCUMENTS = (
    ("IDENTITY", "Паспорт или ID-карта", "MISSING"),
    ("CONTRACT", "Трудовой договор", "GENERATED_LATER"),
    ("HIRE_ORDER", "Приказ о приёме", "GENERATED_LATER"),
)

#: Табельный номер вида `HT-0001`.
NUMBER_PREFIX = "HT-"
NUMBER_DIGITS = 4
_NUMBER_RE = re.compile(r"^(?:[A-Za-z]+-)?(\d+)$")

#: Насколько далеко вперёд разрешено оформлять выход на работу.
MAX_FUTURE_DAYS = 365


@dataclass(frozen=True)
class TelegramOutcome:
    """Чем закончилась подготовка доступа к боту."""

    state: str
    link: str | None = None
    message: str = ""


@dataclass(frozen=True)
class Onboarded:
    """Результат приёма: карточка и отчёт по каждому шагу."""

    card: EmployeeCard
    created: bool
    schedule_assigned: bool
    telegram: TelegramOutcome
    documents: list[EmployeeDocument] = field(default_factory=list)


class EmployeeOnboardingService(BaseService):
    """Один приём сотрудника — одна транзакция."""

    def __init__(self) -> None:
        super().__init__()
        self.employees = EmployeeService()
        self.schedules = WorkScheduleService()
        self.telegram = TelegramLinkService()
        self.attachments = EmployeeAttachmentService()

    # ------------------------------------------------------------------ приём

    def onboard(
        self,
        actor: Actor,
        *,
        idempotency_key: str,
        first_name: str,
        last_name: str,
        hire_date: date,
        office_id: uuid.UUID,
        pinfl: str,
        phone: str,
        schedule_id: uuid.UUID,
        position_id: uuid.UUID,
        department_id: uuid.UUID,
        employment_type: str = "FULL_TIME",
        work_mode: str = "ONSITE",
        middle_name: str | None = None,
        birth_date: date | None = None,
        corporate_email: str | None = None,
        region_id: uuid.UUID | None = None,
        manager_employee_id: uuid.UUID | None = None,
        employment_status: str = "ACTIVE",
        telegram_username: str | None = None,
        telegram_user_id: int | None = None,
        preferred_language: str = "ru",
        gender: str | None = None,
        marital_status: str | None = None,
        photo_file_id: uuid.UUID | None = None,
        documents: Sequence[dict] | None = None,
    ) -> Onboarded:
        """Создать сотрудника со всем, что нужно ему для работы."""
        self.access.require(actor, "employees.manage")

        key = clean_text(
            idempotency_key, field="idempotency_key", required=True, max_length=100
        )
        assert key is not None  # required=True гарантирует значение

        # Повтор того же нажатия отдаёт уже созданного сотрудника. Это не
        # «ничего не делать»: клиент, потерявший ответ, обязан получить ту же
        # карточку, иначе он решит, что приём не прошёл, и повторит его
        # руками — уже с новым ключом и новым человеком в базе.
        already = (
            EmployeeOnboardingKey.objects.filter(
                organization_id=actor.organization_id, key=key
            )
            .values_list("employee_id", flat=True)
            .first()
        )
        if already is not None:
            return Onboarded(
                card=self._card_on_first_day(actor, already),
                created=False,
                schedule_assigned=True,
                telegram=self._telegram_state(already),
            )

        pinfl_value = validate_pinfl(pinfl, field="pinfl", required=True)
        assert pinfl_value is not None
        phone_value = validate_phone(phone)
        if phone_value is None:
            raise ValidationFailed("Укажите телефон", details={"field": "phone"})
        email_value = validate_email(corporate_email, field="corporate_email")
        self._require_unique(
            actor,
            pinfl=pinfl_value,
            phone=phone_value,
            corporate_email=email_value,
        )

        # Дата выхода в будущем законна: человека оформляют заранее, и график
        # начинает действовать с его первого дня, а не с сегодняшнего.
        # Ограничение только сверху — на случай опечатки в годе.
        if (hire_date - timezone.localdate()).days > MAX_FUTURE_DAYS:
            raise ValidationFailed(
                "Дата начала работы дальше чем на год вперёд — похоже на опечатку",
                details={"field": "hire_date", "value": str(hire_date)},
            )

        # Файлы проверяются ДО первой вставки: чужой или несуществующий
        # идентификатор обязан отменить приём целиком, а не оставить
        # сотрудника без бумаг, о которых кадровик уверен, что приложил.
        wanted = list(documents or [])
        files = self.attachments.take(
            actor,
            [one["file_id"] for one in wanted]
            + ([photo_file_id] if photo_file_id else []),
        )

        employee_id, telegram, papers = self._attempt(
            actor,
            key=key,
            fields={
                "first_name": first_name,
                "last_name": last_name,
                "middle_name": middle_name,
                "hire_date": hire_date,
                "office_id": office_id,
                "region_id": region_id,
                "department_id": department_id,
                "position_id": position_id,
                "manager_employee_id": manager_employee_id,
                "employment_type": employment_type,
                "work_mode": work_mode,
                "employment_status": employment_status,
                "phone": phone_value,
                "corporate_email": email_value,
                "birth_date": birth_date,
                "preferred_language": preferred_language,
            },
            pinfl=pinfl_value,
            schedule_id=schedule_id,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            extra={
                "gender": gender or None,
                "marital_status": marital_status or None,
                "photo_id": photo_file_id,
            },
            attached=wanted,
            files=files,
        )
        return Onboarded(
            card=self._card_on_first_day(actor, employee_id),
            created=True,
            schedule_assigned=True,
            telegram=telegram,
            documents=papers,
        )

    def _card_on_first_day(self, actor: Actor, employee_id: uuid.UUID) -> EmployeeCard:
        """Карточка на дату выхода, а не на сегодня.

        Сотрудника оформляют заранее, и на сегодняшний день его назначение
        ещё не действует: карточка «на сегодня» вернула бы человека без
        офиса и без графика — ровно то, чего приём и не допускает. Экран
        показывает первый рабочий день, и сервер отвечает про него же.
        """
        hire_date = Employee.objects.values_list("hire_date", flat=True).get(
            pk=employee_id
        )
        return self.employees.get(actor, employee_id, at=hire_date)

    def _attempt(
        self,
        actor: Actor,
        *,
        key: str,
        fields: dict,
        pinfl: str,
        schedule_id: uuid.UUID,
        telegram_user_id: int | None,
        telegram_username: str | None,
        extra: dict,
        attached: Sequence[dict],
        files: dict,
        tries: int = 3,
    ) -> tuple[uuid.UUID, TelegramOutcome, list[EmployeeDocument]]:
        """Приём с повтором, если табельный номер успели занять.

        Номер выбирается по максимальному из выданных, и между выбором и
        вставкой всегда есть зазор: два одновременных приёма выберут одно и
        то же число. Побеждает вставивший первым, второй повторяет попытку
        уже с новым максимумом. Правой при этом остаётся база: уникальность
        номера проверяет она, а не эта функция.
        """
        for attempt in range(tries):
            try:
                with self.atomic():
                    card = self.employees.create(
                        actor, employee_number=self._next_number(actor), **fields
                    )
                    employee_id = card.employee.id
                    # ПИНФЛ не принимает `EmployeeService.create`: он часть
                    # приёма, а не общей кадровой правки. Пишется тем же
                    # запросом, внутри той же транзакции.
                    # Анкетные поля и фотография идут тем же запросом:
                    # `EmployeeService.create` их не принимает — это часть
                    # приёма, а не общей кадровой правки.
                    Employee.objects.filter(pk=employee_id).update(
                        pinfl=pinfl, **extra
                    )

                    self.schedules.assign_to_employee(
                        actor,
                        employee_id=employee_id,
                        schedule_id=schedule_id,
                        valid_from=fields["hire_date"],
                    )
                    documents = self._create_documents(
                        actor, employee_id, attached=attached, files=files
                    )
                    telegram = self._prepare_telegram(
                        actor,
                        employee_id=employee_id,
                        telegram_user_id=telegram_user_id,
                        telegram_username=telegram_username,
                    )
                    EmployeeOnboardingKey.objects.create(
                        organization_id=actor.organization_id,
                        key=key,
                        employee_id=employee_id,
                        created_by_user_id=actor.user_id,
                    )
                    self.audit.record(
                        actor,
                        action="employee.onboard",
                        entity_type="employees",
                        entity_id=employee_id,
                        after={
                            "employee_number": card.employee.employee_number,
                            "office_id": str(fields["office_id"]),
                            "schedule_id": str(schedule_id),
                            "hire_date": str(fields["hire_date"]),
                            "telegram": telegram.state,
                            "idempotency_key": key,
                        },
                    )
                return employee_id, telegram, documents
            except Conflict as exc:
                taken = "табельным номером" in self._reason(exc)
                if taken and attempt < tries - 1:
                    continue
                raise
        raise Conflict("Не удалось подобрать свободный табельный номер")

    # -------------------------------------------------------------- проверки

    def _require_unique(
        self,
        actor: Actor,
        *,
        pinfl: str,
        phone: str | None,
        corporate_email: str | None,
    ) -> None:
        """Дубликаты ищутся по всей организации, а не по видимым офисам.

        Иначе HR одного региона завёл бы человека, который уже работает в
        соседнем: его записей он не видит, и проверка молча прошла бы.
        Поэтому здесь прямой запрос по организации, без сужения по области.
        """
        taken = Employee.objects.filter(organization_id=actor.organization_id)
        checks: list[tuple[str, str, str]] = [
            ("pinfl", pinfl, "Сотрудник с таким ПИНФЛ уже есть"),
        ]
        if phone:
            checks.append(("phone", phone, "Сотрудник с таким телефоном уже есть"))
        if corporate_email:
            checks.append(
                ("corporate_email", corporate_email,
                 "Сотрудник с таким email уже есть")
            )

        for name, value, message in checks:
            if name == "corporate_email":
                lookup = Q(corporate_email__iexact=value) | Q(
                    personal_email__iexact=value
                )
            else:
                lookup = Q(**{name: value})
            if taken.filter(lookup).exists():
                raise Conflict(message, details={"field": name, "value": value})

    # ----------------------------------------------------------- табельный №

    def _next_number(self, actor: Actor) -> str:
        """Следующий свободный табельный номер организации.

        Считается по наибольшему из уже выданных, а не по количеству строк:
        уволенные остаются в базе, и счёт по количеству рано или поздно
        предложил бы занятый номер.
        """
        numbers = Employee.objects.filter(
            organization_id=actor.organization_id
        ).values_list("employee_number", flat=True)
        highest = 0
        for raw in numbers:
            match = _NUMBER_RE.match((raw or "").strip())
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{NUMBER_PREFIX}{highest + 1:0{NUMBER_DIGITS}d}"

    # ------------------------------------------------------------- документы

    def _create_documents(
        self,
        actor: Actor,
        employee_id: uuid.UUID,
        *,
        attached: Sequence[dict] = (),
        files: dict | None = None,
    ) -> list[EmployeeDocument]:
        """Чек-лист бумаг плюс то, что кадровик приложил прямо в форме.

        Приложенный паспорт ДОПОЛНЯЕТ строку чек-листа, а не заводит
        вторую: на сотрудника приходится одна бумага каждого вида, это
        правило базы (`uq_employee_documents_kind`), и вторая вставка
        порвала бы весь приём на IntegrityError. Исключение — «прочее»:
        его может быть сколько угодно, и каждый такой файл получает
        собственную строку.
        """
        files = files or {}
        # По одному файлу на вид; «прочее» собирается отдельным списком.
        by_kind: dict[str, dict] = {}
        others: list[dict] = []
        for one in attached:
            if one["kind"] == "OTHER":
                others.append(one)
            else:
                by_kind[one["kind"]] = one

        rows: list[EmployeeDocument] = []
        for kind, title, status in REQUIRED_DOCUMENTS:
            got = by_kind.pop(kind, None)
            rows.append(
                EmployeeDocument(
                    organization_id=actor.organization_id,
                    employee_id=employee_id,
                    kind=kind,
                    title=self._title(got, title, files),
                    status="UPLOADED" if got else status,
                    file=files.get(got["file_id"]) if got else None,
                )
            )

        # Вид, которого нет в чек-листе, но файл для него прислан.
        for kind, got in by_kind.items():
            rows.append(
                EmployeeDocument(
                    organization_id=actor.organization_id,
                    employee_id=employee_id,
                    kind=kind,
                    title=self._title(got, kind, files),
                    status="UPLOADED",
                    file=files.get(got["file_id"]),
                )
            )

        for got in others:
            rows.append(
                EmployeeDocument(
                    organization_id=actor.organization_id,
                    employee_id=employee_id,
                    kind="OTHER",
                    title=self._title(got, "Прочее", files),
                    status="UPLOADED",
                    file=files.get(got["file_id"]),
                )
            )

        return EmployeeDocument.objects.bulk_create(rows)

    @staticmethod
    def _title(got: dict | None, fallback: str, files: dict) -> str:
        """Название строки: что прислали, иначе имя файла, иначе вид бумаги."""
        if got is None:
            return fallback
        given = (got.get("title") or "").strip()
        if given:
            return given[:255]
        record = files.get(got["file_id"])
        name = getattr(record, "original_filename", "") or ""
        return (name or fallback)[:255]

    # --------------------------------------------------------------- telegram

    def _prepare_telegram(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID,
        telegram_user_id: int | None,
        telegram_username: str | None,
    ) -> TelegramOutcome:
        """Подготовка доступа к боту.

        Две ветки, и обе честные.

        Известен числовой `telegram_user_id` — привязка создаётся сразу: это
        единственный идентификатор, который Telegram не даёт подменить.

        Не известен — выдаётся одноразовая ссылка. Написать человеку первым
        бот не может, запустить бот на его телефоне тоже: пока сотрудник сам
        не нажмёт Start, чата попросту не существует. Поэтому «доступ
        подготовлен» — это максимум, который здесь возможен, и так это и
        называется.

        `@username` не служит доказательством личности ни в одной из веток:
        имя меняется и передаётся другому человеку, а отметки о приходе на
        работу привязались бы к прежнему владельцу.
        """
        username = clean_text(
            telegram_username, field="telegram_username", max_length=255
        )
        if username:
            username = username.lstrip("@") or None

        if telegram_user_id is not None:
            return self._bind_known_account(
                actor,
                employee_id=employee_id,
                telegram_user_id=telegram_user_id,
                username=username,
            )

        try:
            issued = self.telegram.create_invitation(actor, employee_id)
        except Exception as exc:  # noqa: BLE001 — причина уходит в ответ
            # Нет права на Telegram или не настроено имя бота — это не повод
            # отменять приём: карточка, назначение и график уже правильные, а
            # ссылку выдадут позже из карточки сотрудника.
            return TelegramOutcome(state="SKIPPED", message=self._reason(exc))
        return TelegramOutcome(
            state="INVITED",
            link=issued.link,
            message="Ссылка одноразовая: сотрудник открывает её и нажимает Start",
        )

    def _bind_known_account(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID,
        telegram_user_id: int,
        username: str | None,
    ) -> TelegramOutcome:
        if TelegramAccount.objects.filter(
            organization_id=actor.organization_id,
            telegram_user_id=telegram_user_id,
            status__in=("ACTIVE", "PENDING"),
        ).exists():
            raise Conflict(
                "Этот Telegram уже привязан к другому сотруднику",
                details={"telegram_user_id": telegram_user_id},
            )
        TelegramAccount.objects.create(
            organization_id=actor.organization_id,
            employee_id=employee_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_user_id,
            telegram_username=username,
            status="ACTIVE",
            connected_at=timezone.now(),
        )
        Employee.objects.filter(pk=employee_id).update(telegram_connected=True)
        self.audit.record(
            actor,
            action="telegram.account.link",
            entity_type="telegram_accounts",
            entity_id=employee_id,
            after={"telegram_user_id": telegram_user_id, "source": "onboarding"},
        )
        return TelegramOutcome(state="CONNECTED", message="Telegram подключён")

    def _telegram_state(self, employee_id: uuid.UUID) -> TelegramOutcome:
        """Состояние доступа для повторного ответа по тому же ключу.

        Ссылка здесь не повторяется намеренно: она одноразовая и показана
        один раз. Повторный запрос — не повод выдавать вторую.
        """
        connected = TelegramAccount.objects.filter(
            employee_id=employee_id, status="ACTIVE"
        ).exists()
        return TelegramOutcome(
            state="CONNECTED" if connected else "INVITED",
            message="Сотрудник уже добавлен этим запросом",
        )

    @staticmethod
    def _reason(exc: Exception) -> str:
        detail = getattr(exc, "detail", None) or getattr(exc, "message", None)
        return str(detail or exc)
