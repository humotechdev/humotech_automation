"""Сценарии для будущей HR CRM.

Каждый сценарий требует разрешения из существующей RBAC-системы: проверка
идёт через `core/permissions/scopes.permission_codes`, новых механизмов прав
не заводим. Разрешения используются те же, что уже есть в каталоге:

    knowledge.write   — создание и правка черновика
    knowledge.index   — запуск индексации
    knowledge.publish — публикация и архивация
    knowledge.read    — история версий, preview, статус
    questions.read    — список неизвестных вопросов
    questions.answer  — назначение, создание FAQ, закрытие вопроса
    ai.metrics.read   — журнал и метрики
    audit.read        — audit trail изменений

Все административные действия пишутся в `audit_logs`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from humotech.core.errors import NotFound, PermissionDenied
from humotech.core.rbac import AccessControl, Actor, AuditTrail
from humotech.ai_assistant.errors import PublishingError
from humotech.ai_assistant.models import UnansweredQuestion
from humotech.ai_assistant.schemas import (
    IndexStatusResponse,
    PreviewSearchHit,
    PreviewSearchRequest,
    UnansweredQuestionView,
)
from humotech.ai_assistant.services.chunking import hash_text
from humotech.ai_assistant.services.escalation import QuestionEscalationService
from humotech.ai_assistant.services.personal_data_service import (
    SqlPersonalDataQueryService,
)
from humotech.ai_assistant.services.publishing import KnowledgePublishingService
from humotech.ai_assistant.services.retrieval import RetrievalService
from humotech.ai_assistant.services.scoping import (
    EmployeeScope,
    resolve_employee_scope,
)
from humotech.audit.models import AuditLog
from humotech.knowledge.models import (
    FaqEntry,
    KnowledgeChunk,
    KnowledgeIndexJob,
    KnowledgeSource,
)


class KnowledgeAdminUseCases:
    def __init__(self) -> None:
        self.publishing = KnowledgePublishingService()
        self.escalation = QuestionEscalationService()
        # Тот же слой прав, что и у кадровой части: параллельной системы
        # ролей в проекте нет, и заводить её здесь было бы худшим решением
        # из возможных — права разъехались бы незаметно.
        self.access = AccessControl()
        self.audit = AuditTrail()

    # ------------------------------------------------------------ права и аудит

    def _require(self, actor: Actor, permission: str) -> None:
        self.access.require(actor, permission)

    def _require_source(
        self, actor: Actor, source_id: uuid.UUID
    ) -> KnowledgeSource:
        """Источник существует И принадлежит организации актора.

        Проверка живёт здесь, а не в `KnowledgePublishingService`: тот
        слой ниже и работает без пользователя — его зовёт ещё и воркер
        индексации, у которого организация приходит из самой строки.
        Организацию знает только этот слой, и только он может сверить.

        Без этой сверки правка, публикация и архивация чужой базы знаний
        проходили по одному идентификатору: разрешение `knowledge.write`
        отвечает «что можно делать», но не «с чьими данными».
        """
        source = KnowledgeSource.objects.filter(
            id=source_id, organization_id=actor.organization_id
        ).first()
        if source is None:
            # Чужая организация отвечает как отсутствие записи: иначе
            # перебором можно пересчитать документы соседей.
            raise NotFound("Источник знаний не найден")
        return source

    def _require_question(
        self, actor: Actor, question_id: uuid.UUID
    ) -> UnansweredQuestion:
        question = UnansweredQuestion.objects.filter(
            id=question_id, organization_id=actor.organization_id
        ).first()
        if question is None:
            raise NotFound("Вопрос не найден")
        return question

    def _audit(
        self,
        actor: Actor,
        *,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        old_values: dict | None = None,
        new_values: dict | None = None,
    ) -> None:
        self.audit.record(
            actor, action=action, entity_type=entity_type, entity_id=entity_id,
            before=old_values, after=new_values,
        )

    # --------------------------------------------------------------- черновики

    def create_draft(self, actor: Actor, **payload) -> KnowledgeSource:
        self._require(actor, "knowledge.write")
        parent_id = payload.get("parent_source_id")
        if parent_id is not None:
            # Новая версия чужого документа — тот же обход, только через
            # родителя: содержимое соседней организации попало бы в нашу.
            self._require_source(actor, parent_id)
        source = self.publishing.create_draft(
            organization_id=actor.organization_id,
            created_by_user_id=actor.user_id,
            **payload,
        )
        self._audit(
            actor, action="knowledge.draft.create",
            entity_type="knowledge_sources", entity_id=source.id,
            new_values={"title": source.title, "status": source.status},
        )
        return source

    def update_draft(
        self, actor: Actor, source_id: uuid.UUID, **fields
    ) -> KnowledgeSource:
        self._require(actor, "knowledge.write")
        before = self._require_source(actor, source_id)
        old = {"title": before.title, "status": before.status}
        source = self.publishing.update_draft(source_id, **fields)
        self._audit(
            actor, action="knowledge.draft.update",
            entity_type="knowledge_sources", entity_id=source.id,
            old_values=old, new_values={"title": source.title},
        )
        return source

    # --------------------------------------------------------------- индексация

    def start_indexing(self, actor: Actor, source_id: uuid.UUID) -> KnowledgeIndexJob:
        self._require(actor, "knowledge.index")
        self._require_source(actor, source_id)
        job = self.publishing.enqueue_indexing(source_id)
        self._audit(
            actor, action="knowledge.index.start",
            entity_type="knowledge_sources", entity_id=source_id,
            new_values={"job_id": str(job.id)},
        )
        return job

    def index_status(self, actor: Actor, source_id: uuid.UUID) -> IndexStatusResponse:
        self._require(actor, "knowledge.read")
        source = self._require_source(actor, source_id)

        job = (
            KnowledgeIndexJob.objects.filter(source_id=source_id)
            .order_by("-created_at")
            .first()
        )
        chunks = KnowledgeChunk.objects.filter(
            source_id=source_id, embedding__isnull=False
        ).count()
        return IndexStatusResponse(
            source_id=source_id,
            source_status=source.status,
            job_status=job.status if job else None,
            attempts=job.attempts if job else 0,
            error_summary=job.error_summary if job else None,
            chunks_indexed=int(chunks or 0),
        )

    # --------------------------------------------------------------- публикация

    def publish(self, actor: Actor, source_id: uuid.UUID):
        self._require(actor, "knowledge.publish")
        self._require_source(actor, source_id)
        result = self.publishing.publish(source_id, approved_by_user_id=actor.user_id)
        self._audit(
            actor, action="knowledge.publish",
            entity_type="knowledge_sources", entity_id=source_id,
            new_values={
                "version": result.version,
                "archived_source_id": str(result.archived_source_id)
                if result.archived_source_id
                else None,
                "knowledge_revision": result.knowledge_revision,
            },
        )
        return result

    def archive(self, actor: Actor, source_id: uuid.UUID) -> int:
        self._require(actor, "knowledge.publish")
        self._require_source(actor, source_id)
        revision = self.publishing.archive(source_id)
        self._audit(
            actor, action="knowledge.archive",
            entity_type="knowledge_sources", entity_id=source_id,
            new_values={"knowledge_revision": revision},
        )
        return revision

    def version_history(self, actor: Actor, title: str, language: str):
        self._require(actor, "knowledge.read")
        return self.publishing.version_history(
            actor.organization_id, title, language
        )

    def audit_trail(self, actor: Actor, source_id: uuid.UUID, *, limit: int = 50):
        self._require(actor, "audit.read")
        return list(
            AuditLog.objects.filter(
                organization_id=actor.organization_id,
                entity_type="knowledge_sources",
                entity_id=source_id,
            ).order_by("-occurred_at")[:limit]
        )

    # ------------------------------------------------------------ preview поиска

    def preview_search(
        self, actor: Actor, request: PreviewSearchRequest, *, question_vector: list[float]
    ) -> list[PreviewSearchHit]:
        """Показывает HR, что найдёт ассистент по такому вопросу.

        Область берётся из запроса (HR проверяет чужой офис), а не из
        сотрудника — это инструмент отладки базы знаний.
        """
        self._require(actor, "knowledge.read")
        scope = EmployeeScope(
            employee_id=uuid.uuid4(),
            organization_id=actor.organization_id,
            office_id=request.office_id,
            region_id=request.region_id,
            department_id=None,
            language=request.language,
            employment_status="ACTIVE",
        )
        result = RetrievalService().search(
            scope=scope,
            question=request.query,
            question_vector=question_vector,
            language=request.language,
        )
        return [
            PreviewSearchHit(
                source_id=chunk.source_id,
                title=chunk.source_title,
                chunk_index=chunk.chunk_index,
                score=round(chunk.score, 4),
                excerpt=chunk.text[:300],
            )
            for chunk in result.chunks[: request.limit]
        ]

    # ------------------------------------------------------- неизвестные вопросы

    def list_unanswered(
        self, actor: Actor, *, status: str | None = None, limit: int = 20
    ) -> list[UnansweredQuestionView]:
        self._require(actor, "questions.read")
        records = self.escalation.top_unanswered(
            actor.organization_id, limit=limit, status=status
        )
        return [
            UnansweredQuestionView(
                id=r.id,
                question_text=r.question_text,
                language=r.language,
                occurrences_count=r.occurrences_count,
                best_retrieval_score=(
                    float(r.best_retrieval_score)
                    if r.best_retrieval_score is not None
                    else None
                ),
                status=r.status,
                assigned_to_user_id=r.assigned_to_user_id,
                first_asked_at=r.first_asked_at,
                last_asked_at=r.last_asked_at,
            )
            for r in records
        ]

    def assign_question(
        self, actor: Actor, question_id: uuid.UUID, *, to_user_id: uuid.UUID
    ) -> UnansweredQuestion:
        self._require(actor, "questions.answer")
        self._require_question(actor, question_id)
        record = self.escalation.assign(question_id, user_id=to_user_id)
        self._audit(
            actor, action="ai.question.assign",
            entity_type="unanswered_questions", entity_id=question_id,
            new_values={"assigned_to": str(to_user_id)},
        )
        return record

    def create_faq_from_question(
        self,
        actor: Actor,
        question_id: uuid.UUID,
        *,
        canonical_question: str,
        approved_answer: str,
        language: str,
        source_id: uuid.UUID | None = None,
        office_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        priority: int = 0,
    ) -> FaqEntry:
        """Превращает неизвестный вопрос в утверждённый FAQ.

        Эмбеддинг вопроса здесь НЕ считается: его посчитает воркер индексации.
        До этого запись остаётся в статусе DRAFT и в поиск не попадает.
        """
        self._require(actor, "questions.answer")
        self._require_question(actor, question_id)
        if source_id is not None:
            self._require_source(actor, source_id)
        faq = FaqEntry.objects.create(
            organization_id=actor.organization_id,
            canonical_question=canonical_question,
            approved_answer=approved_answer,
            source_id=source_id,
            language=language,
            office_id=office_id,
            region_id=region_id,
            status="DRAFT",
            priority=priority,
            created_by_user_id=actor.user_id,
            content_hash=hash_text(canonical_question + approved_answer),
        )

        self.escalation.resolve_as_answered(
            question_id, faq_id=faq.id, note="Создан FAQ"
        )
        self._audit(
            actor, action="ai.faq.create",
            entity_type="faq_entries", entity_id=faq.id,
            new_values={"from_question": str(question_id)},
        )
        return faq

    # ------------------------------------------------- личные данные сотрудника

    def employee_personal_data(
        self, actor: Actor, employee_id: uuid.UUID, *, question: str,
        language: str = "ru",
    ):
        """Личные данные сотрудника глазами HR.

        Доступ идёт через СУЩЕСТВУЮЩУЮ модель прав: нужно `attendance.read`,
        и офис сотрудника должен попадать в область видимости пользователя.
        Региональный HR не увидит чужой регион — за это отвечает
        `humotech/core/rbac.AccessControl`, а не проверка в этом методе.
        """
        self._require(actor, "attendance.read")

        scope = resolve_employee_scope(employee_id=employee_id)
        if scope is None:
            raise PermissionDenied("Сотрудник не найден")
        if scope.organization_id != actor.organization_id:
            # чужая организация — даже не сообщаем, существует ли сотрудник
            raise PermissionDenied("Сотрудник не найден")
        visible = self.access.visible_office_ids(actor)
        if (
            scope.office_id is not None
            and visible is not None
            and scope.office_id not in visible
        ):
            raise PermissionDenied("Офис сотрудника вне вашей области видимости")

        answer = SqlPersonalDataQueryService().answer(
            employee_id=employee_id, question=question, language=language
        )
        self._audit(
            actor, action="ai.personal_data.read",
            entity_type="employees", entity_id=employee_id,
            new_values={"intent": answer.intent.value if answer.intent else None},
        )
        return answer

    def close_question(
        self,
        actor: Actor,
        question_id: uuid.UUID,
        *,
        as_answered: bool,
        note: str | None = None,
    ) -> UnansweredQuestion:
        self._require(actor, "questions.answer")
        self._require_question(actor, question_id)
        record = (
            self.escalation.resolve_as_answered(question_id, note=note)
            if as_answered
            else self.escalation.ignore(question_id, note=note)
        )
        self._audit(
            actor, action="ai.question.close",
            entity_type="unanswered_questions", entity_id=question_id,
            new_values={"status": record.status},
        )
        return record
