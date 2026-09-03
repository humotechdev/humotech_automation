"""Реестр версий системного промпта.

Версия выбирается настройкой `AI_PROMPT_VERSION` и записывается в журнал
вместе с каждым ответом. Старые версии не удаляются: без них нельзя
объяснить ответ, выданный полгода назад.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.modules.ai_assistant.errors import ConfigurationError
from src.modules.ai_assistant.prompts import v1


@dataclass(frozen=True)
class PromptBundle:
    version: str
    system_prompt: str
    user_template: str
    context_item_template: str
    no_answer_text: str
    conflict_text: str
    unavailable_text: str
    personal_data_text: str


_REGISTRY: dict[str, PromptBundle] = {
    v1.PROMPT_VERSION: PromptBundle(
        version=v1.PROMPT_VERSION,
        system_prompt=v1.SYSTEM_PROMPT,
        user_template=v1.USER_TEMPLATE,
        context_item_template=v1.CONTEXT_ITEM_TEMPLATE,
        no_answer_text=v1.NO_ANSWER_TEXT,
        conflict_text=v1.CONFLICT_TEXT,
        unavailable_text=v1.UNAVAILABLE_TEXT,
        personal_data_text=v1.PERSONAL_DATA_TEXT,
    ),
}


def get_prompt(version: str) -> PromptBundle:
    bundle = _REGISTRY.get(version)
    if bundle is None:
        raise ConfigurationError(
            f"Неизвестная версия системного промпта: '{version}'. "
            f"Доступны: {', '.join(sorted(_REGISTRY))}"
        )
    return bundle


__all__ = ["PromptBundle", "get_prompt"]
