-- Выполняется один раз при создании тома данных.
-- Заводит тестовую базу и расширения, которые нужны миграциям:
--   btree_gist — EXCLUDE-ограничения против пересечения назначений и графиков (0001);
--   vector     — эмбеддинги базы знаний (0002).

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;

-- Рабочая база Django. Отдельная от `humotech` (POSTGRES_DB), потому что
-- в той живёт прежний слой Alembic, и смешивать две схемы в одной базе
-- значило бы потерять возможность сравнивать их между собой.
--
-- Без этой строки на чистом томе миграции падают в первую же секунду:
-- база, указанная в DJANGO_DATABASE_URL, просто не существует.
CREATE DATABASE humotech_django OWNER humotech;

\connect humotech_django

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;

\connect humotech

CREATE DATABASE humotech_test OWNER humotech;

\connect humotech_test

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;
