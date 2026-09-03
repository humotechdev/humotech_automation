"""Настройки AI-ассистента.

Все значения приходят из .env — в коде нет ни одного имени модели, ни одного
порога и ни одного ключа. Это сознательно: неверное имя модели должно
исправляться правкой конфига, а не выпуском новой версии кода.

Модуль по умолчанию ВЫКЛЮЧЕН (`AI_ASSISTANT_ENABLED=false`). Пока он выключен,
ни один провайдер не создаётся и наружу не уходит ни одного запроса.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parents[3]


class AiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=APP_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- главный рубильник ---
    ai_assistant_enabled: bool = False
    ai_fallback_enabled: bool = True

    # --- провайдер ---
    openai_api_key: str = ""
    openai_chat_model: str = ""
    openai_fallback_model: str = ""
    openai_embedding_model: str = ""
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 2

    # --- пороги поиска ---
    # Значения предварительные. Окончательные выставим после прогона
    # на реальных вопросах сотрудников — угадать их заранее нельзя.
    ai_exact_faq_threshold: float = 0.92
    ai_rag_min_score: float = 0.72

    # Насколько близко должны быть баллы двух РАЗНЫХ документов одного уровня,
    # чтобы считать это конфликтом, который решает HR, а не модель.
    ai_conflict_score_delta: float = 0.05

    ai_max_retrieved_chunks: int = 6
    ai_max_context_tokens: int = 4000
    ai_query_max_length: int = 1000
    ai_cache_ttl_seconds: int = 900
    ai_log_retention_days: int = 90
    ai_prompt_version: str = "v1"

    ai_rate_limit_per_minute: int = 10
    ai_rate_limit_per_day: int = 200

    ai_cache_backend: str = "memory"
    ai_redis_url: str | None = None

    ai_supported_languages: str = "ru"
    ai_default_language: str = "ru"

    # размерность text-embedding-3-small; менять только вместе с миграцией,
    # потому что она зашита в тип колонки vector(1536)
    ai_embedding_dimensions: int = Field(default=1536)

    @field_validator("ai_exact_faq_threshold", "ai_rag_min_score")
    @classmethod
    def _threshold_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("порог похожести должен лежать в диапазоне 0..1")
        return value

    @property
    def supported_languages(self) -> tuple[str, ...]:
        return tuple(
            code.strip().lower()
            for code in self.ai_supported_languages.split(",")
            if code.strip()
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.openai_api_key.strip())


ai_settings = AiSettings()
