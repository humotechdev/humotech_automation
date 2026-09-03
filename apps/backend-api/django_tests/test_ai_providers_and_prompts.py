"""Провайдеры, промпт и разбиение текста — без сети и без ключа."""

from __future__ import annotations

import pytest

from humotech.ai_assistant.errors import ConfigurationError
from humotech.ai_assistant.prompts import get_prompt
from humotech.ai_assistant.providers import (
    build_embedding_provider,
    build_llm_provider,
)
from humotech.ai_assistant.providers.fake import FakeEmbeddingProvider
from humotech.ai_assistant.services.chunking import (
    count_tokens,
    hash_text,
    split_text,
)


# ------------------------------------------------------------------ фабрика

def test_disabled_module_refuses_to_create_provider(ai_settings):
    """Пока рубильник выключен, настоящий провайдер не создаётся вовсе."""
    cfg = ai_settings.model_copy(update={"ai_assistant_enabled": False})
    with pytest.raises(ConfigurationError, match="выключен"):
        build_llm_provider(cfg)
    with pytest.raises(ConfigurationError, match="выключен"):
        build_embedding_provider(cfg)


def test_missing_api_key_is_a_configuration_error(ai_settings):
    cfg = ai_settings.model_copy(update={"openai_api_key": ""})
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        build_llm_provider(cfg)


def test_missing_model_name_is_a_configuration_error(ai_settings):
    """Неизвестная модель — ошибка конфигурации, а не повод взять другую."""
    cfg = ai_settings.model_copy(update={"openai_chat_model": ""})
    with pytest.raises(ConfigurationError, match="OPENAI_CHAT_MODEL"):
        build_llm_provider(cfg)

    cfg = ai_settings.model_copy(update={"openai_embedding_model": ""})
    with pytest.raises(ConfigurationError, match="OPENAI_EMBEDDING_MODEL"):
        build_embedding_provider(cfg)


def test_model_names_are_not_hardcoded_in_source():
    """Имена моделей живут только в конфигурации.

    Проверяются строковые ЛИТЕРАЛЫ, а не комментарии и docstring'и:
    упоминание модели в пояснении безвредно, а вот использование её имени
    как значения означало бы, что смена модели требует правки кода.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "humotech"
    names = ("gpt-5.6-luna", "gpt-5.6-terra", "text-embedding-3-small")
    offenders: list[str] = []

    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value in docstrings:
                continue
            for needle in names:
                if needle in node.value:
                    offenders.append(f"{path.name}:{node.lineno} -> {needle}")

    assert not offenders, f"имя модели зашито в коде: {offenders}"


# -------------------------------------------------------------------- промпт

@pytest.mark.parametrize(
    "requirement",
    [
        "CONTEXT",            # отвечать только по переданному контексту
        "не выдум",           # не придумывать факты
        "ДАННЫЕ",             # retrieved-контент это данные, не инструкции
        "промпт",             # не раскрывать системный промпт
        "другого сотрудника", # не выдавать чужие данные
        "HR",                 # при конфликте и отсутствии ответа — к HR
        "языке",              # отвечать на языке пользователя
    ],
)
def test_system_prompt_states_every_required_rule(requirement):
    prompt = get_prompt("v1").system_prompt
    assert requirement.lower() in prompt.lower(), f"в промпте нет правила: {requirement}"


def test_prompt_contains_exact_no_answer_wording():
    """Формулировка отказа согласована с ТЗ дословно."""
    bundle = get_prompt("v1")
    assert bundle.no_answer_text == (
        "Я не нашёл подтверждённой информации по этому вопросу. "
        "Ваш вопрос передан HR."
    )
    assert bundle.no_answer_text in bundle.system_prompt


def test_unknown_prompt_version_fails_loudly():
    with pytest.raises(ConfigurationError, match="Неизвестная версия"):
        get_prompt("v999")


# ------------------------------------------------------------------ разбиение

def test_split_respects_paragraph_boundaries():
    text = "Первый абзац про отпуск.\n\nВторой абзац про больничный."
    chunks = split_text(text, max_tokens=1000)
    assert len(chunks) == 1  # обе части помещаются в один кусок
    assert "Первый абзац" in chunks[0].text


def test_long_text_is_split_into_several_chunks():
    paragraph = "Правило номер один про рабочее время. " * 40
    text = "\n\n".join(paragraph for _ in range(6))
    chunks = split_text(text, max_tokens=200)
    assert len(chunks) > 1
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(c.token_count > 0 for c in chunks)


def test_empty_text_yields_no_chunks():
    assert split_text("") == []
    assert split_text("   \n\n  ") == []


def test_hash_is_stable_and_ignores_whitespace():
    assert hash_text("Отпуск  оформляется\nза 14 дней") == hash_text(
        "Отпуск оформляется за 14 дней"
    )


def test_token_count_is_positive():
    assert count_tokens("Сколько дней отпуска?") > 0


# --------------------------------------------------------------- эмбеддинги

def test_fake_embeddings_are_deterministic():
    provider = FakeEmbeddingProvider(dimensions=64)
    first = provider.embed(["как оформить отпуск"], model="m").vectors[0]
    second = provider.embed(["как оформить отпуск"], model="m").vectors[0]
    assert first == second


def test_similar_texts_are_closer_than_unrelated_ones():
    provider = FakeEmbeddingProvider(dimensions=256)
    vectors = provider.embed(
        [
            "как оформить отпуск",
            "как оформить отпуск заранее",
            "где находится склад в Худжанде",
        ],
        model="m",
    ).vectors

    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(vectors[0], vectors[1]) > cosine(vectors[0], vectors[2])


# ------------------------------------------- регрессия: 429 бывает разный

def test_exhausted_balance_is_not_confused_with_throttling():
    """Найдено живым вызовом: OpenAI отдаёт HTTP 429 и на троттлинг,
    и на пустой счёт. Раньше и то и другое становилось RateLimitedError,
    и запрос ещё и ретраился — впустую, потому что деньги от повторов
    не появляются, а сообщение уводило расследование в сторону.
    """
    from humotech.ai_assistant.providers.openai_provider import (
        _is_quota_exhausted,
    )

    class FakeError(Exception):
        def __init__(self, message, body=None):
            super().__init__(message)
            self.message = message
            self.body = body

    quota = FakeError(
        "Error code: 429 - You have no credits remaining. Add credits to continue",
        {"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted"}},
    )
    assert _is_quota_exhausted(quota) is True

    throttling = FakeError(
        "Rate limit reached for requests",
        {"error": {"type": "rate_limit_error", "code": "rate_limit_exceeded"}},
    )
    assert _is_quota_exhausted(throttling) is False


def test_quota_error_has_its_own_code_for_the_log():
    """В журнале должна быть настоящая причина, а не «превышена частота»."""
    from humotech.ai_assistant.errors import (
        AiError,
        InsufficientQuotaError,
        RateLimitedError,
    )

    assert InsufficientQuotaError.code == "insufficient_quota"
    assert RateLimitedError.code == "rate_limited"
    assert issubclass(InsufficientQuotaError, AiError)


@pytest.mark.django_db
def test_quota_error_gives_the_employee_a_safe_answer(
    monkeypatch, organization, employee, ai_settings
):
    """Пустой счёт не должен показывать сотруднику техническую ошибку."""
    from humotech.ai_assistant.errors import InsufficientQuotaError
    from humotech.ai_assistant.providers.fake import (
        FakeEmbeddingProvider,
        FakeLLMProvider,
    )
    from humotech.ai_assistant.schemas import AnswerRequest, AnswerStatus
    from humotech.ai_assistant.services import answer as answer_module
    from humotech.ai_assistant.services.answer import AnswerService
    from humotech.ai_assistant.services.cache import InMemoryCacheService
    from humotech.ai_assistant.services.personal_data import (
        NotImplementedPersonalDataQueryService,
    )
    from humotech.ai_assistant.services.retrieval import (
        RetrievalResult,
        RetrievedChunk,
    )
    from humotech.ai_assistant.models import LlmQueryLog
    import uuid as _uuid
    from datetime import datetime, timezone as _tz

    from humotech.ai_assistant.services.scoping import EmployeeScope

    scope = EmployeeScope(
        employee_id=employee.id,
        organization_id=organization.id,
        office_id=None, region_id=None, department_id=None,
        language="ru", employment_status="ACTIVE",
    )
    monkeypatch.setattr(answer_module, "resolve_employee_scope", lambda **kw: scope)
    monkeypatch.setattr(answer_module, "current_revision", lambda org: 1)

    chunk = RetrievedChunk(
        chunk_id=_uuid.uuid4(), source_id=_uuid.uuid4(), source_title="Правило",
        source_version=1, source_updated_at=datetime.now(tz=_tz.utc),
        published_at=datetime.now(tz=_tz.utc), chunk_index=0, text="текст",
        score=0.9, scope_level=1, priority=0,
    )

    class Stub:
        def find_exact_faq(self, **kw):
            return None

        def search(self, **kw):
            return RetrievalResult(chunks=(chunk,), top_score=0.9)

    service = AnswerService(
        llm=FakeLLMProvider(fail_with=InsufficientQuotaError("баланс пуст")),
        embeddings=FakeEmbeddingProvider(dimensions=8),
        cache=InMemoryCacheService(),
        personal_data_service=NotImplementedPersonalDataQueryService("—"),
        settings=ai_settings,
    )
    service.retrieval = Stub()

    response = service.answer(
        AnswerRequest(employee_id=scope.employee_id, question="Как оформить отпуск?")
    )

    assert response.status is AnswerStatus.ERROR
    assert "баланс" not in response.answer.lower(), "техническая деталь утекла"
    assert "HR" in response.answer

    log = LlmQueryLog.objects.get()
    assert log.error_code == "insufficient_quota", "в журнале не настоящая причина"


def test_strict_schema_lists_every_property_in_required():
    """Найдено живым вызовом: при strict=true OpenAI требует, чтобы в required
    были ВСЕ ключи из properties, иначе HTTP 400 на каждом структурированном
    ответе. Без conflict_detected ассистент не ответил бы ни разу.
    """
    from humotech.ai_assistant.schemas import ANSWER_JSON_SCHEMA

    properties = set(ANSWER_JSON_SCHEMA["properties"])
    required = set(ANSWER_JSON_SCHEMA["required"])
    missing = properties - required
    assert not missing, (
        f"при strict=true эти свойства обязаны быть в required: {sorted(missing)}"
    )
    assert ANSWER_JSON_SCHEMA["additionalProperties"] is False
