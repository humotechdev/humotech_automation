"""Вопросы: чего не знает ассистент и кто ждёт ответа лично.

Две разные вещи под одним словом, и путать их дорого.

`UnansweredQuestion` — кластер формулировок, на которые ответа не
нашлось. Он отвечает на вопрос «чего не хватает в базе знаний»: сорок
человек спросили одно и то же — это одна строка со счётчиком, а не сорок
обращений. Работа по ней — пополнить базу.

`EmployeeQuestion` — обращение конкретного человека, переданное кадровику.
Он отвечает на вопрос «кто сидит и ждёт». Работа по нему — ответить
этому человеку, и ответ обязан до него дойти: вопрос, отвеченный в
интерфейсе и не ушедший в чат, для сотрудника не отвечен вовсе.

Поэтому ответ на эскалацию ставит уведомление в ту же очередь, что и
остальные, и той же транзакцией — не отдельной. Откатился ответ,
откатилось и сообщение о нём.
"""

from __future__ import annotations

import uuid

from django.db.models import Count, Q
from django.utils import timezone

from humotech.ai_assistant.models import UnansweredQuestion
from humotech.ai_assistant.use_cases.crm import KnowledgeAdminUseCases
from humotech.core.enums import QUESTION_STATUSES, UNANSWERED_QUESTION_STATUSES
from humotech.core.errors import Conflict, NotFound, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor, snapshot
from humotech.core.service import BaseService
from humotech.core.validation import clean_text
from humotech.employees.models import EmployeeAssignment
from humotech.employees.selectors import require_visible_employee
from humotech.notifications import outbox
from humotech.questions.models import EmployeeQuestion

QUESTION_FIELDS = ("status", "assigned_to_user_id", "answered_at")

#: Из каких состояний вопрос можно взять в работу и ответить.
#:
#: CLOSED и HR_ANSWERED отсутствуют: ответить второй раз значит прислать
#: человеку второе сообщение по закрытому вопросу.
ANSWERABLE = frozenset({"NEW", "AI_ANSWERED", "ESCALATED_TO_HR"})


class QuestionService(BaseService):
    """Неизвестные вопросы и эскалации кадровику."""

    def __init__(self) -> None:
        super().__init__()
        self.use_cases = KnowledgeAdminUseCases()
        self.use_cases.access = self.access
        self.use_cases.audit = self.audit

    # ------------------------------------------------- чего не знает ассистент

    def list_unanswered(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        language: str | None = None,
        search: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "questions.read")
        queryset = UnansweredQuestion.objects.filter(
            organization_id=actor.organization_id
        ).select_related("office", "region", "assigned_to_user")
        if status is not None:
            queryset = queryset.filter(
                status=_known(status, "status", UNANSWERED_QUESTION_STATUSES)
            )
        if language:
            queryset = queryset.filter(language=language.strip())
        if search:
            queryset = queryset.filter(question_text__icontains=search.strip())
        return paginate(queryset, limit=limit, cursor=cursor)

    def get_unanswered(
        self, actor: Actor, question_id: uuid.UUID
    ) -> UnansweredQuestion:
        self.access.require(actor, "questions.read")
        return self.use_cases._require_question(actor, question_id)

    def assign_unanswered(
        self, actor: Actor, question_id: uuid.UUID, *, to_user_id: uuid.UUID
    ) -> UnansweredQuestion:
        from humotech.accounts.models import User

        if not User.objects.filter(
            id=to_user_id, organization_id=actor.organization_id
        ).exists():
            # Назначить на пользователя другой организации нельзя: он не
            # увидит ни вопроса, ни базы знаний, и вопрос молча зависнет.
            raise NotFound("Учётная запись не найдена")
        with self.atomic():
            self.use_cases.assign_question(
                actor, question_id, to_user_id=to_user_id
            )
        return self.use_cases._require_question(actor, question_id)

    def close_unanswered(
        self,
        actor: Actor,
        question_id: uuid.UUID,
        *,
        as_answered: bool,
        note: str | None = None,
    ) -> UnansweredQuestion:
        with self.atomic():
            self.use_cases.close_question(
                actor, question_id, as_answered=as_answered, note=note
            )
        return self.use_cases._require_question(actor, question_id)

    def create_faq_from_unanswered(
        self, actor: Actor, question_id: uuid.UUID, **payload
    ):
        with self.atomic():
            return self.use_cases.create_faq_from_question(
                actor, question_id, **payload
            )

    # ----------------------------------------------------- кто ждёт ответа

    def list_escalations(
        self,
        actor: Actor,
        *,
        status: str | None = None,
        employee_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        assigned_to_me: bool = False,
        search: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page:
        self.access.require(actor, "questions.read")
        queryset = EmployeeQuestion.objects.filter(
            organization_id=actor.organization_id
        ).select_related("employee", "assigned_to_user", "answer_source")

        if employee_id is not None:
            require_visible_employee(self.access, actor, employee_id)
            queryset = queryset.filter(employee_id=employee_id)
        else:
            queryset = self._limit_to_scope(actor, queryset)

        if status is not None:
            queryset = queryset.filter(
                status__in=[
                    _known(one, "status", QUESTION_STATUSES)
                    for one in status.split(",")
                    if one
                ]
            )
        else:
            # Умолчание — именно ожидающие. Список «всех вопросов за год»
            # не отвечает ни на один вопрос кадровика.
            queryset = queryset.filter(status="ESCALATED_TO_HR")
        if assigned_to_me:
            queryset = queryset.filter(assigned_to_user_id=actor.user_id)
        if office_id is not None:
            queryset = self._limit_to_office(actor, queryset, office_id)
        if search:
            needle = search.strip()
            queryset = queryset.filter(
                Q(question_text__icontains=needle)
                | Q(normalized_topic__icontains=needle)
            )
        return paginate(queryset, limit=limit, cursor=cursor)

    def escalation_counts(
        self,
        actor: Actor,
        *,
        office_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> dict[str, int]:
        """Сколько обращений в каждом состоянии при текущих фильтрах.

        Нужно вкладкам списка. Состояние в счёт не входит намеренно:
        иначе, выбрав «Новые», кадровик видел бы нули у остальных
        вкладок и решил, что работы больше нет.
        """
        self.access.require(actor, "questions.read")
        queryset = self._limit_to_scope(
            actor,
            EmployeeQuestion.objects.filter(organization_id=actor.organization_id),
        )
        if office_id is not None:
            queryset = self._limit_to_office(actor, queryset, office_id)
        if search:
            needle = search.strip()
            queryset = queryset.filter(
                Q(question_text__icontains=needle)
                | Q(normalized_topic__icontains=needle)
            )
        rows = queryset.values("status").annotate(n=Count("id"))
        by_status = {row["status"]: row["n"] for row in rows}
        return {"total": sum(by_status.values()), **by_status}

    def _limit_to_office(self, actor: Actor, queryset, office_id: uuid.UUID):
        """Обращения сотрудников одного офиса.

        Право на офис проверяется до фильтра: чужой офис не должен давать
        ни строк, ни подсказок о том, что он существует.
        """
        self.access.require_office(actor, office_id)
        return queryset.filter(
            employee_id__in=EmployeeAssignment.objects.filter(
                office_id=office_id
            ).values_list("employee_id", flat=True)
        )

    def get_escalation(
        self, actor: Actor, question_id: uuid.UUID
    ) -> EmployeeQuestion:
        self.access.require(actor, "questions.read")
        return self._require_escalation(actor, question_id)

    def assign_escalation(
        self, actor: Actor, question_id: uuid.UUID, *, to_user_id: uuid.UUID
    ) -> EmployeeQuestion:
        from humotech.accounts.models import User

        self.access.require(actor, "questions.answer")
        if not User.objects.filter(
            id=to_user_id, organization_id=actor.organization_id
        ).exists():
            raise NotFound("Учётная запись не найдена")

        question = self._require_escalation(actor, question_id)
        before = snapshot(question, QUESTION_FIELDS)
        with self.atomic():
            question.assigned_to_user_id = to_user_id
            question.save(update_fields=["assigned_to_user", "updated_at"])
            self.audit.record(
                actor,
                action="question.escalation.assign",
                entity_type="employee_questions",
                entity_id=question.id,
                before=before,
                after=snapshot(question, QUESTION_FIELDS),
            )
        return question

    def answer_escalation(
        self, actor: Actor, question_id: uuid.UUID, *, answer: str
    ) -> EmployeeQuestion:
        """Ответить человеку и отправить ответ ему в чат.

        Уведомление ставится в очередь ТОЙ ЖЕ транзакцией: ответ,
        сохранившийся без сообщения, сотрудник не увидит вовсе, а
        сообщение без ответа сослалось бы на пустоту.

        Ключ идемпотентности — по идентификатору вопроса: повторное
        нажатие не даёт второго сообщения.
        """
        self.access.require(actor, "questions.answer")
        text = clean_text(answer, field="answer", required=True)
        question = self._require_escalation(actor, question_id)

        if question.status not in ANSWERABLE:
            raise Conflict(
                "На этот вопрос уже ответили",
                details={"status": question.status,
                         "answerable_from": sorted(ANSWERABLE)},
            )

        before = snapshot(question, QUESTION_FIELDS)
        with self.atomic():
            question.hr_answer_text = text
            question.status = "HR_ANSWERED"
            question.answered_at = timezone.now()
            if question.assigned_to_user_id is None:
                # Ответил — значит взял. Иначе в списке «мои» его нет,
                # а разбирался с ним именно этот человек.
                question.assigned_to_user_id = actor.user_id
            question.save(
                update_fields=[
                    "hr_answer_text", "status", "answered_at",
                    "assigned_to_user", "updated_at",
                ]
            )
            outbox.enqueue(
                organization_id=actor.organization_id,
                employee_id=question.employee_id,
                notification_type="question.answered",
                title="Ответ на ваш вопрос",
                body=text,
                idempotency_key=f"question.answered:{question.id}",
                related_entity_type="employee_questions",
                related_entity_id=question.id,
            )
            self.audit.record(
                actor,
                action="question.escalation.answer",
                entity_type="employee_questions",
                entity_id=question.id,
                before=before,
                # Текста ответа в журнале нет намеренно: он уже лежит
                # в самой строке вопроса, а копия в журнале — это второе
                # место, из которого его придётся вычищать.
                after=snapshot(question, QUESTION_FIELDS),
            )
        return question

    def close_escalation(
        self, actor: Actor, question_id: uuid.UUID
    ) -> EmployeeQuestion:
        """Снять вопрос без ответа: спросили не туда или уже неактуально."""
        self.access.require(actor, "questions.answer")
        question = self._require_escalation(actor, question_id)
        if question.status == "CLOSED":
            return question

        before = snapshot(question, QUESTION_FIELDS)
        with self.atomic():
            question.status = "CLOSED"
            question.save(update_fields=["status", "updated_at"])
            self.audit.record(
                actor,
                action="question.escalation.close",
                entity_type="employee_questions",
                entity_id=question.id,
                before=before,
                after=snapshot(question, QUESTION_FIELDS),
            )
        return question

    # ------------------------------------------------------------------ внутри

    def _limit_to_scope(self, actor: Actor, queryset):
        visible = self.access.visible_office_ids(actor)
        if visible is None:
            return queryset
        return queryset.filter(
            employee_id__in=EmployeeAssignment.objects.filter(
                office_id__in=visible
            ).values_list("employee_id", flat=True)
        )

    def _require_escalation(
        self, actor: Actor, question_id: uuid.UUID
    ) -> EmployeeQuestion:
        question = (
            EmployeeQuestion.objects.select_related(
                "employee", "assigned_to_user", "answer_source"
            )
            .filter(id=question_id, organization_id=actor.organization_id)
            .first()
        )
        if question is None:
            raise NotFound("Вопрос не найден")
        require_visible_employee(self.access, actor, question.employee_id)
        return question


def _known(value: str, field: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ValidationFailed(
            f"Неизвестное значение параметра «{field}»",
            details={"field": field, "value": value, "allowed": list(allowed)},
        )
    return value


__all__ = ["QuestionService"]
