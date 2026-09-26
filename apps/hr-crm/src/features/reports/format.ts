/**
 * Как выглядят на экране величины из ответа очереди.
 *
 * Две разные сущности, которые легко смешать в одну колонку:
 *
 * — ДАТЫ ОТЧЁТА — обычные календарные даты без времени и без пояса.
 *   Это то, за что собран файл;
 * — ВРЕМЯ СОЗДАНИЯ — момент, `created_at` из базы, со смещением.
 *   Это то, когда его заказали.
 *
 * Разные форматы у них не для красоты: «01–31 авг 2026» в колонке
 * «Создан» означало бы, что заказ длился месяц.
 */

export const MONTHS_SHORT = [
  'янв', 'фев', 'мар', 'апр', 'мая', 'июн',
  'июл', 'авг', 'сен', 'окт', 'ноя', 'дек',
];

/** «01–31 авг 2026», «12 авг 2026», «01 авг — 03 сен 2026». */
export function spanTitle(first?: string, last?: string): string {
  if (!first || !last) return first ? dayTitle(first) : '—';
  if (first === last) return dayTitle(first);

  const a = parts(first);
  const b = parts(last);
  if (!a || !b) return `${first} — ${last}`;

  if (a.year === b.year && a.month === b.month) {
    return `${pad(a.day)}–${pad(b.day)} ${MONTHS_SHORT[a.month]} ${a.year}`;
  }
  if (a.year === b.year) {
    return `${pad(a.day)} ${MONTHS_SHORT[a.month]} — ${pad(b.day)} ${MONTHS_SHORT[b.month]} ${a.year}`;
  }
  return `${dayTitle(first)} — ${dayTitle(last)}`;
}

export function dayTitle(day: string): string {
  const value = parts(day);
  if (!value) return day;
  return `${pad(value.day)} ${MONTHS_SHORT[value.month]} ${value.year}`;
}

/** Момент заказа: «Сегодня, 10:42» или «06 сен, 18:12». */
export function momentTitle(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const clock = at.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const sameDay =
    at.getFullYear() === now.getFullYear() &&
    at.getMonth() === now.getMonth() &&
    at.getDate() === now.getDate();
  if (sameDay) return `Сегодня, ${clock}`;
  return `${pad(at.getDate())} ${MONTHS_SHORT[at.getMonth()]}, ${clock}`;
}

/**
 * Размер файла — тот, что прислал сервер.
 *
 * `null` означает, что файла нет: он ещё не собран или уже удалён по
 * сроку хранения. Ноль килобайт на этом месте выглядел бы как пустой,
 * но существующий файл.
 */
export function sizeTitle(bytes: number | null): string | null {
  if (bytes === null || bytes === undefined) return null;
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
}

export function parts(day: string) {
  const [year, month, date] = day.split('-').map(Number);
  if (!year || !month || !date) return null;
  return { year, month: month - 1, day: date };
}

export const pad = (value: number) => String(value).padStart(2, '0');
