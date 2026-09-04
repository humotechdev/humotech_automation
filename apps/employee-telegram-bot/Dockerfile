# Образ Telegram-бота: aiogram, long polling.
#
# Портов не открывает и наружу не публикуется. Бот сам ходит к Telegram
# и к backend по внутренней сети Docker; входящих соединений у него нет,
# и открывать ему порт значило бы создать поверхность без надобности.
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10002 humotech \
    && chown -R humotech:humotech /app
USER humotech

# Проверка настроек, а не сети: она отвечает на вопрос «этот процесс
# вообще способен работать». Обращение к Telegram здесь было бы
# проверкой чужой доступности — от неё контейнер не становится больным.
HEALTHCHECK --interval=60s --timeout=15s --start-period=20s --retries=3 \
    CMD ["python", "-m", "src.check_config"]

CMD ["python", "-m", "src.main"]
