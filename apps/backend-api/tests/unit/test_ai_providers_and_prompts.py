"""Провайдеры, промпт и разбиение текста — без сети и без ключа."""

from __future__ import annotations

import pytest

from src.modules.ai_assistant.errors import ConfigurationError
from src.modules.ai_assistant.prompts import get_prompt
from src.modules.ai_assistant.providers import (
    build_embedding_provider,
    build_llm_provider,
)
from src.modules.ai_assistant.providers.fake import FakeEmbeddingProvider
from src.modules.ai_assistant.services.chunking import (
    count_tokens,
    hash_text,
    split_text,
)


# ------------------------------------------------------------------ фабрика

def test_disabled_module_refuses_to_create_provider(settings):
    """Пока рубильник выключен, настоящий провайдер не создаётся вовсе."""
    cfg = settings.model_copy(update={"ai_assistant_enabled": False})
    with pytest.raises(ConfigurationError, match="выключен"):
        build_llm_provider(cfg)
    with pytest.raises(ConfigurationError, match="выключен"):
        build_embedding_provider(cfg)


def test_missing_api_key_is_a_configuration_error(settings):
    cfg = settings.model_copy(update={"openai_api_key": ""})
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        build_llm_provider(cfg)


def test_missing_model_name_is_a_configuration_error(settings):
    """Неизвестная модель — ошибка конфигурации, а не повод взять другую."""
    cfg = settings.model_copy(update={"openai_chat_model": ""})
    with pytest.raises(ConfigurationError, match="OPENAI_CHAT_MODEL"):
        build_llm_provider(cfg)

    cfg = settings.model_copy(update={"openai_embedding_model": ""})
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

    root = pathlib.Path(__file__).resolve().parents[2] / "src"
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
