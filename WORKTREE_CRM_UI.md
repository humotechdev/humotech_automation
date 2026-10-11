# Рабочая копия: CRM — вкладки, «Проверка HR», движение переписки

| | |
|---|---|
| Путь | `D:\HUMO-crm-ui` (git worktree основного репозитория) |
| Ветка | `feature/crm-ui-chat-motion` (от `origin/main` `e074e27`) |
| Что внутри | только `apps/hr-crm` (React + Vite); backend, бот, Mini App, инфраструктура не менялись |
| Сравнение | https://github.com/humotechdev/humotech_automation/compare/main...feature/crm-ui-chat-motion?expand=1 |

## Правило слияния

**Ветку не сливать в `main` без ревью владельца.** Ни merge, ни push в
другие ветки из этой копии не делать. Изменения проверяются через
сравнение по ссылке выше.

Остальные рабочие копии (`D:\HUMO`, `D:\HUMO-miniapp-ui`, `D:\HUMO-fw`,
`D:\HUMO-main`, `D:\HUMO-sec-base`) — чужие задачи: в них отсюда ничего не
переключать, не коммитить и не прятать. Все команды git — с `-C`:

```sh
git -C /d/HUMO-crm-ui status
git -C /d/HUMO-crm-ui diff --stat origin/main -- apps/hr-crm
```

## Отдельная сессия Claude Code

```powershell
cd D:\HUMO-crm-ui
claude
```

Сессия, открытая в этой папке, видит только эту рабочую копию и эту ветку.

## Запуск CRM из этой копии

Контейнер `humotech_crm` (порт 5174) смотрит в `D:\HUMO\apps\hr-crm`, то есть
в ДРУГУЮ копию: правки этой ветки он не покажет. Его, туннели
(`humotech_crm_tunnel5174`, `…5181`) и `humotech_crm_logincheck` (5181) не
останавливать.

Занятые порты на момент записи: 5174, 5181, 5433, 80/443, 18080/18443,
19080/19443. Перед запуском проверить ещё раз:

```sh
docker ps --format '{{.Names}} {{.Ports}}'
netstat -ano | findstr LISTENING | findstr :519
```

Ниже — порт **5190** (на момент записи свободен).

### Вариант 1. Страницы-предпросмотры, без сервера и входа

Сеть подменена внутри страницы, данные подставные. Подходит для проверки
вёрстки и движения переписки.

```sh
MSYS_NO_PATHCONV=1 docker run --rm -it --name crmui_dev --memory=1500m \
  -e NODE_OPTIONS=--max-old-space-size=1200 \
  -v "D:/HUMO-crm-ui/apps/hr-crm:/app" -v /app/node_modules -w /app \
  -p 127.0.0.1:5190:5190 node:22-alpine \
  sh -c "npm ci --no-audit --no-fund && npx vite --port 5190 --host 0.0.0.0 --strictPort"
```

Пустой том `-v /app/node_modules` обязателен: он закрывает Windows-сборку
зависимостей на диске, и `npm ci` её не портит.

Открыть:

- «Обращения» — http://127.0.0.1:5190/preview-questions.html
- «Заявки» — http://127.0.0.1:5190/preview-requests.html
- больничный, «Проверка HR» — http://127.0.0.1:5190/preview-requests.html?path=/requests/r-1
- отпуск — http://127.0.0.1:5190/preview-requests.html?path=/requests/r-3

В предпросмотре «Обращений» ответ не отправляется (сети нет). Анимацию
нового сообщения и прокрутку честнее смотреть на стенде (вариант 2).

### Вариант 2. С настоящим backend стенда

Dev-сервер проксирует `/api` на `VITE_BACKEND_ORIGIN`. Значение по
умолчанию в `vite.config.ts` (`http://127.0.0.1:8000`) изнутри контейнера
не работает. Как у `humotech_crm`: сеть `docker_humotech`, шлюз `http://gateway`.

```sh
MSYS_NO_PATHCONV=1 docker run --rm -it --name crmui_dev --memory=1500m \
  -e NODE_OPTIONS=--max-old-space-size=1200 \
  -e VITE_BACKEND_ORIGIN=http://gateway \
  -e VITE_ORGANIZATION_CODE=DEMO \
  --network docker_humotech \
  -v "D:/HUMO-crm-ui/apps/hr-crm:/app" -v /app/node_modules -w /app \
  -p 127.0.0.1:5190:5190 node:22-alpine \
  sh -c "npm ci --no-audit --no-fund && npx vite --port 5190 --host 0.0.0.0 --strictPort"
```

Открыть http://localhost:5190/ и войти учётной записью HR стенда (вход —
только руками). Cookie `Secure` dev-сервер снимает сам (см. `vite.config.ts`),
поэтому вход по `http://localhost` работает.

Через bind-mount с Windows Vite не видит правок (inotify не доходит):
после изменения файлов перезапустить контейнер `crmui_dev`.

## Проверки

Тесты и типы — только в контейнере и по одному. Сначала убедиться, что не
идёт чужой одноразовый `node:`-контейнер:

```sh
docker ps --format '{{.Names}} {{.Image}}'
MSYS_NO_PATHCONV=1 docker run --rm --memory=1500m -e NODE_OPTIONS=--max-old-space-size=1200 \
  -v "D:/HUMO-crm-ui/apps/hr-crm:/app" -v /app/node_modules -w /app node:22-alpine \
  sh -c "npm ci --no-audit --no-fund && npx tsc --noEmit -p . && npx vitest run \
    tests/questions-chat.test.tsx tests/questions.test.tsx tests/requests.test.tsx tests/request-page.test.tsx \
    --pool=forks --poolOptions.forks.singleFork=true"
```

Весь набор CRM за раз может не уложиться в память: запускайте нужные файлы.
