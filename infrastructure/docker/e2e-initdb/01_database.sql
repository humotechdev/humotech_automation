-- База изолированного стенда. Имя оканчивается на `_e2e` намеренно:
-- по нему и настройки, и команды посева отличают стенд от рабочей базы.
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE DATABASE humotech_e2e OWNER humotech;

\connect humotech_e2e

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS vector;
