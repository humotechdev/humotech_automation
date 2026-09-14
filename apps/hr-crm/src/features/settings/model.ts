/**
 * Логика страницы настроек без разметки.
 *
 * Главное разделение здесь — между СОХРАНЁННЫМ и ЧЕРНОВИКОМ. Это не
 * стилистика: панель «есть несохранённые изменения» обязана появляться
 * от фактического отличия, а не от того, что человек поставил курсор в
 * поле. Одна копия значений сделала бы эти два случая неразличимыми.
 *
 * Отправляются только изменённые поля. Слать группу целиком значило бы
 * записывать поверх соседа то, чего не трогали: два администратора,
 * открывшие страницу одновременно, отменяли бы правки друг друга даже
 * в разных полях.
 */

import type { SettingSection } from '../../api/crm';
import type { AppIconName } from '../../components/AppIcon';

/** Значения группы: то, что показано в полях и что уйдёт на сервер. */
export type Draft = Record<string, unknown>;

export type Tab =
  | 'org'
  | 'attendance'
  | 'requests'
  | 'notifications'
  | 'integrations'
  | 'me';

export const TABS: Array<{ key: Tab; title: string; icon: AppIconName }> = [
  { key: 'org', title: 'Организация', icon: 'building' },
  { key: 'attendance', title: 'Учёт посещаемости', icon: 'clock' },
  { key: 'requests', title: 'Заявки и документы', icon: 'doc' },
  { key: 'notifications', title: 'Уведомления', icon: 'bell' },
  { key: 'integrations', title: 'Подключения', icon: 'globe' },
];

/** Личный раздел стоит за разделителем: он не про организацию. */
export const PERSONAL: { key: Tab; title: string; icon: AppIconName } = {
  key: 'me', title: 'Мои предпочтения', icon: 'users',
};

export const TAB_SUBTITLE: Record<Tab, string> = {
  org: 'Основные сведения, дата и время',
  attendance: 'Где какое правило живёт и кто его меняет',
  requests: 'Правила отсутствий и требования к вложениям',
  notifications: 'Каналы доставки и журнал отправок',
  integrations: 'Что настроено и что из этого подтверждено',
  me: 'Действует только для вас',
};

/** Ключ группы настроек на сервере. У раздела без своей группы — `null`. */
export const SECTION_OF: Partial<Record<Tab, string>> = {
  org: 'organization.defaults',
  requests: 'absences.policy',
};

export function isTab(value: string | null): value is Tab {
  return (
    value === 'org' || value === 'attendance' || value === 'requests'
    || value === 'notifications' || value === 'integrations' || value === 'me'
  );
}

/** Найти группу по ключу. */
export function sectionOf(
  items: SettingSection[],
  key: string | undefined,
): SettingSection | null {
  if (!key) return null;
  return items.find((item) => item.key === key) ?? null;
}

/**
 * Поля, которыми черновик отличается от сохранённого.
 *
 * Сравнение через `JSON.stringify` намеренно: значения приходят из
 * JSONB и бывают списками (`allowed_document_types`), а `!==` на двух
 * одинаковых списках всегда истинно.
 */
export function changedFields(saved: Draft, draft: Draft): string[] {
  const keys = new Set([...Object.keys(saved), ...Object.keys(draft)]);
  return [...keys].filter(
    (key) => JSON.stringify(saved[key] ?? null) !== JSON.stringify(draft[key] ?? null),
  );
}

/** Только изменённое — то, что действительно уйдёт на сервер. */
export function changedOnly(saved: Draft, draft: Draft): Draft {
  const result: Draft = {};
  for (const key of changedFields(saved, draft)) result[key] = draft[key] ?? null;
  return result;
}

/**
 * Список поясов из самого браузера, а не зашитый в код.
 *
 * Зашитый список устаревает: пояса переименовывают и заводят новые.
 * Сервер всё равно проверяет значение через `zoneinfo`, поэтому здесь
 * нужен не источник правды, а удобный выбор.
 */
export function timezones(current: string | null): string[] {
  const known: string[] = [];
  const supported = (
    Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
  ).supportedValuesOf;
  if (typeof supported === 'function') {
    try {
      known.push(...supported.call(Intl, 'timeZone'));
    } catch {
      // Старый браузер. Ниже остаётся хотя бы текущее значение.
    }
  }
  if (current && !known.includes(current)) known.unshift(current);
  return known;
}

/** «Asia/Dushanbe — UTC+05:00». Смещение считает браузер, а не таблица в коде. */
export function zoneLabel(name: string): string {
  if (!name) return '';
  try {
    const parts = new Intl.DateTimeFormat('ru-RU', {
      timeZone: name,
      timeZoneName: 'longOffset',
    }).formatToParts(new Date());
    const offset = parts.find((part) => part.type === 'timeZoneName')?.value;
    return offset ? `${name} — ${offset}` : name;
  } catch {
    return name;
  }
}

/** Человеческое имя MIME-типа. Незнакомый показывается как есть. */
const DOCUMENT_TITLES: Record<string, string> = {
  'application/pdf': 'PDF',
  'image/jpeg': 'JPEG',
  'image/png': 'PNG',
};

export function documentTitle(mime: string): string {
  return DOCUMENT_TITLES[mime] ?? mime;
}

/** «10 МБ» из числа байт. Ноль и отрицательное — прочерк. */
export function megabytes(bytes: unknown): string {
  const value = typeof bytes === 'number' ? bytes : Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '—';
  return `${Math.round((value / (1024 * 1024)) * 10) / 10} МБ`;
}

/** Что показать про состояние подключения. Цветом не различаем — словом. */
export const STATE_TITLE: Record<string, string> = {
  working: 'Работает',
  unknown: 'Не удалось проверить',
  off: 'Выключено',
};

export function stateTitle(state: string): string {
  return STATE_TITLE[state] ?? 'Не удалось проверить';
}

/**
 * Демонстрационный режим. Отсюда бейдж «Демо-данные» берут
 * «Администрирование» и «Настройки» — страницы, с которых меняют
 * организацию. На обзорной главной его нет: там он ничего не защищает.
 *
 * Признак берётся из сборки, а не угадывается по данным: организация с
 * кодом DEMO в бою — обычная организация, и объявлять её ненастоящей
 * значило бы врать про её данные.
 */
export function demoMode(): boolean {
  return (import.meta.env['VITE_DEMO_MODE'] as string | undefined) === 'true';
}
