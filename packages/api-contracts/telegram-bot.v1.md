# Контракт: backend-api ↔ employee-telegram-bot (v1)

Статус: **draft**. Базовый путь: `{BACKEND_API_URL}/api/v1`.

---

## 1. Модель доступа

Бот **не является границей безопасности**. Права проверяет только бэкенд.

- При привязке аккаунта бэкенд выдаёт **персональный токен сотрудника**.
- Бот хранит токен в привязке к `telegram_id` и шлёт его в каждом запросе:
  `Authorization: Bearer <access_token>`
- Бот **никогда** не передаёт `telegram_id` как признак личности в защищённых
  эндпоинтах — личность определяется токеном.
- Роль (`role`) приходит с сервера и используется ботом **только для отрисовки меню**.
  Если сотрудник каким-то образом вызовет HR-эндпоинт — бэкенд обязан вернуть `403`.

### Роли

| role | Кто | Видит |
|---|---|---|
| `employee` | обычный сотрудник | только свои данные |
| `hr` | HR-менеджер | сотрудников своих офисов/отделов + заявки |
| `admin` | администратор системы | всё |

---

## 2. Формат ошибок

Единый для всех эндпоинтов:

```json
{
  "error": {
    "code": "forbidden",
    "message": "Недостаточно прав для этой операции",
    "details": null
  }
}
```

| HTTP | `code` | Поведение бота |
|---|---|---|
| 400 | `validation_error` | показать `message` пользователю |
| 401 | `invalid_token` / `token_expired` | стереть токен, предложить `/start` заново |
| 403 | `forbidden` | «Недостаточно прав», обновить меню (роль могла измениться) |
| 404 | `not_found` | показать `message` |
| 409 | `conflict` | например «заявка уже обработана» |
| 429 | `rate_limited` | тихий retry с backoff |
| 5xx | `internal_error` | «Сервис временно недоступен» |

---

## 3. Привязка аккаунта

### `POST /auth/telegram/request-code`

Публичный. Сотрудник вводит рабочий телефон, бэкенд шлёт код (SMS/иной канал).

```json
// request
{ "phone": "+992900000000", "telegram_id": 123456789 }
```
```json
// 200
{ "sent": true, "expires_in": 300, "retry_after": 60 }
```
`404 not_found` — телефона нет в базе сотрудников.

### `POST /auth/telegram/link`

Публичный. Подтверждение кода → выдача персонального токена.

```json
// request
{
  "phone": "+992900000000",
  "code": "482913",
  "telegram_id": 123456789,
  "telegram_username": "ivanov"
}
```
```json
// 200
{
  "access_token": "eyJhbGci...",
  "expires_at": "2027-03-01T00:00:00Z",
  "employee": { "id": 42, "full_name": "Иванов Иван", "role": "employee" }
}
```
`400 validation_error` — неверный или просроченный код.

### `POST /auth/telegram/unlink`

Защищённый. Отвязка Telegram от сотрудника (кнопка «Выйти»). `204 No Content`.

---

## 4. Эндпоинты сотрудника — роль: любая

### `GET /employees/me`

Точка, из которой бот строит меню. Роль **всегда** берётся отсюда.

```json
{
  "id": 42,
  "full_name": "Иванов Иван Иванович",
  "role": "employee",
  "position": "Оператор",
  "department": { "id": 3, "name": "Контакт-центр" },
  "office": { "id": 1, "name": "Душанбе, центральный" },
  "region": { "id": 1, "name": "Душанбе" },
  "hired_at": "2024-02-01",
  "photo_url": null,
  "language": "ru"
}
```

### `GET /attendance/my?from=2026-09-01&to=2026-09-30`

Список отметок. Считает и агрегирует **бэкенд**, бот только рисует.

```json
{
  "items": [
    {
      "date": "2026-09-01",
      "check_in": "2026-09-01T08:57:00+05:00",
      "check_out": "2026-09-01T18:03:00+05:00",
      "worked_minutes": 546,
      "late_minutes": 0,
      "status": "ok"
    }
  ],
  "total": 22
}
```

`status`: `ok` | `late` | `early_leave` | `absent` | `no_checkout` | `holiday` | `weekend` | `vacation` | `sick_leave`

### `GET /attendance/my/summary?month=2026-09`

Готовые цифры для экрана «Статистика». Бот **не считает ничего сам**.

```json
{
  "period": { "month": "2026-09", "work_days": 22 },
  "days_present": 20,
  "days_absent": 2,
  "worked_minutes": 10800,
  "norm_minutes": 11616,
  "overtime_minutes": 0,
  "late_count": 3,
  "late_minutes_total": 47,
  "early_leave_count": 1,
  "vacation_days": 0,
  "sick_leave_days": 2
}
```

### `POST /absences/sick-leave`

```json
// request
{
  "date_from": "2026-09-10",
  "date_to": "2026-09-14",
  "comment": "ОРВИ",
  "attachment_file_id": "AgACAgIAAxk..."
}
```
```json
// 201
{ "id": 118, "type": "sick_leave", "status": "pending", "created_at": "..." }
```

`attachment_file_id` — `file_id` из Telegram, необязателен. Скачивает файл бэкенд.

### `POST /absences/vacation`

```json
// request
{ "date_from": "2026-10-01", "date_to": "2026-10-14", "comment": "" }
```
```json
// 201
{ "id": 119, "type": "vacation", "status": "pending", "days": 14, "balance_days_left": 14 }
```
`409 conflict` — пересечение с существующей заявкой или нехватка дней.

### `GET /absences/my?status=pending`

```json
{
  "items": [
    {
      "id": 119, "type": "vacation", "status": "pending",
      "date_from": "2026-10-01", "date_to": "2026-10-14", "days": 14,
      "decided_by": null, "decided_at": null, "reject_reason": null
    }
  ],
  "total": 1
}
```

`status`: `pending` | `approved` | `rejected` | `cancelled`

### `POST /absences/{id}/cancel`

Отмена собственной заявки, пока она `pending`. `200` → объект заявки.

### `POST /attendance/corrections`

Просьба исправить отметку (забыл отметиться, сбой терминала).

```json
// request
{ "date": "2026-09-08", "kind": "check_in", "proposed_time": "09:00",
  "reason": "Терминал не распознал" }
```
```json
// 201
{ "id": 77, "status": "pending" }
```

`kind`: `check_in` | `check_out` | `both`

### `POST /questions`

```json
// request
{ "text": "Когда будет справка о доходах?", "category": "documents" }
```
```json
// 201
{ "id": 501, "status": "open", "created_at": "..." }
```

### `GET /questions/my`

```json
{
  "items": [
    { "id": 501, "text": "...", "status": "answered",
      "answer": "Заберите в отделе кадров", "answered_at": "...", "created_at": "..." }
  ],
  "total": 1
}
```

`status`: `open` | `answered` | `closed`

---

## 5. Эндпоинты HR — роль: `hr` | `admin`

Для `employee` бэкенд обязан возвращать `403 forbidden`.

### `GET /hr/absences/pending?type=&office_id=&limit=20&offset=0`

Очередь заявок на согласование.

```json
{
  "items": [
    {
      "id": 119, "type": "vacation", "status": "pending",
      "employee": {
        "id": 42, "full_name": "Иванов И.И.",
        "position": "Оператор", "office": "Душанбе, центральный"
      },
      "date_from": "2026-10-01", "date_to": "2026-10-14", "days": 14,
      "comment": "", "attachment_url": null,
      "balance_days_left": 14, "created_at": "..."
    }
  ],
  "total": 7
}
```

### `POST /hr/absences/{id}/approve`

```json
// request
{ "comment": "" }
```
```json
// 200
{ "id": 119, "status": "approved", "decided_at": "...", "decided_by": 9 }
```

`409 conflict` — заявку уже обработал другой HR. Важный случай: два HR могут нажать
кнопку одновременно, бот обязан показать «заявку уже обработали» и обновить список.

### `POST /hr/absences/{id}/reject`

```json
// request (reason обязателен)
{ "reason": "Пересечение с отпуском коллеги" }
```
```json
// 200
{ "id": 119, "status": "rejected", "decided_at": "...", "decided_by": 9 }
```

### `GET /hr/attendance/today?office_id=1`

Оперативная сводка «кто на месте».

```json
{
  "date": "2026-09-03",
  "office": { "id": 1, "name": "Душанбе, центральный" },
  "total_employees": 84,
  "present": 71, "late": 6, "absent": 5, "on_vacation": 2, "on_sick_leave": 0,
  "late_list": [
    { "employee_id": 42, "full_name": "Иванов И.И.",
      "check_in": "2026-09-03T09:14:00+05:00", "late_minutes": 14 }
  ]
}
```

### `GET /hr/employees?query=&office_id=&department_id=&limit=20&offset=0`

Поиск сотрудника по ФИО или телефону.

```json
{
  "items": [
    { "id": 42, "full_name": "Иванов Иван Иванович", "position": "Оператор",
      "department": "Контакт-центр", "office": "Душанбе, центральный",
      "phone": "+992900000000", "is_active": true }
  ],
  "total": 1
}
```

### `GET /hr/employees/{id}/summary?month=2026-09`

Та же схема, что `GET /attendance/my/summary`, плюс блок `employee`.

### `GET /hr/corrections/pending` · `POST /hr/corrections/{id}/approve` · `POST /hr/corrections/{id}/reject`

Схемы аналогичны разделу заявок.

### `GET /hr/questions?status=open` · `POST /hr/questions/{id}/answer`

```json
// POST answer request
{ "text": "Справка готова, заберите в отделе кадров" }
```
```json
// 200
{ "id": 501, "status": "answered", "answered_at": "..." }
```

---

## 6. Обратный канал: backend → bot

Бэкенд инициирует отправку сообщения сотруднику. Бот поднимает внутренний HTTP-порт,
доступный только из внутренней сети.

### `POST {BOT_INTERNAL_URL}/internal/notify`

Заголовок `X-Internal-Token: <BOT_INTERNAL_TOKEN>` — общий секрет, **не** токен бота.

```json
{
  "telegram_id": 123456789,
  "event": "absence_approved",
  "title": "Отпуск согласован",
  "body": "Ваша заявка с 01.10 по 14.10 одобрена.",
  "payload": { "absence_id": 119 }
}
```

Ответ `202 Accepted`, доставка асинхронная. Если Telegram вернёт `403` (чат заблокирован),
бот вызывает `POST /internal/telegram-blocked` на бэкенде, чтобы тот пометил привязку
неактивной и перестал слать уведомления в мёртвый чат.

### События (`event`)

`absence_approved`, `absence_rejected`, `correction_approved`, `correction_rejected`,
`question_answered`, `shift_reminder`, `checkout_missing`,
`hr_new_absence_request` (только ролям `hr`/`admin`), `hr_daily_digest`.

---

## 7. Открытые вопросы

- [ ] Канал доставки кода привязки: SMS-провайдер для TJ/UZ не выбран.
- [ ] Срок жизни `access_token`, нужен ли refresh-токен.
- [ ] Часовой пояс: хранить в UTC, отдавать с offset офиса — подтвердить.
- [ ] Языки: `ru` / `tg` / `uz` / `en` — какие реально нужны.
- [ ] Нужен ли HR доступ к зарплатным данным через бота (сейчас — нет).

## История версий

| Версия | Дата | Изменение |
|---|---|---|
| v1 draft | 2026-09-03 | Первая редакция: привязка, сотрудник, HR, обратный канал |
