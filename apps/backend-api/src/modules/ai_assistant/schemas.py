"""Типизированные схемы обмена. Это контракт для Telegram-бота и будущей CRM.

Ответ описан pydantic-моделью, а не словарём: любое расхождение формы
ломается на границе модуля, а не в интерфейсе у сотрудника.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AnswerStatus(StrEnum):
    """Итог обработки вопроса. Совпадает с llm_query_logs.status."""

    EXACT_FAQ = "EXACT_FAQ"
    RAG_ANSWERED = "RAG_ANSWERED"
    ESCALATED = "ESCALATED"
    PERSONAL_DATA = "PERSONAL_DATA"
    ERROR = "ERROR"


class ScoreBand(StrEnum):
    """Огрублённая оценка качества поиска — для показа человеку.

    Точный retrieval_score остаётся для аналитики; сотруднику число
    ни о чём не говорит и создаёт ложное ощущение точности.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class SourceRef(BaseModel):
    """Ссылка на документ, из которого взят ответ."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    title: str
    version: int
    updated_at: datetime | None = None


class AnswerRequest(BaseModel):
    """Запрос от бота. Личность сотрудника определяет вызывающий слой,
    а не текст вопроса."""

    employee_id: uuid.UUID
    question: str
    language: str | None = None
    # для идемпотентности повторной отправки при плохой связи
    client_request_id: str | None = None


class AnswerResponse(BaseModel):
    request_id: uuid.UUID
    status: AnswerStatus
    answer: str
    language: str
    sources: list[SourceRef] = Field(default_factory=list)
    # балл считает приложение по результатам поиска;
    # у модели уверенность не спрашиваем принципиально
    retrieval_score: float | None = None
    score_band: ScoreBand = ScoreBand.NONE
    used_model: str | None = None
    cache_hit: bool = False
    fallback_used: bool = False
    knowledge_revision: int = 0


class FeedbackRequest(BaseModel):
    query_log_id: uuid.UUID
    employee_id: uuid.UUID
    rating: str
    comment: str | None = None


class FeedbackResponse(BaseModel):
    accepted: bool
    message: str | None = None


class HealthResponse(BaseModel):
    enabled: bool
    llm_provider: str
    embedding_provider: str
    prompt_version: str
    chat_model: str | None = None
    embedding_model: str | None = None
    database_ok: bool
    pgvector_ok: bool
    issues: list[str] = Field(default_factory=list)


# --- схема структурированного ответа модели (Structured Outputs) ---
#
# Числовой уверенности здесь намеренно нет: приложение не должно доверять
# самооценке модели. Единственная оценка качества — retrieval_score.
ANSWER_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "answered", "used_fragments"],
    "properties": {
        "answer": {
            "type": "string",
            "description": "Ответ сотруднику на языке вопроса.",
        },
        "answered": {
            "type": "boolean",
            "description": (
                "true — ответ полностью основан на CONTEXT; "
                "false — сведений в CONTEXT недостаточно."
            ),
        },
        "used_fragments": {
            "type": "array",
            "description": "Номера фрагментов CONTEXT, на которых основан ответ.",
            "items": {"type": "integer"},
        },
        "conflict_detected": {
            "type": "boolean",
            "description": "Фрагменты CONTEXT противоречат друг другу.",
        },
    },
}


# --- схемы для CRM ---

class DraftCreateRequest(BaseModel):
    organization_id: uuid.UUID
    title: str
    source_type: str
    language: str
    content: str
    office_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    priority: int = 0
    metadata: dict | None = None


class DraftUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    language: str | None = None
    source_type: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    priority: int | None = None
    metadata: dict | None = None


class IndexStatusResponse(BaseModel):
    source_id: uuid.UUID
    source_status: str
    job_status: str | None = None
    attempts: int = 0
    error_summary: str | None = None
    chunks_indexed: int = 0


class PreviewSearchRequest(BaseModel):
    organization_id: uuid.UUID
    query: str
    language: str
    office_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    limit: int = 5


class PreviewSearchHit(BaseModel):
    source_id: uuid.UUID
    title: str
    chunk_index: int
    score: float
    excerpt: str


class UnansweredQuestionView(BaseModel):
    id: uuid.UUID
    question_text: str
    language: str
    occurrences_count: int
    best_retrieval_score: float | None
    status: str
    assigned_to_user_id: uuid.UUID | None
    first_asked_at: datetime
    last_asked_at: datetime
