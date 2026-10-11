# Рабочая копия: редизайн страницы «Уведомления» (HR CRM)

- **Путь:** `D:\HUMO-notifications-ui` (git worktree основного репозитория)
- **Ветка:** `feature/crm-notifications-redesign`, ответвлена от `origin/main` (e074e27)
- **Задача:** страница «Уведомления» по последнему образцу: одна карточка,
  вкладки «Лента событий» / «Очередь отправки», таблица событий с серверной
  пагинацией. Это страница событий и уведомлений, а не чат.
- **Что меняется:** только страница и её локальные части:
  `apps/hr-crm/src/pages/NotificationsPage.tsx`,
  `apps/hr-crm/src/features/notifications/{FeedBoard.tsx,PageCard.tsx,feed-page.ts}`,
  `apps/hr-crm/src/styles/notifications.css`, тест
  `apps/hr-crm/tests/notifications-page.test.tsx`, предпросмотр
  `apps/hr-crm/preview-notifications.html` + `src/dev/notifications-preview.tsx`,
  снимки в `docs/notifications-redesign/`.
  Оболочку CRM, колокольчик (`FeedParts.tsx`, `nf__*` в `crm.css`), другие
  страницы, бэкенд, бот и Mini App эта ветка не трогает.

## Отдельная сессия Claude Code

Работайте в этой папке отдельной сессией, не из `D:\HUMO`:

```
cd D:\HUMO-notifications-ui
claude
```

Все git-команды — только для этой папки: `git -C D:/HUMO-notifications-ui ...`.
Другие рабочие копии (`D:\HUMO`, `D:\HUMO-miniapp-ui`, `D:\HUMO-crm-ui`,
`D:\HUMO-admin-ui` и др.) принадлежат другим задачам.

## Запуск CRM из этой копии

Контейнер `humotech_crm` монтирует основную копию `D:\HUMO` — для этой ветки
его не использовать и не перезапускать.

1. Проверить занятые порты (на 11.10.2026 заняты как минимум 5174 и 5181):

   ```
   netstat -ano | findstr LISTENING | findstr ":51"
   docker ps --format "{{.Names}} {{.Ports}}"
   ```

2. Взять свободный порт, например 5191, и поднять dev-сервер в одноразовом
   контейнере (анонимный том для `node_modules` обязателен — иначе Linux-модули
   попадут в папку Windows):

   ```
   MSYS_NO_PATHCONV=1 docker run --rm -it --name ntf-ui-dev --memory=1500m ^
     -e NODE_OPTIONS=--max-old-space-size=1200 ^
     -e VITE_BACKEND_ORIGIN=http://host.docker.internal:8000 ^
     -p 5191:5191 ^
     -v "D:/HUMO-notifications-ui/apps/hr-crm:/app" -v /app/node_modules -w /app ^
     node:22-alpine sh -c "npm ci --no-audit --no-fund && npx vite --host 0.0.0.0 --port 5191"
   ```

   - `http://localhost:5191/` — CRM (вход на стенд выполняет человек);
   - `http://localhost:5191/preview-notifications.html` — страница на подставных
     данных без входа и без сервера; `?view=delivery` — вкладка очереди.

   Bind-mount с Windows не передаёт события изменения файлов: после правок
   перезапустите контейнер.

3. Проверки (только связанные файлы, по одному контейнеру за раз):

   ```
   MSYS_NO_PATHCONV=1 docker run --rm --memory=1500m -e NODE_OPTIONS=--max-old-space-size=1200 ^
     -v "D:/HUMO-notifications-ui/apps/hr-crm:/app" -v /app/node_modules -w /app node:22-alpine ^
     sh -c "npm ci --no-audit --no-fund && npx tsc --noEmit -p . && npx vitest run tests/notifications-page.test.tsx tests/notifications.test.tsx tests/notification-feed.test.tsx --pool=forks --poolOptions.forks.singleFork=true"
   ```

## Правило слияния

Ветку **не сливать** в `main` самостоятельно: слияние, выкладка и проверка на
стенде — решение владельца после просмотра. Сравнение:
https://github.com/humotechdev/humotech_automation/compare/main...feature/crm-notifications-redesign?expand=1
