"""Фабрика провайдеров.

Единственное место, где решается, какая реализация используется. Пока
`AI_ASSISTANT_ENABLED=false`, настоящие провайдеры не создаются вовсе —
значит, ни одного обращения наружу произойти не может физически.
"""

from __future__ import annotations

from src.modules.ai_assistant.config import AiSettings, ai_settings
from src.modules.ai_assistant.errors import ConfigurationError
from src.modules.ai_assistant.providers.base import (
    EmbeddingProvider,
    EmbeddingResult,
    LlmResponse,
    LLMProvider,
)
from src.modules.ai_assistant.providers.fake import (
    FakeEmbeddingProvider,
    FakeLLMProvider,
    UnavailableLLMProvider,
)


def build_llm_provider(settings: AiSettings | None = None) -> LLMProvider:
    settings = settings or ai_settings
    if not settings.ai_assistant_enabled:
        raise ConfigurationError(
            "AI-ассистент выключен (AI_ASSISTANT_ENABLED=false)"
        )
    if not settings.has_credentials:
        raise ConfigurationError("OPENAI_API_KEY не задан")
    if not settings.openai_chat_model.strip():
        raise ConfigurationError("OPENAI_CHAT_MODEL не задан")

    from src.modules.ai_assistant.providers.openai_provider import OpenAILLMProvider

    return OpenAILLMProvider(
        api_key=settings.openai_api_key,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )


def build_embedding_provider(
    settings: AiSettings | None = None,
) -> EmbeddingProvider:
    settings = settings or ai_settings
    if not settings.ai_assistant_enabled:
        raise ConfigurationError(
            "AI-ассистент выключен (AI_ASSISTANT_ENABLED=false)"
        )
    if not settings.has_credentials:
        raise ConfigurationError("OPENAI_API_KEY не задан")
    if not settings.openai_embedding_model.strip():
        raise ConfigurationError("OPENAI_EMBEDDING_MODEL не задан")

    from src.modules.ai_assistant.providers.openai_provider import (
        OpenAIEmbeddingProvider,
    )

    return OpenAIEmbeddingProvider(
        api_key=settings.openai_api_key,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
        dimensions=settings.ai_embedding_dimensions,
    )


__all__ = [
    "LLMProvider",
    "EmbeddingProvider",
    "LlmResponse",
    "EmbeddingResult",
    "FakeLLMProvider",
    "FakeEmbeddingProvider",
    "UnavailableLLMProvider",
    "build_llm_provider",
    "build_embedding_provider",
]
