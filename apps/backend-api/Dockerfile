# Образ backend-api: Django под gunicorn.
#
# Python 3.11 — та же версия, на которой собран и проверен проект на
# машине разработчика. Совпадение версий здесь не педантизм: расхождение
# между «работает у меня» и «работает в контейнере» ищется дольше, чем
# стоит его предотвратить.
FROM python:3.11-slim AS runtime

# PYTHONUNBUFFERED: без него логи Python оседают в буфере и появляются
# в `docker logs` пачками с задержкой — ровно тогда, когда нужны сразу.
# PYTHONDONTWRITEBYTECODE: .pyc в контейнере писать некуда и незачем.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

# curl нужен healthcheck'у: без него проверка готовности внутри
# контейнера невозможна, а от неё зависит запуск бота и рабочих
# процессов через `depends_on: service_healthy`.
# fonts-dejavu-core нужен печатным заявлениям: во встроенных шрифтах
# PDF кириллицы нет вовсе, и без TTF заявление вышло бы из пустых
# прямоугольников. Пакет маленький и без зависимостей.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости отдельным слоем и раньше кода: список меняется редко,
# код — постоянно, и при таком порядке пересборка после правки кода
# не тянет установку пакетов заново.
COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt

COPY . .

# Процесс не от root: уязвимость в зависимости не должна сразу означать
# полный доступ к контейнеру. Каталоги, куда приложение пишет, создаются
# заранее и передаются пользователю — том монтируется поверх, но права
# на точку монтирования Docker берёт с образа.
#
# Каталог, забытый в этом списке, ломается не при сборке и не в тестах,
# а в бою: том появляется от root, и процесс молча не может в него
# писать. Именно так и вышло с `private-exports` — выгрузки собирались
# и падали на записи файла.
RUN useradd --create-home --uid 10001 humotech \
    && mkdir -p /app/staticfiles /app/private-media /app/private-exports \
    && chown -R humotech:humotech /app
USER humotech

EXPOSE 8000

# Воркеров немного: это ноутбук, а не сервер, и десяток процессов
# отнимет память у самой базы. Таймаут больше умолчания из-за выгрузок:
# XLSX на несколько тысяч строк собирается дольше 30 секунд.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
