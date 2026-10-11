# Рабочая копия: «Администрирование» — premium UI и движение

| | |
|---|---|
| Путь | `D:\HUMO-admin-ui` (git worktree основного репозитория) |
| Ветка | `feature/admin-premium-ui-motion` (от `origin/main` e074e27) |
| Приложение | `apps/hr-crm` (React + Vite) |
| Сравнение | https://github.com/humotechdev/humotech_automation/compare/main...feature/admin-premium-ui-motion?expand=1 |

Задача: «Сохрани существующий дизайн HUMOTECH, доведи его до premium-качества и
добавь продуманные микроанимации, не меняя бизнес-логику.» Меняется только
фронтенд раздела «Администрирование». Backend, API, база, миграции, Mini App,
«Обращения» и «Заявки» не затрагиваются.

## Правило: без слияния

Ветку **не сливать** в `main` самостоятельно. Только push этой ветки и
ревью через страницу сравнения выше. Все git-команды — через
`git -C D:\HUMO-admin-ui …`. Чужие рабочие копии (`D:\HUMO`, `D:\HUMO-crm-ui`,
`D:\HUMO-miniapp-ui`, `D:\HUMO-fw`, `D:\HUMO-main`, `D:\HUMO-sec-base`) не трогать.

## Отдельная сессия Claude Code

```
cd D:\HUMO-admin-ui
claude
```

## Запуск CRM из этой копии

Контейнер `humotech_crm` монтирует **основную** копию (`D:\HUMO`), не эту, и
останавливать его нельзя. Порты 5174 (`humotech_crm`) и 5181
(`humotech_crm_logincheck`) заняты. Перед запуском проверьте:
`netstat -ano | findstr LISTENING | findstr :519` и
`docker ps --format "{{.Names}} {{.Image}}"`.

### Предпросмотр без сервера и без входа (рекомендуется)

`preview-administration.html` рендерит настоящий `App` на подставных данных
(`src/dev/administration-preview.tsx`, в сборку не попадает):

```
MSYS_NO_PATHCONV=1 docker run -d --name admin-ui-dev --memory=1200m \
  -p 127.0.0.1:5191:5191 \
  -v "D:/HUMO-admin-ui/apps/hr-crm:/app" -v /app/node_modules -w /app \
  node:22-alpine sh -c "npm ci --no-audit --no-fund && npx vite --port 5191 --host 0.0.0.0 --strictPort"
```

Открыть: http://localhost:5191/preview-administration.html
(`?open=departments|positions|schedules` — вкладка; `?fail=1` — сервер
отказывает при сохранении; сохранение отвечает через ~0,9 с, чтобы было
видно «Сохраняем…»). Остановить: `docker rm -f admin-ui-dev`.

Bind-mount с Windows не всегда отдаёт события изменения файлов: после правки
перезапустите контейнер (`docker restart admin-ui-dev`).

### С настоящим API

Dev-сервер проксирует `/api` на `VITE_BACKEND_ORIGIN` (по умолчанию
`http://127.0.0.1:8000`, см. `vite.config.ts`), клиент ходит на
`VITE_API_URL` (по умолчанию `/api/v1`). Для работы со стендом запустите
контейнер в сети стенда и укажите шлюз:
`--network docker_humotech -e VITE_BACKEND_ORIGIN=http://gateway`, порт —
свободный (например 5191). Вход в стенд выполняет человек.

## Проверки

Только в контейнере, по одному, с ограничением памяти:

```
MSYS_NO_PATHCONV=1 docker run --rm --memory=1500m -e NODE_OPTIONS=--max-old-space-size=1200 \
  -v "D:/HUMO-admin-ui/apps/hr-crm:/app" -v /app/node_modules -w /app node:22-alpine \
  sh -c "npm ci --no-audit --no-fund && npx tsc --noEmit -p . && npx vitest run tests/admin.test.tsx tests/admin-motion.test.tsx --pool=forks --poolOptions.forks.singleFork=true"
```

## Возможные конфликты при слиянии — нужна координация

- `apps/hr-crm/src/pages/AdministrationPage.tsx` переписан (блоки → вкладки и
  таблицы). Основная ветка аудита добавит в форму графика поля «действует с
  даты» (версии графиков) — `ScheduleForm` в этом файле. Сама форма здесь
  почти не менялась (только кнопка сохранения без прыжка ширины), но слияние
  придётся сводить вручную.
- Коммит `ca51a05` в `security/fix-2026-10-11` меняет тот же
  `AdministrationPage.tsx` и `tests/admin.test.tsx` (владелец графика,
  `can_manage=false` → «Изменить»/«Удалить» неактивны). При слиянии это
  правило нужно перенести в новые `RowTools`/`ScheduleLine`.
- `tests/admin.test.tsx`: часть проверок адаптирована под вкладки
  (`getByRole('tab')`, `?open=…`), бизнес-утверждения не менялись.
