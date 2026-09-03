-- Выполняется один раз при создании тома данных.
-- Заводит тестовую базу и расширения, которые нужны миграциям:
--   btree_gist — EXCLUDE-ограничения против пересечения назначений и графиков (0001);
--   vector     — эмбеддинги базы знаний (0002).

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE DATABASE humotech_test OWNER humotech;

\connect humotech_test

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;
