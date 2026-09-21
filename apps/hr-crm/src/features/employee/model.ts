/**
 * Логика полной карточки сотрудника без разметки.
 *
 * Здесь нет ни одного бизнес-расчёта: время в офисе, опоздание и
 * состояние дня считает сервер, а карточка их только называет. Это не
 * педантизм — у ночной смены, открытой сессии и допуска опоздания
 * должен быть ОДИН ответ, и второй, посчитанный в браузере, рано или
 * поздно разойдётся с дашбордом и с выгрузкой.
 */

import type { DailyRow } from '../../api/crm';

export type Tab = 'overview' | 'attendance' | 'schedule' | 'requests' | 'history';

export const TABS: Array<{ key: Tab; title: string }> = [
  { key: 'overview', title: 'Обзор' },
  { key: 'attendance', title: 'Посещаемость' },
  { key: 'schedule', title: 'График и назначения' },
  { key: 'requests', title: 'Заявки и документы' },
  { key: 'history', title: 'История изменений' },
];

export function isTab(value: string | null): value is Tab {
  return TABS.some((item) => item.key === value);
}

/**
 * Состояние дня словами.
 *
 * «Нет графика», «выходной» и «не пришёл» — три разных ответа, и
 * склеивать их нельзя: без графика сравнивать не с чем, а в выходной
 * человек и не должен был приходить. Обвинение в прогуле остаётся
 * ровно одному состоянию.
 */
export const DAY_STATE: Record<string, string> = {
  IN_OFFICE: 'В офисе',
  LEFT: 'Ушёл',
  NOT_COME: 'Нет отметки',
  DAY_OFF: 'Выходной',
  NO_SCHEDULE: 'Без графика',
  VACATION: 'Отпуск',
  SICK_LEAVE: 'Больничный',
  OTHER_ABSENCE: 'Отсутствие',
};

export function dayState(row: DailyRow): string {
  return row.absence_name ?? DAY_STATE[row.state] ?? row.state;
}

/** «7 ч 45 мин» из секунд. Ноль — это ноль, а отсутствие — прочерк. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds <= 0) return '0 ч';
  // Сначала целые минуты, потом часы: округление остатка давало «8 ч 60 мин».
  const total = Math.round(seconds / 60);
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  if (!hours) return `${minutes} мин`;
  if (!minutes) return `${hours} ч`;
  return `${hours} ч ${minutes} мин`;
}

/** Опоздание. `null` означает «сравнивать не с чем», а не «не опоздал». */
export function lateness(minutes: number | null): string {
  if (minutes === null) return '—';
  if (minutes === 0) return 'Вовремя';
  return `+${minutes} мин`;
}

/** Пустое поле называется вслух, а не оставляет дыру в разметке. */
export function orDash(value: unknown): string {
  if (value === null || value === undefined) return 'Не указано';
  const text = String(value).trim();
  return text || 'Не указано';
}

/** Состояния заявки. Загруженный документ — ещё не подтверждённое отсутствие. */
export const REQUEST_STATUS: Record<string, string> = {
  DRAFT: 'Черновик',
  SUBMITTED: 'На рассмотрении',
  IN_REVIEW: 'На рассмотрении',
  APPROVED: 'Подтверждена',
  REJECTED: 'Отклонена',
  CANCELLED: 'Отменена',
};

export const DOCUMENT_STATUS: Record<string, string> = {
  PENDING: 'Ожидает проверки',
  VERIFIED: 'Проверен',
  REJECTED: 'Отклонён',
};

/** Ждёт ли заявка решения. Только у такой показываются действия. */
export function awaitingDecision(status: string): boolean {
  return status === 'SUBMITTED' || status === 'IN_REVIEW';
}

export const CORRECTION_STATUS: Record<string, string> = {
  PENDING: 'На рассмотрении',
  SUBMITTED: 'На рассмотрении',
  APPROVED: 'Одобрено',
  REJECTED: 'Отклонено',
};

/** Название периода для подписи под показателем. Без него число немо. */
export function periodTitle(first: string, last: string): string {
  return first === last ? first : `${first} — ${last}`;
}

/**
 * Первый и последний день текущего месяца в `YYYY-MM-DD`.
 *
 * Считается по календарю браузера намеренно: это ВЫБОР периода, а не
 * расчёт посещаемости. Границы суток внутри периода сервер всё равно
 * режет по поясу офиса — и только он.
 */
export function currentMonth(today = new Date()): { first: string; last: string } {
  const year = today.getFullYear();
  const month = today.getMonth();
  const pad = (n: number) => String(n).padStart(2, '0');
  const lastDay = new Date(year, month + 1, 0).getDate();
  return {
    first: `${year}-${pad(month + 1)}-01`,
    last: `${year}-${pad(month + 1)}-${pad(lastDay)}`,
  };
}

/** Сегодня в `YYYY-MM-DD` — для показателя «за сегодня». */
export function todayIso(today = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
}

/** Инициалы для аватара. Пустое имя даёт пустой кружок, а не «??». */
export function initials(first?: string | null, last?: string | null): string {
  const letters = [last, first]
    .map((part) => (part ?? '').trim().charAt(0).toUpperCase())
    .filter(Boolean);
  return letters.join('');
}
