from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Приложение без моделей.

    Существует ради одной вещи: расширения PostgreSQL должны ставиться ДО
    первой таблицы, которой они нужны. Отдельная миграция в отдельном
    приложении делает этот порядок явным — от неё зависят все остальные.
    """

    name = "humotech.core"
    label = "core"
    verbose_name = "Общее ядро"
