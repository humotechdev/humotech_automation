"""Метрики качества и стоимости ассистента.

В метки метрик НЕ попадают тексты вопросов и персональные данные: метки
уходят в системы мониторинга с другим сроком хранения и другим кругом
доступа, чем прикладная база.

Считается из `llm_query_logs` — отдельного хранилища не заводим, журнал
уже содержит всё нужное.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import Session

from src.modules.ai_assistant.models import AnswerFeedback, LlmQueryLog, UnansweredQuestion


@dataclass(frozen=True)
class AssistantMetrics:
    period_from: datetime
    period_to: datetime
    total_questions: int = 0
    exact_faq_rate: float = 0.0
    rag_answered_rate: float = 0.0
    escalated_rate: float = 0.0
    personal_data_rate: float = 0.0
    error_rate: float = 0.0
    cache_hit_rate: float = 0.0
    fallback_rate: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    helpful_rate: float = 0.0
    feedback_count: int = 0
    top_unanswered: tuple[tuple[str, int], ...] = field(default=())


class MetricsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def collect(
        self,
        organization_id: uuid.UUID,
        *,
        days: int = 7,
        top_unanswered_limit: int = 10,
    ) -> AssistantMetrics:
        now = datetime.now(tz=timezone.utc)
        since = now - timedelta(days=days)

        base = [
            LlmQueryLog.organization_id == organization_id,
            LlmQueryLog.created_at >= since,
        ]

        def share(status: str):
            return func.sum(case((LlmQueryLog.status == status, 1), else_=0))

        row = self.session.execute(
            select(
                func.count(LlmQueryLog.id).label("total"),
                share("EXACT_FAQ").label("exact_faq"),
                share("RAG_ANSWERED").label("rag"),
                share("ESCALATED").label("escalated"),
                share("PERSONAL_DATA").label("personal"),
                share("ERROR").label("errors"),
                func.sum(case((LlmQueryLog.cache_hit.is_(True), 1), else_=0)).label("cached"),
                func.sum(case((LlmQueryLog.fallback_used.is_(True), 1), else_=0)).label("fallback"),
                func.coalesce(func.avg(cast(LlmQueryLog.latency_ms, Float)), 0.0).label("avg_latency"),
                func.coalesce(
                    func.percentile_cont(0.95).within_group(
                        cast(LlmQueryLog.latency_ms, Float)
                    ),
                    0.0,
                ).label("p95_latency"),
                func.coalesce(func.sum(LlmQueryLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(LlmQueryLog.output_tokens), 0).label("output_tokens"),
            ).where(*base)
        ).one()

        total = int(row.total or 0)

        def rate(value) -> float:
            return round(float(value or 0) / total, 4) if total else 0.0

        feedback = self.session.execute(
            select(
                func.count(AnswerFeedback.id).label("total"),
                func.sum(
                    case((AnswerFeedback.rating == "HELPFUL", 1), else_=0)
                ).label("helpful"),
            ).where(
                AnswerFeedback.organization_id == organization_id,
                AnswerFeedback.created_at >= since,
            )
        ).one()
        feedback_total = int(feedback.total or 0)

        top = self.session.execute(
            select(
                UnansweredQuestion.question_text,
                UnansweredQuestion.occurrences_count,
            )
            .where(
                UnansweredQuestion.organization_id == organization_id,
                UnansweredQuestion.status.in_(("NEW", "IN_REVIEW")),
            )
            .order_by(UnansweredQuestion.occurrences_count.desc())
            .limit(top_unanswered_limit)
        ).all()

        return AssistantMetrics(
            period_from=since,
            period_to=now,
            total_questions=total,
            exact_faq_rate=rate(row.exact_faq),
            rag_answered_rate=rate(row.rag),
            escalated_rate=rate(row.escalated),
            personal_data_rate=rate(row.personal),
            error_rate=rate(row.errors),
            cache_hit_rate=rate(row.cached),
            fallback_rate=rate(row.fallback),
            avg_latency_ms=round(float(row.avg_latency or 0), 1),
            p95_latency_ms=round(float(row.p95_latency or 0), 1),
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
            helpful_rate=(
                round(float(feedback.helpful or 0) / feedback_total, 4)
                if feedback_total
                else 0.0
            ),
            feedback_count=feedback_total,
            # тексты вопросов нужны HR в CRM, но в метки мониторинга
            # они не уходят — только сюда, в прикладной отчёт
            top_unanswered=tuple((text, int(count)) for text, count in top),
        )


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    *,
    input_price_per_1k: float,
    output_price_per_1k: float,
) -> float:
    """Приблизительная стоимость.

    Цены не зашиты в код: тарифы меняются, и захардкоженное число молча
    начало бы врать. Значения передаёт вызывающая сторона из конфигурации.
    """
    return round(
        input_tokens / 1000 * input_price_per_1k
        + output_tokens / 1000 * output_price_per_1k,
        4,
    )
