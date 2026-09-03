"""Расширения PostgreSQL. Должны существовать до создания таблиц.

btree_gist — EXCLUDE-ограничения против пересечения периодов назначений
             и графиков: без него gist-индекс не примет колонку uuid;
vector     — эмбеддинги базы знаний, тип vector(1536) и индексы HNSW.

Обе операции идемпотентны (CREATE EXTENSION IF NOT EXISTS), поэтому
на базе, где расширения уже поставлены, миграция ничего не делает.
"""

from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        BtreeGistExtension(),
        VectorExtension(),
    ]
