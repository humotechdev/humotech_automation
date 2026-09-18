/**
 * Правила раздела «Администрирование», отделённые от разметки.
 *
 * Здесь нет ни одного решения, которое принадлежит серверу. Что человеку
 * можно, какие роли ему доступны для выдачи, действует ли назначение —
 * всё это приходит готовым, а этот модуль отвечает только на вопрос
 * «как это назвать по-русски».
 *
 * Одно исключение объявлено явно: различение будущего и закончившегося
 * назначения (`grantPhase`). Само «действует» тоже берётся у сервера —
 * список действующих он отдаёт отдельным запросом, — и клиент лишь
 * раскладывает остальные на «ещё не началось» и «уже закончилось».
 */

import type * as api from '../../api/crm';

/** Показывать ли метку демонстрационных данных. Только по явной настройке. */
export const demoMode = (): boolean =>
  (import.meta.env['VITE_DEMO_MODE'] as string | undefined) === 'true';

// --- учётные записи ---------------------------------------------------------

export const STATUS: Record<string, string> = {
  ACTIVE: 'Активен',
  INACTIVE: 'Неактивен',
  LOCKED: 'Заблокирован',
};

export const STATUS_TABS = [
  { key: '', title: 'Все статусы' },
  { key: 'ACTIVE', title: 'Активные' },
  { key: 'INACTIVE', title: 'Неактивные' },
] as const;

/**
 * Как назвать учётную запись.
 *
 * Имя есть у сотрудника, а не у записи. Технической записи имя не
 * выдумывается: адрес и есть её обозначение, и подставлять его в
 * колонку «имя» значит выдавать одно за другое.
 */
export const userTitle = (user: api.CrmUser): string =>
  user.full_name ?? login(user.email);

/** Логин — то, что человек вводит при входе. Показывается как есть. */
export const login = (email: string): string => email;

/** Короткая подпись под именем: адрес, если он не занял место имени. */
export const userSubtitle = (user: api.CrmUser): string | null =>
  user.full_name ? user.email : null;

export const initials = (title: string): string =>
  title
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');

// --- области ----------------------------------------------------------------

/** Как называется область одного назначения. */
export function scopeTitle(grant: {
  office_name: string | null;
  region_name: string | null;
}): string {
  if (grant.office_name) return grant.office_name;
  if (grant.region_name) return grant.region_name;
  return 'Вся организация';
}

/**
 * Область в строке списка, когда назначений несколько.
 *
 * Несколько областей НЕ схлопываются в «Вся организация»: управление в
 * одном регионе и чтение в другом — это не доступ ко всей организации,
 * и написать так значило бы приписать человеку права, которых у него
 * нет. Показывается первая и число остальных.
 */
export function scopeSummary(grants: api.ShortGrant[]): string {
  if (grants.length === 0) return '—';
  const names = unique(grants.map(scopeTitle));
  if (names.length === 1) return names[0]!;
  return `${names[0]} +${names.length - 1}`;
}

/**
 * Роль в строке списка. Та же логика: первая и счётчик остальных.
 *
 * Одна и та же роль в двух областях считается один раз — это одна роль,
 * а не две; областей при этом по-прежнему две, и их считает `scopeSummary`.
 */
export function roleSummary(grants: api.ShortGrant[]): string {
  if (grants.length === 0) return 'Без роли';
  const names = unique(grants.map((grant) => grant.role_name));
  if (names.length === 1) return names[0]!;
  return `${names[0]} +${names.length - 1}`;
}

const unique = (values: string[]): string[] => [...new Set(values)];

/**
 * Есть ли у записи действующий доступ.
 *
 * Активная учётная запись и наличие прав — разные вещи: человек может
 * войти и не увидеть ничего, если все его назначения закончились.
 * Список действующих назначений отдаёт сервер, поэтому вопрос решается
 * его ответом, а не сравнением дат здесь.
 */
export const hasAccess = (user: api.CrmUser): boolean =>
  user.active_grants.length > 0;

export function accessNote(user: api.CrmUser): string | null {
  if (!user.grants_visible) return null;
  if (user.status !== 'ACTIVE') {
    return 'Учётная запись отключена: войти нельзя.';
  }
  if (!hasAccess(user)) {
    return 'Войти можно, но действующих назначений нет — разделы будут пустыми.';
  }
  return null;
}

// --- назначения -------------------------------------------------------------

export type Phase = 'active' | 'future' | 'ended';

/**
 * Стадия назначения из истории.
 *
 * «Действует» определяется НЕ здесь: множество действующих приходит с
 * сервера отдельным запросом. Остальные раскладываются на «ещё не
 * началось» и «уже закончилось» — это единственное сравнение дат в
 * разделе, и оно ничего не решает про доступ.
 */
export function grantPhase(
  grant: api.Grant,
  activeIds: ReadonlySet<string>,
  now: Date = new Date(),
): Phase {
  if (activeIds.has(grant.id)) return 'active';
  return new Date(grant.valid_from) > now ? 'future' : 'ended';
}

export const PHASE_TITLE: Record<Phase, string> = {
  active: 'Действует',
  future: 'Начнётся позже',
  ended: 'Закончилось',
};

/** Строка срока: «Без ограничения» или дата окончания. */
export const validityTitle = (
  grant: api.Grant,
  format: (at: string) => string,
): string => (grant.valid_to ? `до ${format(grant.valid_to)}` : 'Без ограничения');

/** Почему нельзя выдать роль. Причину называет сервер. */
export function grantBlockedBecause(role: api.RoleFull): string | null {
  if (role.grantable) return null;
  const names = role.missing_permissions.join(', ');
  return `Эту роль нельзя выдать: у вас самих нет прав ${names}.`;
}

// --- разрешения -------------------------------------------------------------

/*
 * Группировка каталога разрешений убрана вместе с интерфейсом ролей:
 * администратор один, и раскладывать права по разделам стало некому и
 * не для кого. Сервер права по-прежнему проверяет — просто показывать
 * их список больше негде.
 */

// --- журнал действий --------------------------------------------------------

/** Человеческие названия действий. Незнакомое показывается кодом. */
export const ACTIONS: Record<string, string> = {
  'user.create': 'Заведена учётная запись',
  'user.update': 'Изменена учётная запись',
  'user.status': 'Изменён статус учётной записи',
  'user.password.set': 'Установлен пароль',
  'role.create': 'Создана роль',
  'role.update': 'Изменена роль',
  'user_role_scope.assign': 'Выдана роль',
  'user_role_scope.revoke': 'Отозвана роль',
  'user_role_scope.validity': 'Изменён срок назначения',
};

export const actionTitle = (action: string): string =>
  ACTIONS[action] ?? action;

/** Название таблицы по-русски: «объект изменения» в журнале. */
export const ENTITIES: Record<string, string> = {
  users: 'Учётная запись',
  roles: 'Роль',
  user_role_scopes: 'Назначение роли',
  employees: 'Сотрудник',
  offices: 'Офис',
  regions: 'Регион',
  notifications: 'Уведомление',
  knowledge_sources: 'Документ базы знаний',
  faq_entries: 'Вопрос-ответ',
  export_jobs: 'Выгрузка',
  telegram_accounts: 'Привязка Telegram',
  telegram_link_invitations: 'Приглашение привязки',
  absence_requests: 'Заявка на отсутствие',
  qr_display_devices: 'Экран показа QR',
  employee_questions: 'Обращение сотрудника',
};

export const entityTitle = (entity: string): string =>
  ENTITIES[entity] ?? entity;

/** Действия раздела — для фильтра журнала. Префиксом, как принимает API. */
export const AUDIT_FILTERS = [
  { key: '', title: 'Все действия' },
  { key: 'user.', title: 'Учётные записи' },
  { key: 'user_role_scope.', title: 'Назначения ролей' },
  { key: 'role.', title: 'Роли' },
  { key: 'employee', title: 'Сотрудники' },
  { key: 'telegram.', title: 'Telegram' },
  // Отбора по материалам ассистента здесь нет: раздела «База знаний» у
  // кадровика не существует, и пункт вёл бы к списку действий над тем,
  // чего он не видит. САМИ записи журнала остаются и показываются в
  // общем списке — стирать историю ради чистоты меню нельзя.
  { key: 'notification.', title: 'Уведомления' },
] as const;

/**
 * Что изменилось: «было → стало» по понятным полям.
 *
 * Значения приходят уже очищенными: `AuditTrail.sanitize` вырезает
 * пароли, токены и хеши при записи. Здесь ничего не расшифровывается
 * обратно — показывается то, что сервер счёл безопасным сохранить.
 */
export type Change = { field: string; before: string; after: string };

const FIELD_TITLE: Record<string, string> = {
  email: 'Адрес',
  status: 'Статус',
  mfa_enabled: 'Двухфакторная проверка',
  employee_id: 'Сотрудник',
  name: 'Название',
  code: 'Код',
  permissions: 'Разрешения',
  role_code: 'Роль',
  // Идентификаторы остаются идентификаторами. Подставить сегодняшнее
  // название по id значило бы приписать прошлой записи нынешнее имя:
  // роль могли переименовать, офис — закрыть.
  role_id: 'Роль',
  user_id: 'Пользователь',
  region_id: 'Регион',
  office_id: 'Офис',
  valid_from: 'Действует с',
  valid_to: 'Срок',
  has_usable_password: 'Пароль',
};

export function changes(
  entry: api.AuditEntry,
  at?: (value: string) => string,
): Change[] {
  const before = entry.old_values ?? {};
  const after = entry.new_values ?? {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  const result: Change[] = [];
  for (const key of keys.sort()) {
    const was = show(before[key], at);
    const now = show(after[key], at);
    if (was === now) continue;
    result.push({ field: FIELD_TITLE[key] ?? key, before: was, after: now });
  }
  return result;
}

/** Похоже ли значение на момент времени в записи журнала. */
const MOMENT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

function show(value: unknown, at?: (value: string) => string): string {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
  if (typeof value === 'boolean') return value ? 'да' : 'нет';
  if (typeof value === 'object') return JSON.stringify(value);
  const text = String(value);
  // Момент времени — в поясе организации, тем же форматом, что и всё
  // остальное время на экране. Своей арифметики над поясами здесь нет:
  // форматирует переданная функция.
  if (at && MOMENT.test(text)) return at(text);
  return STATUS[text] ?? text;
}

/** Честная подпись под списком при курсорной подгрузке. */
export function shownLine(shown: number, hasMore: boolean, total: number | null): string {
  if (shown === 0) return '';
  const word = plural(shown, 'запись', 'записи', 'записей');
  if (total === null) return `Показано ${shown} ${word}`;
  if (!hasMore && shown >= total) return `Показаны все ${shown} ${word}`;
  return `Показано ${shown} ${word} из ${total}`;
}

export function userLine(shown: number, hasMore: boolean, total: number | null): string {
  if (shown === 0) return '';
  const word = plural(shown, 'пользователь', 'пользователя', 'пользователей');
  if (total === null) return `Показано ${shown} ${word}`;
  if (!hasMore && shown >= total) return `Показаны все ${shown} ${word}`;
  return `Показано ${shown} ${word} из ${total}`;
}

function plural(n: number, one: string, few: string, many: string): string {
  const tens = n % 100;
  if (tens >= 11 && tens <= 14) return many;
  const units = n % 10;
  if (units === 1) return one;
  if (units >= 2 && units <= 4) return few;
  return many;
}

/** Что написать в пустой таблице. Причина пустоты у каждого случая своя. */
export function emptyText(search: string, filtered: boolean): string {
  if (search) return 'По этому запросу ничего не нашлось.';
  if (filtered) return 'Под выбранные условия никто не подходит.';
  return 'Учётных записей пока нет. Их заводит администратор.';
}

/**
 * Почему выбранную область отправить нельзя — или `null`, если можно.
 *
 * Варианты в списках приходят с сервера (`/grants/scopes`) и уже
 * проверены его же кодом, поэтому выбранный из списка регион или офис
 * сомнений не вызывает. Выразить в форме можно ровно одно недопустимое
 * состояние: не выбрано ничего. Пустой выбор означает не «пока не
 * решил», а «вся организация» — самое широкое из назначений, и выдать
 * его вправе не каждый.
 *
 * Отказ здесь не заменяет серверный, а объясняет его заранее: узнать
 * причину после нажатия «Назначить» — значит узнать её слишком поздно.
 */
export function scopeHint(
  scopes: { all_organization: boolean; regions: unknown[]; offices: unknown[] },
  region: string,
  office: string,
): string | null {
  if (office || region || scopes.all_organization) return null;
  if (!scopes.regions.length && !scopes.offices.length) {
    return 'Областей, доступных вам для выдачи, нет: ваша собственная '
      + 'область пуста. Роль отсюда не выдать.';
  }
  return 'Доступ на всю организацию выдаёт только тот, чья область — '
    + 'вся организация. Выберите регион или офис.';
}
