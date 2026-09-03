"""Каталог разрешений и их привязка к системным ролям.

Перенесён без изменений: список разрешений — часть контракта данных,
а не деталь реализации. Отсюда берут значения и команда наполнения
справочников, и проверки прав, и документация.

Один источник правды: отсюда берут значения и seed-скрипт, и проверки прав,
и документация. Добавление разрешения — правка этого файла плюс миграция,
которая доливает строки в `permissions` и `role_permissions`.

Разрешение отвечает на вопрос «что можно делать», область
(`user_role_scopes.region_id` / `office_id`) — на вопрос «с чьими данными».
Права проверяет ТОЛЬКО бэкенд; CRM и бот лишь скрывают недоступные кнопки.
"""

from __future__ import annotations

PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    # справочники организации
    ("regions.read", "Просмотр регионов", "Видеть список регионов"),
    ("regions.manage", "Управление регионами", "Создавать и изменять регионы"),
    ("offices.read", "Просмотр офисов", "Видеть офисы и их настройки"),
    ("offices.manage", "Управление офисами", "Создавать и изменять офисы, сети офиса"),
    ("departments.manage", "Управление отделами", "Создавать и изменять отделы"),
    ("positions.manage", "Управление должностями", "Создавать и изменять должности"),
    # сотрудники
    ("employees.read", "Просмотр сотрудников", "Видеть карточки сотрудников"),
    ("employees.manage", "Управление сотрудниками", "Создавать и изменять сотрудников"),
    ("employees.archive", "Архивирование сотрудников", "Переводить в архив"),
    ("employees.access", "Доступ к офисам", "Выдавать доступ к дополнительным офисам"),
    # отметки
    ("attendance.read", "Просмотр отметок", "Видеть события и рабочие сессии"),
    ("attendance.correct", "Исправление отметок", "Рассматривать заявки на корректировку"),
    ("attendance.manual", "Ручная отметка", "Создавать события с source = MANUAL"),
    # QR
    ("qr_points.read", "Просмотр QR-точек", "Видеть QR-точки офиса"),
    ("qr_points.manage", "Управление QR-точками", "Создавать точки и перевыпускать токены"),
    ("qr_display.start", "Запуск экрана QR", "Открывать сессию показа rotating QR"),
    # графики
    ("schedules.read", "Просмотр графиков", "Видеть графики работы"),
    ("schedules.manage", "Управление графиками", "Создавать графики и назначать сотрудникам"),
    ("calendar.manage", "Управление календарём", "Праздники и переносы рабочих дней"),
    # отсутствия
    ("absences.read", "Просмотр отсутствий", "Видеть заявки и подтверждённые периоды"),
    ("absences.approve", "Согласование отсутствий", "Одобрять и отклонять заявки"),
    ("absences.manage_types", "Типы отсутствий", "Настраивать справочник типов"),
    ("absences.documents", "Проверка документов", "Проверять справки и больничные листы"),
    ("leave_balances.manage", "Балансы отпусков", "Начислять и корректировать баланс"),
    # знания и вопросы
    ("knowledge.read", "Чтение базы знаний", "Видеть статьи"),
    ("knowledge.write", "Редактирование статей", "Создавать и изменять статьи"),
    ("knowledge.publish", "Публикация статей", "Утверждать и публиковать статьи"),
    ("knowledge.index", "Индексация знаний", "Запускать переиндексацию источников"),
    ("questions.read", "Просмотр вопросов", "Видеть вопросы сотрудников"),
    ("questions.answer", "Ответы на вопросы", "Отвечать на эскалированные вопросы"),
    # аналитика и администрирование
    ("analytics.read", "Аналитика", "Дашборды и сводные показатели"),
    ("reports.export", "Выгрузка отчётов", "Экспорт данных в файлы"),
    ("users.manage", "Управление пользователями", "Создавать учётные записи CRM"),
    ("roles.manage", "Управление ролями", "Назначать роли и области видимости"),
    ("settings.manage", "Настройки организации", "Изменять organization_settings"),
    ("audit.read", "Просмотр аудита", "Читать журнал изменений"),
    ("ai.metrics.read", "Метрики ассистента", "Журнал обращений и показатели качества"),
)

ALL_PERMISSION_CODES: tuple[str, ...] = tuple(code for code, _, _ in PERMISSIONS)

SYSTEM_ROLES: tuple[tuple[str, str, str], ...] = (
    ("SUPER_ADMIN", "Суперадминистратор", "Полный доступ ко всей организации"),
    ("HR_ADMIN", "HR-администратор", "Все кадровые операции по организации"),
    ("REGIONAL_HR", "Региональный HR", "Кадровые операции в рамках своего региона"),
    ("OFFICE_ADMIN", "Администратор офиса", "Отметки и QR-точки своего офиса"),
    ("MANAGER", "Руководитель", "Отметки и заявки своих подчинённых"),
    ("ACCOUNTANT", "Бухгалтер", "Чтение отметок и выгрузка отчётов"),
    ("TECH_ADMIN", "Технический администратор", "Учётные записи, роли, настройки, аудит"),
)

_HR_FULL = (
    "regions.read", "regions.manage", "offices.read", "offices.manage",
    "departments.manage", "positions.manage",
    "employees.read", "employees.manage", "employees.archive", "employees.access",
    "attendance.read", "attendance.correct", "attendance.manual",
    "qr_points.read", "qr_points.manage",
    "schedules.read", "schedules.manage", "calendar.manage",
    "absences.read", "absences.approve", "absences.manage_types",
    "absences.documents", "leave_balances.manage",
    "knowledge.read", "knowledge.write", "knowledge.publish", "knowledge.index",
    "questions.read", "questions.answer",
    "analytics.read", "reports.export", "ai.metrics.read",
)

# Роль отвечает за НАБОР действий; территорию ограничивает user_role_scopes.
# Поэтому у REGIONAL_HR и OFFICE_ADMIN разрешения похожи на HR — разница
# в области видимости, а не в списке прав.
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "SUPER_ADMIN": ALL_PERMISSION_CODES,
    "HR_ADMIN": _HR_FULL,
    "REGIONAL_HR": (
        "offices.read", "employees.read", "employees.manage", "employees.access",
        "attendance.read", "attendance.correct",
        "qr_points.read",
        "schedules.read", "schedules.manage",
        "absences.read", "absences.approve", "absences.documents",
        "knowledge.read", "questions.read", "questions.answer",
        "analytics.read", "reports.export",
    ),
    "OFFICE_ADMIN": (
        "offices.read", "employees.read",
        "attendance.read", "attendance.correct", "attendance.manual",
        "qr_points.read", "qr_points.manage", "qr_display.start",
        "schedules.read", "absences.read",
        "knowledge.read",
    ),
    "MANAGER": (
        "employees.read", "attendance.read",
        "absences.read", "absences.approve",
        "schedules.read", "knowledge.read", "analytics.read",
    ),
    "ACCOUNTANT": (
        "employees.read", "attendance.read", "absences.read",
        "schedules.read", "analytics.read", "reports.export",
    ),
    "TECH_ADMIN": (
        "users.manage", "roles.manage", "settings.manage", "audit.read",
        "offices.read", "qr_points.read", "qr_points.manage",
        "knowledge.index", "ai.metrics.read",
    ),
}
