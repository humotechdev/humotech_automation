"""Уведомления глазами кадровика: что ушло, что нет и почему.

Очередь уже есть — `outbox.py`. Она смотрит на таблицу со стороны
отправщика: захватить пачку, отчитаться об успехе, отложить неудачу.
Здесь та же таблица со стороны человека, который разбирается, почему
сотрудник не получил сообщение.

Поэтому набор действий узкий и не пересекается с очередью. Создать
уведомление руками нельзя вовсе: строка заводится ТОЙ ЖЕ транзакцией,
что и событие, о котором она сообщает (см. `enqueue`), и уведомление
без события означало бы сообщение о том, чего не было. Отправить прямо
сейчас — тоже нельзя: отправляет бот, backend только помечает строку
готовой.

Остаются три вещи: посмотреть, вернуть в очередь и снять с отправки.

Про счётчик попыток. Автоматический предел (`MAX_ATTEMPTS`) защищает от
молчаливого повторения вечно — сломанную доставку надо заметить, а не
пережидать. Ручной повтор — противоположная ситуация: человек уже
посмотрел причину и устранил её, поэтому счётчик обнуляется. Иначе
повтор давал бы ровно одну попытку и снова FAILED, и кнопка была бы
бесполезной. Само нажатие остаётся в журнале, так что «повторяли
двадцать раз» видно по журналу, а не по счётчику.
"""

from __future__ import annotations

import uuid

from django.db.models import Q
from django.utils import timezone

from humotech.core.enums import NOTIFICATION_CHANNELS, NOTIFICATION_STATUSES
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.employees.models import EmployeeAssignment
from humotech.employees.selectors import require_visible_employee
from humotech.notifications.models import Notification

NOTIFICATION_FIELDS = (
    "status", "attempts", "next_attempt_at", "error_message", "sent_at",
)

#: Из каких состояний уведомление можно вернуть в очередь.
#:
#: SENT отсутствует намеренно: сообщение уже в чате у человека, и
#: «повторить» означало бы прислать второе. PENDING и RUNNING тоже —
#: первое уже в очереди, второе прямо сейчас держит отправщик.
RETRYABLE = frozenset({"FAILED", "CANCELLED"})

#: Из каких состояний уведомление можно снять с отправки.
#:
#: RUNNING отсутствует: строку уже забрал отправщик, и снять её здесь
#: значило бы записать «отменено» рядом с сообщением, которое в этот
#: момент уходит в Telegram. Зависшую строку возвращает в PENDING
#: `reclaim_stale`, после чего отмена работает честно.
CANCELLABLE = frozenset({"PENDING", "FAILED"})

#: Потолок массового повтора. Не техническое ограничение, а защита от
#: нажатия, смысл которого нажимающий не представляет: «повторить всё»
#: на десяти тысячах строк — это десять тысяч сообщений в чаты людей.
MAX_BULK_RETRY = 500


class NotificationService(BaseService):
    """Чтение по `notifications.read`, изменение по `notifications.manage`."""

    # ------------------------------------------------------------------ чтение

    def list(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID | None = None,
        status: str | None = None,
        channel: str | None = None,
        notification_type: str | None = None,
        search: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "notifications.read")

        queryset = Notification.objects.filter(
            organization_id=actor.organization_id
        ).select_related("employee")

        if employee_id is not None:
            # Проверка отдельным вызовом, а не фильтром: чужой сотрудник
            # обязан дать отказ, а не пустой список. Пустой список
            # неотличим от «уведомлений нет» и скрывает нехватку прав.
            require_visible_employee(self.access, actor, employee_id)
            queryset = queryset.filter(employee_id=employee_id)
        else:
            queryset = self._limit_to_scope(actor, queryset)

        if status is not None:
            queryset = queryset.filter(status=self._known(status, "status"))
        if channel is not None:
            queryset = queryset.filter(channel=self._known(channel, "channel"))
        if notification_type:
            queryset = queryset.filter(
                notification_type__startswith=notification_type.strip()
            )
        if search:
            needle = search.strip()
            queryset = queryset.filter(
                Q(title__icontains=needle) | Q(body__icontains=needle)
            )

        return paginate(queryset, limit=limit, cursor=cursor)

    def get(self, actor: Actor, notification_id: uuid.UUID) -> Notification:
        self.access.require(actor, "notifications.read")
        return self._require(actor, notification_id)

    # -------------------------------------------------------------- изменение

    def retry(self, actor: Actor, notification_id: uuid.UUID) -> Notification:
        """Вернуть уведомление в очередь на отправку."""
        self.access.require(actor, "notifications.manage")

        with self.atomic():
            row = self._lock(actor, notification_id)
            if row.status not in RETRYABLE:
                raise Conflict(
                    "Это уведомление нельзя отправить повторно",
                    details={
                        "status": row.status,
                        "retryable_from": sorted(RETRYABLE),
                    },
                )
            before = snapshot(row, NOTIFICATION_FIELDS)
            self._requeue(row)
            self.audit.record(
                actor,
                action="notification.retry",
                entity_type="notifications",
                entity_id=row.id,
                before=before,
                after=snapshot(row, NOTIFICATION_FIELDS),
            )
        return row

    def cancel(self, actor: Actor, notification_id: uuid.UUID) -> Notification:
        """Снять уведомление с отправки: адресату оно уже не нужно."""
        self.access.require(actor, "notifications.manage")

        with self.atomic():
            row = self._lock(actor, notification_id)
            if row.status not in CANCELLABLE:
                raise Conflict(
                    "Это уведомление нельзя снять с отправки",
                    details={
                        "status": row.status,
                        "cancellable_from": sorted(CANCELLABLE),
                    },
                )
            before = snapshot(row, NOTIFICATION_FIELDS)
            row.status = "CANCELLED"
            row.next_attempt_at = None
            row.locked_at = None
            row.error_message = "cancelled_by_operator"
            row.save(
                update_fields=[
                    "status", "next_attempt_at", "locked_at",
                    "error_message", "updated_at",
                ]
            )
            self.audit.record(
                actor,
                action="notification.cancel",
                entity_type="notifications",
                entity_id=row.id,
                before=before,
                after=snapshot(row, NOTIFICATION_FIELDS),
            )
        return row

    def retry_failed(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID | None = None,
        notification_type: str | None = None,
    ) -> int:
        """Вернуть в очередь всё, что упало окончательно.

        Разбор обычно кончается одинаково: сломалось одно и то же у всех,
        причину устранили, и теперь надо переотправить пачку. По одному
        это десятки нажатий.

        Берётся только FAILED. CANCELLED сюда не попадает: отмена — это
        решение человека, и массовый повтор не должен его отменять.
        """
        self.access.require(actor, "notifications.manage")

        with self.atomic():
            queryset = Notification.objects.filter(
                organization_id=actor.organization_id, status="FAILED"
            )
            if employee_id is not None:
                require_visible_employee(self.access, actor, employee_id)
                queryset = queryset.filter(employee_id=employee_id)
            else:
                queryset = self._limit_to_scope(actor, queryset)
            if notification_type:
                queryset = queryset.filter(
                    notification_type__startswith=notification_type.strip()
                )

            rows = list(
                queryset.select_for_update(skip_locked=True).order_by(
                    "created_at", "id"
                )[: MAX_BULK_RETRY + 1]
            )
            if len(rows) > MAX_BULK_RETRY:
                raise Conflict(
                    f"Сразу можно повторить не больше {MAX_BULK_RETRY} "
                    "уведомлений: сузьте отбор",
                    details={"limit": MAX_BULK_RETRY},
                )

            for row in rows:
                before = snapshot(row, NOTIFICATION_FIELDS)
                self._requeue(row)
                after = snapshot(row, NOTIFICATION_FIELDS)
                # Журнал ведётся по каждой строке, а не одной записью на
                # пачку: разбирая через месяц, почему человеку пришло
                # сообщение, ищут по этому уведомлению, а не по нажатию.
                after["bulk"] = True
                self.audit.record(
                    actor,
                    action="notification.retry",
                    entity_type="notifications",
                    entity_id=row.id,
                    before=before,
                    after=after,
                )
        return len(rows)

    # ------------------------------------------------------------------ внутри

    def _requeue(self, row: Notification) -> None:
        """PENDING прямо сейчас, счётчик попыток с нуля."""
        row.status = "PENDING"
        row.attempts = 0
        row.next_attempt_at = timezone.now()
        row.locked_at = None
        row.error_message = None
        row.save(
            update_fields=[
                "status", "attempts", "next_attempt_at", "locked_at",
                "error_message", "updated_at",
            ]
        )

    def _limit_to_scope(self, actor: Actor, queryset):
        """Уведомления видны по офисам сотрудника-адресата.

        Область берётся по ЛЮБОМУ периоду назначения, а не только по
        текущему, — ровно как в `require_visible_employee`. Иначе список
        и карточка расходились бы: после перевода человека его прошлые
        уведомления пропали бы из списка, оставшись доступными по ссылке.
        """
        visible = self.access.visible_office_ids(actor)
        if visible is None:
            return queryset
        # Пустое множество — это «ничего не видно», а не «видно всё».
        return queryset.filter(
            employee_id__in=EmployeeAssignment.objects.filter(
                office_id__in=visible
            ).values_list("employee_id", flat=True)
        )

    def _require(self, actor: Actor, notification_id: uuid.UUID) -> Notification:
        row = (
            Notification.objects.select_related("employee")
            .filter(id=notification_id, organization_id=actor.organization_id)
            .first()
        )
        if row is None:
            # чужая организация отвечает так же, как несуществующая запись
            raise NotFound("Уведомление не найдено")
        require_visible_employee(self.access, actor, row.employee_id)
        return row

    def _lock(self, actor: Actor, notification_id: uuid.UUID) -> Notification:
        """Та же проверка, но со строкой, взятой под блокировку.

        Без неё отправщик успевает перевести строку в RUNNING между
        чтением статуса и записью нового, и повтор затирает захват.
        """
        row = self._require(actor, notification_id)
        return Notification.objects.select_for_update().get(id=row.id)

    @staticmethod
    def _known(value: str, field: str) -> str:
        allowed = {
            "status": NOTIFICATION_STATUSES,
            "channel": NOTIFICATION_CHANNELS,
        }[field]
        if value not in allowed:
            raise ValidationFailed(
                f"Неизвестное значение параметра «{field}»",
                details={"field": field, "value": value,
                         "allowed": list(allowed)},
            )
        return value


__all__ = ["MAX_BULK_RETRY", "NotificationService"]
