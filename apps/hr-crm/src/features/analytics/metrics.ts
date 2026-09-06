/**
 * Арифметика показателей аналитики.
 *
 * Правила, которые здесь закреплены и проверены тестами:
 *
 * — процент считается из СУММ числителей и знаменателей, а не как
 *   среднее процентов офисов: офис на 820 сотрудников-дней и офис на 390
 *   не равны по весу;
 * — нулевой знаменатель даёт `null` — «сравнивать не с чем», а не 0%;
 * — разница процентов измеряется в процентных ПУНКТАХ и знаком «%» не
 *   подписывается;
 * — относительное изменение при нулевой базе не вычисляется: делить на
 *   ноль и показывать «∞» — значит показывать неправду;
 * — округление только для показа. Промежуточные значения не округляются,
 *   иначе ошибка копится от шага к шагу.
 */

import type { Ratio } from '../../api/crm';

/** Доля в процентах или `null`, если знаменателя нет. */
export function share(numerator: number, denominator: number): number | null {
  if (!denominator) return null;
  return (numerator / denominator) * 100;
}

/** Сумма долей: складываем числители и знаменатели, а не проценты. */
export function weighted(parts: { numerator: number; denominator: number }[]): {
  numerator: number;
  denominator: number;
  percent: number | null;
} {
  const numerator = parts.reduce((sum, part) => sum + part.numerator, 0);
  const denominator = parts.reduce((sum, part) => sum + part.denominator, 0);
  return { numerator, denominator, percent: share(numerator, denominator) };
}

/** Разница в процентных пунктах. `null`, если одну из сторон не измерили. */
export function points(left: number | null, right: number | null): number | null {
  if (left === null || right === null) return null;
  return left - right;
}

/**
 * Относительное изменение в процентах.
 *
 * При нулевой базе не определено: «рост с нуля» — это не бесконечность,
 * а отсутствие базы для сравнения.
 */
export function relative(now: number, before: number): number | null {
  if (!before) return null;
  return ((now - before) / before) * 100;
}

const RU = (value: number, digits = 1) =>
  value.toFixed(digits).replace('.', ',');

export function formatPercent(value: number | null, digits = 1): string {
  return value === null ? '—' : `${RU(value, digits)}%`;
}

/** Процентные пункты: со знаком, но БЕЗ знака процента. */
export function formatPoints(value: number | null, digits = 1): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${RU(Math.abs(value), digits)} п.п.`;
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat('ru-RU').format(value);
}

/** Время в часах и минутах. Секунды сюда не выводятся: точность мнимая. */
export function formatSpan(seconds: number | null): string {
  if (seconds === null) return '—';
  const total = Math.round(seconds / 60);
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return hours ? `${hours} ч ${String(minutes).padStart(2, '0')} м` : `${minutes} м`;
}

export function formatMinutes(value: number | null): string {
  if (value === null) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(Math.round(value))} мин`;
}

/** Единица показателя словами — чтобы число нельзя было прочитать неверно. */
export const UNIT_TITLE: Record<string, string> = {
  days: 'сотрудник-дней',
  hours: 'часов',
  people: 'человек',
};

export const findRatio = (ratios: Ratio[], key: string): Ratio | undefined =>
  ratios.find((ratio) => ratio.key === key);

/**
 * Среднее время на сотрудник-день С ЯВКОЙ.
 *
 * Именно на день с явкой, а не «за рабочий день»: делить на ожидаемые дни
 * значило бы размазывать отработанное по дням, когда человека не было.
 */
export function averagePerAttendedDay(
  workedSeconds: number,
  attendedDays: number,
): number | null {
  if (!attendedDays) return null;
  return workedSeconds / attendedDays;
}
