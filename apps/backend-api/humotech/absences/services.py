"""Больничные и отпуска: заявка, решение, продление, отмена.

Своих статусов здесь не заводится — их хватает в схеме, и они уже описывают
нужный жизненный цикл. Соответствие такое:

    «ожидает»           = AbsenceRequest.status SUBMITTED
    «на рассмотрении»   = IN_REVIEW
    «подтверждено»      = APPROVED  (+ EmployeeAbsence PLANNED/ACTIVE)
    «отклонено»         = REJECTED
    «отменено»          = CANCELLED
    «продление ждёт»    = ДОЧЕРНЯЯ заявка: request_kind EXTEND,
                          parent_request заполнен, status SUBMITTED
    «завершено»         = EmployeeAbsence.status COMPLETED

Продление намеренно отдельная заявка, а не правка дат в существующей.
Подтверждённые даты — это документ: по ним посчитана статистика, на них
могли сослаться. Молча сдвинуть их значит переписать прошлое.

Разделены две сущности, и путать их нельзя:

  * `AbsenceRequest` — просьба. Она может быть отклонена;
  * `EmployeeAbsence` — подтверждённый период. Только он попадает
    в статистику и только он существует после согласования.

Все правила, по которым что-то разрешено или запрещено, берутся из
`policy.py`, то есть из настроек организации. В коде их нет.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from humotech.absences.models import (
    AbsenceAction,
    AbsenceDocument,
    AbsenceRequest,
    AbsenceType,
    EmployeeAbsence,
    LeaveBalance,
)
from humotech.absences.policy import AbsencePolicy, policy_for
from humotech.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.timeframes import (
    closed_range_bounds,
    days_in,
    local_date,
    range_bounds,
)
from humotech.files.storage import open_stored, store
from humotech.notifications import messages
from humotech.notifications.outbox import enqueue
from humotech.schedules.models import CalendarException, EmployeeScheduleAssignment

ENTITY_REQUEST = "absence_requests"
ENTITY_ABSENCE = "employee_absences"

# Что видно в журнале. Ни комментария сотрудника, ни имени файла: в первом
# бывает диагноз, во втором — фамилия и диагноз разом.
REQUEST_AUDIT_FIELDS = (
    "employee_id", "absence_type_id", "request_kind", "status",
    "requested_start_at", "requested_end_at", "parent_request_id",
    "submitted_at", "reviewed_at",
)

# Заявка, которую ещё не рассмотрели.
OPEN_STATUSES = ("DRAFT", "SUBMITTED", "IN_REVIEW")
# Отсутствие, которое считается существующим.
LIVE_ABSENCE_STATUSES = ("PLANNED", "ACTIVE", "COMPLETED")

MINUTES_PER_WORKING_DAY = 8 * 60


@dataclass(frozen=True)
class RequestView:
    """Заявка глазами сотрудника, вместе с производными состояниями."""

    request: AbsenceRequest
    working_days: int
    documents: int
    extension_pending: bool
    absence: EmployeeAbsence | None

    @property
    def can_cancel(self) -> bool:
        return self.request.status in OPEN_STATUSES


@dataclass(frozen=True)
class BalanceView:
    absence_type: AbsenceType
    year: int
    allocated_minutes: int
    used_minutes: int
    reserved_minutes: int
    adjustment_minutes: int

    @property
    def available_minutes(self) -> int:
        return (
            self.allocated_minutes
            + self.adjustment_minutes
            - self.used_minutes
            - self.reserved_minutes
        )


class AbsenceService(BaseService):
    """Заявки сотрудника. Actor здесь не участвует: действует сам человек."""

    # ------------------------------------------------------------ чтение

    def policy(self, organization_id) -> AbsencePolicy:
        return policy_for(organization_id)

    def types(self, context):
        return AbsenceType.objects.filter(
            organization_id=context.organization_id, is_active=True
        ).order_by("name")

    def requests(self, context, *, limit: int = 50, offset: int = 0):
        queryset = (
            AbsenceRequest.objects.filter(employee_id=context.employee.id)
            .select_related("absence_type", "parent_request")
            .prefetch_related("documents", "absences")
            .order_by("-created_at", "-id")
        )
        total = queryset.count()
        rows = list(queryset[offset:offset + limit])
        return [self._view(context, row) for row in rows], total

    def request(self, context, request_id: uuid.UUID) -> RequestView:
        row = (
            AbsenceRequest.objects.filter(
                id=request_id, employee_id=context.employee.id
            )
            .select_related("absence_type", "parent_request")
            .prefetch_related("documents", "absences")
            .first()
        )
        if row is None:
            # Чужая заявка отвечает так же, как отсутствие записи.
            raise NotFound("Заявка не найдена")
        return self._view(context, row)

    def balances(self, context, *, year: int | None = None) -> list[BalanceView]:
        target = year or timezone.now().year
        rows = (
            LeaveBalance.objects.filter(
                employee_id=context.employee.id, year=target
            )
            .select_related("absence_type")
            .order_by("absence_type__name")
        )
        return [
            BalanceView(
                absence_type=row.absence_type,
                year=row.year,
                allocated_minutes=row.allocated_minutes,
                used_minutes=row.used_minutes,
                reserved_minutes=row.reserved_minutes,
                adjustment_minutes=row.adjustment_minutes,
            )
            for row in rows
        ]

    # ----------------------------------------------------------- создание

    def create(
        self,
        context,
        *,
        absence_type_code: str,
        first_day: date,
        last_day: date,
        comment: str | None = None,
        document=None,
        now: datetime | None = None,
    ) -> RequestView:
        """Новая заявка на отсутствие — больничный или отпуск."""
        moment = now or timezone.now()
        policy = policy_for(context.organization_id)
        absence_type = self._require_type(context, absence_type_code)

        self._check_period(context, first_day, last_day)
        self._check_backdating(context, first_day, policy, moment)
        self._check_lead_time(context, absence_type, first_day, policy, moment)
        self._check_overlap(context, first_day, last_day)

        # Конец ВКЛЮЧЁН: `end_at` читают через `local_date(end_at)`, и
        # полуинтервал дал бы начало следующих суток — лишний день
        # в каждом больничном и в каждом отпуске.
        start_at, end_at = closed_range_bounds(
            first_day, last_day, context.timezone
        )
        working_days = self._working_days(context, first_day, last_day)
        if absence_type.deducts_leave_balance:
            self._check_balance(
                context, absence_type, working_days, policy, start_at
            )

        if self._document_needed(policy, working_days) and document is None:
            raise ValidationFailed(
                "К заявке нужно приложить справку",
                details={
                    "reason": "document_required",
                    "from_day": policy.document_required_from_day,
                    "days": working_days,
                },
            )

        with self.atomic():
            request = AbsenceRequest.objects.create(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                absence_type=absence_type,
                request_kind="CREATE",
                status="SUBMITTED",
                requested_start_at=start_at,
                requested_end_at=end_at,
                employee_comment=comment or None,
                submitted_at=moment,
            )
            self._act(context, request, "SUBMITTED", None, "SUBMITTED")
            if document is not None:
                self._attach(context, request, document, policy)

            if absence_type.deducts_leave_balance:
                # Резерв, а не списание: заявка ещё может быть отклонена.
                # Списывать до решения значило бы удерживать остаток за то,
                # чего не случилось.
                self._reserve(context, absence_type, working_days, start_at)

            if not policy.require_hr_approval:
                # Организация решила обходиться без согласования: заявка
                # подтверждается сразу и становится отсутствием.
                self._approve(context, request, actor=None, comment=None, now=moment)

            self._notify(request, "created")
            self.audit.record_by_employee(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                action="absence.request.create",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        return self.request(context, request.id)

    def extend(
        self,
        context,
        request_id: uuid.UUID,
        *,
        new_last_day: date,
        comment: str | None = None,
        now: datetime | None = None,
    ) -> RequestView:
        """Продление: ОТДЕЛЬНАЯ заявка, ссылающаяся на исходную.

        Подтверждённые даты не правятся: по ним уже посчитана статистика,
        и молча сдвинуть их значит переписать прошлое.
        """
        moment = now or timezone.now()
        policy = policy_for(context.organization_id)
        if not policy.extensions_allowed:
            raise Conflict(
                "Продление в этой организации не предусмотрено",
                details={"reason": "extensions_disabled"},
            )

        parent = self._require_own(context, request_id)
        if parent.status != "APPROVED":
            raise Conflict(
                "Продлить можно только подтверждённое отсутствие",
                details={"reason": "not_approved"},
            )
        if parent.request_kind != "CREATE":
            raise Conflict("Продлевают исходную заявку, а не продление")
        if self._pending_extension(parent) is not None:
            raise Conflict(
                "Продление уже отправлено и ждёт решения",
                details={"reason": "extension_pending"},
            )

        current_end = parent.requested_end_at
        _, new_end = closed_range_bounds(
            new_last_day, new_last_day, context.timezone
        )
        if current_end is not None and new_end <= current_end:
            raise ValidationFailed(
                "Новая дата окончания должна быть позже текущей",
                details={"reason": "not_longer"},
            )

        # Период продления — только ДОБАВЛЕННЫЕ дни, со следующего дня после
        # прежнего конца. Начать его прежним концом значило бы посчитать
        # последний день исходного отсутствия дважды: и в нём, и в продлении.
        first_extra_day = current_end.astimezone(context.timezone).date() + (
            timedelta(days=1)
        )
        extra_start, _ = closed_range_bounds(
            first_extra_day, new_last_day, context.timezone
        )
        extra_days = self._working_days(context, first_extra_day, new_last_day)
        if parent.absence_type.deducts_leave_balance:
            # Продление отпуска стоит остатка ровно так же, как сам отпуск.
            # Без этой проверки запрет уходить в минус обходился бы одним
            # продлением, а добавленные дни не списывались бы вовсе.
            self._check_balance(
                context, parent.absence_type, extra_days, policy, extra_start
            )

        with self.atomic():
            child = AbsenceRequest.objects.create(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                absence_type=parent.absence_type,
                request_kind="EXTEND",
                parent_request=parent,
                status="SUBMITTED",
                requested_start_at=extra_start,
                requested_end_at=new_end,
                employee_comment=comment or None,
                submitted_at=moment,
            )
            self._act(context, child, "SUBMITTED", None, "SUBMITTED")
            if parent.absence_type.deducts_leave_balance:
                self._reserve(
                    context, parent.absence_type, extra_days, extra_start
                )
            self._notify(child, "created")
            self.audit.record_by_employee(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                action="absence.request.extend",
                entity_type=ENTITY_REQUEST,
                entity_id=child.id,
                after=snapshot(child, REQUEST_AUDIT_FIELDS),
            )
        return self.request(context, child.id)

    def cancel(
        self, context, request_id: uuid.UUID, *, now: datetime | None = None
    ) -> RequestView:
        """Отмена своей заявки — в пределах того, что разрешила организация."""
        moment = now or timezone.now()
        policy = policy_for(context.organization_id)
        request = self._require_own(context, request_id)

        if request.status in ("CANCELLED", "REJECTED"):
            raise Conflict("Заявка уже закрыта")
        if request.status == "APPROVED":
            if policy.cancelling_approved_requires_hr:
                raise PermissionDenied(
                    "Подтверждённое отсутствие отменяет отдел кадров",
                    details={"reason": "hr_required"},
                )
        elif not policy.employee_may_cancel_pending:
            raise PermissionDenied(
                "Отменить заявку может отдел кадров",
                details={"reason": "hr_required"},
            )

        with self.atomic():
            before = snapshot(request, REQUEST_AUDIT_FIELDS)
            previous = request.status
            request.status = "CANCELLED"
            request.save(update_fields=["status", "updated_at"])
            self._act(context, request, "CANCELLED", previous, "CANCELLED")
            self._release(
                context, request, moment, was_approved=previous == "APPROVED"
            )
            EmployeeAbsence.objects.filter(
                origin_request=request, status__in=("PLANNED", "ACTIVE")
            ).update(status="CANCELLED", cancelled_at=moment)
            self._notify(request, "cancelled")
            self.audit.record_by_employee(
                organization_id=context.organization_id,
                employee_id=context.employee.id,
                action="absence.request.cancel",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                before=before,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        return self.request(context, request.id)

    def attach_document(
        self, context, request_id: uuid.UUID, document, *,
        now: datetime | None = None,
    ) -> RequestView:
        """Донести справку позже — если организация это разрешает."""
        policy = policy_for(context.organization_id)
        request = self._require_own(context, request_id)

        if request.status in ("CANCELLED", "REJECTED"):
            raise Conflict("Заявка закрыта")
        if request.status == "APPROVED" and not policy.document_can_be_added_later:
            raise Conflict(
                "Справку нужно было приложить при подаче",
                details={"reason": "too_late"},
            )

        with self.atomic():
            self._attach(context, request, document, policy)
        return self.request(context, request.id)

    # ------------------------------------------------------ решение отдела

    def decide(
        self,
        actor: Actor,
        request_id: uuid.UUID,
        *,
        approve: bool,
        comment: str | None = None,
        now: datetime | None = None,
    ) -> AbsenceRequest:
        """Решение HR по заявке. Требует права и своей организации."""
        self.access.require(actor, "absences.approve")
        moment = now or timezone.now()

        request = (
            AbsenceRequest.objects.filter(
                id=request_id, organization_id=actor.organization_id
            )
            .select_related("absence_type", "employee", "parent_request")
            .first()
        )
        if request is None:
            raise NotFound("Заявка не найдена")
        if request.status not in OPEN_STATUSES:
            raise Conflict(
                "Заявка уже рассмотрена", details={"status": request.status}
            )
        self._forbid_self_approval(actor, request)

        with self.atomic():
            before = snapshot(request, REQUEST_AUDIT_FIELDS)
            previous = request.status
            if approve:
                self._approve(
                    _ContextFromRequest(request), request, actor=actor,
                    comment=comment, now=moment,
                )
            else:
                request.status = "REJECTED"
                request.reviewed_by_user_id = actor.user_id
                request.reviewed_at = moment
                request.review_comment = comment or None
                request.save(
                    update_fields=[
                        "status", "reviewed_by_user", "reviewed_at",
                        "review_comment", "updated_at",
                    ]
                )
                self._act(
                    _ContextFromRequest(request), request, "REJECTED",
                    previous, "REJECTED", actor=actor, comment=comment,
                )
                self._release(_ContextFromRequest(request), request, moment)

            self._notify(request, "approved" if approve else "rejected")
            self.audit.record(
                actor,
                action="absence.request.approve" if approve else
                       "absence.request.reject",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                before=before,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        request.refresh_from_db()
        return request

    def cancel_approved(
        self, actor: Actor, request_id: uuid.UUID, *,
        comment: str | None = None, now: datetime | None = None,
    ) -> AbsenceRequest:
        """Отмена уже подтверждённого отсутствия — только отделом кадров."""
        self.access.require(actor, "absences.approve")
        moment = now or timezone.now()
        request = AbsenceRequest.objects.filter(
            id=request_id, organization_id=actor.organization_id
        ).select_related("absence_type", "employee").first()
        if request is None:
            raise NotFound("Заявка не найдена")
        if request.status != "APPROVED":
            raise Conflict("Отменять нечего: заявка не подтверждена")

        context = _ContextFromRequest(request)
        with self.atomic():
            before = snapshot(request, REQUEST_AUDIT_FIELDS)
            request.status = "CANCELLED"
            request.reviewed_by_user_id = actor.user_id
            request.reviewed_at = moment
            request.review_comment = comment or None
            request.save(
                update_fields=[
                    "status", "reviewed_by_user", "reviewed_at",
                    "review_comment", "updated_at",
                ]
            )
            self._act(context, request, "CANCELLED", "APPROVED", "CANCELLED",
                      actor=actor, comment=comment)
            EmployeeAbsence.objects.filter(
                origin_request=request, status__in=("PLANNED", "ACTIVE")
            ).update(status="CANCELLED", cancelled_at=moment)
            self._release(context, request, moment, was_approved=True)
            self._notify(request, "cancelled")
            self.audit.record(
                actor,
                action="absence.request.cancel",
                entity_type=ENTITY_REQUEST,
                entity_id=request.id,
                before=before,
                after=snapshot(request, REQUEST_AUDIT_FIELDS),
            )
        request.refresh_from_db()
        return request

    def queue(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        type_code: str | None = None,
        employee_id=None,
        office_id=None,
        region_id=None,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        request_kind: str | None = None,
    ):
        """Заявки на отсутствие под фильтрами очереди HR.

        `request_kind` — вид заявки: `CREATE`, `EXTEND` или `CANCEL`. Отмена
        подтверждённого отпуска — тоже заявка, и у неё своя вкладка.

        Область видимости — та же, что у списка сотрудников: заявка видна,
        если виден сам сотрудник. Фильтр не расширяет доступ: он сужает
        уже разрешённое.

        Период фильтруется по датам САМОГО ОТСУТСТВИЯ, а не по дате
        подачи: кадровик ищет «кто отсутствует в сентябре», а не «кто
        подал заявление в сентябре». Смешивать эти два значения нельзя,
        и в интерфейсе подпись говорит, какое из них выбрано.
        """
        self.access.require(actor, "absences.read")

        queryset = AbsenceRequest.objects.filter(
            organization_id=actor.organization_id
        ).select_related("absence_type", "employee", "parent_request").prefetch_related(
            # Документы и история страницей, а не по запросу на строку.
            "documents__file",
            "actions",
        )

        if status:
            queryset = queryset.filter(status__in=[s for s in status.split(",") if s])
        if request_kind:
            kinds = [k for k in request_kind.split(",") if k]
            queryset = queryset.filter(request_kind__in=kinds)
        if employee_id:
            # Отбор по человеку не расширяет доступ: область ниже
            # по-прежнему применяется, и чужой сотрудник даст пустой
            # набор, а не чужие заявки.
            queryset = queryset.filter(employee_id=employee_id)
        if type_code:
            codes = [c for c in type_code.split(",") if c]
            queryset = queryset.filter(absence_type__code__in=codes)
        if search:
            pattern = search.strip()
            queryset = queryset.filter(
                Q(employee__first_name__icontains=pattern)
                | Q(employee__last_name__icontains=pattern)
                | Q(employee__employee_number__icontains=pattern)
            )
        if date_from:
            queryset = queryset.filter(requested_end_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(requested_start_at__date__lte=date_to)

        visible = self._scope_ids(actor, office_id=office_id, region_id=region_id)
        if visible is not None:
            queryset = queryset.filter(employee_id__in=visible)
        return queryset

    def _scope_ids(self, actor: Actor, *, office_id=None, region_id=None):
        """Сотрудники в области видимости. `None` — вся организация.

        Именно `None` для «всех» и ПУСТОЙ набор для «ничего не видно»:
        слить эти случаи проверкой `if ids:` значит молча показать всю
        организацию тому, у кого прав нет.
        """
        from datetime import date

        from humotech.employees.models import EmployeeAssignment
        from humotech.employees.services import current_primary_assignment_filter

        condition = None
        if office_id:
            self.access.require_office(actor, office_id)
            condition = Q(office_id=office_id)
        elif region_id:
            self.access.require_region(actor, region_id)
            condition = Q(office__region_id=region_id)
        else:
            visible = self.access.visible_office_ids(actor)
            if visible is not None:
                condition = Q(office_id__in=visible)

        if condition is None:
            return None
        return EmployeeAssignment.objects.filter(
            current_primary_assignment_filter(date.today()) & condition
        ).values_list("employee_id", flat=True)

    def open_document(self, actor: Actor, request_id, document_id):
        """Файл справки к заявке — тем, кто видит саму заявку.

        Право то же, что на очередь, и область та же: справка чужого офиса
        не открывается по угаданному адресу. Отказ по области отвечает «не
        найдено», а не «запрещено»: иначе по ответу можно было бы
        перебирать, какие заявки существуют.
        """
        self.access.require(actor, "absences.read")
        document = (
            AbsenceDocument.objects.filter(
                id=document_id,
                absence_request_id=request_id,
                organization_id=actor.organization_id,
            )
            .select_related("file", "absence_request")
            .first()
        )
        if document is None:
            raise NotFound("Документ не найден")
        visible = self._scope_ids(actor)
        if visible is not None and not visible.filter(
            employee_id=document.absence_request.employee_id
        ).exists():
            raise NotFound("Документ не найден")
        return open_stored(document.file), document.file

    def pending(self, actor: Actor):
        """Заявки, ждущие решения. Для будущего интерфейса HR."""
        self.access.require(actor, "absences.read")
        return (
            AbsenceRequest.objects.filter(
                organization_id=actor.organization_id, status__in=OPEN_STATUSES
            )
            .select_related("absence_type", "employee", "parent_request")
            .order_by("submitted_at")
        )

    # ------------------------------------------------------------- внутри

    def _approve(self, context, request, *, actor, comment, now) -> None:
        """Подтверждение заявки: сама заявка плюс период отсутствия."""
        previous = request.status
        request.status = "APPROVED"
        request.reviewed_at = now
        request.review_comment = comment or None
        fields = ["status", "reviewed_at", "review_comment", "updated_at"]
        if actor is not None:
            request.reviewed_by_user_id = actor.user_id
            fields.append("reviewed_by_user")
        request.save(update_fields=fields)
        self._act(context, request, "APPROVED", previous, "APPROVED",
                  actor=actor, comment=comment)

        if request.request_kind == "EXTEND":
            # Продление сдвигает конец ИСХОДНОГО отсутствия, а не создаёт
            # второе: у человека один непрерывный больничный, а не два.
            parent = request.parent_request
            parent.requested_end_at = request.requested_end_at
            parent.save(update_fields=["requested_end_at", "updated_at"])
            EmployeeAbsence.objects.filter(
                origin_request=parent, status__in=("PLANNED", "ACTIVE")
            ).update(end_at=request.requested_end_at)
            if request.absence_type.deducts_leave_balance:
                # Резерв под добавленные дни превращается в списание —
                # ровно как у исходной заявки. Без этого продлением можно
                # было бы отгулять сколько угодно бесплатно.
                self._consume_reservation(request, now)
            return

        absence = EmployeeAbsence.objects.create(
            organization_id=request.organization_id,
            employee_id=request.employee_id,
            absence_type=request.absence_type,
            origin_request=request,
            start_at=request.requested_start_at,
            end_at=request.requested_end_at,
            status="ACTIVE" if request.requested_start_at <= now else "PLANNED",
        )

        if request.absence_type.deducts_leave_balance:
            # Резерв превращается в списание: отсутствие подтверждено.
            self._consume_reservation(request, now)
        return absence

    def _view(self, context, request: AbsenceRequest) -> RequestView:
        absence = next(
            (a for a in request.absences.all()
             if a.status in LIVE_ABSENCE_STATUSES),
            None,
        )
        working = 0
        if request.requested_start_at and request.requested_end_at:
            tz = context.timezone
            working = self._working_days(
                context,
                request.requested_start_at.astimezone(tz).date(),
                request.requested_end_at.astimezone(tz).date(),
            )
        return RequestView(
            request=request,
            working_days=working,
            documents=len(request.documents.all()),
            extension_pending=self._pending_extension(request) is not None,
            absence=absence,
        )

    def _pending_extension(self, request: AbsenceRequest):
        return AbsenceRequest.objects.filter(
            parent_request_id=request.id,
            request_kind="EXTEND",
            status__in=OPEN_STATUSES,
        ).first()

    def _require_type(self, context, code: str) -> AbsenceType:
        absence_type = AbsenceType.objects.filter(
            organization_id=context.organization_id, code=code, is_active=True
        ).first()
        if absence_type is None:
            raise NotFound("Такой вид отсутствия недоступен")
        return absence_type

    def _require_own(self, context, request_id: uuid.UUID) -> AbsenceRequest:
        request = (
            AbsenceRequest.objects.filter(
                id=request_id, employee_id=context.employee.id
            )
            .select_related("absence_type", "parent_request")
            .first()
        )
        if request is None:
            raise NotFound("Заявка не найдена")
        return request

    def _check_period(self, context, first_day: date, last_day: date) -> None:
        if last_day < first_day:
            raise ValidationFailed(
                "Конец периода раньше начала", details={"reason": "bad_range"}
            )
        if (last_day - first_day).days > 365:
            raise ValidationFailed(
                "Период длиннее года", details={"reason": "too_long"}
            )

    @staticmethod
    def _document_needed(policy, days: int) -> bool:
        """Нужна ли справка к заявке такой длины.

        `document_required_from_day` = 0 означает «с первого дня», то есть
        требование действует всегда. Значение 4 означает «справка нужна,
        если отсутствие длиннее трёх дней» — короткие больничные многие
        организации принимают без неё.
        """
        if not policy.document_required:
            return False
        return days >= max(policy.document_required_from_day, 1)

    def _check_backdating(self, context, first_day: date, policy, now) -> None:
        """Насколько глубоко в прошлое можно оформить отсутствие.

        Ноль означает «без ограничения»: больничный по своей природе
        оформляется задним числом — человек заболел, вышел и принёс
        справку. Организация может поставить границу, но её отсутствие
        не должно ломать главный сценарий.
        """
        allowed = policy.backdating_days_allowed
        if not allowed:
            return
        today = local_date(now, context.timezone)
        if first_day >= today:
            return
        behind = (today - first_day).days
        if behind > allowed:
            raise ValidationFailed(
                "Задним числом отсутствие так далеко не оформляется",
                details={
                    "reason": "backdating_not_allowed",
                    "days_back": behind,
                    "allowed": allowed,
                },
            )

    def _check_lead_time(
        self, context, absence_type, first_day: date, policy, now
    ) -> None:
        """За сколько дней подаётся заявка на отпуск.

        Правило применяется только к типам, которые списывают остаток
        отпуска: больничный по определению не планируется заранее, и
        требовать срок подачи от него было бы бессмыслицей.
        """
        required = policy.vacation_min_days_ahead
        if not required or not absence_type.deducts_leave_balance:
            return
        today = local_date(now, context.timezone)
        ahead = (first_day - today).days
        if ahead < required:
            raise ValidationFailed(
                f"Заявка на отпуск подаётся не позднее чем за {required} дн.",
                details={
                    "reason": "lead_time_required",
                    "days_ahead": ahead,
                    "required": required,
                },
            )

    def _check_overlap(self, context, first_day: date, last_day: date) -> None:
        start, end = range_bounds(first_day, last_day, context.timezone)
        clash = EmployeeAbsence.objects.filter(
            employee_id=context.employee.id,
            status__in=("PLANNED", "ACTIVE"),
            start_at__lt=end,
            end_at__gt=start,
        ).exists()
        if clash:
            raise Conflict(
                "На эти даты уже оформлено отсутствие",
                details={"reason": "overlap"},
            )
        pending = AbsenceRequest.objects.filter(
            employee_id=context.employee.id,
            request_kind="CREATE",
            status__in=OPEN_STATUSES,
            requested_start_at__lt=end,
            requested_end_at__gt=start,
        ).exists()
        if pending:
            raise Conflict(
                "На эти даты уже подана заявка",
                details={"reason": "duplicate_request"},
            )

    def _working_days(self, context, first_day: date, last_day: date) -> int:
        """Сколько рабочих дней в периоде — по графику и календарю.

        Отпуск считается рабочими днями: суббота, попавшая в отпуск,
        не тратит остаток. Без этого две недели отпуска стоили бы
        четырнадцать дней вместо десяти.
        """
        assignments = list(
            EmployeeScheduleAssignment.objects.filter(
                employee_id=context.employee.id, valid_from__lte=last_day
            )
            .select_related("schedule")
            .prefetch_related("schedule__days")
            .order_by("valid_from")
        )
        exceptions = {
            row.date: (row.is_working_day, row.office_id)
            for row in sorted(
                CalendarException.objects.filter(
                    organization_id=context.organization_id,
                    date__gte=first_day,
                    date__lte=last_day,
                    is_active=True,
                ),
                key=lambda r: r.office_id is not None,
            )
            if row.office_id in (None, getattr(context.office, "id", None))
        }

        if not assignments:
            # Графика нет — считаем все дни периода. Занижать нельзя:
            # заниженный отпуск списал бы меньше, чем человек отгулял.
            return (last_day - first_day).days + 1

        total = 0
        for day in days_in(first_day, last_day):
            schedule = next(
                (
                    a.schedule for a in assignments
                    if a.valid_from <= day
                    and (a.valid_to is None or a.valid_to >= day)
                ),
                None,
            )
            if schedule is None:
                total += 1
                continue
            override = exceptions.get(day)
            if override is not None:
                total += 1 if override[0] else 0
                continue
            match = next(
                (d for d in schedule.days.all() if d.weekday == day.isoweekday()),
                None,
            )
            total += 1 if (match and match.is_working_day) else 0
        return total

    def _check_balance(
        self, context, absence_type, working_days: int, policy, start_at
    ) -> None:
        """Хватает ли остатка ТОГО года, на который просят отпуск.

        Не года подачи заявки: в декабре просят январь, и остаток за январь
        лежит в другой строке. Проверить одну, а списать с другой значило бы
        и пропустить перерасход, и оставить в старом году вечный резерв.
        """
        balance = LeaveBalance.objects.filter(
            employee_id=context.employee.id,
            absence_type=absence_type,
            year=start_at.astimezone(context.timezone).year,
        ).first()
        if balance is None:
            raise Conflict(
                "Остаток отпуска не начислен — обратитесь в отдел кадров",
                details={"reason": "no_balance"},
            )
        available = (
            balance.allocated_minutes
            + balance.adjustment_minutes
            - balance.used_minutes
            - balance.reserved_minutes
        )
        needed = working_days * MINUTES_PER_WORKING_DAY
        if needed > available and not policy.allow_negative_leave_balance:
            raise Conflict(
                "Не хватает остатка отпуска",
                details={
                    "reason": "insufficient_balance",
                    "available_minutes": max(available, 0),
                    "requested_minutes": needed,
                },
            )

    def _reserve(
        self, context, absence_type, working_days: int, start_at
    ) -> None:
        """Отложить минуты под неподтверждённую заявку.

        Резерв, а не списание: заявку ещё могут отклонить, и списывать
        остаток за то, чего не случилось, нельзя. Но и не резервировать
        тоже нельзя — иначе на один остаток подадут пять заявок.

        Год — тот, на который просят отпуск: та же строка, из которой
        потом спишут и в которую вернут.
        """
        year = start_at.astimezone(context.timezone).year
        row = (
            LeaveBalance.objects.select_for_update()
            .filter(
                employee_id=context.employee.id,
                absence_type=absence_type,
                year=year,
            )
            .first()
        )
        if row is None:
            return
        row.reserved_minutes += working_days * MINUTES_PER_WORKING_DAY
        row.save(update_fields=["reserved_minutes", "updated_at"])

    def _release(
        self, context, request: AbsenceRequest, now, *,
        was_approved: bool = False,
    ) -> None:
        """Вернуть дни в остаток: заявка отклонена или отменена.

        Куда возвращать — зависит от того, где эти дни лежат сейчас.
        Пока заявку не подтвердили, они в резерве. После подтверждения
        резерв уже переехал в израсходованное (`_consume_reservation`),
        и вычитать отмену из резерва нельзя: он ноль, вычитание из нуля
        ничего не меняет, а израсходованное остаётся начисленным —
        сотрудник насовсем теряет отпуск, которого не было.
        """
        if not request.absence_type.deducts_leave_balance:
            return
        if not (request.requested_start_at and request.requested_end_at):
            return
        tz = context.timezone
        working = self._working_days(
            context,
            request.requested_start_at.astimezone(tz).date(),
            request.requested_end_at.astimezone(tz).date(),
        )
        row = (
            LeaveBalance.objects.select_for_update()
            .filter(
                employee_id=request.employee_id,
                absence_type=request.absence_type,
                year=request.requested_start_at.astimezone(tz).year,
            )
            .first()
        )
        if row is None:
            return
        field = "used_minutes" if was_approved else "reserved_minutes"
        # Ниже нуля не опускаем: ограничение в базе это запрещает, а
        # расхождение лучше оставить нулём, чем уронить отмену заявки.
        setattr(
            row,
            field,
            max(getattr(row, field) - working * MINUTES_PER_WORKING_DAY, 0),
        )
        row.save(update_fields=[field, "updated_at"])

    def _consume_reservation(self, request: AbsenceRequest, now) -> None:
        context = _ContextFromRequest(request)
        tz = context.timezone
        working = self._working_days(
            context,
            request.requested_start_at.astimezone(tz).date(),
            request.requested_end_at.astimezone(tz).date(),
        )
        minutes = working * MINUTES_PER_WORKING_DAY
        row = (
            LeaveBalance.objects.select_for_update()
            .filter(
                employee_id=request.employee_id,
                absence_type=request.absence_type,
                year=request.requested_start_at.astimezone(tz).year,
            )
            .first()
        )
        if row is None:
            return
        row.reserved_minutes = max(row.reserved_minutes - minutes, 0)
        row.used_minutes += minutes
        row.save(update_fields=["reserved_minutes", "used_minutes", "updated_at"])

    def _attach(self, context, request, document, policy) -> AbsenceDocument:
        stored = store(
            document,
            organization_id=context.organization_id,
            employee=context.employee,
            allowed_types=policy.allowed_document_types,
            max_bytes=policy.max_document_bytes,
        )
        record = AbsenceDocument.objects.create(
            organization_id=context.organization_id,
            absence_request=request,
            file=stored.file,
            document_type="MEDICAL_CERTIFICATE",
            verification_status="PENDING",
        )
        self._act(context, request, "DOCUMENT_ATTACHED", None, None)
        return record

    def _notify(self, request, event: str) -> None:
        """Уведомление о судьбе заявки — той же транзакцией, что и она сама.

        Отдельная транзакция означала бы, что сообщение может уцелеть при
        откате заявки: человек получил бы «отпуск подтверждён» про отпуск,
        которого нет.

        Ключ повтора собран из заявки и события: обработчик, сработавший
        дважды, даёт одну строку, а не два одинаковых сообщения в чате.
        """
        tz = _ContextFromRequest(request).timezone
        first = (
            request.requested_start_at.astimezone(tz).date()
            if request.requested_start_at else None
        )
        last = (
            request.requested_end_at.astimezone(tz).date()
            if request.requested_end_at else None
        )
        if event == "cancelled":
            body = messages.REQUEST_CANCELLED.format(
                first=messages.human_date(first), last=messages.human_date(last)
            )
        elif request.request_kind == "EXTEND" and event == "approved":
            body = messages.SICK_EXTENSION_APPROVED.format(
                last=messages.human_date(last)
            )
        elif request.request_kind == "EXTEND" and event == "rejected":
            body = messages.SICK_EXTENSION_REJECTED.format(
                last=messages.human_date(last)
            )
        else:
            body = messages.for_request(
                request.absence_type.code, event, first, last
            )

        enqueue(
            organization_id=request.organization_id,
            employee_id=request.employee_id,
            notification_type=f"absence.{event}",
            body=body,
            idempotency_key=f"absence:{request.id}:{event}",
            related_entity_type=ENTITY_REQUEST,
            related_entity_id=request.id,
        )

    def _act(
        self, context, request, action, previous, new, *, actor=None, comment=None
    ) -> None:
        """Строка в неизменяемой истории заявки.

        Комментарий сотрудника сюда НЕ попадает: в нём бывает диагноз,
        а история заявки видна шире, чем сама заявка.
        """
        AbsenceAction.objects.create(
            organization_id=request.organization_id,
            absence_request=request,
            action=action,
            actor_user_id=actor.user_id if actor else None,
            actor_employee_id=None if actor else request.employee_id,
            previous_status=previous,
            new_status=new,
            comment=comment or None,
        )

    def _forbid_self_approval(self, actor: Actor, request) -> None:
        """Своё отсутствие не согласовывают.

        Проверка по связи пользователь -> сотрудник: кадровик, у которого
        есть и учётная запись, и карточка сотрудника, не должен подтверждать
        сам себе отпуск.
        """
        from humotech.accounts.models import User

        # Связь идёт от учётной записи к карточке (`User.employee`),
        # а не наоборот: у большинства сотрудников учётной записи нет вовсе.
        own = (
            User.objects.filter(id=actor.user_id)
            .values_list("employee_id", flat=True)
            .first()
        )
        if own is not None and own == request.employee_id:
            raise PermissionDenied(
                "Своё отсутствие согласовывает кто-то другой",
                details={"reason": "self_approval"},
            )


class _ContextFromRequest:
    """Минимальный контекст, собранный из заявки.

    Нужен там, где действует HR: у него нет `EmployeeContext` сотрудника,
    а расчёт рабочих дней и часовой пояс требуются те же самые. Собирается
    из самой заявки, а не из запроса, — подменить нечем.
    """

    def __init__(self, request: AbsenceRequest) -> None:
        self._request = request

    @property
    def employee(self):
        return self._request.employee

    @property
    def organization_id(self):
        return self._request.organization_id

    @property
    def office(self):
        from humotech.telegram.identity import current_assignment

        assignment = current_assignment(self._request.employee_id)
        return assignment.office if assignment else None

    @property
    def timezone(self):
        from humotech.core.timeframes import office_zone

        return office_zone(self.office)


__all__ = ["AbsenceService", "BalanceView", "RequestView"]
