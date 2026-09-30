-- Разделение прав PostgreSQL для HUMOTECH.
--
-- До этого backend и фоновые процессы ходили в базу СУПЕРПОЛЬЗОВАТЕЛЕМ
-- кластера (POSTGRES_USER=humotech): любая SQL-инъекция превращалась бы
-- в выполнение кода (COPY ... PROGRAM), удаление базы или создание ролей.
--
-- Роли:
--   humotech          — суперпользователь кластера (bootstrap, POSTGRES_USER);
--                       им выполняется только этот initdb, приложение им
--                       НЕ подключается;
--   humotech_owner    — владелец схемы и объектов; им выполняются миграции
--                       (сервис migrate). Не суперпользователь;
--   humotech_app      — рантайм backend и workers: только чтение и запись
--                       данных, без DDL, без CREATE ROLE, без COPY PROGRAM.
--
-- Пароли ролей задаются переменными окружения контейнера postgres
-- (POSTGRES_OWNER_PASSWORD, POSTGRES_APP_PASSWORD) и подставляются
-- entrypoint-скриптом 03_roles_passwords.sh ПЕРЕД этим файлом переменной
-- psql. Здесь пароли не хранятся.
\set ON_ERROR_STOP on

-- Создание ролей. `\gexec` выполняет сгенерированный текст, и подстановка
-- пароля идёт в ОБЫЧНОЙ строке (не в dollar-quoted блоке DO, где psql
-- переменные не подставляются). `format(%L)` экранирует значение.
SELECT format('CREATE ROLE humotech_owner LOGIN PASSWORD %L', :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'humotech_owner')
\gexec
SELECT format('CREATE ROLE humotech_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'humotech_app')
\gexec

-- База создаётся в 01_databases.sql владельцем humotech; переназначаем её
-- владельцу humotech_owner, чтобы миграции им и шли.

ALTER DATABASE humotech_django OWNER TO humotech_owner;

\connect humotech_django

-- Расширения ставит суперпользователь заранее (01_databases.sql); владелец
-- схемы их только использует.
ALTER SCHEMA public OWNER TO humotech_owner;

-- Рантайм-роль: подключение и работа с данными, без права менять схему.
GRANT CONNECT ON DATABASE humotech_django TO humotech_app;
GRANT USAGE ON SCHEMA public TO humotech_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO humotech_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO humotech_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO humotech_app;

-- Будущие объекты миграций тоже доступны рантайм-роли: DEFAULT PRIVILEGES
-- для того, ЧТО создаст humotech_owner.
ALTER DEFAULT PRIVILEGES FOR ROLE humotech_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO humotech_app;
ALTER DEFAULT PRIVILEGES FOR ROLE humotech_owner IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO humotech_app;
ALTER DEFAULT PRIVILEGES FOR ROLE humotech_owner IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO humotech_app;

-- Рантайм-роль НЕ должна создавать объекты в public.
REVOKE CREATE ON SCHEMA public FROM humotech_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
