/**
 * Виды отчётов, состояния истории и подписи параметров.
 *
 * Поля видов здесь НЕ перечислены: их отдаёт `/reports/catalog`, и
 * галочка, которую не умеет сервер, появиться на странице не может.
 * Здесь только то, что нужно карточке и строке истории: иконка, короткое
 * пояснение, название по-русски.
 */

import type { AppIconName } from '../../components/AppIcon';
import type * as api from '../../api/crm';
import { MONTHS_SHORT, pad, parts } from './format';

export type KindKey = api.ReportKindKey;

export const KIND_ORDER: KindKey[] = ['attendance', 'worktime', 'lateness', 'absences', 'employees'];

export const KIND_LOOK: Record<KindKey, { title: string; note: string; icon: AppIconName }> = {
  attendance: { title: 'Посещаемость', note: 'Входы, выходы и явка', icon: 'calendar' },
  worktime: { title: 'Рабочее время', note: 'Плановые и фактические часы', icon: 'clock' },
  lateness: { title: 'Опоздания', note: 'Относительно личного графика', icon: 'late' },
  absences: { title: 'Отсутствия', note: 'Отпуска и больничные', icon: 'doc' },
  employees: { title: 'Сотрудники', note: 'Состав и назначения', icon: 'users' },
};

/**
 * Название вида для истории. В очереди лежат и старые заказы — `sessions`
 * и `summary`; строку такого задания надо показать, а не уронить.
 */
export function kindTitle(key: string): string {
  if (key in KIND_LOOK) return KIND_LOOK[key as KindKey].title;
  if (key === 'sessions') return 'Рабочие сессии';
  if (key === 'summary') return 'Сводка по офисам';
  return key;
}

export type DisplayStatus = api.ExportJob['display_status'];

export const STATUS_TITLE: Record<DisplayStatus, string> = {
  QUEUED: 'В очереди',
  RUNNING: 'Формируется',
  SUCCEEDED: 'Готов',
  FAILED: 'Ошибка',
  CANCELLED: 'Отменён',
  EXPIRED: 'Истёк',
};

export const TABS = [
  { key: 'all', title: 'Все', statuses: '' },
  { key: 'ready', title: 'Готовы', statuses: 'SUCCEEDED' },
  { key: 'work', title: 'В работе', statuses: 'QUEUED,RUNNING' },
  { key: 'bad', title: 'С ошибкой', statuses: 'FAILED' },
] as const;

export type TabKey = (typeof TABS)[number]['key'];

export function tabCount(tab: TabKey, counts: api.ExportCounts): number {
  if (tab === 'all') return counts.total;
  if (tab === 'ready') return counts.SUCCEEDED;
  if (tab === 'work') return counts.QUEUED + counts.RUNNING;
  return counts.FAILED;
}

/**
 * Процент готовности — только когда сервер знает знаменатель.
 * У старых заказов `progress_total` пуст, и процента нет вовсе.
 */
export function progressPercent(job: api.ExportJob): number | null {
  if (job.status !== 'RUNNING' || !job.progress_total) return null;
  return Math.max(0, Math.min(99, Math.floor((job.progress_done / job.progress_total) * 100)));
}

/** «Этот месяц» — с первого числа по сегодня, «прошлый» — целиком. */
export function periodRange(mode: 'this_month' | 'last_month', now: string): [string, string] {
  const value = parts(now);
  if (!value) return [now, now];
  if (mode === 'this_month') {
    return [`${value.year}-${pad(value.month + 1)}-01`, now];
  }
  const year = value.month === 0 ? value.year - 1 : value.year;
  const month = value.month === 0 ? 12 : value.month;
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return [`${year}-${pad(month)}-01`, `${year}-${pad(month)}-${pad(last)}`];
}

/** Какой режим у пары дат: совпадает с кнопкой — кнопка горит. */
export function periodMode(from: string, to: string, now: string): api.ReportPeriod {
  const [a, b] = periodRange('this_month', now);
  if (from === a && to === b) return 'this_month';
  const [c, d] = periodRange('last_month', now);
  if (from === c && to === d) return 'last_month';
  return 'custom';
}

const MONTHS_FULL = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

/** Короткий период для строки истории: «Август 2026» или «15 авг — 13 сен». */
export function periodShort(from?: string, to?: string): string {
  const a = from ? parts(from) : null;
  const b = to ? parts(to) : null;
  if (!a || !b) return '—';
  const lastDay = new Date(Date.UTC(b.year, b.month + 1, 0)).getUTCDate();
  if (a.year === b.year && a.month === b.month && a.day === 1 && b.day === lastDay) {
    return `${MONTHS_FULL[a.month]} ${a.year}`;
  }
  if (from === to) return `${a.day} ${MONTHS_SHORT[a.month]} ${a.year}`;
  const tail = a.year === b.year ? '' : ` ${a.year}`;
  const end = a.year === b.year ? '' : ` ${b.year}`;
  return `${a.day} ${MONTHS_SHORT[a.month]}${tail} — ${b.day} ${MONTHS_SHORT[b.month]}${end}`;
}

const MONTHS_GENITIVE = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

/** Длинный период для поля и карточки: «15 августа — 13 сентября 2026». */
export function periodLong(from: string, to: string, withBothYears = false): string {
  const a = parts(from);
  const b = parts(to);
  if (!a || !b) return `${from} — ${to}`;
  const left = `${a.day} ${MONTHS_GENITIVE[a.month]}`;
  const right = `${b.day} ${MONTHS_GENITIVE[b.month]} ${b.year}`;
  if (withBothYears || a.year !== b.year) return `${left} ${a.year} — ${right}`;
  return `${left} — ${right}`;
}

export function plural(count: number, forms: [string, string, string]): string {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return forms[2];
  const last = count % 10;
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

export const number = (value: number) => value.toLocaleString('ru-RU');

export const OFFICES: [string, string, string] = ['офис', 'офиса', 'офисов'];
export const PEOPLE: [string, string, string] = ['сотрудник', 'сотрудника', 'сотрудников'];
export const ROWS: [string, string, string] = ['строка', 'строки', 'строк'];
export const COLUMNS: [string, string, string] = ['столбец', 'столбца', 'столбцов'];

/** Название строки истории: имя файла или вид с периодом. */
export function jobTitle(job: api.ExportJob): string {
  const f = job.filters ?? {};
  const kind = kindTitle(job.kind);
  if (job.title) return job.title;
  if (job.kind === 'employees') {
    return `${kind} • ${f.include_inactive ? 'С неактивными' : 'Активные'}`;
  }
  if (f.date_from || f.date_to) return `${kind} • ${periodShort(f.date_from, f.date_to)}`;
  if (f.date) return `${kind} • ${periodShort(f.date, f.date)}`;
  return kind;
}

/** Главные параметры строки истории: офисы, сотрудник и объём. */
export function jobParams(
  job: api.ExportJob,
  offices: { id: string; name: string }[],
  regions: { id: string; name: string }[],
): string {
  const f = job.filters ?? {};
  const ids = f.office_ids ?? (f.office_id ? [f.office_id] : []);
  const parts: string[] = [];
  if (ids.length === 1) {
    parts.push(offices.find((one) => one.id === ids[0])?.name ?? '1 офис');
  } else if (ids.length > 1) {
    parts.push(`${ids.length} ${plural(ids.length, OFFICES)}`);
  } else if (f.region_id) {
    parts.push(regions.find((one) => one.id === f.region_id)?.name ?? 'Один регион');
  } else {
    parts.push('Все офисы');
  }
  if (f.employee_id) parts.push('1 сотрудник');
  if (job.status === 'SUCCEEDED' && job.total_rows !== null) {
    parts.push(`${number(job.total_rows)} ${plural(job.total_rows, ROWS)}`);
  }
  return parts.join(' • ');
}
