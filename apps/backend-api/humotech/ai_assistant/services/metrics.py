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

from django.db.models import Avg, Count, FloatField, Q, Sum
from django.db.models.aggregates import Aggregate
from django.db.models.functions import Cast, Coalesce

from humotech.ai_assistant.models import (
    AnswerFeedback,
    LlmQueryLog,
    UnansweredQuestion,
)


class PercentileCont(Aggregate):
    """`percentile_cont(p) WITHIN GROUP (ORDER BY ...)`.

    Встроенного эквивалента в Django нет, а среднее вместо перцентиля здесь
    обманывает: один тяжёлый запрос из тысячи вытягивает среднее вверх,
    а p95 показывает, что чувствует почти каждый.
    """

    function = "PERCENTILE_CONT"
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()

    def __init__(self, expression, percentile: float = 0.95, **extra):
        super().__init__(expression, percentile=percentile, **extra)


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
    def collect(
        self,
        organization_id: uuid.UUID,
        *,
        days: int = 7,
        top_unanswered_limit: int = 10,
    ) -> AssistantMetrics:
        now = datetime.now(tz=timezone.utc)
        since = now - timedelta(days=days)

        logs = LlmQueryLog.objects.filter(
            organization_id=organization_id, created_at__gte=since
        )
        latency = Cast("latency_ms", FloatField())
        row = logs.aggregate(
            total=Count("id"),
            exact_faq=Count("id", filter=Q(status="EXACT_FAQ")),
            rag=Count("id", filter=Q(status="RAG_ANSWERED")),
            escalated=Count("id", filter=Q(status="ESCALATED")),
            personal=Count("id", filter=Q(status="PERSONAL_DATA")),
            errors=Count("id", filter=Q(status="ERROR")),
            cached=Count("id", filter=Q(cache_hit=True)),
            fallback=Count("id", filter=Q(fallback_used=True)),
            avg_latency=Coalesce(Avg(latency), 0.0, output_field=FloatField()),
            p95_latency=Coalesce(
                PercentileCont(latency, 0.95), 0.0, output_field=FloatField()
            ),
            input_tokens=Coalesce(Sum("input_tokens"), 0),
            output_tokens=Coalesce(Sum("output_tokens"), 0),
        )

        total = int(row["total"] or 0)

        def rate(value) -> float:
            return round(float(value or 0) / total, 4) if total else 0.0

        feedback = AnswerFeedback.objects.filter(
            organization_id=organization_id, created_at__gte=since
        ).aggregate(
            total=Count("id"),
            helpful=Count("id", filter=Q(rating="HELPFUL")),
        )
        feedback_total = int(feedback["total"] or 0)

        top = list(
            UnansweredQuestion.objects.filter(
                organization_id=organization_id,
                status__in=("NEW", "IN_REVIEW"),
            )
            .order_by("-occurrences_count")
            .values_list("question_text", "occurrences_count")[:top_unanswered_limit]
        )

        return AssistantMetrics(
            period_from=since,
            period_to=now,
            total_questions=total,
            exact_faq_rate=rate(row["exact_faq"]),
            rag_answered_rate=rate(row["rag"]),
            escalated_rate=rate(row["escalated"]),
            personal_data_rate=rate(row["personal"]),
            error_rate=rate(row["errors"]),
            cache_hit_rate=rate(row["cached"]),
            fallback_rate=rate(row["fallback"]),
            avg_latency_ms=round(float(row["avg_latency"] or 0), 1),
            p95_latency_ms=round(float(row["p95_latency"] or 0), 1),
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            helpful_rate=(
                round(float(feedback["helpful"] or 0) / feedback_total, 4)
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
