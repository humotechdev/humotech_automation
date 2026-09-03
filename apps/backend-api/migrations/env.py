"""Окружение Alembic.

URL базы берётся из настроек (.env), а не из alembic.ini — чтобы пароль
не оказался в репозитории. Для тестов URL можно переопределить переменной
окружения ALEMBIC_DATABASE_URL.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.core.config.settings import settings
from src.core.database.registry import target_metadata  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("ALEMBIC_DATABASE_URL") or settings.database_url
config.set_main_option("sqlalchemy.url", database_url)

# Функциональные индексы, которые Alembic сравнивать не умеет.
#
# PostgreSQL хранит выражение индекса в нормализованном виде с приведениями
# типов: to_tsvector('simple'::regconfig, (((title)::text || ' '::text) || content)).
# К исходной строке из модели оно не сводится, поэтому autogenerate на КАЖДОМ
# прогоне предлагает удалить и создать индекс заново, хотя в базе он верный.
# Однажды такая «правка» попадёт в миграцию, а пересоздание GIN-индекса на
# большой таблице — это блокировка на запись.
#
# Индексы остаются под контролем: их выражение задано в моделях и создано
# миграцией 0002. Менять его нужно вручную отдельной миграцией.
UNCOMPARED_EXPRESSION_INDEXES = {
    "ix_knowledge_sources_fts",
    "ix_knowledge_chunks_fts",
}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "index" and name in UNCOMPARED_EXPRESSION_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    """Режим --sql: рендерит SQL без подключения к базе."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
