/**
 * Правила страницы «Уведомления» (вкладка «Лента событий»), вынесенные
 * из разметки, чтобы их можно было проверить без браузера.
 *
 * Здесь только то, что нужно странице: колокольчик живёт своими
 * правилами в `feed-model.ts` и `components/FeedParts.tsx`, и эти
 * файлы страница не трогает.
 *
 * Ни одного значения не придумывается: число берётся из счётчиков
 * ответа, дата — из `created_at` в поясе организации, вид — из `type`
 * или `group`. Незнакомый вид показывается нейтрально, а не роняет
 * страницу.
 */

import type { FeedCounts, FeedEvent } from '../../api/crm';

/** Строк на странице. Сервер режет страницу сам, это только просьба. */
export const FEED_PAGE_SIZE = 20;

/** Сервер не отдаёт больше этого за один запрос (`MAX_PAGE_SIZE`). */
export const FEED_MAX_LIMIT = 200;

/** Сегменты отбора: состояние события. */
export const FEED_SEGMENTS = [
  { key: 'all', title: 'Все' },
  { key: 'unread', title: 'Непрочитанные' },
  { key: 'action', title: 'Требуют действия' },
] as const;

/** Отбор по виду. Ровно три группы, которые знает сервер. */
export const FEED_KINDS = [
  { key: 'requests', title: 'Заявки' },
  { key: 'documents', title: 'Документы' },
  { key: 'questions', title: 'Обращения' },
] as const;

const SCOPES = new Set<string>([
  ...FEED_SEGMENTS.map((one) => one.key),
  ...FEED_KINDS.map((one) => one.key),
]);

/**
 * Отбор, который можно отправить серверу.
 *
 * Сервер понимает ОДИН `scope`: «непрочитанные заявки» он не умеет,
 * и склеивать два отбора по загруженной странице значило бы показывать
 * чужую сумму под видом общей. Поэтому сегмент и вид взаимно
 * исключают друг друга, а неизвестное значение (старое из памяти
 * вкладки) становится «Все».
 */
export function knownScope(value: string | null | undefined): string {
  return value && SCOPES.has(value) ? value : 'all';
}

export const isKindScope = (scope: string): boolean =>
  FEED_KINDS.some((one) => one.key === scope);

/** Сколько всего событий в отборе — из счётчиков сервера, не из страницы. */
export function scopeTotal(scope: string, counts: FeedCounts | null): number | null {
  if (!counts) return null;
  const value = counts[knownScope(scope) as keyof FeedCounts];
  return typeof value === 'number' ? value : null;
}

/** Колонка «Тип» словами. */
const TYPE_LABEL: Record<string, string> = {
  absence_request: 'Заявка',
  sick_leave: 'Заявка',
  absence_cancel: 'Заявка',
  absence_document: 'Документ',
  policy_declined: 'Документ',
  attendance_correction: 'Посещаемость',
  question: 'Обращение',
};

const GROUP_LABEL: Record<string, string> = {
  requests: 'Заявка',
  documents: 'Документ',
  questions: 'Обращение',
};

/**
 * Вид события. Незнакомый вид не роняет строку и не показывается
 * сырым кодом: сначала группа, если она знакома, иначе — «Событие».
 */
export function typeLabel(event: Pick<FeedEvent, 'type' | 'group'>): string {
  return TYPE_LABEL[event.type as string]
    ?? GROUP_LABEL[event.group as string]
    ?? 'Событие';
}

/** Инициалы для кружка: «Мирзаева Лола» → «МЛ». Пустое имя — пусто. */
export function initials(name: string | null | undefined): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean);
  const letters = parts.slice(0, 2).map((part) => Array.from(part)[0] ?? '');
  return letters.join('').toLocaleUpperCase('ru-RU');
}

/** Вторая строка: имя · офис · коротко о событии. Пустое не показывается. */
export function contextLine(event: Pick<FeedEvent, 'employee_name' | 'office_name' | 'short_text'>): string {
  return [event.employee_name, event.office_name, event.short_text]
    .map((part) => (part ?? '').trim())
    .filter(Boolean)
    .join(' · ');
}

function parse(at: string | null | undefined): Date | null {
  if (!at) return null;
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** «25 сентября» в поясе организации. Нет даты — пустая строка, не «сегодня». */
export function dayLabel(at: string, zone: string): string {
  const date = parse(at);
  if (!date) return '';
  try {
    return new Intl.DateTimeFormat('ru-RU', {
      day: 'numeric', month: 'long', ...(zone ? { timeZone: zone } : {}),
    }).format(date);
  } catch {
    return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(date);
  }
}

/** «09:14» в поясе организации. */
export function timeLabel(at: string, zone: string): string {
  const date = parse(at);
  if (!date) return '';
  const options: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' };
  try {
    return new Intl.DateTimeFormat('ru-RU', { ...options, ...(zone ? { timeZone: zone } : {}) }).format(date);
  } catch {
    return new Intl.DateTimeFormat('ru-RU', options).format(date);
  }
}

/**
 * Адрес кнопки «Открыть».
 *
 * Адрес присылает сервер, но переход делает браузер, поэтому он
 * проверяется: только внутренний путь CRM (один ведущий `/`, без
 * схемы, без `//` и `\`), и только в разделы, куда сервер такие
 * ссылки и ставит. Всё прочее — `null`, и кнопка честно выключена.
 */
const ROUTES = ['/requests', '/questions', '/notifications', '/employees', '/onboarding', '/reports'];

export function safeActionPath(url: string | null | undefined): string | null {
  if (typeof url !== 'string') return null;
  const value = url.trim();
  if (!value.startsWith('/') || value.startsWith('//')) return null;
  if (/[\\\s<>"']/.test(value) || value.includes(':')) return null;
  const path = value.split(/[?#]/)[0] ?? '';
  const ok = ROUTES.some((route) => path === route || path.startsWith(`${route}/`));
  return ok ? value : null;
}

/** Номера страниц для пагинации: 1 … 4 5 6 … 12. `null` — многоточие. */
export function pageNumbers(current: number, total: number): (number | null)[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const wanted = new Set([1, total, current - 1, current, current + 1]);
  const list: (number | null)[] = [];
  let last = 0;
  for (let page = 1; page <= total; page += 1) {
    if (!wanted.has(page)) continue;
    if (page - last > 1) list.push(null);
    list.push(page);
    last = page;
  }
  return list;
}

/** Сколько страниц у набора. Без итога — неизвестно. */
export function pageCount(total: number | null, size = FEED_PAGE_SIZE): number | null {
  if (total === null) return null;
  return Math.max(1, Math.ceil(total / size));
}

/** «1–20 из 57 уведомлений». Пусто, пока нечего показывать. */
export function rangeLine(page: number, shown: number, total: number | null, size = FEED_PAGE_SIZE): string {
  if (shown === 0) return '';
  const from = (page - 1) * size + 1;
  const to = from + shown - 1;
  if (total === null) return `${from}–${to}`;
  return `${from}–${to} из ${total} ${plural(total, 'уведомления', 'уведомлений', 'уведомлений')}`;
}

function plural(n: number, one: string, few: string, many: string): string {
  const tens = n % 100;
  if (tens >= 11 && tens <= 14) return many;
  const units = n % 10;
  if (units === 1) return one;
  if (units >= 2 && units <= 4) return few;
  return many;
}
