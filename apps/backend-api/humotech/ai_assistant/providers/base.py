"""Абстракции провайдеров LLM и эмбеддингов.

Вся бизнес-логика модуля работает ТОЛЬКО с этими классами. SDK OpenAI
импортируется ровно в одном файле (`openai_provider.py`), поэтому смена
провайдера — это новая реализация двух абстрактных классов и одна строка
в фабрике, без правок сервисов.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LlmResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    # структурированный разбор, если провайдер вернул его отдельно
    parsed: dict | None = None


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0


class LLMProvider(ABC):
    """Порождает текст ответа по системному промпту и подготовленному контексту."""

    name: str = "abstract"

    @abstractmethod
    def complete(
        self,
        *,
        system_prompt: str,
        user_content: str,
        model: str,
        max_output_tokens: int | None = None,
        response_schema: dict | None = None,
    ) -> LlmResponse:
        """Один вызов модели.

        `user_content` уже собран приложением: вопрос + извлечённый контекст.
        Провайдер ничего не дособирает и ничего не решает — он транспорт.
        """

    @abstractmethod
    def health(self) -> bool:
        """Готов ли провайдер обслуживать запросы. Сетевых вызовов не делает."""


class EmbeddingProvider(ABC):
    """Превращает тексты в векторы для поиска по базе знаний."""

    name: str = "abstract"
    dimensions: int = 0

    @abstractmethod
    def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingResult:
        """Вектор для каждого текста, в том же порядке."""

    @abstractmethod
    def health(self) -> bool:
        ...
