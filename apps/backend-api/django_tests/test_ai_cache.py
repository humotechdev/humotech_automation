"""Ключ кэша и инвалидация по ревизии базы знаний."""

from __future__ import annotations

from dataclasses import replace

import pytest

from humotech.ai_assistant.services.cache import (
    CacheKeyParts,
    InMemoryCacheService,
    NullCacheService,
    build_cache,
)


@pytest.fixture()
def parts() -> CacheKeyParts:
    return CacheKeyParts(
        normalized_question_hash="a" * 64,
        language="ru",
        office_id="office-1",
        region_id="region-1",
        scope_fingerprint="office-1:region-1:-",
        knowledge_revision=7,
        prompt_version="v1",
        model="test-chat-model",
    )


def test_new_revision_invalidates_old_cache(parts):
    """Публикация правила увеличивает ревизию — старый ответ больше не найдётся."""
    cache = InMemoryCacheService()
    old_key = parts.build()
    cache.set(old_key, {"answer": "старый ответ"}, ttl_seconds=300)

    new_key = replace(parts, knowledge_revision=8).build()
    assert new_key != old_key
    assert cache.get(new_key) is None
    # прежняя запись физически не удалялась — она просто больше не адресуется
    assert cache.get(old_key) is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("language", "en"),
        ("office_id", "office-2"),
        ("region_id", "region-2"),
        ("scope_fingerprint", "other"),
        ("prompt_version", "v2"),
        ("model", "another-model"),
        ("normalized_question_hash", "b" * 64),
    ],
)
def test_every_meaningful_field_changes_the_key(parts, field, value):
    """Всё, что влияет на ответ, обязано влиять и на ключ."""
    assert replace(parts, **{field: value}).build() != parts.build()


def test_key_is_stable_for_identical_input(parts):
    assert parts.build() == parts.build()


def test_ttl_expiry(parts, monkeypatch):
    cache = InMemoryCacheService()
    key = parts.build()
    cache.set(key, {"answer": "ответ"}, ttl_seconds=1)
    assert cache.get(key) is not None

    import humotech.ai_assistant.services.cache as cache_module

    real = cache_module.time.monotonic()
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: real + 10)
    assert cache.get(key) is None


def test_zero_ttl_does_not_store(parts):
    cache = InMemoryCacheService()
    cache.set(parts.build(), {"answer": "ответ"}, ttl_seconds=0)
    assert cache.get(parts.build()) is None


def test_null_cache_never_remembers(parts):
    cache = NullCacheService()
    cache.set(parts.build(), {"answer": "ответ"}, ttl_seconds=300)
    assert cache.get(parts.build()) is None


def test_redis_backend_fails_loudly(ai_settings):
    """Молчаливый откат на память в проде хуже явной ошибки."""
    cfg = ai_settings.model_copy(update={"ai_cache_backend": "redis"})
    with pytest.raises(NotImplementedError):
        build_cache(cfg)
