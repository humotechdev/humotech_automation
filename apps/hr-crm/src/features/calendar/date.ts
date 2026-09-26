/**
 * Календарные даты без часовых поясов.
 *
 * Всё считается целыми числами и строками `ГГГГ-ММ-ДД`. `Date` берётся
 * только там, где нужен день недели, и только через `Date.UTC`.
 *
 * Причина жёсткая. `new Date('2026-09-11')` в браузере разбирается как
 * полночь UTC, и в поясе восточнее нуля `getDate()` вернёт уже
 * двенадцатое. Кадровая дата — это число в календаре, а не момент
 * времени: у отпуска, больничного и смены нет часов, и сдвиг на день
 * здесь означает не «неточность отображения», а другой рабочий день.
 */

/** Сколько дней в месяце. Февраль високосного года считается здесь же. */
export function daysInMonth(year: number, month: number): number {
  if (month === 2) {
    const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
    return leap ? 29 : 28;
  }
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/** Существует ли такая дата в календаре. `31.02` — нет. */
export function exists(year: number, month: number, day: number): boolean {
  if (!Number.isInteger(year) || year < 1900 || year > 2200) return false;
  if (!Number.isInteger(month) || month < 1 || month > 12) return false;
  return Number.isInteger(day) && day >= 1 && day <= daysInMonth(year, month);
}

export function iso(year: number, month: number, day: number): string {
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

export function parts(value: string): [number, number, number] | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const [, y, m, d] = match.map(Number) as [number, number, number, number];
  return exists(y, m, d) ? [y, m, d] : null;
}

/** `2026-09-11` → `11.09.2026`. Непонятное значение отдаётся пустым. */
export function toRu(value: string): string {
  const got = parts(value);
  if (!got) return '';
  const [y, m, d] = got;
  return `${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')}.${y}`;
}

export type Parsed =
  | { ok: true; value: string }
  | { ok: false; reason: 'empty' | 'shape' | 'calendar' };

/**
 * Разбор того, что человек напечатал.
 *
 * Точка, косая черта и дефис считаются одним и тем же разделителем:
 * люди набирают дату всеми тремя способами, и спорить с этим незачем.
 * А вот несуществующий день НЕ исправляется на соседний — `31.02.2026`
 * это ошибка, а не третье марта: молчаливая правка меняет смысл того,
 * что человек имел в виду.
 */
export function parseRu(text: string): Parsed {
  const clean = text.trim();
  if (!clean) return { ok: false, reason: 'empty' };
  const match = /^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$/.exec(clean);
  if (!match) return { ok: false, reason: 'shape' };
  const [, d, m, y] = match.map(Number) as [number, number, number, number];
  if (!exists(y, m, d)) return { ok: false, reason: 'calendar' };
  return { ok: true, value: iso(y, m, d) };
}

/**
 * Точки по ходу набора: `11092026` → `11.09.2026`.
 *
 * Только когда курсор в конце строки. Если человек правит середину,
 * подстановка увела бы курсор и испортила набор — тогда текст остаётся
 * как есть, а разбор случится при подтверждении.
 */
export function maskRu(text: string, atEnd: boolean): string {
  if (!atEnd) return text;
  const digits = text.replace(/\D/g, '').slice(0, 8);
  if (digits.length !== text.replace(/[.\-/]/g, '').length) return text;
  const chunks = [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4, 8)];
  return chunks.filter(Boolean).join('.');
}

/** Сдвиг на N дней. Арифметика в UTC, поэтому без сюрпризов перехода. */
export function addDays(value: string, days: number): string {
  const got = parts(value);
  if (!got) return value;
  const [y, m, d] = got;
  const at = new Date(Date.UTC(y, m - 1, d + days));
  return iso(at.getUTCFullYear(), at.getUTCMonth() + 1, at.getUTCDate());
}

/**
 * Сдвиг на N месяцев с прижатием к последнему дню.
 *
 * 31 марта минус месяц — это 28 или 29 февраля, а не 3 марта: у месяцев
 * разная длина, и переполнение здесь означало бы прыжок через месяц.
 */
export function addMonths(value: string, months: number): string {
  const got = parts(value);
  if (!got) return value;
  const [y, m, d] = got;
  const total = (y * 12 + (m - 1)) + months;
  const year = Math.floor(total / 12);
  const month = (total % 12) + 1;
  return iso(year, month, Math.min(d, daysInMonth(year, month)));
}

/** Первое число месяца, в котором лежит дата. */
export function firstOfMonth(value: string): string {
  const got = parts(value);
  if (!got) return value;
  return iso(got[0], got[1], 1);
}

/**
 * Шесть недель подряд, начиная с понедельника.
 *
 * Всегда шесть, даже когда месяц укладывается в пять: иначе высота
 * календаря меняется от месяца к месяцу, и кнопки внизу прыгают под
 * курсором.
 */
export function monthGrid(anchor: string): string[] {
  const got = parts(firstOfMonth(anchor));
  if (!got) return [];
  const [year, month] = got;
  const weekday = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  // getUTCDay: воскресенье это 0. Неделя начинается с понедельника.
  const back = (weekday + 6) % 7;
  const start = addDays(iso(year, month, 1), -back);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

const MONTHS = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

export function monthTitle(value: string): string {
  const got = parts(firstOfMonth(value));
  if (!got) return '';
  return `${MONTHS[got[1] - 1]} ${got[0]}`;
}

export const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/** Тот же месяц, что у опорной даты. */
export function sameMonth(value: string, anchor: string): boolean {
  return value.slice(0, 7) === anchor.slice(0, 7);
}
