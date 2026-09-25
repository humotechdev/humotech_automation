# HR CRM: сборка `vite build` и раздача статикой через nginx.
#
# На сервере CRM не работает как dev-сервер Vite: у того открыт
# HMR-сокет, исходники и source map целиком, а подмена Origin/Secure из
# vite.config.ts существует только для локальной разработки.
#
# Сборка — `tsc --noEmit` и `vite build` (договорённость с владельцами
# CRM): ошибка типов останавливает сборку образа. Тесты и страницы
# предпросмотра в контекст не входят (.dockerignore), поэтому проверяется
# только код, который попадает в бандл.
#
# Настройки `VITE_*` попадают в собранный бандл и видны в браузере любому,
# поэтому секретов среди них быть не может. Файлы .env в контекст сборки
# не входят (.dockerignore) — значения приходят только аргументами.

# --- сборка -----------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Адрес API относительный: CRM и /api/ отдаются с одного origin (Caddy
# направляет /api/* на шлюз), cookie сессии остаётся first-party, CORS
# не нужен.
ARG VITE_API_URL=/api/v1
ARG VITE_ORGANIZATION_CODE=
ARG VITE_GOOGLE_MAPS_KEY=
# Демо-режим CRM на сервере выключен всегда.
ARG VITE_DEMO_MODE=false
ENV VITE_API_URL=$VITE_API_URL \
    VITE_ORGANIZATION_CODE=$VITE_ORGANIZATION_CODE \
    VITE_GOOGLE_MAPS_KEY=$VITE_GOOGLE_MAPS_KEY \
    VITE_DEMO_MODE=$VITE_DEMO_MODE

# Код организации обязателен: без него форма входа не знает, куда входить.
RUN test -n "$VITE_ORGANIZATION_CODE" \
    && test "$VITE_DEMO_MODE" = "false" \
    && npx tsc --noEmit \
    && npx vite build \
    && test -f dist/index.html \
    && ! find dist -name '*.map' | grep -q .

# --- раздача ----------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html

# Конфигурация раздачи (SPA-маршруты, заголовки безопасности, CSP) лежит
# в deploy/test-server/crm/nginx.conf и монтируется томом.

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/crm-health || exit 1
