"""Вопросы: чего не знает ассистент.

Два разных понятия под одним словом, и путать их дорого.

`UnansweredQuestion` — кластер формулировок, на которые ответа не
нашлось. Он отвечает на вопрос «чего не хватает в базе знаний»: сорок
человек спросили одно и то же — это одна строка со счётчиком, а не сорок
обращений. Работа по ней — пополнить базу. Она здесь.

`EmployeeQuestion` — обращение конкретного человека, которое ждёт ответа
в чате. Его механика — очередь, переписка, отправка в Telegram — живёт
в `humotech.questions.inbox`.
"""

from __future__ import annotations

import uuid

from django.db.models import Q

from humotech.ai_assistant.models import UnansweredQuestion
from humotech.ai_assistant.use_cases.crm import KnowledgeAdminUseCases
from humotech.core.enums import UNANSWERED_QUESTION_STATUSES
from humotech.core.errors import NotFound, PermissionDenied, ValidationFailed
from humotech.core.pagination import Page, paginate
from humotech.core.rbac import Actor
from humotech.core.service import BaseService


class QuestionService(BaseService):
    """Неизвестные вопросы — пробелы в базе знаний."""

    def __init__(self) -> None:
        super().__init__()
        self.use_cases = KnowledgeAdminUseCases()
        self.use_cases.access = self.access
        self.use_cases.audit = self.audit

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
        queryset = self._scoped(actor).select_related(
            "office", "region", "assigned_to_user"
        )
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
        return self._require(actor, question_id)

    def assign_unanswered(
        self, actor: Actor, question_id: uuid.UUID, *, to_user_id: uuid.UUID
    ) -> UnansweredQuestion:
        from humotech.accounts.models import User

        # Право — до поиска пользователя: иначе 404/403 без права отвечать
        # подсказывали бы, есть ли в организации такая учётная запись.
        self.access.require(actor, "questions.answer")
        self._require(actor, question_id)
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
        self.access.require(actor, "questions.answer")
        self._require(actor, question_id)
        with self.atomic():
            self.use_cases.close_question(
                actor, question_id, as_answered=as_answered, note=note
            )
        return self.use_cases._require_question(actor, question_id)

    def create_faq_from_unanswered(
        self, actor: Actor, question_id: uuid.UUID, **payload
    ):
        self.access.require(actor, "questions.answer")
        self._require(actor, question_id)
        # Область FAQ приходит из тела запроса. Без проверки сюда можно было
        # вписать офис или регион чужой организации либо офис вне своей
        # области — и завести правило там, куда доступа нет.
        office_id = payload.get("office_id")
        region_id = payload.get("region_id")
        if office_id is not None:
            self.access.require_office(actor, office_id)
        if region_id is not None:
            self.access.require_region(actor, region_id)
            scope = self.access.scope(actor)
            if not scope.all_offices and region_id not in scope.region_ids:
                # Офис внутри региона не даёт права писать правило на
                # весь регион: оно коснулось бы и чужих офисов.
                raise PermissionDenied("Регион вне вашей области видимости")
        with self.atomic():
            return self.use_cases.create_faq_from_question(
                actor, question_id, **payload
            )

    # ------------------------------------------------------------ внутри

    def _scoped(self, actor: Actor):
        """Кластеры в области видимости актора.

        У кластера — офис и регион того, кто спросил. Кадровик офиса видит
        кластеры своих офисов, региональный — ещё и кластеры своего региона
        без офиса. Кластеры без офиса и региона — только тем, кому видна
        вся организация: спросивший не относится ни к одной области.
        """
        rows = UnansweredQuestion.objects.filter(
            organization_id=actor.organization_id
        )
        scope = self.access.scope(actor)
        if scope.all_offices:
            return rows
        return rows.filter(
            Q(office_id__in=scope.office_ids)
            | Q(office_id__isnull=True, region_id__in=scope.region_ids)
        )

    def _require(self, actor: Actor, question_id: uuid.UUID) -> UnansweredQuestion:
        question = self._scoped(actor).filter(id=question_id).first()
        if question is None:
            # Чужой офис отвечает так же, как отсутствие записи.
            raise NotFound("Вопрос не найден")
        return question


def _known(value: str, field: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ValidationFailed(
            f"Неизвестное значение параметра «{field}»",
            details={"field": field, "value": value, "allowed": list(allowed)},
        )
    return value


__all__ = ["QuestionService"]
