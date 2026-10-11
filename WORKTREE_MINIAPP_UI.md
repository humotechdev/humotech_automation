# Отдельный worktree: интерфейс Mini App

Отдельная рабочая копия репозитория для UI-задач Telegram Mini App
сотрудника. Здесь не трогают backend, CRM, бота и инфраструктуру.

- Путь: `D:\HUMO-miniapp-ui` (в Git Bash — `/d/HUMO-miniapp-ui`)
- Ветка: `feature/miniapp-tab-transitions`, создана от `origin/main` (e074e27)
- Mini App: `apps/employee-mini-app`
- Первая задача ветки: переходы между вкладками —
  `apps/employee-mini-app/docs/TAB_TRANSITIONS.md`

Рядом живут другие worktree (`D:\HUMO` — аудит, ветка
`security/fix-2026-10-11`; `D:\HUMO-fw`, `D:\HUMO-main`, `D:\HUMO-sec-base`).
Из этой копии их не трогаем, git-команды — только с `-C`:

```bash
git -C /d/HUMO-miniapp-ui status
```

## Правило веток

- **Никогда не сливать в `main` и в `security/*`** — ни merge, ни push.
- Пушится только своя ветка:
  `git -C /d/HUMO-miniapp-ui push -u origin feature/miniapp-tab-transitions`.
- PR открывается вручную:
  https://github.com/humotechdev/humotech_automation/compare/main...feature/miniapp-tab-transitions?expand=1
  Решение о слиянии — за владельцем.

## Отдельная сессия Claude Code

```powershell
cd D:\HUMO-miniapp-ui
claude
```

Сессия будет видеть только эту копию и её ветку.

## Проверки (тесты, типы, сборка)

На хосте `node_modules` нет; гоняем в одноразовом контейнере с лимитом памяти.
Анонимный том `-v /app/node_modules` обязателен — он не даёт Linux-модулям
попасть в рабочую копию на Windows.

```bash
MSYS_NO_PATHCONV=1 docker run --rm --memory=1200m \
  -v "D:/HUMO-miniapp-ui/apps/employee-mini-app:/app" -v /app/node_modules \
  -w /app node:22-alpine sh -c \
  "npm ci --no-audit --no-fund && npx tsc --noEmit && npx vitest run --pool=forks --poolOptions.forks.singleFork=true && npx vite build"
```

После — `docker ps`, чтобы ничего не осталось запущенным. Сборка кладёт
`dist/` в папку приложения (в `.gitignore`).

## Dev-сервер

Порт по умолчанию в `vite.config.ts` — 5174, но он **занят** (контейнер
`humotech_crm` и туннель). Для этой копии — **5190** (на 11.10 свободен;
перед запуском проверить `netstat -ano | findstr :5190`).

```bash
MSYS_NO_PATHCONV=1 docker run --rm --name miniapp-ui-dev --memory=1200m \
  -p 127.0.0.1:5190:5190 \
  -e CHOKIDAR_USEPOLLING=true \
  -e VITE_BACKEND_ORIGIN=http://host.docker.internal:8000 \
  -v "D:/HUMO-miniapp-ui/apps/employee-mini-app:/app" -v /app/node_modules \
  -w /app node:22-alpine sh -c \
  "npm ci --no-audit --no-fund && npx vite --host 0.0.0.0 --port 5190 --strictPort"
```

Открыть http://127.0.0.1:5190. Остановить: `docker stop miniapp-ui-dev`.

- `CHOKIDAR_USEPOLLING=true` — bind-mount с Windows не отдаёт inotify, без
  опроса Vite не видит правок.
- `VITE_BACKEND_ORIGIN` — куда Vite проксирует `/api` (по умолчанию
  `http://127.0.0.1:8000`). `VITE_API_URL` менять не нужно: по умолчанию
  `/api/v1` через этот прокси.
- **Мок-режима нет.** Вне Telegram у приложения нет подписи запуска
  (`initData`), и оно останавливается на экране «Откройте кнопкой в чате».
  Кабинет с вкладками открывается только из Telegram (через HTTPS-туннель,
  хост туннеля — в `VITE_ALLOWED_HOSTS`) и с работающим backend. Опубликованных
  на хост портов backend у текущих стендов нет — для живой проверки нужен
  отдельный стенд; рабочий стенд, бота и реальные данные отсюда не трогаем.
- Без Telegram переходы проверяются тестами (`tests/tab-transition.test.tsx`).
