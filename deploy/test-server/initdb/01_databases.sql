-- Выполняется один раз при создании тома данных тестового сервера.
-- Рабочая база Django и расширения, которые нужны миграциям:
--   btree_gist — EXCLUDE-ограничения против пересечения назначений и графиков;
--   vector     — эмбеддинги базы знаний.
-- Тестовых баз (humotech_test) здесь нет: на сервере pytest не запускается.

CREATE DATABASE humotech_django OWNER humotech;

\connect humotech_django

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;
