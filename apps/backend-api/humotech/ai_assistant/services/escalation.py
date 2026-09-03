"""Передача вопроса HR, когда подтверждённого ответа нет.

Повторный такой же вопрос НЕ создаёт новую строку, а увеличивает счётчик.
Иначе HR получил бы сотни одинаковых обращений и не увидел бы, какой пробел
в базе знаний действительно массовый — а именно это и есть главный сигнал,
ради которого таблица заведена.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from humotech.ai_assistant.models import UnansweredQuestion
from humotech.ai_assistant.services.safety import sanitize_for_log
from humotech.ai_assistant.services.scoping import EmployeeScope

logger = logging.getLogger("humotech.ai.escalation")


class QuestionEscalationService:
    def escalate(
        self,
        *,
        scope: EmployeeScope,
        question_text: str,
        normalized_hash: str,
        language: str,
        best_score: float | None = None,
        reason: str | None = None,
    ) -> UnansweredQuestion:
        now = datetime.now(tz=timezone.utc)
        # в базу кладём текст, из которого вычищены секреты:
        # сотрудник мог вставить в чат токен или пароль
        safe_text = sanitize_for_log(question_text) or ""

        existing = UnansweredQuestion.objects.filter(
            organization_id=scope.organization_id,
            normalized_hash=normalized_hash,
            language=language,
            office_id=scope.office_id,
            region_id=scope.region_id,
        ).first()

        if existing is not None:
            existing.occurrences_count += 1
            existing.last_asked_at = now
            if best_score is not None:
                current = float(existing.best_retrieval_score or 0)
                if best_score > current:
                    existing.best_retrieval_score = Decimal(str(round(best_score, 5)))
            # закрытый вопрос, заданный снова, снова требует внимания
            if existing.status == "IGNORED":
                existing.status = "NEW"
            existing.save()
            logger.info(
                "повтор неотвеченного вопроса %s, всего обращений %s",
                existing.id, existing.occurrences_count,
            )
            return existing

        record = UnansweredQuestion.objects.create(
            organization_id=scope.organization_id,
            employee_id=scope.employee_id,
            office_id=scope.office_id,
            region_id=scope.region_id,
            language=language,
            question_text=safe_text,
            normalized_hash=normalized_hash,
            occurrences_count=1,
            best_retrieval_score=(
                Decimal(str(round(best_score, 5))) if best_score is not None else None
            ),
            status="NEW",
            resolution_note=reason,
            first_asked_at=now,
            last_asked_at=now,
        )
        logger.info("новый неотвеченный вопрос %s", record.id)
        return record

    # ------------------------------------------------------------- для CRM

    def assign(
        self, question_id: uuid.UUID, *, user_id: uuid.UUID
    ) -> UnansweredQuestion:
        record = self._get(question_id)
        record.assigned_to_user_id = user_id
        record.status = "IN_REVIEW"
        record.save()
        return record

    def resolve_as_answered(
        self,
        question_id: uuid.UUID,
        *,
        faq_id: uuid.UUID | None = None,
        note: str | None = None,
    ) -> UnansweredQuestion:
        record = self._get(question_id)
        record.status = "ANSWERED"
        record.resolved_faq_id = faq_id
        record.resolution_note = note
        record.save()
        return record

    def ignore(
        self, question_id: uuid.UUID, *, note: str | None = None
    ) -> UnansweredQuestion:
        record = self._get(question_id)
        record.status = "IGNORED"
        record.resolution_note = note
        record.save()
        return record

    def top_unanswered(
        self, organization_id: uuid.UUID, *, limit: int = 20, status: str | None = None
    ) -> list[UnansweredQuestion]:
        queryset = UnansweredQuestion.objects.filter(
            organization_id=organization_id
        )
        if status:
            queryset = queryset.filter(status=status)
        return list(
            queryset.order_by("-occurrences_count", "-last_asked_at")[:limit]
        )

    def _get(self, question_id: uuid.UUID) -> UnansweredQuestion:
        record = UnansweredQuestion.objects.filter(id=question_id).first()
        if record is None:
            raise ValueError(f"Неотвеченный вопрос {question_id} не найден")
        return record
