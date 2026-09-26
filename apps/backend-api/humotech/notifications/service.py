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
from datetime import date

from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.utils import timezone

from humotech.core.enums import NOTIFICATION_CHANNELS, NOTIFICATION_STATUSES
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.timeframes import organization_zone, range_bounds
from humotech.employees.models import EmployeeAssignment
from humotech.employees.selectors import require_visible_employee
from humotech.notifications.models import Notification, NotificationAttempt

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

#: Как шесть состояний очереди раскладываются по вкладкам.
#:
#: Правило одно: вкладка и её счётчик берут ОДИН И ТОТ ЖЕ список
#: состояний, а вкладки в сумме дают весь набор. Иначе число рядом
#: с вкладкой не совпадает с числом строк под ней, а какие-то
#: уведомления не видны ни на одной вкладке, кроме «Все».
#:
#: READ — это отправленное, которое к тому же прочитали, поэтому оно
#: у «Отправлено». RUNNING держит отправщик прямо сейчас — это «В
#: очереди», а не отдельное состояние для кадровика. CANCELLED —
#: собственная вкладка: снятое не отправлено, не в очереди и не упало.
#: Чаще всего его ставит не человек, а очередь, когда у сотрудника нет
#: живой привязки Telegram, и прятать такие строки в «ошибки» значило бы
#: звать неполадкой обычное положение дел.
STATUS_GROUPS: dict[str, tuple[str, ...]] = {
    "sent": ("SENT", "READ"),
    "queued": ("PENDING", "RUNNING"),
    "failed": ("FAILED",),
    "cancelled": ("CANCELLED",),
}

#: Потолок массового повтора. Не техническое ограничение, а защита от
#: нажатия, смысл которого нажимающий не представляет: «повторить всё»
#: на десяти тысячах строк — это десять тысяч сообщений в чаты людей.
MAX_BULK_RETRY = 500

#: Потолок длины строки поиска и префикса типа. Длиннее не бывает ни
#: у текста уведомления, который помнит человек, ни у типа.
MAX_FILTER_LENGTH = 200


def _text(raw: str, field: str) -> str:
    """Строковый фильтр: обрезать пробелы, отказать на мусоре.

    Нулевой байт PostgreSQL не принимает в тексте вовсе — без проверки
    это 500 вместо понятного отказа.
    """
    value = raw.strip()
    if "\x00" in value or len(value) > MAX_FILTER_LENGTH:
        raise ValidationFailed(
            f"Недопустимое значение параметра «{field}»",
            details={"field": field},
        )
    return value


class NotificationService(BaseService):
    """Чтение по `notifications.read`, изменение по `notifications.manage`."""

    # ------------------------------------------------------------------ чтение

    def list(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        **filters,
    ) -> Page:
        self.access.require(actor, "notifications.read")
        queryset = self._visible(actor, **filters)
        if status:
            queryset = queryset.filter(status__in=self._statuses(status))
        return paginate(self._with_office(actor, queryset), limit=limit,
                        cursor=cursor)

    def counts(self, actor: Actor, **filters) -> dict:
        """Сводка по всему доступному набору, а не по странице таблицы.

        Фильтр состояния сюда не передаётся намеренно: число рядом
        с вкладкой не должно зависеть от того, какая вкладка открыта.
        Остальные фильтры — период, область, поиск — применяются те же,
        что и к списку, иначе сводка описывала бы другой набор.
        """
        self.access.require(actor, "notifications.read")
        filters.pop("status", None)
        rows = dict(
            self._visible(actor, **filters)
            .values_list("status")
            .annotate(number=Count("id"))
        )
        counted = {code: rows.get(code, 0) for code in NOTIFICATION_STATUSES}
        return {
            **counted,
            "total": sum(counted.values()),
            **{
                group: sum(counted[code] for code in codes)
                for group, codes in STATUS_GROUPS.items()
            },
            # Пояс, в котором показывать время. Своей арифметики над
            # поясами у интерфейса быть не должно — она разошлась бы
            # с границами суток, по которым здесь режется период.
            "timezone": str(self._zone(actor)),
        }

    def get(self, actor: Actor, notification_id: uuid.UUID) -> Notification:
        self.access.require(actor, "notifications.read")
        row = self._require(actor, notification_id)
        # Карточке нужен тот же офис, что и строке списка.
        return self._annotated(actor, row.id)

    def attempts(
        self, actor: Actor, notification_id: uuid.UUID
    ) -> tuple[list[NotificationAttempt], bool]:
        """История попыток одного уведомления и признак её полноты.

        Права те же, что на само уведомление: историю попыток нельзя
        прочитать в обход проверки области видимости.

        Полнота берётся из строки, а не выводится из того, сколько
        записей нашлось. Вывести её по данным нельзя: у строки, чья
        история потеряна, после ручного повтора и счётчик нулевой, и
        записей нет — ровно как у только что заведённой.
        """
        self.access.require(actor, "notifications.read")
        row = self._require(actor, notification_id)
        rows = list(
            NotificationAttempt.objects.filter(notification_id=row.id).order_by(
                "attempted_at", "number"
            )
        )
        return rows, row.attempt_history_complete

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
        # Ответ несёт то же, что строка списка: интерфейс кладёт его
        # на место карточки, не перечитывая её отдельным запросом.
        return self._annotated(actor, row.id)

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
        return self._annotated(actor, row.id)

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
                    notification_type__startswith=_text(
                        notification_type, "notification_type"
                    )
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

    def _visible(
        self,
        actor: Actor,
        *,
        employee_id: uuid.UUID | None = None,
        channel: str | None = None,
        notification_type: str | None = None,
        search: str | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ):
        """Набор, доступный этому пользователю, с общими фильтрами.

        Один и тот же метод питает список и сводку — иначе счётчики
        описывали бы не тот набор, который показан под ними.
        """
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

        # Явно запрошенная область проверяется отдельно: запрос про офис
        # вне доступа обязан дать отказ, а не пустой список.
        if office_id is not None:
            self.access.require_office(actor, office_id)
            queryset = queryset.filter(
                employee_id__in=self._employees_of(office_id=office_id)
            )
        elif region_id is not None:
            self.access.require_region(actor, region_id)
            queryset = queryset.filter(
                employee_id__in=self._employees_of(region_id=region_id)
            )

        if channel is not None:
            queryset = queryset.filter(channel=self._known(channel, "channel"))
        if notification_type:
            queryset = queryset.filter(
                notification_type__startswith=_text(
                    notification_type, "notification_type"
                )
            )
        if search:
            needle = _text(search, "search")
            # Ищется и текст сообщения, и человек: кадровик одинаково
            # часто помнит либо одно, либо другое.
            queryset = queryset.filter(
                Q(title__icontains=needle)
                | Q(body__icontains=needle)
                | Q(employee__last_name__icontains=needle)
                | Q(employee__first_name__icontains=needle)
                | Q(employee__middle_name__icontains=needle)
                | Q(employee__employee_number__icontains=needle)
            )
        if date_from or date_to:
            queryset = self._within(actor, queryset, date_from, date_to)
        return queryset

    def _annotated(self, actor: Actor, notification_id: uuid.UUID) -> Notification:
        return self._with_office(
            actor,
            Notification.objects.select_related("employee").filter(
                id=notification_id
            ),
        )[0]

    def _employees_of(self, **scope):
        """Сотрудники области — по ЛЮБОМУ периоду назначения.

        Не только по текущему: иначе после перевода человека его прошлые
        уведомления исчезли бы из отбора по прежнему офису, хотя тогда
        он работал именно там.
        """
        return EmployeeAssignment.objects.filter(
            **({"office_id": scope["office_id"]} if "office_id" in scope
               else {"office__region_id": scope["region_id"]})
        ).values_list("employee_id", flat=True)

    def _within(self, actor: Actor, queryset, first: date | None, last: date | None):
        if first and last and last < first:
            raise ValidationFailed(
                "Конец периода раньше начала",
                details={"date_from": first.isoformat(),
                         "date_to": last.isoformat()},
            )
        start, end = range_bounds(first or last, last or first, self._zone(actor))
        return queryset.filter(created_at__gte=start, created_at__lt=end)

    def _with_office(self, actor: Actor, queryset):
        """Офис получателя НА МОМЕНТ уведомления, а не сегодняшний.

        Правило явное: основное назначение, чей период содержит день
        создания уведомления. Подставлять текущий офис человеку, которого
        год назад перевели, значит переписывать историю: сообщение уходило
        сотруднику другого офиса.

        Считается подзапросом, а не обращением на строку: двадцать строк
        списка иначе дают шестьдесят запросов.
        """
        zone = self._zone(actor)
        # День берётся снаружи отдельной аннотацией: `TruncDate` не умеет
        # принимать `OuterRef` напрямую — у него нет типа, пока ссылка
        # не разрешена.
        queryset = queryset.annotate(sent_day=TruncDate("created_at", tzinfo=zone))
        assignment = EmployeeAssignment.objects.filter(
            employee_id=OuterRef("employee_id"),
            is_primary=True,
            valid_from__lte=OuterRef("sent_day"),
        ).filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=OuterRef("sent_day"))
        ).order_by("-valid_from")
        return queryset.annotate(
            office_at_id=Subquery(assignment.values("office_id")[:1]),
            office_at_name=Subquery(assignment.values("office__name")[:1]),
            region_at_name=Subquery(assignment.values("office__region__name")[:1]),
        )

    def _zone(self, actor: Actor):
        """Пояс организации: у списка нет одного офиса, а сутки нужны одни."""
        return organization_zone(actor.organization_id)

    def _statuses(self, raw: str) -> list[str]:
        """Одно состояние или несколько через запятую.

        Вкладка «Отправлено» — это SENT и READ сразу; без списка она
        теряла бы прочитанные сообщения.
        """
        wanted = [self._known(part, "status") for part in raw.split(",") if part]
        if not wanted:
            raise ValidationFailed(
                "Параметр «status» пуст", details={"field": "status"}
            )
        return wanted

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


__all__ = ["MAX_BULK_RETRY", "STATUS_GROUPS", "NotificationService"]
