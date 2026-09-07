/**
 * Время в часовом поясе организации — одним способом на всю CRM.
 *
 * Сервер отдаёт моменты в UTC (`TIME_ZONE = "UTC"`, `USE_TZ = True`) и
 * отдельно называет пояс, в котором их надо читать. Значит, перевод —
 * работа клиента, и делать её надо ровно одним способом.
 *
 * Способов, которыми это делать НЕЛЬЗЯ, ровно два, и оба встречались:
 *
 *   — взять подстроку ISO-строки (`at.slice(11, 16)`). Это показывает
 *     UTC под подписью «Asia/Dushanbe», то есть врёт на пять часов и
 *     выглядит правдоподобно;
 *   — прибавить фиксированное смещение. Это врёт в тот день, когда пояс
 *     переводит стрелки, и врёт в каждом другом поясе.
 *
 * Здесь всё считает `Intl.DateTimeFormat` с явным `timeZone`. Он знает
 * правила переходов, и его результат не зависит от пояса машины, на
 * которой открыта страница.
 */

const MONTHS_SHORT = [
  'янв', 'фев', 'мар', 'апр', 'мая', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
];

/** Момент времени: часы и минуты, при `sameDay: false` — ещё и дата. */
export function moment(at: string, zone: string, sameDay: boolean): string {
  const date = parse(at);
  if (!date) return '—';
  const options: Intl.DateTimeFormatOptions = {
    hour: '2-digit',
    minute: '2-digit',
    ...(sameDay ? {} : { day: '2-digit', month: 'short' }),
    ...(zone ? { timeZone: zone } : {}),
  };
  return new Intl.DateTimeFormat('ru-RU', options).format(date);
}

/** Только часы и минуты. */
export function clock(at: string | null, zone: string): string {
  return at ? moment(at, zone, true) : '—';
}

/**
 * Часы и минуты — и дата, если момент пришёлся на соседний день.
 *
 * Ночная смена заканчивается завтра. «08:15» и «08:15» в одной строке,
 * где первое — вчерашний вход, а второе — сегодняшний выход, читаются
 * как одно и то же время, и разница в сутки пропадает бесследно.
 *
 * `day` — календарный день строки в том же поясе, в формате `YYYY-MM-DD`
 * (его отдаёт сервер). Если день совпадает, приписка не появляется:
 * лишняя дата в каждой ячейке мешает не меньше, чем её отсутствие
 * в редкой.
 */
export function clockOnDay(
  at: string | null,
  zone: string,
  day: string,
): string {
  if (!at) return '—';
  const time = moment(at, zone, true);
  const actual = dayInZone(at, zone);
  if (!day || !actual || actual === day) return time;
  return `${time} (${shortDate(at, zone)})`;
}

/** Календарный день момента в указанном поясе, `YYYY-MM-DD`. */
export function dayInZone(at: string, zone: string): string {
  const date = parse(at);
  if (!date) return '';
  const parts = new Intl.DateTimeFormat('ru-RU', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    ...(zone ? { timeZone: zone } : {}),
  }).formatToParts(date);
  const get = (type: string) =>
    parts.find((part) => part.type === type)?.value ?? '';
  const year = get('year');
  const month = get('month');
  const dayOfMonth = get('day');
  return year && month && dayOfMonth ? `${year}-${month}-${dayOfMonth}` : '';
}

/** «8 сен» — короткая дата в поясе организации. */
export function shortDate(at: string, zone: string): string {
  const iso = dayInZone(at, zone);
  if (!iso) return '';
  const [, month, day] = iso.split('-');
  return `${Number(day)} ${MONTHS_SHORT[Number(month) - 1]}`;
}

function parse(at: string): Date | null {
  if (!at) return null;
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? null : date;
}
