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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.permissions.scopes import can_see_office, permission_codes
from src.modules.ai_assistant.errors import PublishingError
from src.modules.ai_assistant.models import UnansweredQuestion
from src.modules.ai_assistant.schemas import (
    IndexStatusResponse,
    PreviewSearchHit,
    PreviewSearchRequest,
    UnansweredQuestionView,
)
from src.modules.ai_assistant.services.chunking import hash_text
from src.modules.ai_assistant.services.escalation import QuestionEscalationService
from src.modules.ai_assistant.services.personal_data_service import (
    SqlPersonalDataQueryService,
)
from src.modules.ai_assistant.services.publishing import KnowledgePublishingService
from src.modules.ai_assistant.services.retrieval import RetrievalService
from src.modules.ai_assistant.services.scoping import (
    EmployeeScope,
    resolve_employee_scope,
)
from src.modules.audit.models import AuditLog
from src.modules.knowledge_base.models import (
    FaqEntry,
    KnowledgeChunk,
    KnowledgeIndexJob,
    KnowledgeSource,
)


class PermissionDenied(Exception):
    """У пользователя нет нужного разрешения."""


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    organization_id: uuid.UUID


class KnowledgeAdminUseCases:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.publishing = KnowledgePublishingService(session)
        self.escalation = QuestionEscalationService(session)

    # ------------------------------------------------------------ права и аудит

    def _require(self, actor: Actor, permission: str) -> None:
        codes = permission_codes(self.session, user_id=actor.user_id)
        if permission not in codes:
            raise PermissionDenied(
                f"Нужно разрешение {permission}, у пользователя его нет"
            )

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
        self.session.add(
            AuditLog(
                organization_id=actor.organization_id,
                actor_user_id=actor.user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                old_values=old_values,
                new_values=new_values,
                occurred_at=datetime.now(tz=timezone.utc),
            )
        )
        self.session.flush()

    # --------------------------------------------------------------- черновики

    def create_draft(self, actor: Actor, **payload) -> KnowledgeSource:
        self._require(actor, "knowledge.write")
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
        before = self.session.get(KnowledgeSource, source_id)
        old = {"title": before.title, "status": before.status} if before else None
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
        job = self.publishing.enqueue_indexing(source_id)
        self._audit(
            actor, action="knowledge.index.start",
            entity_type="knowledge_sources", entity_id=source_id,
            new_values={"job_id": str(job.id)},
        )
        return job

    def index_status(self, actor: Actor, source_id: uuid.UUID) -> IndexStatusResponse:
        self._require(actor, "knowledge.read")
        source = self.session.get(KnowledgeSource, source_id)
        if source is None:
            raise PublishingError(f"Источник {source_id} не найден")

        job = self.session.scalars(
            select(KnowledgeIndexJob)
            .where(KnowledgeIndexJob.source_id == source_id)
            .order_by(KnowledgeIndexJob.created_at.desc())
            .limit(1)
        ).first()

        chunks = self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.source_id == source_id,
                KnowledgeChunk.embedding.is_not(None),
            )
        )
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
            self.session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.organization_id == actor.organization_id,
                    AuditLog.entity_type == "knowledge_sources",
                    AuditLog.entity_id == source_id,
                )
                .order_by(AuditLog.occurred_at.desc())
                .limit(limit)
            )
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
        result = RetrievalService(self.session).search(
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
        faq = FaqEntry(
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
        self.session.add(faq)
        self.session.flush()

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
        `core/permissions/scopes.can_see_office`, а не проверка в этом методе.
        """
        self._require(actor, "attendance.read")

        scope = resolve_employee_scope(self.session, employee_id=employee_id)
        if scope is None:
            raise PermissionDenied("Сотрудник не найден")
        if scope.organization_id != actor.organization_id:
            # чужая организация — даже не сообщаем, существует ли сотрудник
            raise PermissionDenied("Сотрудник не найден")
        if scope.office_id is not None and not can_see_office(
            self.session, user_id=actor.user_id, office_id=scope.office_id
        ):
            raise PermissionDenied("Офис сотрудника вне вашей области видимости")

        answer = SqlPersonalDataQueryService(self.session).answer(
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
