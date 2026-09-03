"""Ошибки AI-модуля.

Отдельный слой нужен, чтобы бизнес-логика не ловила исключения OpenAI SDK
напрямую: провайдер переводит их в эти классы, и смена провайдера ничего
за пределами адаптера не ломает.
"""

from __future__ import annotations


class AiError(Exception):
    """Базовая ошибка модуля."""

    code = "ai_error"


class ConfigurationError(AiError):
    """Модуль настроен неверно: нет ключа, не задано имя модели и т.п.

    Сознательно НЕ приводит к автоматическому переключению на другую модель:
    молчаливая подмена модели опаснее явного отказа.
    """

    code = "configuration_error"


class ModelUnavailableError(AiError):
    """Провайдер не знает такую модель или доступ к ней закрыт."""

    code = "model_unavailable"


class ProviderUnavailableError(AiError):
    """Провайдер недоступен: сеть, таймаут, 5xx, исчерпаны попытки."""

    code = "provider_unavailable"


class RateLimitedError(AiError):
    """Превышен лимит запросов — наш собственный или провайдера.

    Состояние временное: имеет смысл повторить позже.
    """

    code = "rate_limited"


class InsufficientQuotaError(AiError):
    """На счету провайдера кончились средства.

    Провайдер отдаёт это тем же кодом HTTP 429, что и троттлинг, но природа
    другая: повторять запрос бессмысленно, пока человек не пополнит баланс.
    Отдельный класс нужен, чтобы не жечь попытки впустую и чтобы в журнале
    было видно настоящую причину, а не «превышена частота запросов».
    """

    code = "insufficient_quota"


class QuestionRejectedError(AiError):
    """Вопрос отклонён до обращения к модели: длина, пустота, небезопасность."""

    code = "question_rejected"


class KnowledgeConflictError(AiError):
    """Два действующих правила одного уровня противоречат друг другу.

    Разрешать такое должен HR, а не модель.
    """

    code = "knowledge_conflict"


class PublishingError(AiError):
    """Публикацию новой версии выполнить нельзя."""

    code = "publishing_error"
