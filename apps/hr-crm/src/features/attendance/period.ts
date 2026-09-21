/**
 * Период на странице посещаемости: день, неделя, месяц или произвольный.
 *
 * Отдельный модуль, потому что границы периода считаются в двух местах —
 * в адресе страницы и в запросе к серверу, — и посчитанные по-разному
 * они дают расхождение, которое видно только глазом: на экране «неделя»,
 * а в выгрузке восемь дней.
 *
 * Неделя начинается с понедельника. Это не вкусовщина: рабочая неделя в
 * Узбекистане понедельник–пятница, и неделя, начатая с воскресенья,
 * разрезала бы выходные пополам.
 */

export type PeriodKind = 'day' | 'week' | 'month' | 'range';

export const PERIODS: Array<{ value: PeriodKind; label: string }> = [
  { value: 'day', label: 'День' },
  { value: 'week', label: 'Неделя' },
  { value: 'month', label: 'Месяц' },
  { value: 'range', label: 'Период' },
];

export type Span = { from: string; to: string };

/** `YYYY-MM-DD` без часовых поясов: дата — это дата, а не момент. */
export function iso(value: Date): string {
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${value.getFullYear()}-${month}-${day}`;
}

function parse(value: string): Date {
  const [year, month, day] = value.split('-').map(Number);
  // Полдень, а не полночь: при переходе на летнее время полночь в
  // некоторых поясах не существует, и `new Date` уезжает на сутки.
  return new Date(year ?? 1970, (month ?? 1) - 1, day ?? 1, 12);
}

function shift(value: Date, days: number): Date {
  const copy = new Date(value);
  copy.setDate(copy.getDate() + days);
  return copy;
}

/**
 * Границы периода вокруг выбранного дня.
 *
 * `range` возвращает то, что выбрал человек: произвольный период
 * подразумевает, что границы задаёт он, а не мы.
 */
export function spanOf(
  kind: PeriodKind,
  day: string,
  // Не `Partial<Span>`: при `exactOptionalPropertyTypes` это разные
  // вещи — «ключа нет» и «ключ есть со значением undefined», а из
  // адреса страницы приходит второе.
  custom?: { from?: string | undefined; to?: string | undefined },
): Span {
  const anchor = parse(day);

  if (kind === 'day') return { from: day, to: day };

  if (kind === 'week') {
    // `getDay()` считает воскресенье нулём — сдвигаем к понедельнику.
    const weekday = (anchor.getDay() + 6) % 7;
    const first = shift(anchor, -weekday);
    return { from: iso(first), to: iso(shift(first, 6)) };
  }

  if (kind === 'month') {
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1, 12);
    const last = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0, 12);
    return { from: iso(first), to: iso(last) };
  }

  const from = custom?.from || day;
  const to = custom?.to || day;
  // Переставленные границы — обычная опечатка, а не повод показать
  // пустой экран: меняем местами и считаем то, что человек имел в виду.
  return from <= to ? { from, to } : { from: to, to: from };
}

/** «12 сентября» или «1–7 сентября 2026» — как период читают вслух. */
export function spanTitle(kind: PeriodKind, span: Span): string {
  const first = parse(span.from);
  const last = parse(span.to);
  const months = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
  ];

  if (span.from === span.to) {
    return `${first.getDate()} ${months[first.getMonth()]} ${first.getFullYear()}`;
  }
  if (kind === 'month') {
    return `${months[first.getMonth()]} ${first.getFullYear()}`;
  }
  if (first.getMonth() === last.getMonth() && first.getFullYear() === last.getFullYear()) {
    return `${first.getDate()}–${last.getDate()} ${months[first.getMonth()]} `
      + `${first.getFullYear()}`;
  }
  return `${first.getDate()} ${months[first.getMonth()]} — `
    + `${last.getDate()} ${months[last.getMonth()]} ${last.getFullYear()}`;
}

/** Сколько дней в периоде, включительно. */
export function lengthOf(span: Span): number {
  const from = parse(span.from).getTime();
  const to = parse(span.to).getTime();
  return Math.round((to - from) / 86_400_000) + 1;
}

export function isPeriodKind(value: string | null): value is PeriodKind {
  return value === 'day' || value === 'week' || value === 'month' || value === 'range';
}
