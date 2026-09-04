"""Готов ли ассистент считать эмбеддинги — и если нет, то почему.

Проверка отдельно от `providers.build_*` по одной причине: там ответ
приходит исключением одного класса (`ConfigurationError`) с разным
текстом, а интерфейсу нужно различать два случая, и различать не по
тексту — текст переводится.

  * `ai_disabled` — рубильник `AI_ASSISTANT_ENABLED` выключен. Это
    решение, а не поломка: чинит тот, кто его принимал.
  * `provider_not_configured` — рубильник включён, но ключа или имени
    модели нет. Чинит тот, у кого есть ключ.

Один код на оба случая отправлял бы обоих не туда.

Проверка стоит ПЕРЕД созданием провайдера, а не вместо него: платного
вызова здесь не происходит вовсе, и выключенный ассистент отказывает
раньше, чем кто-нибудь успеет обратиться наружу.
"""

from __future__ import annotations

from humotech.ai_assistant.config import AiSettings, ai_settings
from humotech.core.errors import AiDisabled, ProviderNotConfigured


def require_embeddings_available(settings: AiSettings | None = None) -> None:
    """Отказать, если посчитать эмбеддинг сейчас нечем.

    Подставить вместо настоящего вектора случайный — худшее из
    возможного: база наполнится значениями, которые выглядят как
    эмбеддинги, ищутся как эмбеддинги и не значат ничего. Отличить их
    потом от настоящих будет нечем.
    """
    settings = settings or ai_settings
    if not settings.ai_assistant_enabled:
        raise AiDisabled(
            "AI-ассистент выключен: индексация и поиск по смыслу недоступны",
            details={"setting": "AI_ASSISTANT_ENABLED"},
        )
    if not settings.has_credentials:
        raise ProviderNotConfigured(
            "Ключ провайдера не задан",
            details={"setting": "OPENAI_API_KEY"},
        )
    if not settings.openai_embedding_model.strip():
        raise ProviderNotConfigured(
            "Модель эмбеддингов не задана",
            details={"setting": "OPENAI_EMBEDDING_MODEL"},
        )


def embeddings_available(settings: AiSettings | None = None) -> bool:
    """То же самое вопросом, а не отказом — для карточек и списков."""
    try:
        require_embeddings_available(settings)
    except (AiDisabled, ProviderNotConfigured):
        return False
    return True


__all__ = ["embeddings_available", "require_embeddings_available"]
