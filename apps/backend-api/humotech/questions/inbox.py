"""Обращения сотрудников: очередь, переписка и ответ в Telegram.

Здесь живёт вся механика «кто ждёт ответа». Правила собраны в одном
месте, потому что их читают три стороны сразу: CRM (кадровик отвечает),
бот (сотрудник пишет) и сид витрины. Разойдись они — одно и то же
обращение было бы «в работе» для CRM и «новым» для бота.

Состояния:

    NEW              — никто не взял;
    IN_PROGRESS      — у ответственного;
    WAITING_EMPLOYEE — кадровик спросил уточнение и ждёт человека;
    CLOSED           — снято, с автором, датой и причиной.

Переходы:

  * «взять в работу» — ответственный = текущий пользователь, IN_PROGRESS;
  * назначить или передать — ответственный меняется, NEW становится
    IN_PROGRESS: у обращения появился тот, кто его ведёт;
  * ответ с запросом уточнения — WAITING_EMPLOYEE;
  * новое сообщение сотрудника в WAITING_EMPLOYEE — снова IN_PROGRESS
    и «не прочитано»;
  * сообщение в закрытое обращение. Готового правила в проекте не было,
    оно определено здесь: в течение `REOPEN_WINDOW` после закрытия
    обращение переоткрывается (человек дописал «а ещё…» к тому же
    разговору), позже — заводится новое, а закрытое остаётся как было.

Каждое изменение состояния, ответственного, приоритета и категории
оставляет системную строку в ленте и запись в журнале аудита. Лента —
для кадровика, журнал — для разбора; рассказывают они одно и то же.

Ответ уходит сотруднику только при живой привязке Telegram. Проверка
стоит ДО записи: ответ, сохранённый как отправленный и никуда не
ушедший, — это ложь человеку, который его ждёт.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import (
    BooleanField,
    Case,
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.utils import timezone

from humotech.core.enums import (
    QUESTION_CATEGORIES,
    QUESTION_PRIORITIES,
    QUESTION_STATUSES,
)
from humotech.core.errors import Conflict, DomainError, NotFound, ValidationFailed
from humotech.core.pagination import Page, normalize_limit
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_text
from humotech.employees.models import EmployeeAssignment
from humotech.employees.selectors import require_visible_employee
from humotech.notifications import outbox
from humotech.questions.models import EmployeeQuestion, QuestionMessage

logger = logging.getLogger("humotech.questions")

OPEN = ("NEW", "IN_PROGRESS", "WAITING_EMPLOYEE")
#: Состояния, в которых срок ответа идёт: ждут кадровика.
WAITING_HR = ("NEW", "IN_PROGRESS")

#: Срок первого ответа от последнего сообщения сотрудника.
SLA = {
    "URGENT": timedelta(hours=1),
    "HIGH": timedelta(hours=2),
    "NORMAL": timedelta(hours=4),
    "LOW": timedelta(hours=24),
}

#: Сколько после закрытия сообщение сотрудника возвращает то же обращение.
REOPEN_WINDOW = timedelta(days=3)

#: Первая строка ответа HR в Telegram. По ней человек видит, на какое
#: обращение ответ, а бот узнаёт ответ HR, когда на него отвечают.
#: Бот держит тот же префикс (`REPLY_PREFIX` в его текстах).
REPLY_HEADER = "💬 Ответ HR по обращению №{number}"

QUICK_FILTERS = ("all", "unanswered", "mine", "urgent")
REPLY_AFTER = ("KEEP", "WAIT", "CLOSE")

QUESTION_FIELDS = (
    "status", "priority", "category", "assigned_to_user_id", "due_at",
    "closed_at", "closed_by_user_id", "close_reason",
)

ENTITY = "employee_questions"


class TelegramUnavailable(DomainError):
    """Отправить некуда: привязки нет, она отозвана или сотрудник уволен.

    Отдельный код, а не общий конфликт: интерфейс обязан сказать именно
    «Telegram не подключён» и не показывать сообщение отправленным.
    """

    code = "telegram_not_connected"
    http_status = 409


class AssistantUnavailable(DomainError):
    code = "assistant_unavailable"
    http_status = 503


@dataclass(frozen=True)
class InboxFilters:
    status: str | None = None
    office_id: uuid.UUID | None = None
    assignee: str | None = None
    category: str | None = None
    priority: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    search: str | None = None
    quick: str | None = None


class InboxService(BaseService):
    """Очередь обращений и всё, что с ними делает кадровик."""

    # ------------------------------------------------------------- список

    def list(
        self,
        actor: Actor,
        filters: InboxFilters,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        now: datetime | None = None,
    ) -> Page:
        """Очередь: срочные и просроченные сверху, дальше — по свежести.

        Порядок зависит от времени (обращение становится просроченным само),
        поэтому ключ страницы — тройка «ранг, последнее сообщение, id».
        Номеров страниц нет, как и во всех списках проекта.
        """
        self.access.require(actor, "questions.read")
        moment = now or timezone.now()
        size = normalize_limit(limit)

        rows = self._filtered(actor, filters, moment, with_status=True)
        rows = rows.annotate(
            rank=Case(
                When(_urgent(moment), then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by("rank", "-last_message_at", "-id")

        if cursor:
            rank, last, last_id = _decode(cursor)
            rows = rows.filter(
                Q(rank__gt=rank)
                | Q(rank=rank, last_message_at__lt=last)
                | Q(rank=rank, last_message_at=last, id__lt=last_id)
            )

        page = list(
            _with_last_message(rows).select_related(
                "employee", "assigned_to_user__employee"
            )[: size + 1]
        )
        has_more = len(page) > size
        page = page[:size]
        next_cursor = (
            _encode(page[-1].rank, page[-1].last_message_at, page[-1].id)
            if has_more and page
            else None
        )
        places = _places([row.employee_id for row in page])
        items = [self._item(row, places, moment) for row in page]
        return Page(items=items, next_cursor=next_cursor, has_more=has_more)

    def counts(
        self, actor: Actor, filters: InboxFilters, *, now: datetime | None = None
    ) -> dict:
        """Счётчики вкладок и быстрых фильтров.

        Вкладки считаются без учёта состояния: иначе, открыв «Новые»,
        кадровик видел бы нули у остальных и решал, что работы нет.
        Быстрые фильтры — внутри открытой вкладки: это её разрезы.
        """
        self.access.require(actor, "questions.read")
        moment = now or timezone.now()

        base = self._filtered(actor, filters, moment, with_status=False)
        by_status = {
            row["status"]: row["n"]
            for row in base.values("status").annotate(n=Count("id"))
        }
        statuses = {status: by_status.get(status, 0) for status in QUESTION_STATUSES}

        tab = base
        if filters.status:
            tab = tab.filter(status__in=_statuses(filters.status))
        quick = tab.aggregate(
            all=Count("id"),
            unanswered=Count("id", filter=_unanswered()),
            mine=Count("id", filter=Q(assigned_to_user_id=actor.user_id)),
            urgent=Count("id", filter=_urgent(moment)),
            unread=Count("id", filter=Q(unread=True)),
        )
        return {
            "statuses": statuses,
            "total": sum(statuses.values()),
            "quick": quick,
        }

    def assignees(self, actor: Actor) -> list[dict]:
        """Кому можно передать обращение: у кого есть право отвечать."""
        from humotech.accounts.models import User, UserRoleScope

        self.access.require(actor, "questions.read")
        moment = timezone.now()
        user_ids = (
            UserRoleScope.objects.filter(
                organization_id=actor.organization_id,
                valid_from__lte=moment,
                role__permission_links__permission__code="questions.answer",
            )
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=moment))
            .values_list("user_id", flat=True)
        )
        users = (
            User.objects.filter(
                id__in=user_ids, organization_id=actor.organization_id,
                status="ACTIVE",
            )
            .select_related("employee")
            .order_by("email")
        )
        people = [_person(user) for user in users]
        return sorted(people, key=lambda one: one["name"].lower())

    # ------------------------------------------------------------ карточка

    def get(self, actor: Actor, question_id: uuid.UUID, *, now=None) -> dict:
        self.access.require(actor, "questions.read")
        question = self._require(actor, question_id)
        return self.detail(actor, question, now=now)

    def detail(self, actor: Actor, question: EmployeeQuestion, *, now=None) -> dict:
        moment = now or timezone.now()
        places = _places([question.employee_id])
        item = self._item(_with_last_message_one(question), places, moment)

        messages = list(
            question.messages.select_related(
                "author_user__employee", "author_employee", "notification"
            ).order_by("created_at", "id")
        )
        can_answer = self.access.has(actor, "questions.answer")
        status = question.status
        item.update(
            {
                "question_text": question.question_text,
                "channel": question.channel,
                "first_response_at": question.first_response_at,
                "closed_at": question.closed_at,
                "closed_by": (
                    _person(question.closed_by_user)
                    if question.closed_by_user_id
                    else None
                ),
                "close_reason": question.close_reason,
                "telegram": _telegram(question.employee_id),
                "messages": [_message(one) for one in messages],
                "draft": _draft(question),
                "actions": {
                    "take": can_answer and status in OPEN and not (
                        status == "IN_PROGRESS"
                        and question.assigned_to_user_id == actor.user_id
                    ),
                    "assign": can_answer and status in OPEN,
                    "priority": can_answer and status in OPEN,
                    "category": can_answer,
                    "wait": can_answer and status in WAITING_HR,
                    "close": can_answer and status in OPEN,
                    "reopen": can_answer and status == "CLOSED",
                    "reply": can_answer and status in OPEN,
                    "draft": can_answer and status in OPEN,
                },
            }
        )
        return item

    def read(self, actor: Actor, question_id: uuid.UUID) -> dict:
        """Кадровик открыл обращение — последнее сообщение прочитано.

        В журнал не пишется: это не решение, а просмотр, и строка на
        каждое открытие утопила бы в журнале настоящие действия.
        """
        self.access.require(actor, "questions.read")
        question = self._require(actor, question_id)
        if question.unread:
            EmployeeQuestion.objects.filter(id=question.id).update(unread=False)
            question.unread = False
        return {"id": question.id, "unread": False}

    def context(self, actor: Actor, question_id: uuid.UUID) -> dict:
        """Правая колонка: кто спрашивает и что с ним связано."""
        from humotech.questions.context import question_context

        self.access.require(actor, "questions.read")
        question = self._require(actor, question_id)
        return question_context(self.access, actor, question)

    # ------------------------------------------------------------ действия

    def take(self, actor: Actor, question_id: uuid.UUID) -> dict:
        self.access.require(actor, "questions.answer")
        with self.atomic():
            question = self._lock(actor, question_id)
            _require_open(question)
            if (
                question.status == "IN_PROGRESS"
                and question.assigned_to_user_id == actor.user_id
            ):
                return self.detail(actor, question)
            before = snapshot(question, QUESTION_FIELDS)
            previous = question.assigned_to_user
            question.assigned_to_user_id = actor.user_id
            if question.status == "NEW":
                question.status = "IN_PROGRESS"
            question.save()
            self._event(
                question, actor, "TAKEN",
                {"from": _person(previous) if previous else None,
                 "to": _person_by_id(actor.user_id),
                 "status_from": before["status"], "status_to": question.status},
            )
            self._audit(actor, question, "question.take", before)
        return self.detail(actor, question)

    def assign(
        self, actor: Actor, question_id: uuid.UUID, *, to_user_id: uuid.UUID
    ) -> dict:
        from humotech.accounts.models import User

        self.access.require(actor, "questions.answer")
        target = (
            User.objects.select_related("employee")
            .filter(id=to_user_id, organization_id=actor.organization_id)
            .first()
        )
        if target is None:
            raise NotFound("Учётная запись не найдена")
        if not any(one["id"] == target.id for one in self.assignees(actor)):
            # Передать тому, кто не может ответить, значит повесить
            # обращение в воздух: он его не увидит или не сможет закрыть.
            raise ValidationFailed(
                "У пользователя нет права отвечать на обращения",
                details={"field": "to_user_id"},
            )

        with self.atomic():
            question = self._lock(actor, question_id)
            _require_open(question)
            if question.assigned_to_user_id == target.id:
                return self.detail(actor, question)
            before = snapshot(question, QUESTION_FIELDS)
            previous = question.assigned_to_user
            question.assigned_to_user_id = target.id
            if question.status == "NEW":
                question.status = "IN_PROGRESS"
            question.save()
            event = "TRANSFERRED" if previous is not None else "ASSIGNED"
            self._event(
                question, actor, event,
                {"from": _person(previous) if previous else None,
                 "to": _person(target),
                 "status_from": before["status"], "status_to": question.status},
            )
            self._audit(
                actor, question,
                "question.transfer" if previous is not None else "question.assign",
                before,
            )
        return self.detail(actor, question)

    def set_priority(
        self, actor: Actor, question_id: uuid.UUID, *, priority: str
    ) -> dict:
        self.access.require(actor, "questions.answer")
        _known(priority, "priority", QUESTION_PRIORITIES)
        with self.atomic():
            question = self._lock(actor, question_id)
            _require_open(question)
            if question.priority == priority:
                return self.detail(actor, question)
            before = snapshot(question, QUESTION_FIELDS)
            question.priority = priority
            if question.awaiting_reply and question.status in WAITING_HR:
                # Срок считается от сообщения, которого ждут, а не от
                # момента смены приоритета: иначе повышение приоритета
                # отодвигало бы срок ответа.
                question.due_at = question.last_message_at + SLA[priority]
            question.save()
            self._event(
                question, actor, "PRIORITY",
                {"from": before["priority"], "to": priority},
            )
            self._audit(actor, question, "question.priority", before)
        return self.detail(actor, question)

    def set_category(
        self,
        actor: Actor,
        question_id: uuid.UUID,
        *,
        category: str,
        topic: str | None = None,
    ) -> dict:
        self.access.require(actor, "questions.answer")
        _known(category, "category", QUESTION_CATEGORIES)
        clean_topic = (topic or "").strip()[:255] or None
        with self.atomic():
            question = self._lock(actor, question_id)
            changed_topic = clean_topic is not None and clean_topic != question.normalized_topic
            if question.category == category and not changed_topic:
                return self.detail(actor, question)
            before = snapshot(question, QUESTION_FIELDS + ("normalized_topic",))
            question.category = category
            if changed_topic:
                question.normalized_topic = clean_topic
            question.save()
            self._event(
                question, actor, "CATEGORY",
                {"from": before["category"], "to": category,
                 "topic": question.normalized_topic},
            )
            self._audit(
                actor, question, "question.category", before,
                fields=QUESTION_FIELDS + ("normalized_topic",),
            )
        return self.detail(actor, question)

    def wait_employee(self, actor: Actor, question_id: uuid.UUID) -> dict:
        self.access.require(actor, "questions.answer")
        with self.atomic():
            question = self._lock(actor, question_id)
            if question.status not in WAITING_HR:
                raise Conflict(
                    "Ждать сотрудника можно только по обращению в работе",
                    details={"status": question.status},
                )
            before = snapshot(question, QUESTION_FIELDS)
            self._to_waiting(question, actor)
            question.save()
            self._audit(actor, question, "question.wait", before)
        return self.detail(actor, question)

    def close(
        self, actor: Actor, question_id: uuid.UUID, *, reason: str
    ) -> dict:
        self.access.require(actor, "questions.answer")
        text = clean_text(reason, field="reason", required=True)
        with self.atomic():
            question = self._lock(actor, question_id)
            if question.status == "CLOSED":
                raise Conflict("Обращение уже закрыто", details={"status": "CLOSED"})
            before = snapshot(question, QUESTION_FIELDS)
            self._to_closed(question, actor, text)
            question.save()
            self._audit(actor, question, "question.close", before)
        return self.detail(actor, question)

    def reopen(self, actor: Actor, question_id: uuid.UUID) -> dict:
        self.access.require(actor, "questions.answer")
        with self.atomic():
            question = self._lock(actor, question_id)
            if question.status != "CLOSED":
                raise Conflict(
                    "Переоткрыть можно только закрытое обращение",
                    details={"status": question.status},
                )
            before = snapshot(question, QUESTION_FIELDS)
            self._reopen(question, author_user_id=actor.user_id, by="hr")
            question.save()
            self._audit(actor, question, "question.reopen", before)
        return self.detail(actor, question)

    def reply(
        self,
        actor: Actor,
        question_id: uuid.UUID,
        *,
        text: str,
        after: str = "KEEP",
        close_reason: str | None = None,
        client_request_id: str | None = None,
    ) -> dict:
        """Ответ сотруднику: лента, очередь Telegram, состояние, журнал.

        Всё — одной транзакцией. Откатился ответ — откатилось и сообщение
        в очереди; сообщения в очереди без ответа в ленте тоже не бывает.

        Повтор с тем же `client_request_id` возвращает уже сохранённое и
        второго сообщения человеку не даёт.
        """
        self.access.require(actor, "questions.answer")
        body = clean_text(text, field="text", required=True)
        _known(after, "after", REPLY_AFTER)
        reason = (close_reason or "").strip() or "Ответ отправлен сотруднику"
        request_key = (client_request_id or "").strip()[:100] or None

        with self.atomic():
            question = self._lock(actor, question_id)
            if request_key and question.messages.filter(
                client_request_id=request_key
            ).exists():
                return self.detail(actor, question)
            if question.status not in OPEN:
                raise Conflict(
                    "Обращение закрыто. Переоткройте его, чтобы ответить",
                    details={"status": question.status},
                )

            telegram = _telegram(question.employee_id)
            if not telegram["connected"]:
                raise TelegramUnavailable(
                    "Telegram сотрудника не подключён — сообщение не отправлено",
                    details={"reason": telegram["reason"]},
                )

            before = snapshot(question, QUESTION_FIELDS)
            if question.assigned_to_user_id is None:
                # Ответил — значит взял: иначе в «Мои» его нет, а вёл
                # разговор именно этот человек. Событие — до ответа, как
                # это и произошло.
                question.assigned_to_user_id = actor.user_id
                self._event(
                    question, actor, "TAKEN",
                    {"from": None, "to": _person_by_id(actor.user_id),
                     "status_from": question.status, "status_to": "IN_PROGRESS"},
                )
            moment = timezone.now()
            message_id = uuid.uuid4()
            notification = outbox.enqueue(
                organization_id=question.organization_id,
                employee_id=question.employee_id,
                notification_type="question.reply",
                title=f"Ответ на обращение №{question.number}",
                # В ленте CRM — чистый текст, в чат — с заголовком: без
                # номера человек с двумя обращениями не поймёт, о каком речь.
                body=_reply_body(question, body),
                # Ключ — по сообщению, а не по обращению: у переписки
                # ответов несколько, и ключ по обращению вернул бы второму
                # ответу первое уведомление, так и не отправив его.
                idempotency_key=f"question.reply:{message_id}",
                related_entity_type=ENTITY,
                related_entity_id=question.id,
            )
            QuestionMessage.objects.create(
                id=message_id,
                organization_id=question.organization_id,
                question=question,
                kind="HR",
                source="CRM",
                body=body,
                author_user_id=actor.user_id,
                notification=notification,
                client_request_id=request_key,
                created_at=moment,
            )

            question.last_message_at = moment
            question.awaiting_reply = False
            question.unread = False
            question.due_at = None
            if question.first_response_at is None:
                question.first_response_at = moment

            if after == "CLOSE":
                self._to_closed(question, actor, reason)
            elif after == "WAIT":
                self._to_waiting(question, actor)
            elif question.status == "NEW":
                question.status = "IN_PROGRESS"
            question.save()
            self._audit(
                actor, question, "question.reply", before,
                extra={"message_id": str(message_id),
                       "notification_id": str(notification.id) if notification else None},
            )
        return self.detail(actor, question)

    def refresh_draft(self, actor: Actor, question_id: uuid.UUID) -> dict:
        """Пересобрать черновик по опубликованной базе знаний."""
        self.access.require(actor, "questions.answer")
        question = self._require(actor, question_id)
        if question.status not in OPEN:
            raise Conflict("Обращение закрыто", details={"status": question.status})
        prepare_draft(question, strict=True)
        question.refresh_from_db()
        return self.detail(actor, question)

    # -------------------------------------------------- сообщение сотрудника

    def receive(
        self,
        context,
        *,
        text: str,
        telegram_message_id: int | None = None,
        now: datetime | None = None,
    ) -> tuple[EmployeeQuestion, bool]:
        """Сообщение сотрудника из Telegram.

        Возвращает обращение и признак «заведено новое». Повтор того же
        сообщения Telegram (бот переотправил после сбоя сети) новой строки
        не даёт.
        """
        employee = context.employee
        body = clean_text(text, field="text", required=True)
        moment = now or timezone.now()

        if telegram_message_id is not None:
            existing = (
                QuestionMessage.objects.select_related("question")
                .filter(
                    question__employee_id=employee.id,
                    telegram_message_id=telegram_message_id,
                )
                .first()
            )
            if existing is not None:
                return existing.question, False

        created = False
        with self.atomic():
            question = (
                EmployeeQuestion.objects.select_for_update()
                .filter(employee_id=employee.id)
                .order_by("-last_message_at", "-created_at")
                .first()
            )
            usable = question is not None and (
                question.status in OPEN
                or (
                    question.closed_at is not None
                    and moment - question.closed_at <= REOPEN_WINDOW
                )
            )
            if not usable:
                question = self._create(employee, body, moment)
                created = True
                before = None
            else:
                before = snapshot(question, QUESTION_FIELDS)
                if question.status == "CLOSED":
                    self._reopen(
                        question, author_user_id=None, by="employee", at=moment
                    )
                elif question.status == "WAITING_EMPLOYEE":
                    question.status = "IN_PROGRESS"
                    self._event(
                        question, None, "RESUMED",
                        {"from": "WAITING_EMPLOYEE", "to": "IN_PROGRESS"},
                        at=moment,
                    )

            QuestionMessage.objects.create(
                organization_id=question.organization_id,
                question=question,
                kind="EMPLOYEE",
                source="TELEGRAM",
                body=body,
                author_employee_id=employee.id,
                telegram_message_id=telegram_message_id,
                # На микросекунду позже события: лента упорядочена по
                # времени, и «переоткрыто» обязано стоять до сообщения.
                created_at=moment + timedelta(microseconds=1),
            )
            if not question.awaiting_reply or question.due_at is None:
                question.due_at = moment + SLA[question.priority]
            question.awaiting_reply = True
            question.unread = True
            question.last_message_at = moment
            question.save()
            self.audit.record_by_employee(
                organization_id=question.organization_id,
                employee_id=employee.id,
                action="question.create" if created else "question.message",
                entity_type=ENTITY,
                entity_id=question.id,
                before=before,
                after=snapshot(question, QUESTION_FIELDS),
            )

        if created:
            # Черновик — после фиксации: сотрудник ждёт подтверждения от
            # бота, и недоступный ассистент не должен ни задерживать
            # запись обращения, ни отменять её.
            try:
                prepare_draft(question, strict=False)
            except Exception:  # noqa: BLE001 — черновик необязателен
                logger.exception("черновик для обращения %s не собран", question.id)
        return question, created

    # ------------------------------------------------------------ внутри

    def _create(self, employee, body: str, moment: datetime) -> EmployeeQuestion:
        topic = body.split("\n", 1)[0].strip()
        topic = topic if len(topic) <= 120 else topic[:117].rstrip() + "…"
        for _ in range(5):
            number = next_number(employee.organization_id)
            try:
                with transaction.atomic():
                    question = EmployeeQuestion.objects.create(
                        organization_id=employee.organization_id,
                        employee_id=employee.id,
                        number=number,
                        question_text=body,
                        normalized_topic=topic,
                        status="NEW",
                        last_message_at=moment,
                    )
                break
            except IntegrityError:
                # Номер занял параллельный запрос — берём следующий.
                continue
        else:
            raise Conflict("Не удалось выдать номер обращения, повторите")
        self._event(question, None, "CREATED", {"source": "TELEGRAM"}, at=moment)
        return question

    def _to_waiting(self, question: EmployeeQuestion, actor: Actor) -> None:
        previous = question.status
        if question.assigned_to_user_id is None:
            question.assigned_to_user_id = actor.user_id
        question.status = "WAITING_EMPLOYEE"
        question.due_at = None
        self._event(
            question, actor, "WAITING_EMPLOYEE",
            {"from": previous, "to": "WAITING_EMPLOYEE"},
        )

    def _to_closed(self, question: EmployeeQuestion, actor: Actor, reason: str) -> None:
        previous = question.status
        question.status = "CLOSED"
        question.closed_at = timezone.now()
        question.closed_by_user_id = actor.user_id
        question.close_reason = reason
        question.due_at = None
        question.unread = False
        self._event(
            question, actor, "CLOSED",
            {"from": previous, "to": "CLOSED", "reason": reason},
        )

    def _reopen(
        self, question: EmployeeQuestion, *, author_user_id, by: str, at=None
    ) -> None:
        question.status = "IN_PROGRESS" if question.assigned_to_user_id else "NEW"
        details = {
            "from": "CLOSED", "to": question.status, "by": by,
            "closed_reason": question.close_reason,
        }
        question.closed_at = None
        question.closed_by_user_id = None
        question.close_reason = None
        if question.awaiting_reply:
            question.due_at = timezone.now() + SLA[question.priority]
        QuestionMessage.objects.create(
            organization_id=question.organization_id,
            question=question,
            kind="SYSTEM",
            source="SYSTEM" if author_user_id is None else "CRM",
            event="REOPENED",
            details=details,
            author_user_id=author_user_id,
            created_at=at or timezone.now(),
        )

    def _event(
        self, question, actor: Actor | None, event: str, details: dict, *, at=None
    ) -> None:
        values = dict(
            organization_id=question.organization_id,
            question=question,
            kind="SYSTEM",
            source="CRM" if actor is not None else "SYSTEM",
            event=event,
            details=_jsonable(details),
            author_user_id=actor.user_id if actor is not None else None,
            # Время ставится здесь, а не базой: `now()` в PostgreSQL — начало
            # транзакции, и все строки одного действия получили бы одно
            # время, а лента — случайный порядок.
            created_at=at if at is not None else timezone.now(),
        )
        QuestionMessage.objects.create(**values)

    def _audit(
        self, actor, question, action, before, *, fields=QUESTION_FIELDS, extra=None
    ) -> None:
        after = snapshot(question, fields)
        if extra:
            after.update(extra)
        self.audit.record(
            actor, action=action, entity_type=ENTITY, entity_id=question.id,
            before=before, after=after,
        )

    def _filtered(self, actor: Actor, filters: InboxFilters, moment, *, with_status: bool):
        rows = _scoped(self.access, actor)
        if filters.office_id is not None:
            self.access.require_office(actor, filters.office_id)
            rows = rows.filter(
                employee_id__in=_current_assignments().filter(
                    office_id=filters.office_id
                ).values_list("employee_id", flat=True)
            )
        if filters.assignee:
            if filters.assignee == "me":
                rows = rows.filter(assigned_to_user_id=actor.user_id)
            elif filters.assignee == "none":
                rows = rows.filter(assigned_to_user_id__isnull=True)
            else:
                rows = rows.filter(assigned_to_user_id=_uuid(filters.assignee, "assignee"))
        if filters.category:
            rows = rows.filter(
                category__in=[_known(one, "category", QUESTION_CATEGORIES)
                              for one in filters.category.split(",") if one]
            )
        if filters.priority:
            rows = rows.filter(
                priority__in=[_known(one, "priority", QUESTION_PRIORITIES)
                              for one in filters.priority.split(",") if one]
            )
        if filters.date_from:
            rows = rows.filter(last_message_at__date__gte=filters.date_from)
        if filters.date_to:
            rows = rows.filter(last_message_at__date__lte=filters.date_to)
        if filters.search:
            rows = rows.filter(_search(filters.search))
        if with_status:
            if filters.status:
                rows = rows.filter(status__in=_statuses(filters.status))
            quick = filters.quick or "all"
            _known(quick, "quick", QUICK_FILTERS)
            if quick == "unanswered":
                rows = rows.filter(_unanswered())
            elif quick == "mine":
                rows = rows.filter(assigned_to_user_id=actor.user_id)
            elif quick == "urgent":
                rows = rows.filter(_urgent(moment))
        return rows

    def _item(self, row, places: dict, moment: datetime) -> dict:
        employee = row.employee
        place = places.get(row.employee_id) or {}
        overdue = bool(
            row.status in WAITING_HR and row.due_at is not None and row.due_at < moment
        )
        return {
            "id": row.id,
            "number": row.number,
            "employee": {
                "id": employee.id,
                "full_name": _full_name(employee),
                "employee_number": employee.employee_number,
                "has_photo": employee.photo_id is not None,
            },
            "office": place.get("office"),
            "topic": row.normalized_topic or _snippet(row.question_text, 120),
            "snippet": _snippet(getattr(row, "last_body", None) or row.question_text, 160),
            "last_message_kind": getattr(row, "last_kind", None) or "EMPLOYEE",
            "category": row.category,
            "priority": row.priority,
            "status": row.status,
            "unread": row.unread,
            "awaiting_reply": row.awaiting_reply and row.status in OPEN,
            "due_at": row.due_at,
            "overdue": overdue,
            "last_message_at": row.last_message_at,
            "created_at": row.created_at,
            "assignee": (
                _person(row.assigned_to_user) if row.assigned_to_user_id else None
            ),
        }

    def _require(self, actor: Actor, question_id: uuid.UUID) -> EmployeeQuestion:
        question = (
            EmployeeQuestion.objects.select_related(
                "employee", "assigned_to_user__employee", "closed_by_user__employee"
            )
            .filter(id=question_id, organization_id=actor.organization_id)
            .first()
        )
        if question is None:
            raise NotFound("Обращение не найдено")
        require_visible_employee(self.access, actor, question.employee_id)
        return question

    def _lock(self, actor: Actor, question_id: uuid.UUID) -> EmployeeQuestion:
        """Та же проверка доступа плюс блокировка строки до конца транзакции.

        Два кадровика, одновременно нажавших «Взять в работу», иначе
        оба увидели бы обращение свободным.
        """
        self._require(actor, question_id)
        return (
            EmployeeQuestion.objects.select_for_update(of=("self",))
            .select_related(
                "employee", "assigned_to_user__employee", "closed_by_user__employee"
            )
            .get(id=question_id)
        )


# --- черновик ассистента ----------------------------------------------------


def prepare_draft(question: EmployeeQuestion, *, strict: bool) -> None:
    """Черновик ответа по опубликованной базе знаний.

    Ассистент ищет только по ACTIVE-документам области сотрудника — тот же
    конвейер, что отвечает в боте. Результат сохраняется подсказкой
    кадровику и никуда не отправляется.

    `strict` — вызов из CRM: выключенный или недоступный ассистент там
    ошибка, о которой надо сказать. При приёме сообщения — нет: обращение
    обязано завестись и без черновика.
    """
    from humotech.ai_assistant.container import build_container
    from humotech.ai_assistant.schemas import AnswerRequest, AnswerStatus, ScoreBand
    from humotech.core.errors import AiDisabled

    container = build_container()
    if not container.answer.enabled:
        if strict:
            raise AiDisabled(
                "Ассистент выключен: черновик по базе знаний сейчас не собрать"
            )
        return

    latest = (
        question.messages.filter(kind="EMPLOYEE").order_by("-created_at").first()
    )
    text = latest.body if latest is not None else question.question_text
    response = container.answer.execute(
        AnswerRequest(employee_id=question.employee_id, question=text[:1000])
    )

    sources = [str(one.id) for one in response.sources]
    if response.status in (AnswerStatus.EXACT_FAQ, AnswerStatus.RAG_ANSWERED):
        confident = response.score_band in (ScoreBand.HIGH, ScoreBand.MEDIUM)
        status = "READY" if confident else "LOW_CONFIDENCE"
        answer = response.answer
    elif response.status == AnswerStatus.ESCALATED and sources:
        # Ассистент нашёл несколько правил одного уровня, которые спорят.
        # Ответа нет намеренно: выбирать между правилами должен человек.
        status, answer = "CONFLICT", None
    elif response.status in (AnswerStatus.ESCALATED, AnswerStatus.PERSONAL_DATA):
        status, answer = "NO_SOURCES", None
    else:
        if strict:
            raise AssistantUnavailable("Ассистент сейчас недоступен, повторите позже")
        return

    score = response.retrieval_score
    EmployeeQuestion.objects.filter(id=question.id).update(
        ai_status=status,
        ai_answer_text=answer,
        ai_confidence=(
            Decimal(str(round(max(0.0, min(1.0, score)), 4)))
            if score is not None else None
        ),
        ai_source_ids=sources or None,
        answer_source_id=sources[0] if sources else None,
        ai_generated_at=timezone.now(),
    )


# --- общие для списка и карточки -------------------------------------------


def next_number(organization_id) -> int:
    last = (
        EmployeeQuestion.objects.filter(organization_id=organization_id)
        .order_by("-number")
        .values_list("number", flat=True)
        .first()
    )
    return (last or 0) + 1


def _scoped(access, actor: Actor):
    rows = EmployeeQuestion.objects.filter(organization_id=actor.organization_id)
    visible = access.visible_office_ids(actor)
    if visible is None:
        return rows
    return rows.filter(
        employee_id__in=EmployeeAssignment.objects.filter(
            office_id__in=visible
        ).values_list("employee_id", flat=True)
    )


def _current_assignments():
    today = timezone.localdate()
    return EmployeeAssignment.objects.filter(valid_from__lte=today).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=today)
    )


def _places(employee_ids) -> dict:
    """Офис, отдел и должность по действующему назначению — одним запросом."""
    result: dict = {}
    rows = (
        _current_assignments()
        .filter(employee_id__in=set(employee_ids))
        .select_related("office", "department", "position")
        .order_by("employee_id", "-is_primary", "-valid_from")
    )
    for row in rows:
        if row.employee_id in result:
            continue
        result[row.employee_id] = {
            "office": {"id": row.office_id, "name": row.office.name} if row.office_id else None,
            "department": row.department.name if row.department_id else None,
            "position": row.position.name if row.position_id else None,
        }
    return result


def _with_last_message(rows):
    last = QuestionMessage.objects.filter(
        question_id=OuterRef("pk"), kind__in=("EMPLOYEE", "HR")
    ).order_by("-created_at", "-id")
    return rows.annotate(
        last_body=Subquery(last.values("body")[:1]),
        last_kind=Subquery(last.values("kind")[:1]),
    )


def _with_last_message_one(question: EmployeeQuestion) -> EmployeeQuestion:
    last = (
        question.messages.filter(kind__in=("EMPLOYEE", "HR"))
        .order_by("-created_at", "-id")
        .values("body", "kind")
        .first()
    )
    question.last_body = last["body"] if last else None
    question.last_kind = last["kind"] if last else None
    return question


def _urgent(moment: datetime) -> Q:
    return Q(status__in=OPEN, priority="URGENT") | Q(
        status__in=WAITING_HR, due_at__lt=moment
    )


def _unanswered() -> Q:
    return Q(status__in=OPEN, awaiting_reply=True)


def _search(raw: str) -> Q:
    needle = raw.strip()
    condition = (
        Q(question_text__icontains=needle)
        | Q(normalized_topic__icontains=needle)
        | Q(employee__employee_number__icontains=needle)
        | Exists(
            QuestionMessage.objects.filter(
                question_id=OuterRef("pk"), body__icontains=needle
            )
        )
    )
    number = needle.lstrip("#№ ").strip()
    if number.isdigit() and len(number) < 10:
        condition |= Q(number=int(number))
    words = needle.split()
    if words:
        names = Q()
        for word in words:
            names &= (
                Q(employee__last_name__icontains=word)
                | Q(employee__first_name__icontains=word)
                | Q(employee__middle_name__icontains=word)
            )
        condition |= names
    return condition


def _telegram(employee_id) -> dict:
    """Можно ли сейчас доставить сообщение этому человеку.

    Проверка та же, что делает отправщик очереди: сообщение, которое он
    всё равно снимет как «некому», в CRM отправленным не считается.
    """
    from humotech.telegram.identity import AccessDenied, resolve_account
    from humotech.telegram.models import TelegramAccount

    account = (
        TelegramAccount.objects.select_related("employee", "organization")
        .filter(employee_id=employee_id)
        .first()
    )
    if account is None:
        return {"connected": False, "reason": "not_linked", "status": None}
    resolved = resolve_account(account)
    if isinstance(resolved, AccessDenied):
        return {"connected": False, "reason": resolved.reason, "status": account.status}
    return {"connected": True, "reason": None, "status": account.status}


DELIVERY = {
    "PENDING": "QUEUED",
    "RUNNING": "QUEUED",
    "SENT": "DELIVERED",
    "READ": "READ",
    "FAILED": "FAILED",
    "CANCELLED": "FAILED",
}


def _message(row: QuestionMessage) -> dict:
    if row.author_user_id and row.author_user is not None:
        author = {"type": "user", **_person(row.author_user)}
    elif row.author_employee_id and row.author_employee is not None:
        author = {
            "type": "employee",
            "id": row.author_employee_id,
            "name": _full_name(row.author_employee),
        }
    else:
        author = {"type": "system", "id": None, "name": "Система"}

    delivery = None
    notification = row.notification
    if notification is not None:
        state = DELIVERY.get(notification.status, "QUEUED")
        delivery = {
            "status": state,
            "sent_at": notification.sent_at,
            "read_at": notification.read_at,
            # Код причины, а не текст: текст ошибки Telegram может
            # содержать эхо сообщения.
            "error": notification.error_message if state == "FAILED" else None,
        }
    elif row.kind == "HR":
        delivery = {"status": "UNKNOWN", "sent_at": None, "read_at": None, "error": None}

    return {
        "id": row.id,
        "kind": row.kind,
        "source": row.source,
        "body": row.body,
        "event": row.event,
        "details": row.details,
        "author": author,
        "created_at": row.created_at,
        "delivery": delivery,
    }


def _draft(question: EmployeeQuestion) -> dict | None:
    from humotech.knowledge.models import KnowledgeSource

    if question.ai_status is None:
        return None
    ids = list(question.ai_source_ids or [])
    if not ids and question.answer_source_id:
        ids = [str(question.answer_source_id)]
    rows = {
        str(row.id): row
        for row in KnowledgeSource.objects.filter(
            organization_id=question.organization_id, id__in=ids
        )
    }
    sources = []
    for one in ids:
        row = rows.get(str(one))
        if row is None:
            continue
        sources.append({
            "id": row.id,
            "title": row.title,
            "source_type": row.source_type,
            "version": row.version,
            "status": row.status,
            "published_at": row.published_at,
            "updated_at": row.updated_at,
        })
    # Черновик по документу, который с тех пор сняли с публикации, уже не
    # опирается на действующее правило — говорим об этом прямо.
    outdated = any(one["status"] != "ACTIVE" for one in sources)
    return {
        "status": question.ai_status,
        "text": question.ai_answer_text,
        "confidence": question.ai_confidence,
        "generated_at": question.ai_generated_at,
        "outdated": outdated,
        "sources": sources,
    }


def _person(user) -> dict:
    name = _full_name(user.employee) if getattr(user, "employee_id", None) else None
    return {"id": user.id, "name": name or user.email}


def _person_by_id(user_id) -> dict | None:
    from humotech.accounts.models import User

    user = User.objects.select_related("employee").filter(id=user_id).first()
    return _person(user) if user else None


def _full_name(employee) -> str:
    parts = [employee.last_name, employee.first_name, employee.middle_name]
    return " ".join(part for part in parts if part)


def _snippet(text: str | None, limit: int) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(one) for key, one in value.items()}
    if isinstance(value, list):
        return [_jsonable(one) for one in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _statuses(raw: str) -> list[str]:
    return [_known(one, "status", QUESTION_STATUSES) for one in raw.split(",") if one]


def _require_open(question: EmployeeQuestion) -> None:
    if question.status not in OPEN:
        raise Conflict(
            "Обращение закрыто. Переоткройте его, чтобы менять",
            details={"status": question.status},
        )


def _known(value: str, field: str, allowed) -> str:
    if value not in allowed:
        raise ValidationFailed(
            f"Неизвестное значение параметра «{field}»",
            details={"field": field, "value": value, "allowed": list(allowed)},
        )
    return value


def _uuid(raw: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(
            f"Параметр «{field}» должен быть UUID", details={"field": field}
        ) from exc


def _encode(rank: int, last: datetime, row_id: uuid.UUID) -> str:
    payload = json.dumps({"r": rank, "t": last.isoformat(), "id": str(row_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode(raw: str) -> tuple[int, datetime, uuid.UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return (
            int(payload["r"]),
            datetime.fromisoformat(payload["t"]),
            uuid.UUID(payload["id"]),
        )
    except (binascii.Error, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailed(
            "Некорректный курсор постраничного вывода", details={"cursor": raw}
        ) from exc


__all__ = [
    "AssistantUnavailable",
    "InboxFilters",
    "InboxService",
    "TelegramUnavailable",
    "next_number",
    "prepare_draft",
]


def _reply_body(question, answer: str) -> str:
    """Ответ HR так, как его увидит человек в чате.

    Номер обращения и цитата вопроса — не украшение. Человек задал
    вопрос неделю назад, с тех пор написал ещё два и получил ответ на
    один из них: без цитаты он не поймёт, о каком именно речь, и
    переспросит — то есть заведёт кадровику ещё одну работу.

    Цитата короткая: полный вопрос человек и так помнит, а стена текста
    в чате прячет сам ответ.
    """
    asked = " ".join((question.question_text or "").split())
    head = REPLY_HEADER.format(number=question.number)
    if asked:
        short = asked if len(asked) <= 120 else asked[:117].rstrip() + "…"
        head += f"\n\nВы спрашивали: «{short}»"
    return f"{head}\n\n{answer}"
