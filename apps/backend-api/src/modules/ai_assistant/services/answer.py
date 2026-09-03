"""Основной конвейер ответа сотруднику.

Порядок шагов зафиксирован и важен: каждый следующий дороже предыдущего.
Личный вопрос отсекается до поиска, точный FAQ — до эмбеддинга, эмбеддинг —
до обращения к модели. Самый дорогой путь проходят только те вопросы,
которые иначе не решаются.

    1. область сотрудника (офис, регион, язык) и лимит частоты
    2. длина и безопасность вопроса
    3. личный вопрос -> PersonalDataQueryService, НЕ в RAG
    4. кэш (ключ включает ревизию базы знаний)
    5. эмбеддинг вопроса
    6. точное совпадение в утверждённом FAQ -> ответ БЕЗ обращения к модели
    7. поиск разрешённых ACTIVE-фрагментов
    8. пусто -> вопрос уходит HR
    9. конфликт правил одного уровня -> вопрос уходит HR
   10. вызов основной модели, при неоднозначности один вызов резервной
   11. журнал: токены, задержка, результат
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from src.modules.ai_assistant.config import AiSettings, ai_settings
from src.modules.ai_assistant.errors import (
    AiError,
    QuestionRejectedError,
    RateLimitedError,
)
from src.modules.ai_assistant.models import LlmQueryLog
from src.modules.ai_assistant.prompts import PromptBundle, get_prompt
from src.modules.ai_assistant.providers.base import EmbeddingProvider, LLMProvider
from src.modules.ai_assistant.schemas import (
    ANSWER_JSON_SCHEMA,
    AnswerRequest,
    AnswerResponse,
    AnswerStatus,
    ScoreBand,
    SourceRef,
)
from src.modules.ai_assistant.services.cache import CacheKeyParts, CacheService
from src.modules.ai_assistant.services.escalation import QuestionEscalationService
from src.modules.ai_assistant.services.personal_data import (
    PersonalDataQueryRouter,
    PersonalDataQueryService,
)
from src.modules.ai_assistant.services.publishing import current_revision
from src.modules.ai_assistant.services.rate_limit import RateLimiter
from src.modules.ai_assistant.services.retrieval import (
    RetrievalResult,
    RetrievalService,
)
from src.modules.ai_assistant.services.safety import check_question, sanitize_for_log
from src.modules.ai_assistant.services.scoping import (
    EmployeeScope,
    resolve_employee_scope,
)

logger = logging.getLogger("humotech.ai.answer")


def score_band(score: float | None) -> ScoreBand:
    if score is None or score <= 0:
        return ScoreBand.NONE
    if score >= 0.85:
        return ScoreBand.HIGH
    if score >= 0.75:
        return ScoreBand.MEDIUM
    return ScoreBand.LOW


@dataclass
class _Attempt:
    """Состояние обработки одного вопроса — чтобы журнал заполнялся в одном месте."""

    request_id: uuid.UUID
    started_at: float
    status: AnswerStatus = AnswerStatus.ERROR
    answer: str = ""
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    retrieval_score: float | None = None
    sources: tuple[SourceRef, ...] = ()
    cache_hit: bool = False
    fallback_used: bool = False
    error_code: str | None = None


class AnswerService:
    def __init__(
        self,
        session: Session,
        *,
        llm: LLMProvider,
        embeddings: EmbeddingProvider,
        cache: CacheService,
        personal_data_service: PersonalDataQueryService,
        settings: AiSettings | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.embeddings = embeddings
        self.cache = cache
        self.personal_data = personal_data_service
        self.settings = settings or ai_settings
        self.prompts: PromptBundle = get_prompt(self.settings.ai_prompt_version)
        self.router = PersonalDataQueryRouter()
        self.retrieval = RetrievalService(session, self.settings)
        self.escalation = QuestionEscalationService(session)
        self.rate_limiter = rate_limiter or RateLimiter(
            per_minute=self.settings.ai_rate_limit_per_minute,
            per_day=self.settings.ai_rate_limit_per_day,
        )

    # ------------------------------------------------------------------ вход

    def answer(self, request: AnswerRequest) -> AnswerResponse:
        attempt = _Attempt(request_id=uuid.uuid4(), started_at=time.monotonic())

        scope = resolve_employee_scope(
            self.session, employee_id=request.employee_id
        )
        if scope is None:
            attempt.error_code = "employee_not_found"
            return self._finish(attempt, scope=None, language="ru", question=None)

        language = self._resolve_language(request.language or scope.language)

        try:
            self.rate_limiter.enforce(scope.employee_id)
            check = check_question(
                request.question, max_length=self.settings.ai_query_max_length
            )
        except (RateLimitedError, QuestionRejectedError) as exc:
            attempt.error_code = exc.code
            attempt.answer = self.prompts.unavailable_text
            return self._finish(
                attempt, scope=scope, language=language, question=request.question
            )

        if check.injection_suspected:
            # Не блокируем: попытка перехвата — не повод отказать сотруднику.
            # Защита обеспечивается разделением ролей и правилом в промпте,
            # а здесь мы фиксируем факт для расследования.
            logger.warning(
                "подозрение на prompt injection, сотрудник %s, шаблоны: %s",
                scope.employee_id, check.injection_markers,
            )

        # --- личный вопрос: в RAG не попадает никогда ---
        routing = self.router.classify(check.text)
        if routing.is_personal:
            personal = self.personal_data.answer(
                employee_id=scope.employee_id,
                question=check.text,
                language=language,
            )
            attempt.status = AnswerStatus.PERSONAL_DATA
            attempt.answer = (
                personal.text if personal.available else self.prompts.personal_data_text
            )
            # персональные ответы не кэшируются: данные меняются в течение дня
            return self._finish(
                attempt, scope=scope, language=language, question=check.redacted
            )

        revision = current_revision(self.session, scope.organization_id)

        # --- кэш ---
        cache_key = CacheKeyParts(
            normalized_question_hash=check.normalized_hash,
            language=language,
            office_id=str(scope.office_id or "-"),
            region_id=str(scope.region_id or "-"),
            scope_fingerprint=scope.cache_fingerprint(),
            knowledge_revision=revision,
            prompt_version=self.prompts.version,
            model=self.settings.openai_chat_model,
        ).build()

        cached = self.cache.get(cache_key)
        if cached is not None:
            attempt.cache_hit = True
            attempt.status = AnswerStatus(cached["status"])
            attempt.answer = cached["answer"]
            attempt.model = cached.get("model")
            attempt.retrieval_score = cached.get("retrieval_score")
            attempt.sources = tuple(
                SourceRef(**item) for item in cached.get("sources", [])
            )
            return self._finish(
                attempt, scope=scope, language=language,
                question=check.redacted, revision=revision,
            )

        # --- эмбеддинг вопроса ---
        try:
            embedded = self.embeddings.embed(
                [check.text], model=self.settings.openai_embedding_model
            )
            question_vector = embedded.vectors[0]
        except AiError as exc:
            return self._unavailable(attempt, scope, language, check, exc)

        # --- точный FAQ: ответ без обращения к модели ---
        faq = self.retrieval.find_exact_faq(
            scope=scope, question_vector=question_vector, language=language
        )
        if faq is not None:
            attempt.status = AnswerStatus.EXACT_FAQ
            attempt.answer = faq.answer
            attempt.retrieval_score = faq.score
            attempt.model = None  # модель не вызывалась
            self._store_cache(cache_key, attempt)
            return self._finish(
                attempt, scope=scope, language=language,
                question=check.redacted, revision=revision,
            )

        # --- поиск по базе знаний ---
        result = self.retrieval.search(
            scope=scope,
            question=check.text,
            question_vector=question_vector,
            language=language,
        )
        attempt.retrieval_score = result.top_score or None

        if result.is_empty:
            self.escalation.escalate(
                scope=scope,
                question_text=check.redacted,
                normalized_hash=check.normalized_hash,
                language=language,
                best_score=result.top_score or None,
                reason="Нет подтверждённых источников",
            )
            attempt.status = AnswerStatus.ESCALATED
            attempt.answer = self.prompts.no_answer_text
            return self._finish(
                attempt, scope=scope, language=language,
                question=check.redacted, revision=revision,
            )

        if result.conflict:
            # Разные правила одного уровня с равным приоритетом.
            # Выбирать между ними должен HR — модель к этому не подпускаем.
            self.escalation.escalate(
                scope=scope,
                question_text=check.redacted,
                normalized_hash=check.normalized_hash,
                language=language,
                best_score=result.top_score,
                reason=(
                    "Конфликт источников одного уровня: "
                    + ", ".join(str(sid) for sid in result.conflicting_source_ids)
                ),
            )
            attempt.status = AnswerStatus.ESCALATED
            attempt.answer = self.prompts.conflict_text
            attempt.sources = self._sources_of(result)
            return self._finish(
                attempt, scope=scope, language=language,
                question=check.redacted, revision=revision,
            )

        attempt.sources = self._sources_of(result)
        return self._ask_model(
            attempt, scope, language, check, result, cache_key, revision
        )

    # --------------------------------------------------------------- модель

    def _ask_model(
        self, attempt, scope, language, check, result, cache_key, revision
    ) -> AnswerResponse:
        user_content = self._build_user_content(check.text, result)

        try:
            parsed, raw = self._call_model(
                model=self.settings.openai_chat_model, user_content=user_content
            )
            attempt.model = raw.model
            attempt.input_tokens += raw.input_tokens
            attempt.output_tokens += raw.output_tokens
        except AiError as exc:
            return self._unavailable(attempt, scope, language, check, exc, revision)

        needs_fallback = not parsed.get("answered", True) or parsed.get(
            "conflict_detected", False
        )

        # Резервная модель вызывается САМОЕ БОЛЬШЕЕ один раз: без этого
        # ограничения неудачный вопрос мог бы обойтись в цепочку вызовов.
        if needs_fallback and self.settings.ai_fallback_enabled and not attempt.fallback_used:
            fallback_model = self.settings.openai_fallback_model
            if fallback_model.strip():
                attempt.fallback_used = True
                try:
                    parsed, raw = self._call_model(
                        model=fallback_model, user_content=user_content
                    )
                    attempt.model = raw.model
                    attempt.input_tokens += raw.input_tokens
                    attempt.output_tokens += raw.output_tokens
                except AiError as exc:
                    return self._unavailable(
                        attempt, scope, language, check, exc, revision
                    )

        if not parsed.get("answered", True):
            self.escalation.escalate(
                scope=scope,
                question_text=check.redacted,
                normalized_hash=check.normalized_hash,
                language=language,
                best_score=result.top_score,
                reason="Модель не нашла ответа в переданном контексте",
            )
            attempt.status = AnswerStatus.ESCALATED
            attempt.answer = self.prompts.no_answer_text
            return self._finish(
                attempt, scope=scope, language=language,
                question=check.redacted, revision=revision,
            )

        attempt.status = AnswerStatus.RAG_ANSWERED
        attempt.answer = parsed.get("answer") or self.prompts.no_answer_text
        self._store_cache(cache_key, attempt)
        return self._finish(
            attempt, scope=scope, language=language,
            question=check.redacted, revision=revision,
        )

    def _call_model(self, *, model: str, user_content: str):
        raw = self.llm.complete(
            system_prompt=self.prompts.system_prompt,
            user_content=user_content,
            model=model,
            response_schema=ANSWER_JSON_SCHEMA,
        )
        return self._parse(raw.text), raw

    @staticmethod
    def _parse(text: str) -> dict:
        """Разбор структурированного ответа с мягкой деградацией."""
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "answer" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        # модель вернула обычный текст — считаем его ответом
        return {"answer": text or "", "answered": bool(text), "used_fragments": []}

    def _build_user_content(self, question: str, result: RetrievalResult) -> str:
        """Документы попадают в user-часть, инструкции остаются в system.

        Это и есть основная защита от prompt injection: содержимое документов
        физически не может оказаться в роли системной инструкции.
        """
        blocks = [
            self.prompts.context_item_template.format(
                index=index + 1,
                title=chunk.source_title,
                version=chunk.source_version,
                text=chunk.text,
            )
            for index, chunk in enumerate(result.chunks)
        ]
        return self.prompts.user_template.format(
            question=question, context="\n".join(blocks)
        )

    # ---------------------------------------------------------------- служебное

    def _unavailable(
        self, attempt, scope, language, check, exc, revision: int = 0
    ) -> AnswerResponse:
        """Провайдер недоступен: сотрудник получает понятный ответ, HR — вопрос."""
        logger.error("провайдер недоступен: %s", exc)
        attempt.error_code = getattr(exc, "code", "provider_unavailable")
        attempt.status = AnswerStatus.ERROR
        attempt.answer = self.prompts.unavailable_text
        if scope is not None:
            self.escalation.escalate(
                scope=scope,
                question_text=check.redacted,
                normalized_hash=check.normalized_hash,
                language=language,
                reason=f"Ассистент недоступен: {attempt.error_code}",
            )
        return self._finish(
            attempt, scope=scope, language=language,
            question=check.redacted, revision=revision,
        )

    @staticmethod
    def _sources_of(result: RetrievalResult) -> tuple[SourceRef, ...]:
        seen: dict[uuid.UUID, SourceRef] = {}
        for chunk in result.chunks:
            if chunk.source_id in seen:
                continue
            seen[chunk.source_id] = SourceRef(
                id=chunk.source_id,
                title=chunk.source_title,
                version=chunk.source_version,
                updated_at=chunk.source_updated_at,
            )
        return tuple(seen.values())

    def _store_cache(self, key: str, attempt: _Attempt) -> None:
        self.cache.set(
            key,
            {
                "status": attempt.status.value,
                "answer": attempt.answer,
                "model": attempt.model,
                "retrieval_score": attempt.retrieval_score,
                "sources": [json.loads(s.model_dump_json()) for s in attempt.sources],
            },
            self.settings.ai_cache_ttl_seconds,
        )

    def _resolve_language(self, language: str) -> str:
        supported = self.settings.supported_languages
        code = (language or "").lower()
        return code if code in supported else self.settings.ai_default_language

    def _finish(
        self,
        attempt: _Attempt,
        *,
        scope: EmployeeScope | None,
        language: str,
        question: str | None,
        revision: int = 0,
    ) -> AnswerResponse:
        latency_ms = int((time.monotonic() - attempt.started_at) * 1000)

        if scope is not None:
            # В журнал уходит текст, из которого вычищены секреты.
            # Системный промпт, ключи и заголовки не пишутся никогда.
            self.session.add(
                LlmQueryLog(
                    id=attempt.request_id,
                    organization_id=scope.organization_id,
                    employee_id=scope.employee_id,
                    office_id=scope.office_id,
                    region_id=scope.region_id,
                    language=language,
                    question_text=sanitize_for_log(question),
                    answer_text=sanitize_for_log(attempt.answer),
                    status=attempt.status.value,
                    model=attempt.model,
                    prompt_version=self.prompts.version,
                    input_tokens=attempt.input_tokens,
                    output_tokens=attempt.output_tokens,
                    latency_ms=latency_ms,
                    retrieval_score=(
                        Decimal(str(round(attempt.retrieval_score, 5)))
                        if attempt.retrieval_score is not None
                        else None
                    ),
                    retrieved_source_ids=[str(s.id) for s in attempt.sources],
                    cache_hit=attempt.cache_hit,
                    fallback_used=attempt.fallback_used,
                    error_code=attempt.error_code,
                    created_at=datetime.now(tz=timezone.utc),
                )
            )
            self.session.flush()

        return AnswerResponse(
            request_id=attempt.request_id,
            status=attempt.status,
            answer=attempt.answer or self.prompts.unavailable_text,
            language=language,
            sources=list(attempt.sources),
            retrieval_score=attempt.retrieval_score,
            score_band=score_band(attempt.retrieval_score),
            used_model=attempt.model,
            cache_hit=attempt.cache_hit,
            fallback_used=attempt.fallback_used,
            knowledge_revision=revision,
        )
