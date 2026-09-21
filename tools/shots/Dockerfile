# Chromium из пакетов Alpine вместо образа Playwright: тот весит около
# полутора гигабайт и в этой сети не докачивается. `playwright-core`
# умеет работать с уже установленным браузером — свой он не тянет.
#
# `|| true` у apk не беспечность: пакеты ставятся, но триггеры шрифтового
# кэша в контейнере без D-Bus возвращают ненулевой код. Наличие самого
# браузера проверяется следующей же строкой.
FROM node:22-alpine
RUN apk add --no-cache chromium ttf-freefont || true
RUN test -x /usr/bin/chromium-browser || test -x /usr/bin/chromium
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
# Зависимости лежат РЯДОМ со скриптом, а не глобально: модули ES ищут
# их обходом вверх от файла и переменную NODE_PATH не читают вовсе.
WORKDIR /opt/shoot
RUN npm install --silent playwright-core@1.49.1 pngjs@7 pixelmatch@6
