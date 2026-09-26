"""Сборка модуля: одно место, где решается, какие реализации используются.

Это точка подключения для любого транспорта — Telegram-бота, HTTP-роутов CRM,
консольных команд. Транспорт получает готовые use case'ы и ничего не знает
ни про OpenAI, ни про устройство поиска.

Когда в проекте появится веб-фреймворк, его DI-контейнер вызовет эти функции
и отдаст результат в обработчики. Переписывать бизнес-логику не придётся.
"""

from __future__ import annotations

from dataclasses import dataclass

from humotech.ai_assistant.config import AiSettings, ai_settings
from humotech.ai_assistant.errors import ConfigurationError
from humotech.ai_assistant.prompts import get_prompt
from humotech.ai_assistant.providers import (
    build_embedding_provider,
    build_llm_provider,
)
from humotech.ai_assistant.providers.base import EmbeddingProvider, LLMProvider
from humotech.ai_assistant.services.answer import AnswerService
from humotech.ai_assistant.services.cache import CacheService, build_cache
from humotech.ai_assistant.services.personal_data import (
    NotImplementedPersonalDataQueryService,
    PersonalDataQueryService,
)
from humotech.ai_assistant.services.personal_data_service import (
    SqlPersonalDataQueryService,
)
from humotech.ai_assistant.services.rate_limit import DatabaseRateLimiter
from humotech.ai_assistant.use_cases.bot import (
    AnswerUseCase,
    FeedbackUseCase,
    HealthUseCase,
)
from humotech.ai_assistant.use_cases.crm import KnowledgeAdminUseCases

# Лимитер считает по журналу обращений, а не в памяти процесса: при
# нескольких воркерах счётчики в памяти умножали лимит на их число.
_shared_rate_limiter: DatabaseRateLimiter | None = None


def get_rate_limiter(settings: AiSettings | None = None) -> DatabaseRateLimiter:
    global _shared_rate_limiter
    settings = settings or ai_settings
    if _shared_rate_limiter is None:
        _shared_rate_limiter = DatabaseRateLimiter(
            per_minute=settings.ai_rate_limit_per_minute,
            per_day=settings.ai_rate_limit_per_day,
        )
    return _shared_rate_limiter


def build_personal_data_service(
    settings: AiSettings | None = None, *, available: bool = True
) -> PersonalDataQueryService:
    """Настоящий сервис поверх таблиц проекта.

    Заглушка остаётся только для окружений без базы: подставлять её вместо
    рабочего сервиса «на всякий случай» нельзя — сотрудник получил бы
    «сервис не подключён» там, где данные есть.
    """
    settings = settings or ai_settings
    if not available:
        return NotImplementedPersonalDataQueryService(
            get_prompt(settings.ai_prompt_version).personal_data_text
        )
    return SqlPersonalDataQueryService()


@dataclass(frozen=True)
class AiContainer:
    """Готовый набор точек входа модуля."""

    answer: AnswerUseCase
    feedback: FeedbackUseCase
    health: HealthUseCase
    crm: KnowledgeAdminUseCases
    cache: CacheService
    llm: LLMProvider | None
    embeddings: EmbeddingProvider | None
    enabled: bool


def build_container(
    *,
    settings: AiSettings | None = None,
    llm: LLMProvider | None = None,
    embeddings: EmbeddingProvider | None = None,
    cache: CacheService | None = None,
) -> AiContainer:
    """Собирает модуль.

    Провайдеры можно передать снаружи — так тесты подставляют дублёров,
    не трогая конфигурацию. Если не переданы и модуль выключен, они
    не создаются вовсе: ни одного обращения наружу произойти не может.
    """
    settings = settings or ai_settings
    cache = cache or build_cache(settings)

    if llm is None or embeddings is None:
        try:
            llm = llm or build_llm_provider(settings)
            embeddings = embeddings or build_embedding_provider(settings)
        except ConfigurationError:
            # Модуль выключен или не настроен. Это штатное состояние:
            # health расскажет, чего не хватает, а вопросы получат
            # безопасный ответ вместо обращения к несуществующему провайдеру.
            llm, embeddings = None, None

    answer_service = None
    if llm is not None and embeddings is not None:
        answer_service = AnswerService(
            llm=llm,
            embeddings=embeddings,
            cache=cache,
            personal_data_service=build_personal_data_service(settings),
            settings=settings,
            rate_limiter=get_rate_limiter(settings),
        )

    return AiContainer(
        answer=AnswerUseCase(answer_service) if answer_service else AnswerUseCase(None),
        feedback=FeedbackUseCase(),
        health=HealthUseCase(settings),
        crm=KnowledgeAdminUseCases(),
        cache=cache,
        llm=llm,
        embeddings=embeddings,
        enabled=answer_service is not None,
    )
