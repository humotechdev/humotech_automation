"""Единственное место в проекте, где импортируется SDK OpenAI.

Здесь же ошибки SDK переводятся в собственные исключения модуля, чтобы
сервисы не ловили типы чужой библиотеки и не зависели от её версии.

Используется Responses API. Имена моделей приходят параметром — в этом файле
нет ни одного зашитого имени: неверное имя должно исправляться правкой .env,
а не выпуском новой версии кода.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Sequence

from src.modules.ai_assistant.errors import (
    ConfigurationError,
    ModelUnavailableError,
    ProviderUnavailableError,
    RateLimitedError,
)
from src.modules.ai_assistant.providers.base import (
    EmbeddingProvider,
    EmbeddingResult,
    LlmResponse,
    LLMProvider,
)

logger = logging.getLogger("humotech.ai.openai")

# Подстроки в тексте ошибки, по которым видно, что модель не существует
# или к ней нет доступа. Такое НЕ ретраится и НЕ подменяется другой моделью.
_MODEL_ERROR_MARKERS = (
    "model_not_found",
    "does not exist",
    "do not have access",
    "unknown model",
)


def _client(api_key: str, timeout: float, max_retries: int):
    """Ленивый импорт: без ключа и без включённого модуля SDK не трогаем."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - зависимость объявлена
        raise ConfigurationError(
            "Пакет openai не установлен: pip install -r requirements.txt"
        ) from exc

    # собственные повторы делаем сами, чтобы контролировать backoff и логи
    return OpenAI(api_key=api_key, timeout=timeout, max_retries=0)


class _OpenAIBase:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        if not api_key.strip():
            raise ConfigurationError(
                "OPENAI_API_KEY не задан — провайдер OpenAI создать нельзя"
            )
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._sdk = _client(api_key, timeout_seconds, max_retries)

    def health(self) -> bool:
        # намеренно без сетевого вызова: health не должен стоить денег
        return bool(self._api_key.strip())

    def _run(self, call, *, model: str):
        """Вызов с контролируемыми повторами и экспоненциальным backoff."""
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            RateLimitError,
        )

        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return call()
            except RateLimitError as exc:
                last = exc
                if attempt >= self._max_retries:
                    raise RateLimitedError(
                        "Провайдер ограничил частоту запросов"
                    ) from exc
            except (APITimeoutError, APIConnectionError) as exc:
                last = exc
                if attempt >= self._max_retries:
                    raise ProviderUnavailableError(
                        "Провайдер недоступен: сеть или таймаут"
                    ) from exc
            except APIStatusError as exc:
                message = str(getattr(exc, "message", "") or exc)
                if any(marker in message.lower() for marker in _MODEL_ERROR_MARKERS):
                    # Модель неизвестна или закрыта. Молча переключаться
                    # на другую нельзя — это скрыло бы ошибку конфигурации.
                    raise ModelUnavailableError(
                        f"Модель '{model}' недоступна у провайдера. "
                        "Проверьте OPENAI_CHAT_MODEL / OPENAI_FALLBACK_MODEL "
                        "/ OPENAI_EMBEDDING_MODEL в .env"
                    ) from exc
                if exc.status_code and exc.status_code < 500:
                    raise ProviderUnavailableError(
                        f"Провайдер отклонил запрос: HTTP {exc.status_code}"
                    ) from exc
                last = exc
                if attempt >= self._max_retries:
                    raise ProviderUnavailableError(
                        f"Провайдер вернул ошибку HTTP {exc.status_code}"
                    ) from exc

            # экспоненциальный backoff с джиттером, чтобы клиенты
            # не пошли на повтор одновременно
            delay = (2**attempt) * 0.5 + random.uniform(0, 0.25)
            logger.warning(
                "повтор запроса к провайдеру, попытка %s из %s, пауза %.2fs",
                attempt + 1, self._max_retries, delay,
            )
            time.sleep(delay)

        raise ProviderUnavailableError("Провайдер недоступен") from last


class OpenAILLMProvider(_OpenAIBase, LLMProvider):
    name = "openai"

    def complete(
        self,
        *,
        system_prompt: str,
        user_content: str,
        model: str,
        max_output_tokens: int | None = None,
        response_schema: dict | None = None,
    ) -> LlmResponse:
        if not model.strip():
            raise ConfigurationError("Имя модели не задано")

        def call():
            kwargs: dict = {
                "model": model,
                # системный промпт и пользовательский контент разделены:
                # содержимое документов попадает ТОЛЬКО в user-часть
                "instructions": system_prompt,
                "input": user_content,
            }
            if max_output_tokens:
                kwargs["max_output_tokens"] = max_output_tokens
            if response_schema:
                kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "humotech_answer",
                        "strict": True,
                        "schema": response_schema,
                    }
                }
            return self._sdk.responses.create(**kwargs)

        raw = self._run(call, model=model)

        text = getattr(raw, "output_text", None) or ""
        usage = getattr(raw, "usage", None)
        return LlmResponse(
            text=text,
            model=getattr(raw, "model", model),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )


class OpenAIEmbeddingProvider(_OpenAIBase, EmbeddingProvider):
    name = "openai"

    def __init__(self, *, dimensions: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingResult:
        if not model.strip():
            raise ConfigurationError("Имя embedding-модели не задано")
        if not texts:
            return EmbeddingResult(vectors=[], model=model)

        def call():
            return self._sdk.embeddings.create(model=model, input=list(texts))

        raw = self._run(call, model=model)
        vectors = [item.embedding for item in raw.data]

        for vector in vectors:
            if len(vector) != self.dimensions:
                # Несовпадение размерности означает другую модель.
                # Молча записать такой вектор нельзя: колонка vector(1536)
                # его не примет, а поиск сломается незаметно.
                raise ConfigurationError(
                    f"Модель '{model}' вернула вектор размерности {len(vector)}, "
                    f"а схема рассчитана на {self.dimensions}. "
                    "Смена embedding-модели требует миграции."
                )

        usage = getattr(raw, "usage", None)
        return EmbeddingResult(
            vectors=vectors,
            model=getattr(raw, "model", model),
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        )
