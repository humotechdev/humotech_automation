"""Ознакомление глазами кадровика: прогресс, разделы, документы.

Три сервиса вместо одного, потому что это три разных предмета с разными
правами:

  * `OnboardingService`  — кого позвали, кто где остановился, напоминания
    и повторные приглашения (`onboarding.read` / `onboarding.manage`);
  * `OnboardingContentService` — тексты десяти карточек
    (`onboarding.read` / `onboarding.manage`);
  * `PolicyService` — обязательные документы и их редакции. Публикация
    редакции закрывает бота всем, кто её не подтвердил, поэтому у неё
    своё право — `policies.publish`.

Приглашение выдаёт не этот модуль. Персональная одноразовая ссылка уже
живёт в `telegram.services.TelegramLinkService`, со сроком, отзывом,
хешем вместо токена и журналом; здесь только вызывается она и
подставляется другой префикс полезной нагрузки, чтобы бот понял, что
после привязки человека ждёт ознакомление, а не просто меню.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, normalize_limit, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.employees.selectors import require_visible_employee
from humotech.notifications.outbox import enqueue
from humotech.onboarding import progress as progress_module
from humotech.onboarding import seeding
from humotech.onboarding.content import ACK_DEFAULT
from humotech.audit.models import AuditLog
from humotech.onboarding.models import (
    PolicyCategory,
    EmployeeOnboarding,
    EmployeePolicyAcceptance,
    OnboardingProgram,
    OnboardingSection,
    PolicyDocument,
    PolicyDocumentVersion,
)
from humotech.onboarding.progress import Progress
from humotech.telegram.models import TelegramAccount, TelegramLinkInvitation
from humotech.telegram.services import TelegramLinkService
from humotech.telegram.tokens import ONBOARDING_PREFIX, build_invitation_link

ENTITY_ONBOARDING = "employee_onboarding"
ENTITY_PROGRAM = "onboarding_programs"
ENTITY_SECTION = "onboarding_sections"
ENTITY_DOCUMENT = "policy_documents"
ENTITY_VERSION = "policy_document_versions"
ENTITY_CATEGORY = "policy_categories"

#: Срок ознакомления по умолчанию: столько дней от включения в программу.
DUE_DAYS = 14
#: Через сколько дней после приглашения молчание считается «нет ответа».
SILENT_DAYS = 3

#: Группы списка «Сотрудники». Каждый человек ровно в одной — поэтому
#: счётчики отбора складываются во «все». Порядок — порядок проверки.
GROUPS = ("done", "attention", "waiting", "not_started", "in_progress")

CATEGORY_AUDIT_FIELDS = ("title", "description", "owner_employee_id", "position", "archived_at")

SECTION_AUDIT_FIELDS = ("position", "title", "button_label", "version", "archived_at")
DOCUMENT_AUDIT_FIELDS = ("code", "title", "is_mandatory", "position", "category_id", "archived_at")
VERSION_AUDIT_FIELDS = ("document_id", "version", "status", "published_at")

#: Что бот скажет сотруднику в напоминании. Ни фамилии, ни содержания
#: документов: уведомление сообщает, что дело не доделано, а не что
#: именно в нём написано.
REMINDER_TEXT = (
    "Напоминание: первичное ознакомление не завершено.\n\n"
    "Откройте бота и нажмите «Продолжить ознакомление» — "
    "это займёт несколько минут."
)


# --------------------------------------------------------------- сводка


@dataclass(frozen=True)
class ProgressRow:
    """Строка списка «Ознакомление» — один сотрудник.

    Сюда же попадает состояние привязки Telegram: без него список
    отвечает «0 из 10» и там, где человек не читает, и там, где ему
    просто некуда прислать ссылку. Это разные беды с разными действиями.
    """

    employee: Employee
    progress: Progress
    telegram_state: str
    office_name: str | None
    department_name: str | None
    position_name: str | None
    #: Действующее приглашение, если оно есть.
    invitation: TelegramLinkInvitation | None
    #: Почему человек требует внимания: overdue, declined, renewal,
    #: silent. Пусто — не требует. Одно правило на отбор, счётчик и
    #: колонку «Требуют внимания».
    reasons: tuple[str, ...] = ()
    #: Одна из GROUPS.
    group: str = "in_progress"
    #: Обязательные документы глазами этого человека.
    materials: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def overdue(self) -> bool:
        return "overdue" in self.reasons


def _full_name(employee: Employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(one for one in parts if one)


class OnboardingService(BaseService):
    """Кого позвали, кто где остановился и что с этим делать."""

    # ---------------------------------------------------------- программа

    def program(self, organization_id: uuid.UUID) -> OnboardingProgram:
        """Действующая программа организации.

        Её отсутствие — не 404 для пользователя, а незаполненный стенд:
        наполнение ставится командой `seed_onboarding`. Сообщение
        называет причину прямо, иначе раздел выглядит сломанным.
        """
        found = OnboardingProgram.objects.filter(
            organization_id=organization_id, is_active=True, archived_at__isnull=True
        ).first()
        if found is None:
            raise NotFound(
                "Программа ознакомления не создана. "
                "Выполните: manage.py seed_onboarding"
            )
        return found

    # ------------------------------------------------------------- список

    def _visible_employees(self, actor: Actor):
        queryset = Employee.objects.filter(
            organization_id=actor.organization_id, archived_at__isnull=True
        )
        visible = self.access.visible_office_ids(actor)
        if visible is None:
            return queryset
        allowed = EmployeeAssignment.objects.filter(
            office_id__in=visible
        ).values_list("employee_id", flat=True)
        return queryset.filter(id__in=allowed)

    def _scoped(self, actor: Actor, *, search=None, office_id=None, department_id=None):
        """Участники программы, видимые кадровику, с поиском и местом."""
        rows = EmployeeOnboarding.objects.filter(
            organization_id=actor.organization_id,
            employee_id__in=self._visible_employees(actor).values_list("id", flat=True),
        ).select_related("employee", "program")
        if search:
            needle = search.strip()
            in_department = EmployeeAssignment.objects.filter(
                is_primary=True, department__name__icontains=needle
            ).values_list("employee_id", flat=True)
            rows = rows.filter(
                Q(employee__first_name__icontains=needle)
                | Q(employee__last_name__icontains=needle)
                | Q(employee__middle_name__icontains=needle)
                | Q(employee__employee_number__icontains=needle)
                | Q(employee_id__in=in_department)
            )
        if office_id:
            rows = rows.filter(employee_id__in=EmployeeAssignment.objects.filter(
                office_id=office_id, is_primary=True
            ).values_list("employee_id", flat=True))
        if department_id:
            rows = rows.filter(employee_id__in=EmployeeAssignment.objects.filter(
                department_id=department_id, is_primary=True
            ).values_list("employee_id", flat=True))
        return rows

    def list_progress(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        search: str | None = None,
        office_id: str | None = None,
        department_id: str | None = None,
        group: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        """Сотрудники программы с их прогрессом.

        Состояние и группа считаются пересчётом, а не по колонке: колонка —
        витрина, и выпуск новой редакции документа успевает сделать её
        устаревшей раньше, чем кто-нибудь откроет страницу. Поэтому отбор
        по состоянию или группе идёт по пересчитанным строкам ДО деления
        на страницы: иначе страница приходила бы короткой или пустой при
        `has_more`, и список «терял» людей.
        """
        self.access.require(actor, "onboarding.read")
        rows = self._scoped(actor, search=search, office_id=office_id, department_id=department_id)
        if not status and not group:
            page = paginate(rows, limit=normalize_limit(limit), cursor=cursor)
            return Page(items=self._decorate(actor, page.items),
                        next_cursor=page.next_cursor, has_more=page.has_more)

        if group and group not in GROUPS:
            raise ValidationFailed("Неизвестная группа", details={"group": group})
        built = self._decorate(actor, list(rows.order_by("employee__last_name", "employee__first_name", "id")))
        if status:
            built = [one for one in built if one.progress.status == status]
        if group:
            built = [one for one in built if one.group == group]
        # Курсор здесь — смещение: список уже посчитан целиком.
        size = normalize_limit(limit)
        try:
            offset = max(0, int(cursor)) if cursor else 0
        except ValueError:
            raise ValidationFailed("Неверный курсор", details={"cursor": cursor}) from None
        chunk = built[offset:offset + size]
        more = offset + size < len(built)
        return Page(items=chunk, next_cursor=str(offset + size) if more else None, has_more=more)

    def _decorate(
        self, actor: Actor, rows: list[EmployeeOnboarding]
    ) -> list[ProgressRow]:
        """Прогресс, место работы и привязка — по одному запросу на всё.

        Справочники карточек и документов читаются ОДИН раз на страницу:
        они общие, и перечитывать их на каждого из пятидесяти человек
        значило бы сто лишних запросов ради одинакового ответа.
        """
        if not rows:
            return []

        documents = list(
            progress_module.published_documents(actor.organization_id)
        )
        by_program: dict[uuid.UUID, list[OnboardingSection]] = {}
        employee_ids = [one.employee_id for one in rows]

        places = {
            row.employee_id: row
            for row in EmployeeAssignment.objects.filter(
                employee_id__in=employee_ids, is_primary=True
            )
            .select_related("office", "department", "position")
            .order_by("-valid_from")
        }
        accounts = {
            row.employee_id: row
            for row in TelegramAccount.objects.filter(employee_id__in=employee_ids)
        }
        # Последнее приглашение по каждому человеку. Просроченное
        # помечается здесь, а не в базе: список читают, а не правят, и
        # писать в него на каждый показ незачем. Без этой поправки
        # карточка сообщала бы «действует до» о ссылке, срок которой
        # вышел вчера.
        moment = timezone.now()
        invitations: dict[uuid.UUID, TelegramLinkInvitation] = {}
        for row in TelegramLinkInvitation.objects.filter(
            employee_id__in=employee_ids
        ).order_by("created_at"):
            if row.status == "ACTIVE" and row.expires_at <= moment:
                row.status = "EXPIRED"
            invitations[row.employee_id] = row

        # Кто соглашался с ПРЕЖНЕЙ редакцией документа: такому человеку
        # нужна не первая встреча с документом, а повторное подтверждение.
        earlier = set(
            EmployeePolicyAcceptance.objects.filter(
                employee_id__in=employee_ids, decision="ACCEPTED",
            ).exclude(version__status="PUBLISHED").values_list("employee_id", "version__document_id")
        )
        today = timezone.localdate()

        built: list[ProgressRow] = []
        for row in rows:
            if row.program_id not in by_program:
                by_program[row.program_id] = list(
                    progress_module.active_sections(row.program_id)
                )
            place = places.get(row.employee_id)
            account = accounts.get(row.employee_id)
            progress = progress_module.compute(
                row, sections=by_program[row.program_id], documents=documents,
            )
            telegram = account.status if account else "NOT_LINKED"
            materials = tuple(
                {
                    "document_id": str(one.document.id),
                    "title": one.document.title,
                    "version": one.version.version if one.version else None,
                    "state": (
                        "accepted" if one.accepted
                        else "declined" if one.declined
                        else "renewal" if (row.employee_id, one.document.id) in earlier
                        else "pending"
                    ),
                    "decided_at": one.decided_at,
                }
                for one in progress.required_policies
            )
            reasons, group = _classify(row, progress, telegram, materials, today, moment)
            built.append(
                ProgressRow(
                    employee=row.employee,
                    progress=progress,
                    reasons=reasons,
                    group=group,
                    materials=materials,
                    telegram_state=telegram,
                    office_name=place.office.name if place and place.office_id else None,
                    department_name=(
                        place.department.name
                        if place and place.department_id else None
                    ),
                    position_name=(
                        place.position.name if place and place.position_id else None
                    ),
                    invitation=invitations.get(row.employee_id),
                )
            )
        return built

    def counts(self, actor: Actor, *, search=None, office_id=None, department_id=None) -> dict:
        """Сколько человек в каком состоянии и в какой группе.

        Считается тем же пересчётом и с теми же отборами места и поиска,
        что и список: число на чипе обязано совпасть со строками под ним.
        """
        self.access.require(actor, "onboarding.read")
        rows = list(self._scoped(actor, search=search, office_id=office_id, department_id=department_id))
        # Все состояния перечислены явно, включая нулевые: пустая вкладка
        # должна показывать «0», а не исчезать.
        tally = {
            "all": len(rows), "NOT_STARTED": 0, "IN_PROGRESS": 0,
            "INFO_COMPLETED": 0, "POLICIES_IN_PROGRESS": 0,
            "COMPLETED": 0, "UPDATE_REQUIRED": 0,
            "BLOCKED_BY_DECLINED_POLICY": 0,
        }
        groups = {name: 0 for name in GROUPS}
        overdue = 0
        for one in self._decorate(actor, rows):
            tally[one.progress.status] = tally.get(one.progress.status, 0) + 1
            groups[one.group] += 1
            overdue += one.overdue
        tally["groups"] = {"all": len(rows), **groups, "overdue": overdue}
        return tally

    # -------------------------------------------------------- один человек

    def employee_state(self, actor: Actor, employee_id: uuid.UUID) -> ProgressRow:
        self.access.require(actor, "onboarding.read")
        require_visible_employee(self.access, actor, employee_id)
        row = (
            EmployeeOnboarding.objects.select_related("employee", "program")
            .filter(employee_id=employee_id)
            .first()
        )
        if row is None:
            raise NotFound("Сотрудник не включён в программу ознакомления")
        return self._decorate(actor, [row])[0]

    def timeline(self, actor: Actor, employee_id: uuid.UUID) -> list[dict]:
        """Что и когда произошло: приглашение, привязка, разделы, документы.

        Собирается из тех же строк, что и прогресс, а не из отдельного
        журнала событий: вторая копия расходится с первой на первой же
        правке, и тогда карточка показывает то, чего не было.
        """
        state = self.employee_state(actor, employee_id)
        row = EmployeeOnboarding.objects.get(employee_id=employee_id)
        events: list[dict] = []

        if row.invited_at:
            events.append({
                "kind": "invited", "at": row.invited_at,
                "title": "Приглашение выдано", "detail": None,
            })
        account = TelegramAccount.objects.filter(employee_id=employee_id).first()
        if account is not None:
            events.append({
                "kind": "linked", "at": account.connected_at,
                "title": "Telegram привязан",
                "detail": (
                    f"@{account.telegram_username}"
                    if account.telegram_username else None
                ),
            })
        if row.started_at:
            events.append({
                "kind": "started", "at": row.started_at,
                "title": "Ознакомление начато", "detail": None,
            })
        for one in state.progress.sections:
            if one.acknowledged_at is None:
                continue
            events.append({
                "kind": "section", "at": one.acknowledged_at,
                "title": f"Раздел {one.section.position}: {one.section.title}",
                "detail": f"Редакция {one.acknowledged_version}",
            })
        if row.info_completed_at:
            events.append({
                "kind": "info_completed", "at": row.info_completed_at,
                "title": "Все информационные разделы прочитаны", "detail": None,
            })
        for one in state.progress.policies:
            if one.decided_at is None or one.version is None:
                continue
            events.append({
                "kind": "policy", "at": one.decided_at,
                "title": (
                    f"Документ принят: {one.document.title}"
                    if one.accepted
                    else f"Документ отклонён: {one.document.title}"
                ),
                "detail": f"Версия {one.version.version}",
            })
        if row.completed_at:
            events.append({
                "kind": "completed", "at": row.completed_at,
                "title": "Первичное ознакомление завершено", "detail": None,
            })
        if row.last_reminder_at:
            events.append({
                "kind": "reminded", "at": row.last_reminder_at,
                "title": "Отправлено напоминание", "detail": None,
            })

        events.sort(key=lambda one: one["at"])
        return events

    # ------------------------------------------------------- приглашение

    def enrol(
        self, actor: Actor, employee_id: uuid.UUID, *, now: datetime | None = None
    ) -> EmployeeOnboarding:
        """Включить сотрудника в программу.

        Отдельное действие, а не побочный эффект приёма. Программа
        закрывает человеку рабочие функции бота до её завершения, и
        включать в неё всех подряд миграцией нельзя: те, кто работает
        давно, остались бы без бота в утро выката.
        """
        self.access.require(actor, "onboarding.manage")
        employee = require_visible_employee(self.access, actor, employee_id)
        program = self.program(actor.organization_id)
        moment = now or timezone.now()

        with self.atomic():
            row = EmployeeOnboarding.objects.filter(employee_id=employee_id).first()
            if row is not None:
                return row
            row = EmployeeOnboarding.objects.create(
                organization_id=actor.organization_id,
                employee=employee,
                program=program,
                status="NOT_STARTED",
                due_date=timezone.localdate(moment) + timedelta(days=DUE_DAYS),
                created_by_user_id=actor.user_id,
            )
            self.audit.record(
                actor,
                action="onboarding.enrol",
                entity_type=ENTITY_ONBOARDING,
                entity_id=row.id,
                after={"employee_id": str(employee_id), "program": program.code},
            )
        progress_module.refresh(row, now=moment)
        return row

    def invite(
        self,
        actor: Actor,
        employee_id: uuid.UUID,
        *,
        replace: bool = False,
        now: datetime | None = None,
    ) -> dict:
        """Выдать персональную ссылку на ознакомление.

        Ссылка та же, что и у обычной привязки: одноразовая, с хешем
        вместо токена, со сроком и отзывом. Отличается только префикс
        полезной нагрузки — по нему бот понимает, что после привязки
        человека ждёт ознакомление, а не сразу меню.

        Срок здесь свой, семь дней: ознакомление отправляют до выхода на
        работу, и суточный срок обычной привязки не пережил бы выходные.
        """
        self.access.require(actor, "onboarding.manage")
        self.access.require(actor, "telegram.manage")
        row = self.enrol(actor, employee_id, now=now)

        # У привязанного ссылка не нужна и выдана быть не может: она
        # существует ровно затем, чтобы привязку СОЗДАТЬ. Раньше здесь
        # случался тупик — кадровик жал «Отправить ознакомление» уже
        # работающему человеку и получал «сначала отключите Telegram»,
        # хотя отключать ничего не требовалось. Человек при этом в
        # программу уже включён и увидит ознакомление при следующем
        # обращении к боту.
        account = TelegramAccount.objects.filter(
            employee_id=employee_id, status="ACTIVE"
        ).first()
        if account is not None:
            return {
                "invitation": None,
                "link": None,
                "expires_at": None,
                "linked": True,
            }

        issued = TelegramLinkService().create_invitation(
            actor, employee_id, replace=replace,
            ttl_seconds=settings.TELEGRAM["ONBOARDING_INVITATION_TTL_SECONDS"],
        )
        link = build_invitation_link(
            settings.TELEGRAM["BOT_USERNAME"], issued.token,
            prefix=ONBOARDING_PREFIX,
        )
        moment = now or timezone.now()
        EmployeeOnboarding.objects.filter(pk=row.pk).update(invited_at=moment)
        self.audit.record(
            actor,
            action="onboarding.invite",
            entity_type=ENTITY_ONBOARDING,
            entity_id=row.id,
            after={
                "employee_id": str(employee_id),
                "invitation_id": str(issued.invitation.id),
                "expires_at": issued.invitation.expires_at.isoformat(),
            },
        )
        return {
            "invitation": issued.invitation,
            "link": link,
            "expires_at": issued.invitation.expires_at,
            "linked": False,
        }

    def revoke(self, actor: Actor, employee_id: uuid.UUID) -> TelegramLinkInvitation:
        """Отозвать действующую ссылку."""
        self.access.require(actor, "onboarding.manage")
        self.access.require(actor, "telegram.manage")
        require_visible_employee(self.access, actor, employee_id)
        live = (
            TelegramLinkInvitation.objects.filter(
                employee_id=employee_id, status="ACTIVE"
            )
            .order_by("-created_at")
            .first()
        )
        if live is None:
            raise NotFound("Действующей ссылки нет")
        return TelegramLinkService().revoke_invitation(actor, live.id)

    def remind(
        self, actor: Actor, employee_id: uuid.UUID, *, now: datetime | None = None
    ) -> dict:
        """Напомнить сотруднику, что ознакомление не закончено.

        Работает только при живой привязке: бот не может написать
        первым — это правило Telegram, а не наше решение. Без привязки
        напоминать некуда, и очередь молча проглотила бы сообщение;
        честнее отказать с причиной, чтобы кадровик отправил ссылку.
        """
        self.access.require(actor, "onboarding.manage")
        require_visible_employee(self.access, actor, employee_id)
        state = self.employee_state(actor, employee_id)
        if state.progress.completed:
            raise Conflict("Ознакомление уже завершено")

        account = TelegramAccount.objects.filter(
            employee_id=employee_id, status="ACTIVE"
        ).first()
        if account is None:
            raise Conflict(
                "Напомнить некуда: Telegram не привязан. "
                "Отправьте сотруднику ссылку на ознакомление.",
                details={"reason": "not_linked"},
            )

        moment = now or timezone.now()
        row = EmployeeOnboarding.objects.get(employee_id=employee_id)
        with self.atomic():
            enqueue(
                organization_id=actor.organization_id,
                employee_id=employee_id,
                notification_type="onboarding.reminder",
                title="Ознакомление не завершено",
                body=REMINDER_TEXT,
                # Ключ включает дату: два напоминания в один день —
                # это одно напоминание, нажатое дважды.
                idempotency_key=(
                    f"onboarding-reminder:{employee_id}:{moment.date().isoformat()}"
                ),
                related_entity_type=ENTITY_ONBOARDING,
                related_entity_id=row.id,
            )
            EmployeeOnboarding.objects.filter(pk=row.pk).update(
                last_reminder_at=moment
            )
            self.audit.record(
                actor,
                action="onboarding.remind",
                entity_type=ENTITY_ONBOARDING,
                entity_id=row.id,
                after={"employee_id": str(employee_id), "at": moment.isoformat()},
            )
        return {"sent_at": moment}

    def set_due(self, actor: Actor, employee_id: uuid.UUID, due_date: date | None) -> EmployeeOnboarding:
        """Назначить или снять срок ознакомления."""
        self.access.require(actor, "onboarding.manage")
        require_visible_employee(self.access, actor, employee_id)
        row = EmployeeOnboarding.objects.filter(employee_id=employee_id).first()
        if row is None:
            raise NotFound("Сотрудник не включён в программу ознакомления")
        before = {"due_date": row.due_date.isoformat() if row.due_date else None}
        with self.atomic():
            row.due_date = due_date
            row.save(update_fields=["due_date", "updated_at"])
            self.audit.record(
                actor,
                action="onboarding.due",
                entity_type=ENTITY_ONBOARDING,
                entity_id=row.id,
                before=before,
                after={"due_date": due_date.isoformat() if due_date else None},
            )
        return row

    def remind_many(
        self, actor: Actor, employee_ids: list[uuid.UUID], *, now: datetime | None = None
    ) -> list[dict]:
        """Напомнить нескольким сразу — с итогом по каждому.

        Не «отправлено», а что именно случилось с каждым: у кого нет
        Telegram, кому уже напомнили сегодня, кто успел закончить. Иначе
        кнопка «Напомнить всем» молча проглатывала бы отказы.
        """
        self.access.require(actor, "onboarding.manage")
        moment = now or timezone.now()
        results = []
        for employee_id in dict.fromkeys(employee_ids):
            row = EmployeeOnboarding.objects.filter(
                employee_id=employee_id, organization_id=actor.organization_id
            ).first()
            if row is not None and row.last_reminder_at and (
                timezone.localdate(row.last_reminder_at) == timezone.localdate(moment)
            ):
                results.append({"employee_id": str(employee_id), "outcome": "already_today"})
                continue
            try:
                self.remind(actor, employee_id, now=moment)
                outcome = "sent"
            except Conflict as error:
                reason = (getattr(error, "details", None) or {}).get("reason")
                outcome = "no_telegram" if reason == "not_linked" else "completed"
            except NotFound:
                outcome = "not_enrolled"
            results.append({"employee_id": str(employee_id), "outcome": outcome})
        return results

    # -------------------------------------------------------------- выгрузка

    def export_rows(self, actor: Actor, *, search=None, office_id=None, department_id=None, group=None) -> list[dict]:
        """Плоская таблица состояния — для выгрузки из CRM.

        Постранично не режется намеренно: выгрузка отвечает на вопрос
        «покажи всех», и файл из первых пятидесяти строк ответом не был бы.
        """
        self.access.require(actor, "onboarding.read")
        rows = list(self._scoped(actor, search=search, office_id=office_id, department_id=department_id))
        built = []
        for one in self._decorate(actor, rows):
            if group and one.group != group:
                continue
            # Строки уходят прямо в CSV (его собирает CRM). Имя или отдел,
            # начинающиеся с `=`/`+`/`-`/`@`, Excel исполнил бы как формулу.
            from humotech.surveys.services import safe_cell

            built.append({
                "employee_number": (
                    safe_cell(one.employee.employee_number)
                    if one.employee.employee_number else None
                ),
                "full_name": safe_cell(_full_name(one.employee)),
                "office": safe_cell(one.office_name) if one.office_name else None,
                "department": (
                    safe_cell(one.department_name) if one.department_name else None
                ),
                "position": safe_cell(one.position_name) if one.position_name else None,
                "telegram": one.telegram_state,
                "status": one.progress.status,
                "sections": (
                    f"{one.progress.sections_done}/{one.progress.sections_total}"
                ),
                "policies": (
                    f"{one.progress.policies_done}/{one.progress.policies_total}"
                ),
                "completed_at": one.progress.onboarding.completed_at,
                "due_date": one.progress.onboarding.due_date,
                "attention": ", ".join(REASON_TITLES[r] for r in one.reasons),
                "last_reminder_at": one.progress.onboarding.last_reminder_at,
            })
        return built


# ------------------------------------------------------------- содержимое


class OnboardingContentService(BaseService):
    """Тексты десяти карточек. Правятся кадровиком, а не выкатом."""

    def sections(self, actor: Actor) -> list[OnboardingSection]:
        self.access.require(actor, "onboarding.read")
        program = OnboardingService().program(actor.organization_id)
        return list(progress_module.active_sections(program.id))

    def create_section(self, actor: Actor, data: dict) -> OnboardingSection:
        self.access.require(actor, "onboarding.manage")
        program = OnboardingService().program(actor.organization_id)
        position = data.get("position")
        if position is None:
            last = (
                OnboardingSection.objects.filter(
                    program_id=program.id, archived_at__isnull=True
                )
                .order_by("-position")
                .first()
            )
            position = (last.position + 1) if last else 1

        with self.atomic():
            row = OnboardingSection.objects.create(
                organization_id=actor.organization_id,
                program=program,
                position=position,
                title=data["title"],
                body=data["body"],
                button_label=data.get("button_label") or ACK_DEFAULT,
                version=1,
            )
            self.audit.record(
                actor,
                action="onboarding.section.create",
                entity_type=ENTITY_SECTION,
                entity_id=row.id,
                after=snapshot(row, SECTION_AUDIT_FIELDS),
            )
        # Состав программы изменился — витрина у всех участников
        # устарела. Допуск и так считается заново, но список кадровика
        # читает колонку.
        progress_module.resync(actor.organization_id)
        return row

    def update_section(
        self, actor: Actor, section_id: uuid.UUID, data: dict
    ) -> OnboardingSection:
        """Правка текста поднимает редакцию карточки.

        Перечитывать заново это никого не заставляет: карточка сообщает,
        а не обязывает. Но подтверждение хранит номер редакции, и без
        подъёма через год нельзя было бы сказать, что именно человек
        читал. Правка одной подписи кнопки редакцию не двигает — текст
        от неё не меняется.
        """
        self.access.require(actor, "onboarding.manage")
        row = self._require_section(actor, section_id)
        before = snapshot(row, SECTION_AUDIT_FIELDS)

        fields: list[str] = []
        content_changed = False
        for name in ("title", "body", "button_label", "position"):
            if name in data and data[name] is not None and data[name] != getattr(row, name):
                setattr(row, name, data[name])
                fields.append(name)
                if name in ("title", "body"):
                    content_changed = True
        if not fields:
            return row
        if content_changed:
            row.version += 1
            fields.append("version")

        with self.atomic():
            row.save(update_fields=[*fields, "updated_at"])
            self.audit.record(
                actor,
                action="onboarding.section.update",
                entity_type=ENTITY_SECTION,
                entity_id=row.id,
                before=before,
                after=snapshot(row, SECTION_AUDIT_FIELDS),
            )
        return row

    def archive_section(self, actor: Actor, section_id: uuid.UUID) -> OnboardingSection:
        """Убрать карточку из программы, не стирая подтверждений.

        Физического удаления нет: на карточку ссылаются записи «я
        ознакомился», и вместе с ней исчезло бы свидетельство того, что
        человек её читал.
        """
        self.access.require(actor, "onboarding.manage")
        row = self._require_section(actor, section_id)
        if row.archived_at is not None:
            return row
        before = snapshot(row, SECTION_AUDIT_FIELDS)
        row.archived_at = timezone.now()
        with self.atomic():
            row.save(update_fields=["archived_at", "updated_at"])
            self.audit.record(
                actor,
                action="onboarding.section.archive",
                entity_type=ENTITY_SECTION,
                entity_id=row.id,
                before=before,
                after=snapshot(row, SECTION_AUDIT_FIELDS),
            )
        progress_module.resync(actor.organization_id)
        return row

    def _require_section(
        self, actor: Actor, section_id: uuid.UUID
    ) -> OnboardingSection:
        row = OnboardingSection.objects.filter(
            id=section_id, organization_id=actor.organization_id
        ).first()
        if row is None:
            raise NotFound("Раздел не найден")
        return row


# -------------------------------------------------------------- документы


class PolicyService(BaseService):
    """Обязательные документы и их редакции."""

    def documents(self, actor: Actor) -> list[PolicyDocument]:
        self.access.require(actor, "onboarding.read")
        return list(
            PolicyDocument.objects.filter(
                organization_id=actor.organization_id, archived_at__isnull=True
            )
            .select_related("category", "created_by_user")
            .prefetch_related("versions")
            .order_by("position", "created_at")
        )

    def document_facts(self, actor: Actor, documents: list[PolicyDocument]) -> dict:
        """Кому назначен документ и кто подтвердил — для всех сразу.

        Назначен документ участникам программы, если у него есть
        опубликованная редакция: черновик никого ни к чему не обязывает,
        и у него «назначено» честно равно нулю. Подтвердили — согласие с
        ДЕЙСТВУЮЩЕЙ редакцией. «Новая версия» — соглашались с прежней, но
        не с нынешней. Ближайший срок — самый ранний срок среди тех, кто
        этот документ ещё не подтвердил.

        Кто и когда менял документ — из журнала: там настоящие авторы и
        у старых записей, которых новая колонка не знала бы.
        """
        self.access.require(actor, "onboarding.read")
        service = OnboardingService()
        service.access = self.access
        participants = dict(
            EmployeeOnboarding.objects.filter(
                organization_id=actor.organization_id,
                employee_id__in=service._visible_employees(actor).values_list("id", flat=True),
            ).values_list("employee_id", "due_date")
        )
        live = {}
        version_ids = []
        for document in documents:
            for version in document.versions.all():
                version_ids.append(version.id)
                if version.status == "PUBLISHED":
                    live[document.id] = version
        decisions = {}
        for employee_id, version_id, document_id, decision, status in EmployeePolicyAcceptance.objects.filter(
            employee_id__in=list(participants), version__document_id__in=[d.id for d in documents],
        ).values_list("employee_id", "version_id", "version__document_id", "decision", "version__status"):
            decisions.setdefault(document_id, []).append((employee_id, version_id, decision, status))

        changes = {}
        owner = {version: doc.id for doc in documents for version in (v.id for v in doc.versions.all())}
        for entry in AuditLog.objects.filter(
            organization_id=actor.organization_id,
            entity_type__in=[ENTITY_DOCUMENT, ENTITY_VERSION],
            entity_id__in=[d.id for d in documents] + version_ids,
        ).select_related("actor_user").order_by("occurred_at"):
            document_id = entry.entity_id if entry.entity_type == ENTITY_DOCUMENT else owner.get(entry.entity_id)
            changes[document_id] = entry

        facts = {}
        for document in documents:
            # Необязательный документ показывается, но ни от кого не
            # требуется: назначенным он не считается — как и черновик.
            version = live.get(document.id) if document.is_mandatory else None
            rows = decisions.get(document.id, [])
            accepted = {e for e, v, d, s in rows if version and v == version.id and d == "ACCEPTED"}
            declined = {e for e, v, d, s in rows if version and v == version.id and d == "DECLINED"}
            earlier = {e for e, v, d, s in rows if d == "ACCEPTED" and s != "PUBLISHED"} - accepted
            owing = [e for e in participants if e not in accepted] if version else []
            dues = [participants[e] for e in owing if participants[e] is not None]
            change = changes.get(document.id)
            facts[document.id] = {
                "assigned": len(participants) if version else 0,
                "confirmed": len(accepted),
                "declined": len(declined),
                "renewal_pending": len(earlier) if version else 0,
                "nearest_due": min(dues) if dues else None,
                "created_by": _user_name(document.created_by_user),
                "changed_at": change.occurred_at if change else document.updated_at,
                "changed_by": _user_name(change.actor_user) if change else _user_name(document.created_by_user),
            }
        return facts

    def create_document(self, actor: Actor, data: dict) -> PolicyDocument:
        self.access.require(actor, "policies.publish")
        code = (data.get("code") or "").strip().upper()
        if not code:
            raise ValidationFailed("Код документа обязателен",
                                   details={"field": "code"})
        category_id = data.get("category_id")
        if category_id is not None:
            CategoryService()._require(actor, category_id, live=True)
        with self.atomic():
            row = PolicyDocument.objects.create(
                organization_id=actor.organization_id,
                code=code,
                title=data["title"],
                description=data.get("description") or None,
                is_mandatory=data.get("is_mandatory", True),
                position=data.get("position") or 1,
                category_id=category_id,
                created_by_user_id=actor.user_id,
            )
            self.audit.record(
                actor,
                action="onboarding.document.create",
                entity_type=ENTITY_DOCUMENT,
                entity_id=row.id,
                after=snapshot(row, DOCUMENT_AUDIT_FIELDS),
            )
        return row

    def update_document(
        self, actor: Actor, document_id: uuid.UUID, data: dict
    ) -> PolicyDocument:
        self.access.require(actor, "policies.publish")
        row = self._require_document(actor, document_id)
        before = snapshot(row, DOCUMENT_AUDIT_FIELDS)
        fields: list[str] = []
        for name in ("title", "description", "is_mandatory", "position"):
            if name in data and data[name] is not None:
                setattr(row, name, data[name])
                fields.append(name)
        if "category_id" in data:
            wanted = data["category_id"]
            if wanted is not None:
                CategoryService()._require(actor, wanted, live=True)
            row.category_id = wanted
            fields.append("category")
        if not fields:
            return row
        with self.atomic():
            row.save(update_fields=[*fields, "updated_at"])
            self.audit.record(
                actor,
                action="onboarding.document.update",
                entity_type=ENTITY_DOCUMENT,
                entity_id=row.id,
                before=before,
                after=snapshot(row, DOCUMENT_AUDIT_FIELDS),
            )
        return row

    def archive_document(
        self, actor: Actor, document_id: uuid.UUID
    ) -> PolicyDocument:
        """Убрать документ из обязательных.

        Подтверждения прежних редакций остаются: человек действительно
        соглашался с ними, и архивирование документа это не отменяет.
        """
        self.access.require(actor, "policies.publish")
        row = self._require_document(actor, document_id)
        if row.archived_at is not None:
            return row
        before = snapshot(row, DOCUMENT_AUDIT_FIELDS)
        row.archived_at = timezone.now()
        with self.atomic():
            row.save(update_fields=["archived_at", "updated_at"])
            self.audit.record(
                actor,
                action="onboarding.document.archive",
                entity_type=ENTITY_DOCUMENT,
                entity_id=row.id,
                before=before,
                after=snapshot(row, DOCUMENT_AUDIT_FIELDS),
            )
        return row

    # ---------------------------------------------------------- редакции

    def create_version(
        self, actor: Actor, document_id: uuid.UUID, data: dict
    ) -> PolicyDocumentVersion:
        """Новая редакция. Создаётся черновиком и никого не обязывает."""
        self.access.require(actor, "policies.publish")
        document = self._require_document(actor, document_id)
        number = (data.get("version") or "").strip()
        if not number:
            raise ValidationFailed("Номер редакции обязателен",
                                   details={"field": "version"})
        if PolicyDocumentVersion.objects.filter(
            document_id=document.id, version=number
        ).exists():
            raise Conflict(
                f"Редакция {number} уже существует",
                details={"version": number},
            )

        with self.atomic():
            row = PolicyDocumentVersion.objects.create(
                organization_id=actor.organization_id,
                document=document,
                version=number,
                summary=data["summary"],
                body=data.get("body") or None,
                agree_label=data.get("agree_label") or "Согласен",
                status="DRAFT",
                file_id=data.get("file_id"),
            )
            self.audit.record(
                actor,
                action="onboarding.version.create",
                entity_type=ENTITY_VERSION,
                entity_id=row.id,
                after=snapshot(row, VERSION_AUDIT_FIELDS),
            )
        return row

    def update_version(
        self, actor: Actor, version_id: uuid.UUID, data: dict
    ) -> PolicyDocumentVersion:
        """Правка черновика. Опубликованную редакцию править нельзя.

        Под опубликованной стоят имена согласившихся и время. Правка
        задним числом означала бы, что человек согласился не с тем, что
        записано, — а это ровно та подмена, ради невозможности которой
        документ вообще версионируется.
        """
        self.access.require(actor, "policies.publish")
        row = self._require_version(actor, version_id)
        if row.status != "DRAFT":
            raise Conflict(
                "Опубликованную редакцию править нельзя: выпустите новую",
                details={"status": row.status},
            )
        before = snapshot(row, VERSION_AUDIT_FIELDS)
        fields: list[str] = []
        for name in ("summary", "body", "agree_label", "version"):
            if name in data and data[name] is not None:
                setattr(row, name, data[name])
                fields.append(name)
        if "file_id" in data:
            row.file_id = data["file_id"]
            fields.append("file")
        if not fields:
            return row
        with self.atomic():
            row.save(update_fields=[*fields, "updated_at"])
            self.audit.record(
                actor,
                action="onboarding.version.update",
                entity_type=ENTITY_VERSION,
                entity_id=row.id,
                before=before,
                after=snapshot(row, VERSION_AUDIT_FIELDS),
            )
        return row

    def publish_version(
        self, actor: Actor, version_id: uuid.UUID, *, now: datetime | None = None
    ) -> PolicyDocumentVersion:
        """Выпустить редакцию. Прежняя уходит в архив в той же транзакции.

        Это самое тяжёлое действие раздела: с этой секунды все, кто не
        подтвердил новый текст, теряют рабочие функции бота, пока не
        подтвердят. Поэтому и право отдельное — `policies.publish`.

        Десять карточек при этом НЕ переоткрываются: человеку нужно
        подтвердить только новую редакцию. Это прямо следует из того,
        как считается завершение: карточки проверяются по факту
        подтверждения, документы — по номеру действующей редакции.
        """
        self.access.require(actor, "policies.publish")
        row = self._require_version(actor, version_id)
        if row.status == "PUBLISHED":
            return row
        if row.status == "ARCHIVED":
            raise Conflict("Архивную редакцию опубликовать нельзя")

        moment = now or timezone.now()
        before = snapshot(row, VERSION_AUDIT_FIELDS)
        with self.atomic():
            previous = PolicyDocumentVersion.objects.filter(
                document_id=row.document_id, status="PUBLISHED"
            ).first()
            if previous is not None:
                previous.status = "ARCHIVED"
                previous.save(update_fields=["status", "updated_at"])
                self.audit.record(
                    actor,
                    action="onboarding.version.archive",
                    entity_type=ENTITY_VERSION,
                    entity_id=previous.id,
                    after=snapshot(previous, VERSION_AUDIT_FIELDS),
                )
            row.status = "PUBLISHED"
            row.published_at = moment
            row.published_by_user_id = actor.user_id
            row.save(
                update_fields=[
                    "status", "published_at", "published_by_user", "updated_at",
                ]
            )
            self.audit.record(
                actor,
                action="onboarding.version.publish",
                entity_type=ENTITY_VERSION,
                entity_id=row.id,
                before=before,
                after=snapshot(row, VERSION_AUDIT_FIELDS),
            )
        progress_module.resync(actor.organization_id, now=moment)
        return row

    def pending_employees(
        self, actor: Actor, document_id: uuid.UUID
    ) -> list[dict]:
        """Кто ещё не подтвердил действующую редакцию документа.

        Только участники программы: остальных этот документ ни к чему не
        обязывает, и показывать их в списке «не приняли» было бы
        обвинением на пустом месте.
        """
        self.access.require(actor, "onboarding.read")
        document = self._require_document(actor, document_id)
        live = PolicyDocumentVersion.objects.filter(
            document_id=document.id, status="PUBLISHED"
        ).first()
        if live is None:
            return []

        # Область видимости считается тем же кодом, что и в списке
        # прогресса: два ответа на вопрос «кого этот кадровик видит»
        # разошлись бы, и здесь оказались бы чужие фамилии. Кэш
        # разрешений передаётся вместе с правилом — иначе роли
        # перечитывались бы второй раз на тот же запрос.
        service = OnboardingService()
        service.access = self.access
        visible = service._visible_employees(actor).values_list("id", flat=True)
        rows = EmployeeOnboarding.objects.filter(
            organization_id=actor.organization_id, employee_id__in=visible
        ).select_related("employee")
        accepted = set(
            EmployeePolicyAcceptance.objects.filter(
                version_id=live.id, decision="ACCEPTED"
            ).values_list("employee_id", flat=True)
        )
        declined = {
            row.employee_id: row.decided_at
            for row in EmployeePolicyAcceptance.objects.filter(
                version_id=live.id, decision="DECLINED"
            )
        }
        return [
            {
                "employee_id": str(row.employee_id),
                "full_name": _full_name(row.employee),
                "employee_number": row.employee.employee_number,
                "declined_at": declined.get(row.employee_id),
            }
            for row in rows
            if row.employee_id not in accepted
        ]

    def _require_document(
        self, actor: Actor, document_id: uuid.UUID
    ) -> PolicyDocument:
        row = PolicyDocument.objects.filter(
            id=document_id, organization_id=actor.organization_id
        ).first()
        if row is None:
            raise NotFound("Документ не найден")
        return row

    def _require_version(
        self, actor: Actor, version_id: uuid.UUID
    ) -> PolicyDocumentVersion:
        row = PolicyDocumentVersion.objects.select_related("document").filter(
            id=version_id, organization_id=actor.organization_id
        ).first()
        if row is None:
            raise NotFound("Редакция не найдена")
        return row


#: Причины внимания словами кадровика.
REASON_TITLES = {
    "overdue": "Просрочен срок ознакомления",
    "declined": "Отказ подтвердить документ",
    "renewal": "Новая версия материала",
    "silent": "Нет ответа от сотрудника",
}


def _classify(row, progress, telegram: str, materials, today: date, moment: datetime):
    """Причины внимания и группа строки — одно правило на весь раздел.

    * просрочено — срок назначен, прошёл, а ознакомление не завершено;
    * отказ — сотрудник отказался подтвердить документ;
    * новая версия — с прежней редакцией соглашался, с нынешней ещё нет;
    * нет ответа — Telegram привязан, приглашён больше SILENT_DAYS дней
      назад, а к ознакомлению не приступил.

    Группы взаимоисключающие, поэтому чипы складываются во «все».
    """
    reasons = []
    if not progress.completed:
        due = row.due_date
        if due is not None and due < today:
            reasons.append("overdue")
        if progress.has_declined:
            reasons.append("declined")
        if any(one["state"] == "renewal" for one in materials):
            reasons.append("renewal")
        since = row.invited_at or row.created_at
        if (
            telegram == "ACTIVE" and row.started_at is None and since is not None
            and since <= moment - timedelta(days=SILENT_DAYS)
        ):
            reasons.append("silent")
    if progress.completed:
        group = "done"
    elif reasons:
        group = "attention"
    elif telegram != "ACTIVE" and row.started_at is None:
        group = "waiting"
    elif progress.status == "NOT_STARTED":
        group = "not_started"
    else:
        group = "in_progress"
    return tuple(reasons), group


def _user_name(user) -> str | None:
    if user is None:
        return None
    return user.full_name or user.email


class CategoryService(BaseService):
    """Разделы материалов: группировка для кадровика и порядка."""

    def categories(self, actor: Actor) -> list[dict]:
        self.access.require(actor, "onboarding.read")
        rows = list(
            PolicyCategory.objects.filter(organization_id=actor.organization_id, archived_at__isnull=True)
            .select_related("owner_employee")
            .order_by("position", "title")
        )
        documents = {}
        for doc in PolicyDocument.objects.filter(
            organization_id=actor.organization_id, archived_at__isnull=True, category_id__in=[r.id for r in rows]
        ).order_by("position", "created_at"):
            documents.setdefault(doc.category_id, []).append(doc)
        places = {
            one.employee_id: one for one in EmployeeAssignment.objects.filter(
                employee_id__in=[r.owner_employee_id for r in rows if r.owner_employee_id], is_primary=True,
            ).select_related("position").order_by("-valid_from")
        }
        changes = {}
        for entry in AuditLog.objects.filter(
            organization_id=actor.organization_id, entity_type=ENTITY_CATEGORY, entity_id__in=[r.id for r in rows],
        ).select_related("actor_user").order_by("occurred_at"):
            changes[entry.entity_id] = entry
        result = []
        for row in rows:
            owner = row.owner_employee
            place = places.get(row.owner_employee_id)
            change = changes.get(row.id)
            docs = documents.get(row.id, [])
            result.append({
                "id": str(row.id),
                "title": row.title,
                "description": row.description,
                "position": row.position,
                "owner": {
                    "id": str(owner.id),
                    "full_name": _full_name(owner),
                    "position_name": place.position.name if place and place.position_id else None,
                } if owner else None,
                "documents_count": len(docs),
                "documents": [{"id": str(d.id), "title": d.title} for d in docs],
                "changed_at": change.occurred_at if change else row.updated_at,
                "changed_by": _user_name(change.actor_user) if change else None,
            })
        return result

    def create(self, actor: Actor, data: dict) -> PolicyCategory:
        self.access.require(actor, "policies.publish")
        owner = self._owner(actor, data.get("owner_employee_id"))
        with self.atomic():
            row = PolicyCategory.objects.create(
                organization_id=actor.organization_id,
                title=data["title"].strip(),
                description=(data.get("description") or "").strip() or None,
                owner_employee_id=owner,
                position=data.get("position") or 1,
                created_by_user_id=actor.user_id,
            )
            self.audit.record(actor, action="onboarding.category.create", entity_type=ENTITY_CATEGORY,
                              entity_id=row.id, after=snapshot(row, CATEGORY_AUDIT_FIELDS))
        return row

    def update(self, actor: Actor, category_id: uuid.UUID, data: dict) -> PolicyCategory:
        self.access.require(actor, "policies.publish")
        row = self._require(actor, category_id, live=True)
        before = snapshot(row, CATEGORY_AUDIT_FIELDS)
        fields = []
        if data.get("title"):
            row.title = data["title"].strip()
            fields.append("title")
        if "description" in data:
            row.description = (data["description"] or "").strip() or None
            fields.append("description")
        if "owner_employee_id" in data:
            row.owner_employee_id = self._owner(actor, data["owner_employee_id"])
            fields.append("owner_employee")
        if data.get("position"):
            row.position = data["position"]
            fields.append("position")
        if not fields:
            return row
        with self.atomic():
            row.save(update_fields=[*fields, "updated_at"])
            self.audit.record(actor, action="onboarding.category.update", entity_type=ENTITY_CATEGORY,
                              entity_id=row.id, before=before, after=snapshot(row, CATEGORY_AUDIT_FIELDS))
        return row

    def archive(self, actor: Actor, category_id: uuid.UUID) -> PolicyCategory:
        """Убрать раздел. Только пустой: материалы сначала переносят."""
        self.access.require(actor, "policies.publish")
        row = self._require(actor, category_id, live=True)
        if PolicyDocument.objects.filter(category_id=row.id, archived_at__isnull=True).exists():
            raise Conflict("В разделе есть материалы: перенесите их в другой раздел")
        before = snapshot(row, CATEGORY_AUDIT_FIELDS)
        with self.atomic():
            row.archived_at = timezone.now()
            row.save(update_fields=["archived_at", "updated_at"])
            self.audit.record(actor, action="onboarding.category.archive", entity_type=ENTITY_CATEGORY,
                              entity_id=row.id, before=before, after=snapshot(row, CATEGORY_AUDIT_FIELDS))
        return row

    def _owner(self, actor: Actor, employee_id) -> uuid.UUID | None:
        if not employee_id:
            return None
        if not Employee.objects.filter(id=employee_id, organization_id=actor.organization_id).exists():
            raise ValidationFailed("Ответственный не найден", details={"field": "owner_employee_id"})
        return employee_id

    def _require(self, actor: Actor, category_id, *, live: bool = False) -> PolicyCategory:
        row = PolicyCategory.objects.filter(id=category_id, organization_id=actor.organization_id).first()
        if row is None or (live and row.archived_at is not None):
            raise NotFound("Раздел не найден")
        return row


# ------------------------------------------------------------ наполнение


def seed_organization(organization_id: uuid.UUID) -> dict:
    """Стартовые карточки и документы для одной организации.

    Тонкая обёртка над `seeding.seed`: сама логика общая с миграцией,
    и второго её экземпляра не существует. Отличается только то, какими
    моделями пользоваться — текущими здесь, историческими там.
    """
    return seeding.seed(
        organization_id,
        Program=OnboardingProgram,
        Section=OnboardingSection,
        Document=PolicyDocument,
        Version=PolicyDocumentVersion,
        now=timezone.now(),
    )


__all__ = [
    "OnboardingContentService",
    "OnboardingService",
    "PolicyService",
    "ProgressRow",
    "seed_organization",
]
