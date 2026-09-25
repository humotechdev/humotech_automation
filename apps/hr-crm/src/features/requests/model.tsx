/**
 * Общий язык очереди заявок и страницы одной заявки.
 *
 * Обе смотрят на один и тот же объект с сервера и обязаны называть его
 * одними словами. Пока эти функции жили внутри страницы очереди,
 * страница заявки могла только повторить их — а повторённое правило
 * однажды расходится с оригиналом, и одно состояние получает два имени.
 *
 * Здесь нет ни одного решения о том, что можно делать с заявкой: это
 * решает сервер и присылает готовым (`stage`, `missing_for_approval`).
 * Здесь только перевод на русский.
 */

import { useState } from 'react';

import * as api from '../../api/crm';
import { AppIcon, type AppIconName } from '../../components/AppIcon';
import { initials } from '../../components/AppShell';
import { formatTime } from '../dashboard/data';

export const MONTHS = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];
const MONTHS_SHORT = [
  'янв', 'фев', 'мар', 'апр', 'мая', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
];

export const STEP_TITLE: Record<string, string> = {
  CREATED: 'Заявка создана',
  SUBMITTED: 'Заявка создана',
  TAKEN_IN_REVIEW: 'Взята на рассмотрение',
  APPROVED: 'Заявка подтверждена',
  REJECTED: 'Заявка отклонена',
  CANCELLED: 'Заявка отменена',
  DOCUMENT_ATTACHED: 'Справка загружена',
  DOCUMENT_VERIFIED: 'Справка принята',
  DOCUMENT_REJECTED: 'Справка отклонена',
  PERIOD_SET: 'Проставлены фактические даты',
  MARKS_OVERRIDDEN: 'Утверждено поверх отметок',
  COMMENTED: 'Комментарий',
};

/** Шаги, которые в истории читаются как отказ, а не как обычный ход. */
export const BAD_STEPS = new Set(['REJECTED', 'DOCUMENT_REJECTED', 'CANCELLED']);

/**
 * Стадии больничного. Сотрудник видит эти же слова в приложении:
 * называть одно состояние по-разному значит начинать каждый разговор
 * с выяснения, кто что имел в виду.
 */
export const STAGE: Record<string, { title: string; tone: string }> = {
  WAITING_DOCUMENTS: { title: 'Ожидаем документы', tone: 'orange' },
  HR_REVIEW: { title: 'На проверке HR', tone: 'orange' },
  NEEDS_FIX: { title: 'Нужны исправления', tone: 'red' },
};

export function personOf(item: api.QueueItem) {
  return item.absence?.employee ?? item.correction?.employee ?? null;
}

export function kindOf(
  item: api.QueueItem,
): { title: string; icon: AppIconName; tone: string } {
  if (item.kind === 'correction') {
    return { title: 'Исправление отметок', icon: 'pencil', tone: 'blue' };
  }
  if (item.absence?.kind === 'CANCEL') {
    return { title: 'Отмена заявки', icon: 'cross', tone: 'red' };
  }
  const code = item.absence?.absence_type?.code;
  if (code === 'SICK_LEAVE') return { title: 'Больничный', icon: 'doc', tone: 'blue' };
  if (code === 'ANNUAL_LEAVE') return { title: 'Отпуск', icon: 'send', tone: 'blue' };
  return {
    title: item.absence?.absence_type?.name ?? 'Отсутствие',
    icon: 'calendar',
    tone: 'blue',
  };
}

export function statusOf(item: api.QueueItem): { title: string; tone: string } {
  const status = item.absence?.status ?? item.correction?.status ?? '';
  if (status === 'SUBMITTED' || status === 'IN_REVIEW') {
    if (item.absence?.kind === 'CANCEL') {
      return { title: 'Отмена запрошена', tone: 'grey' };
    }
    const stage = item.absence?.stage;
    if (stage && STAGE[stage]) return STAGE[stage];
    return { title: 'На согласовании', tone: 'orange' };
  }
  if (status === 'APPROVED') return { title: 'Подтверждён', tone: 'green' };
  if (status === 'REJECTED') return { title: 'Отклонён', tone: 'red' };
  if (status === 'CANCELLED') return { title: 'Отменён', tone: 'grey' };
  if (status === 'DRAFT') return { title: 'Черновик', tone: 'grey' };
  return { title: status || '—', tone: 'grey' };
}

/** Заявка ещё в работе: по ней можно принять решение. */
export function isOpen(item: api.QueueItem): boolean {
  const status = item.absence?.status ?? item.correction?.status ?? '';
  return status === 'SUBMITTED' || status === 'IN_REVIEW';
}

/** Заявка закрыта окончательно: бумаг у неё больше нет, только история. */
export function isClosed(item: api.QueueItem): boolean {
  const status = item.absence?.status ?? item.correction?.status ?? '';
  return status === 'REJECTED' || status === 'CANCELLED';
}

/**
 * Последняя справка сотрудника.
 *
 * Системный бланк заявления лежит в той же таблице и справкой не
 * считается. Берётся последняя загруженная: прежние остаются в истории
 * и никуда не деваются, но проверяют всегда новую.
 */
/**
 * Справка, по которой сейчас идёт проверка.
 *
 * Бумаг у заявки бывает несколько: отклонённая из неё не пропадает, а
 * сотрудник приносит замену. Берётся не последняя в списке, а самая
 * сильная — тем же правилом, что и на сервере:
 *
 *   принятая    — вопрос закрыт;
 *   непроверенная — замену уже принесли, ждут кадровика;
 *   отклонённая — остаётся, только если другой бумаги нет.
 *
 * Порядку в списке верить нельзя: он приходит от базы, и страница
 * показывала старый отказ поверх свежей справки — «нужна новая
 * версия» там, где новая версия уже лежала.
 */
const CERTIFICATE_ORDER = ['VERIFIED', 'PENDING', 'REJECTED'];

function certificateRank(status: string): number {
  const at = CERTIFICATE_ORDER.indexOf(status);
  return at === -1 ? CERTIFICATE_ORDER.length : at;
}

export function certificateOf(item: api.QueueItem) {
  const rows = (item.absence?.documents ?? []).filter(
    (one) => one.document_type !== 'APPLICATION',
  );
  if (!rows.length) return null;
  return [...rows].sort((left, right) => {
    const by = certificateRank(left.verification_status)
      - certificateRank(right.verification_status);
    if (by !== 0) return by;
    // Среди равных — свежая: справку меняют не по одному разу.
    return Date.parse(right.file.uploaded_at) - Date.parse(left.file.uploaded_at);
  })[0];
}

/** Системный бланк заявления, если он уже собран. */
export function applicationOf(item: api.QueueItem) {
  return (item.absence?.documents ?? []).find(
    (one) => one.document_type === 'APPLICATION',
  ) ?? null;
}

/**
 * Состояние справки словами. «Загружена» и «принята» — разные вещи:
 * документ не считается проверенным только потому, что он есть.
 */
export function certificateState(
  item: api.QueueItem,
): { title: string; tone: string } {
  if (!item.absence?.requires_document) return { title: '—', tone: 'grey' };
  const paper = certificateOf(item);
  if (!paper) return { title: 'Не приложена', tone: 'orange' };
  if (paper.verification_status === 'VERIFIED') {
    return { title: 'Принята', tone: 'green' };
  }
  if (paper.verification_status === 'REJECTED') {
    return { title: 'Нужна новая версия', tone: 'red' };
  }
  return { title: 'Требует решения', tone: 'orange' };
}

export function periodOf(
  item: api.QueueItem,
): { text: string; long: string; days: number | null } {
  const first = item.absence?.first_day;
  const last = item.absence?.last_day ?? first;
  if (first && last) {
    const days =
      Math.round(
        (Date.parse(`${last}T12:00:00Z`) - Date.parse(`${first}T12:00:00Z`))
          / 86_400_000,
      ) + 1;
    return { text: range(first, last), long: range(first, last), days };
  }
  const at =
    item.correction?.requested_entry_at ?? item.correction?.requested_exit_at ?? null;
  if (at) {
    const day = at.slice(0, 10);
    return { text: dayLong(day), long: dayLong(day), days: 1 };
  }
  return { text: '—', long: '—', days: null };
}

export function submittedOf(item: api.QueueItem, short: boolean): string {
  const at =
    item.absence?.submitted_at ?? item.correction?.submitted_at ?? item.created_at;
  return at ? dateTime(at, short) : '—';
}

/** Чего не хватает — человеческим перечислением. */
export function missingText(missing: string[]): string {
  const names: Record<string, string> = {
    certificate: 'принята справка',
    application: 'отмечено заявление',
    period: 'проставлен период',
  };
  return missing.map((one) => names[one] ?? one).join(', ');
}

// --- даты и размеры ------------------------------------------------------------

function parts(day: string): [number, number, number] {
  const [y = 0, m = 1, d = 1] = day.split('-').map(Number);
  return [y, m, d];
}

export function dayLong(day: string): string {
  const [y, m, d] = parts(day);
  return `${String(d).padStart(2, '0')} ${MONTHS[m - 1] ?? ''} ${y}`;
}

/** «09 – 13 сентября 2026» или «28 сентября – 02 октября 2026». */
export function range(first: string, last: string): string {
  const [y1, m1, d1] = parts(first);
  const [y2, m2, d2] = parts(last);
  if (first === last) return dayLong(first);
  const a = String(d1).padStart(2, '0');
  const b = String(d2).padStart(2, '0');
  if (y1 === y2 && m1 === m2) return `${a} – ${b} ${MONTHS[m2 - 1] ?? ''} ${y2}`;
  if (y1 === y2) {
    return `${a} ${MONTHS[m1 - 1] ?? ''} – ${b} ${MONTHS[m2 - 1] ?? ''} ${y2}`;
  }
  return `${dayLong(first)} – ${dayLong(last)}`;
}

/** «8 сен 2026, 18:42» (кратко) или «8 сентября 2026, 18:42». */
export function dateTime(at: string, short = false): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return '—';
  const month = (short ? MONTHS_SHORT : MONTHS)[date.getMonth()] ?? '';
  return `${date.getDate()} ${month} ${date.getFullYear()}, ${formatTime(date)}`;
}

/** «21 сентября, 11:24» — без года: очередь живёт сегодняшним днём. */
export function dayTime(at: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return '—';
  return `${date.getDate()} ${MONTHS[date.getMonth()] ?? ''}, ${formatTime(date)}`;
}

export function daysWord(n: number): string {
  if (n % 10 === 1 && n % 100 !== 11) return 'день';
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return 'дня';
  return 'дней';
}

export function calendarDaysWord(n: number): string {
  if (n % 10 === 1 && n % 100 !== 11) return 'календарный день';
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) {
    return 'календарных дня';
  }
  return 'календарных дней';
}

export function waitingWord(n: number): string {
  return n % 10 === 1 && n % 100 !== 11 ? 'требует' : 'требуют';
}

export function fileType(mime: string): string {
  if (mime === 'application/pdf') return 'PDF';
  if (mime.startsWith('image/')) {
    return mime.slice(6).toUpperCase().replace('JPEG', 'JPG');
  }
  return 'Файл';
}

export const size = (bytes: number) =>
  bytes < 1024 * 1024
    ? `${Math.round(bytes / 1024)} КБ`
    : `${(Math.round((bytes / 1024 / 1024) * 10) / 10).toString().replace('.', ',')} МБ`;

export function shortName(full: string | null | undefined): string {
  return (full ?? '').split(' ').slice(0, 2).join(' ') || '—';
}

// --- мелкие общие части интерфейса ---------------------------------------------

export function Face({ id, name, className }: {
  id: string;
  name: string;
  className: string;
}) {
  const [broken, setBroken] = useState(!id);
  if (broken) {
    return (
      <span className={`rq-face rq-face--none ${className}`} aria-hidden="true">
        {initials(name)}
      </span>
    );
  }
  return (
    <img
      className={`rq-face ${className}`}
      src={api.employeePhotoUrl(id)}
      alt=""
      onError={() => setBroken(true)}
      onLoad={(event) => {
        if (event.currentTarget.naturalWidth < 32) setBroken(true);
      }}
    />
  );
}

/** Строка файла: тип, имя, вес, дата и кнопка скачать. */
export function FileRow({ tone, title, note, href, download, action }: {
  tone: 'blue' | 'red';
  title: string;
  note: string;
  href: string;
  download?: string;
  action: string;
}) {
  return (
    <div className="rq-file">
      <span className={`rq-file__type rq-file__type--${tone}`} aria-hidden="true">
        <AppIcon name="doc" size={20} />
      </span>
      <span className="rq-file__text">
        <b>{title}</b>
        <small>{note}</small>
      </span>
      <a className="rq-file__get" href={href} download={download} target="_blank" rel="noreferrer">
        <AppIcon name="download" size={18} />
        {action}
      </a>
    </div>
  );
}
