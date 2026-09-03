"""Сценарии для Telegram-бота: ответ, оценка, состояние.

HTTP-слоя в проекте пока нет (`src/api/routes` пуст), поэтому это use cases,
а не роуты. Когда появится фреймворк, обвязка сведётся к трём тонким
обработчикам — бизнес-логику переписывать не придётся:

    POST /ai/answer   -> AnswerUseCase.execute
    POST /ai/feedback -> FeedbackUseCase.execute
    GET  /ai/health   -> HealthUseCase.execute
"""

from __future__ import annotations

import logging
import uuid

from django.db import connection

from humotech.ai_assistant.config import AiSettings, ai_settings
from humotech.ai_assistant.errors import ConfigurationError
from humotech.ai_assistant.models import AnswerFeedback, LlmQueryLog
from humotech.ai_assistant.prompts import get_prompt
from humotech.ai_assistant.schemas import (
    AnswerRequest,
    AnswerResponse,
    AnswerStatus,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
)
from humotech.ai_assistant.services.answer import AnswerService
from humotech.ai_assistant.services.safety import sanitize_for_log

logger = logging.getLogger("humotech.ai.usecases.bot")


class AnswerUseCase:
    """Точка входа бота.

    `service is None` — штатное состояние выключенного модуля: провайдеры
    не созданы. Сотрудник получает понятный ответ, а не ошибку 500.
    """

    def __init__(
        self, service: AnswerService | None, settings: AiSettings | None = None
    ) -> None:
        self.service = service
        self.settings = settings or ai_settings

    @property
    def enabled(self) -> bool:
        return self.service is not None

    def execute(self, request: AnswerRequest) -> AnswerResponse:
        if self.service is None:
            return AnswerResponse(
                request_id=uuid.uuid4(),
                status=AnswerStatus.ERROR,
                answer=get_prompt(self.settings.ai_prompt_version).unavailable_text,
                language=self.settings.ai_default_language,
            )
        return self.service.answer(request)


class FeedbackUseCase:
    """Оценка ответа сотрудником. Один сотрудник — одна оценка на ответ."""

    def execute(self, request: FeedbackRequest) -> FeedbackResponse:
        if request.rating not in ("HELPFUL", "NOT_HELPFUL"):
            return FeedbackResponse(
                accepted=False, message="Допустимы только HELPFUL и NOT_HELPFUL"
            )

        log = LlmQueryLog.objects.filter(id=request.query_log_id).first()
        if log is None:
            return FeedbackResponse(accepted=False, message="Ответ не найден")

        # оценивать можно только собственный ответ
        if log.employee_id is not None and log.employee_id != request.employee_id:
            return FeedbackResponse(
                accepted=False, message="Это ответ другого сотрудника"
            )

        existing = AnswerFeedback.objects.filter(
            query_log_id=request.query_log_id, employee_id=request.employee_id
        ).first()
        if existing is not None:
            existing.rating = request.rating
            existing.comment = sanitize_for_log(request.comment)
            existing.save()
            return FeedbackResponse(accepted=True, message="Оценка обновлена")

        AnswerFeedback.objects.create(
            organization_id=log.organization_id,
            query_log_id=request.query_log_id,
            employee_id=request.employee_id,
            rating=request.rating,
            comment=sanitize_for_log(request.comment),
        )
        return FeedbackResponse(accepted=True)


class HealthUseCase:
    """Состояние модуля. Сетевых вызовов к провайдеру не делает: health
    не должен стоить денег и не должен зависеть от чужой доступности."""

    def __init__(self, settings: AiSettings | None = None) -> None:
        self.settings = settings or ai_settings

    def execute(self) -> HealthResponse:
        issues: list[str] = []

        database_ok = True
        pgvector_ok = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.execute(
                    "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
                )
                pgvector_ok = cursor.fetchone() is not None
            if not pgvector_ok:
                issues.append("расширение pgvector не установлено")
        except Exception as exc:  # noqa: BLE001
            database_ok = False
            issues.append(f"база недоступна: {type(exc).__name__}")

        if not self.settings.ai_assistant_enabled:
            issues.append("AI_ASSISTANT_ENABLED=false — модуль выключен")
        if not self.settings.has_credentials:
            issues.append("OPENAI_API_KEY не задан")
        for field in ("openai_chat_model", "openai_embedding_model"):
            if not getattr(self.settings, field).strip():
                issues.append(f"{field.upper()} не задан")

        try:
            prompt_version = get_prompt(self.settings.ai_prompt_version).version
        except ConfigurationError as exc:
            prompt_version = self.settings.ai_prompt_version
            issues.append(str(exc))

        return HealthResponse(
            enabled=self.settings.ai_assistant_enabled,
            llm_provider="openai" if self.settings.has_credentials else "none",
            embedding_provider="openai" if self.settings.has_credentials else "none",
            prompt_version=prompt_version,
            chat_model=self.settings.openai_chat_model or None,
            embedding_model=self.settings.openai_embedding_model or None,
            database_ok=database_ok,
            pgvector_ok=pgvector_ok,
            issues=issues,
        )


__all__ = [
    "AnswerUseCase",
    "FeedbackUseCase",
    "HealthUseCase",
    "AnswerRequest",
    "AnswerResponse",
    "AnswerStatus",
    "FeedbackRequest",
    "FeedbackResponse",
    "HealthResponse",
]
