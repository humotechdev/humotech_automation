"""Выездная работа: согласованный HR запрос и личный ответ сотрудника."""

from __future__ import annotations

import uuid
from datetime import date

from django.db.models import Q
from django.db import transaction
from django.utils import timezone

from humotech.absences.models import EmployeeAbsence
from humotech.attendance.models import AttendanceEvent, AttendanceSession, FieldWorkRequest
from humotech.attendance.statistics import COUNTED_ABSENCE_STATUSES, _daily_norm_seconds
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.rbac import Actor
from humotech.core.service import BaseService
from humotech.core.timeframes import day_bounds, office_zone
from humotech.employees.models import Employee, EmployeeAssignment
from humotech.notifications import outbox
from humotech.notifications.models import Notification
from humotech.schedules.models import CalendarException, EmployeeScheduleAssignment
from humotech.telegram.identity import AccessDenied, resolve_account, resolve_by_telegram_user_id
from humotech.telegram.models import TelegramAccount


def summary(row: FieldWorkRequest | None, *, delivery_status=None) -> dict | None:
    if row is None:
        return None
    if delivery_status is None:
        notification = Notification.objects.filter(
            related_entity_type="attendance_field_work_request", related_entity_id=row.id,
            notification_type="attendance.field_work_request",
        ).order_by("-created_at").first()
        delivery_status = notification.status if notification else None
    return {"id": str(row.id), "status": row.status, "date": row.date.isoformat(),
            "employee_id": str(row.employee_id), "office_id": str(row.office_id),
            "scheduled_start": row.scheduled_start.isoformat(),
            "scheduled_end": row.scheduled_end.isoformat(),
            "norm_seconds": row.norm_seconds,
            "work_location": row.work_location,
            "work_description": row.work_description,
            "requested_at": row.requested_at.isoformat(),
            "responded_at": row.responded_at.isoformat() if row.responded_at else None,
            "expires_at": row.expires_at.isoformat(),
            "delivery_status": delivery_status}


def expire_stale(*, request_id=None, organization_id=None):
    """Close expired requests before any read, decision or outbox delivery."""
    with transaction.atomic():
        rows = FieldWorkRequest.objects.select_for_update().filter(
            status="PENDING", expires_at__lte=timezone.now())
        if organization_id is not None:
            rows = rows.filter(organization_id=organization_id)
        if request_id is not None:
            rows = rows.filter(id=request_id)
        for row in rows.order_by("expires_at")[:200]:
            row.status = "EXPIRED"
            row.save(update_fields=["status", "updated_at"])
            from humotech.audit.models import AuditLog
            AuditLog.objects.create(organization_id=row.organization_id,
                action="field_work.expired", entity_type="attendance_field_work_requests",
                entity_id=row.id, new_values={"date": row.date.isoformat(), "status": "EXPIRED"})
            Notification.objects.filter(related_entity_type="attendance_field_work_request",
                related_entity_id=row.id, status="PENDING").update(status="CANCELLED")


def require_no_field_work(employee_id, first: date, last: date):
    """Call inside a transaction while holding the employee row lock."""
    if FieldWorkRequest.objects.filter(employee_id=employee_id,
        date__gte=first, date__lte=last,
        status__in=("PENDING", "CONFIRMED")).exists():
        raise Conflict("За этот период есть запрос выездной работы; сначала отмените его",
                       details={"reason": "field_work_conflict"})


class FieldWorkService(BaseService):
    def _employee(self, actor: Actor, employee_id: uuid.UUID, day: date):
        from humotech.absences.services import lock_employee
        lock_employee(employee_id)
        employee = Employee.objects.select_for_update().filter(
            id=employee_id, organization_id=actor.organization_id,
            archived_at__isnull=True,
        ).first()
        if employee is None:
            raise NotFound("Сотрудник не найден")
        assignment = EmployeeAssignment.objects.select_related("office", "office__organization").filter(
            employee_id=employee_id, organization_id=actor.organization_id,
            is_primary=True, valid_from__lte=day,
        ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=day)).order_by("-valid_from").first()
        if assignment is None or not assignment.office_id:
            raise NotFound("Сотрудник не найден")
        self.access.require_office(actor, assignment.office_id)
        return employee, assignment

    @staticmethod
    def _schedule(employee_id, day, office):
        assignment = EmployeeScheduleAssignment.objects.select_related("schedule").select_for_update(
            of=("schedule",)).prefetch_related("schedule__days").filter(employee_id=employee_id, valid_from__lte=day).filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=day)
        ).order_by("-valid_from").first()
        if assignment is None:
            raise Conflict("У сотрудника нет графика на этот день")
        match = next((d for d in assignment.schedule.days.all() if d.weekday == day.isoweekday()), None)
        exceptions = CalendarException.objects.filter(
            organization_id=office.organization_id, date=day, is_active=True,
        ).filter(Q(office_id=office.id) | Q(office__isnull=True))
        exception = exceptions.filter(office_id=office.id).first() or exceptions.filter(office__isnull=True).first()
        working = exception.is_working_day if exception else bool(match and match.is_working_day)
        # Перенесённый выходной без часов смены не допускает фиктивной нормы.
        if not working or match is None or not match.start_time or not match.end_time:
            raise Conflict("На этот день нет полной рабочей смены")
        norm = _daily_norm_seconds(assignment.schedule)
        if norm <= 0:
            raise Conflict("Для графика не определена норма рабочего дня")
        return match.start_time, match.end_time, norm

    @staticmethod
    def _no_conflicts(employee_id, day, tz):
        start, end = day_bounds(day, tz)
        if AttendanceSession.objects.filter(employee_id=employee_id, started_at__lt=end).filter(
                Q(ended_at__gt=start) | Q(ended_at__isnull=True)).exclude(status="INVALID").exists() or \
           AttendanceEvent.objects.filter(employee_id=employee_id, occurred_at__gte=start,
                                          occurred_at__lt=end, verification_status="ACCEPTED").exists():
            raise Conflict("В этот день уже есть отметка посещаемости",
                           details={"reason": "attendance_conflict"})
        if EmployeeAbsence.objects.filter(employee_id=employee_id,
                                          status__in=COUNTED_ABSENCE_STATUSES,
                                          start_at__lt=end, end_at__gte=start).exists():
            raise Conflict("На этот день оформлено отсутствие",
                           details={"reason": "attendance_conflict"})

    @staticmethod
    def _enqueue(row):
        outbox.enqueue(
            organization_id=row.organization_id, employee_id=row.employee_id,
            notification_type="attendance.field_work_request",
            body=(f"Подтверждение работы вне офиса за {row.date:%d.%m.%Y}. "
                  f"Рабочая смена по графику: {row.scheduled_start:%H:%M}–{row.scheduled_end:%H:%M}. "
                  "Подтвердите, что выполняете рабочую задачу вне офиса."),
            related_entity_type="attendance_field_work_request", related_entity_id=row.id,
            idempotency_key=f"field-work:{row.id}",
        )

    def create(self, actor, *, employee_id, day, manager_confirmed,
               work_location="", work_description=""):
        self.access.require(actor, "attendance.correct")
        if manager_confirmed is not True:
            raise ValidationFailed("Требуется подтверждение руководителя")
        if not date(2000, 1, 1) <= day <= date(2100, 12, 31):
            raise ValidationFailed("Дата вне допустимого диапазона")
        with self.atomic():
            employee, assignment = self._employee(actor, employee_id, day)
            tz = office_zone(assignment.office)
            now = timezone.now()
            if now.astimezone(tz).date() != day:
                raise Conflict("Запрос выездной работы доступен только за сегодня")
            if FieldWorkRequest.objects.filter(employee=employee, date=day,
                                               status__in=("PENDING", "CONFIRMED")).exists():
                raise Conflict("Запрос за этот день уже существует")
            self._no_conflicts(employee_id, day, tz)
            start, end, norm = self._schedule(employee_id, day, assignment.office)
            account = TelegramAccount.objects.select_related("employee", "organization").filter(
                employee=employee, status="ACTIVE").first()
            if account is None or isinstance(resolve_account(account, now=now), AccessDenied):
                raise Conflict("У сотрудника нет активной привязки Telegram")
            _, expires = day_bounds(day, tz)
            row = FieldWorkRequest.objects.create(
                employee=employee, organization_id=actor.organization_id,
                office=assignment.office, date=day, status="PENDING",
                scheduled_start=start, scheduled_end=end, norm_seconds=norm,
                requested_by_user_id=actor.user_id, requested_at=now,
                expires_at=expires, work_location=work_location,
                work_description=work_description,
            )
            self._enqueue(row)
            self.audit.record(actor, action="field_work.requested",
                              entity_type="attendance_field_work_requests", entity_id=row.id,
                              after={"employee_id": str(employee_id), "date": str(day),
                                     "office_id": str(row.office_id), "manager_confirmed": True})
        return row

    def list(self, actor, *, employee_id=None, day=None):
        self.access.require(actor, "attendance.read")
        expire_stale(organization_id=actor.organization_id)
        if employee_id:
            # The list is current-day scoped; historical office moves are checked per row below.
            if not Employee.objects.filter(id=employee_id, organization_id=actor.organization_id).exists():
                raise NotFound("Сотрудник не найден")
        rows = FieldWorkRequest.objects.filter(organization_id=actor.organization_id)
        if employee_id:
            rows = rows.filter(employee_id=employee_id)
        if day:
            rows = rows.filter(date=day)
        visible = self.access.visible_office_ids(actor)
        if visible is not None:
            rows = rows.filter(office_id__in=visible)
        return list(rows.order_by("-created_at")[:200])

    def _for_actor(self, actor, request_id):
        row = FieldWorkRequest.objects.select_for_update().filter(
            id=request_id, organization_id=actor.organization_id).first()
        if row is None:
            raise NotFound("Запрос не найден")
        self.access.require_office(actor, row.office_id)
        return row

    def cancel(self, actor, request_id):
        self.access.require(actor, "attendance.correct")
        expire_stale(request_id=request_id, organization_id=actor.organization_id)
        with self.atomic():
            row = self._for_actor(actor, request_id)
            if row.status == "CANCELLED":
                return row
            if row.status not in ("PENDING", "CONFIRMED"):
                raise Conflict("Этот запрос уже закрыт")
            before = row.status
            row.status, row.cancelled_at = "CANCELLED", timezone.now()
            row.save(update_fields=["status", "cancelled_at", "updated_at"])
            Notification.objects.filter(related_entity_type="attendance_field_work_request",
                                        related_entity_id=row.id, status="PENDING").update(status="CANCELLED")
            self.audit.record(actor, action="field_work.cancelled",
                              entity_type="attendance_field_work_requests", entity_id=row.id,
                              before={"status": before}, after={"status": row.status})
        return row

    def retry(self, actor, request_id):
        self.access.require(actor, "attendance.correct")
        expire_stale(request_id=request_id, organization_id=actor.organization_id)
        with self.atomic():
            row = self._for_actor(actor, request_id)
            if row.status != "PENDING" or timezone.now() >= row.expires_at:
                raise Conflict("Запрос уже закрыт или срок подтверждения истёк")
            notification = Notification.objects.select_for_update().filter(
                related_entity_type="attendance_field_work_request", related_entity_id=row.id,
                notification_type="attendance.field_work_request").first()
            if notification is None or notification.status != "FAILED":
                raise Conflict("Повтор возможен только после ошибки доставки")
            notification.status = "PENDING"
            notification.attempts = 0
            notification.next_attempt_at = timezone.now()
            notification.error_message = None
            notification.save(update_fields=["status", "attempts", "next_attempt_at",
                                             "error_message", "updated_at"])
            self.audit.record(actor, action="field_work.delivery_retried",
                              entity_type="attendance_field_work_requests", entity_id=row.id)
        return row

    def decide(self, request_id, *, telegram_user_id: int, decision: str):
        if decision not in ("CONFIRM", "DECLINE"):
            raise ValidationFailed("Неизвестное решение")
        expire_stale(request_id=request_id)
        with self.atomic():
            row = FieldWorkRequest.objects.filter(id=request_id).first()
            if row is None:
                raise NotFound("Запрос не найден")
            # Shared employee lock serializes this operation with QR processing.
            from humotech.absences.services import lock_employee
            lock_employee(row.employee_id)
            Employee.objects.select_for_update().get(id=row.employee_id)
            row = FieldWorkRequest.objects.select_for_update().get(id=request_id)
            identity = resolve_by_telegram_user_id(telegram_user_id)
            if isinstance(identity, AccessDenied) or identity.employee.id != row.employee_id \
                    or identity.organization.id != row.organization_id:
                raise NotFound("Запрос не найден")
            target = "CONFIRMED" if decision == "CONFIRM" else "DECLINED"
            if row.status == target:
                return row
            if row.status != "PENDING" or timezone.now() >= row.expires_at:
                raise Conflict("Срок подтверждения истёк или запрос уже закрыт",
                               details={"reason": "closed_or_expired"})
            if timezone.now().astimezone(office_zone(row.office)).date() != row.date:
                raise Conflict("Подтверждение возможно только в день работы",
                               details={"reason": "closed_or_expired"})
            if decision == "CONFIRM":
                self._no_conflicts(row.employee_id, row.date, office_zone(row.office))
                try:
                    current = self._schedule(row.employee_id, row.date, row.office)
                except Conflict as exc:
                    raise Conflict("График изменился после отправки запроса",
                                   details={"reason": "schedule_changed"}) from exc
                if current != (row.scheduled_start, row.scheduled_end, row.norm_seconds):
                    raise Conflict("График изменился после отправки запроса",
                                   details={"reason": "schedule_changed"})
            row.status, row.responded_at = target, timezone.now()
            row.save(update_fields=["status", "responded_at", "updated_at"])
            from humotech.audit.models import AuditLog
            AuditLog.objects.create(organization_id=row.organization_id,
                actor_employee_id=row.employee_id, action="field_work.confirmed" if decision == "CONFIRM" else "field_work.declined",
                entity_type="attendance_field_work_requests", entity_id=row.id,
                new_values={"status": row.status, "date": row.date.isoformat()})
        return row
