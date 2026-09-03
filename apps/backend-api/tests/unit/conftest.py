"""Фикстуры модульных тестов AI-модуля.

Эти тесты не трогают ни PostgreSQL, ни сеть, ни OpenAI. Они проверяют логику,
а не интеграцию: пороги, маршрутизацию, ключи кэша, приоритеты источников,
защиту от инъекций и то, что секреты не попадают в журнал.

Тесты, которым действительно нужен pgvector и живые запросы, лежат
в tests/integration/ и запускаются после поднятия Docker.
"""

from __future__ import annotations

import socket
import uuid

import pytest

# импорт реестра обязателен: он подтягивает ВСЕ модели, иначе SQLAlchemy
# не может разрешить строковые ссылки в relationship() и падает на первом
# же создании ORM-объекта
import src.core.database.registry  # noqa: F401
from src.modules.ai_assistant.config import AiSettings
from src.modules.ai_assistant.services.scoping import EmployeeScope


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Ни один модульный тест не имеет права выйти в сеть.

    Если провайдер случайно окажется настоящим, тест упадёт здесь,
    а не сходит в интернет и не потратит деньги.
    """

    def guard(*args, **kwargs):
        raise AssertionError(
            "Модульный тест попытался открыть сетевое соединение. "
            "Провайдеры должны быть подменены дублёрами."
        )

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture()
def settings() -> AiSettings:
    """Настройки для тестов: модуль включён, ключ фиктивный, сеть запрещена."""
    return AiSettings(
        ai_assistant_enabled=True,
        ai_fallback_enabled=True,
        openai_api_key="test-key-not-real",
        openai_chat_model="test-chat-model",
        openai_fallback_model="test-fallback-model",
        openai_embedding_model="test-embedding-model",
        ai_exact_faq_threshold=0.92,
        ai_rag_min_score=0.72,
        ai_conflict_score_delta=0.05,
        ai_max_retrieved_chunks=6,
        ai_query_max_length=1000,
        ai_cache_ttl_seconds=300,
        ai_prompt_version="v1",
        ai_rate_limit_per_minute=100,
        ai_rate_limit_per_day=1000,
        ai_supported_languages="ru,en",
        ai_default_language="ru",
    )


class FakeSession:
    """Минимальная подмена SQLAlchemy-сессии.

    Умеет ровно то, что нужно конвейеру ответа: принять объект, «сбросить»
    его и вернуть None на любой поиск. Реальные запросы проверяются
    интеграционными тестами, а не здесь.
    """

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flushes += 1

    def scalar(self, *args, **kwargs):
        return None

    def scalars(self, *args, **kwargs):
        return iter(())

    def get(self, *args, **kwargs):
        return None

    def delete(self, obj) -> None:
        pass

    def added_of(self, cls) -> list:
        return [obj for obj in self.added if isinstance(obj, cls)]


@pytest.fixture()
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture()
def scope() -> EmployeeScope:
    return EmployeeScope(
        employee_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        office_id=uuid.uuid4(),
        region_id=uuid.uuid4(),
        department_id=None,
        language="ru",
        employment_status="ACTIVE",
    )
