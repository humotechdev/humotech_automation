"""Провайдеры-дублёры для тестов и для выключенного модуля.

Ни одного сетевого вызова. Стандартные тесты используют только их, поэтому
проходят без OPENAI_API_KEY и без доступа в интернет.

Эмбеддинги детерминированы: один и тот же текст всегда даёт один и тот же
вектор, а близкие тексты — близкие векторы. Этого хватает, чтобы проверять
логику порогов и ранжирования, не притворяясь настоящей моделью.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

from humotech.ai_assistant.errors import ProviderUnavailableError
from humotech.ai_assistant.providers.base import (
    EmbeddingProvider,
    EmbeddingResult,
    LlmResponse,
    LLMProvider,
)


class FakeLLMProvider(LLMProvider):
    """Возвращает заранее заданный ответ и запоминает, с чем его позвали."""

    name = "fake"

    def __init__(
        self,
        *,
        answer: str = "Ответ из тестового провайдера.",
        fail_with: Exception | None = None,
        healthy: bool = True,
    ) -> None:
        self.answer = answer
        self.fail_with = fail_with
        self._healthy = healthy
        # история вызовов: тесты проверяют по ней, что именно ушло в модель
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        system_prompt: str,
        user_content: str,
        model: str,
        max_output_tokens: int | None = None,
        response_schema: dict | None = None,
    ) -> LlmResponse:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_content": user_content,
                "model": model,
                "max_output_tokens": max_output_tokens,
                "response_schema": response_schema,
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        return LlmResponse(
            text=self.answer,
            model=model,
            input_tokens=len(user_content.split()),
            output_tokens=len(self.answer.split()),
        )

    def health(self) -> bool:
        return self._healthy


class UnavailableLLMProvider(LLMProvider):
    """Провайдер, который всегда недоступен: проверка безопасного поведения."""

    name = "unavailable"

    def complete(self, **kwargs) -> LlmResponse:
        raise ProviderUnavailableError("Провайдер недоступен (тестовый дублёр)")

    def health(self) -> bool:
        return False


class FakeEmbeddingProvider(EmbeddingProvider):
    """Детерминированные псевдо-эмбеддинги на основе мешка слов.

    Похожие по словам тексты дают близкие векторы — этого достаточно,
    чтобы тестировать пороги и ранжирование без обращения к настоящей модели.
    """

    name = "fake"

    def __init__(self, *, dimensions: int = 1536) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingResult:
        self.calls.append(list(texts))
        return EmbeddingResult(
            vectors=[self._vector(text) for text in texts],
            model=model,
            input_tokens=sum(len(text.split()) for text in texts),
        )

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in self._tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            # пустой текст: вектор-заглушка, лишь бы не делить на ноль
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]

    @staticmethod
    def _tokens(text: str) -> list[str]:
        cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
        return [token for token in cleaned.split() if token]

    def health(self) -> bool:
        return True
