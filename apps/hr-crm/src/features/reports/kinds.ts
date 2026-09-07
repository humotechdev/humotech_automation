/**
 * Виды отчётов: что на карточке и что за этим стоит.
 *
 * Карточка — не название на экране, а конкретный `kind` очереди со
 * своими параметрами и своим правом на данные. Пять названий макета
 * сопоставлены существующим построителям backend:
 *
 * | карточка        | kind        | право           |
 * |-----------------|-------------|-----------------|
 * | Посещаемость    | attendance  | attendance.read |
 * | Время в офисе   | sessions    | attendance.read |
 * | Опоздания       | lateness    | attendance.read |
 * | Отсутствия      | absences    | absences.read   |
 * | Сотрудники      | employees   | employees.read  |
 *
 * Право на данные проверяется здесь не вместо сервера, а до него.
 * Исполнитель очереди считает отказ по правам ОКОНЧАТЕЛЬНЫМ: задание,
 * заказанное без права на данные, не повторяется, а сразу становится
 * FAILED. Предлагать кнопку, которая гарантированно родит красную
 * строку в истории, — это не «пусть сервер решает», это обман.
 */

export type KindKey = 'attendance' | 'sessions' | 'lateness' | 'absences' | 'employees';

export type Kind = {
  key: KindKey;
  title: string;
  note: string;
  icon: 'calendar' | 'clock' | 'late' | 'doc' | 'users';
  /** Право на сами данные. `reports.export` требуется сверх него всегда. */
  needs: string;
  /**
   * Берёт ли отчёт период. `employees` не берёт: состав отдаётся
   * ТЕКУЩИЙ, исторического среза «на дату» у него нет, и предлагать
   * выбор даты значило бы обещать срез, которого никто не соберёт.
   */
  period: boolean;
};

export const KINDS: Kind[] = [
  {
    key: 'attendance',
    title: 'Посещаемость',
    note: 'Входы, выходы и явка',
    icon: 'calendar',
    needs: 'attendance.read',
    period: true,
  },
  {
    key: 'sessions',
    title: 'Время в офисе',
    note: 'Часы и сессии',
    icon: 'clock',
    needs: 'attendance.read',
    period: true,
  },
  {
    key: 'lateness',
    title: 'Опоздания',
    note: 'По данным графика',
    icon: 'late',
    needs: 'attendance.read',
    period: true,
  },
  {
    key: 'absences',
    title: 'Отсутствия',
    note: 'Отпуска и больничные',
    icon: 'doc',
    needs: 'absences.read',
    period: true,
  },
  {
    key: 'employees',
    title: 'Сотрудники',
    note: 'Состав и назначения',
    icon: 'users',
    needs: 'employees.read',
    period: false,
  },
];

/**
 * Название вида для истории.
 *
 * В очереди могут лежать задания и других видов — `summary` собирается
 * тем же механизмом, но карточки на этой странице у него нет. Строку
 * такого задания надо показать, а не спрятать и не уронить на ней
 * страницу.
 */
export function kindTitle(key: string): string {
  const known = KINDS.find((kind) => kind.key === key);
  if (known) return known.title;
  if (key === 'summary') return 'Сводка по офисам';
  return key;
}

/** Сколько дней помещается в один отчёт. Тот же предел, что у backend. */
export const MAX_PERIOD_DAYS = 366;

/**
 * Как называется состояние задания по-русски.
 *
 * Пять состояний backend, и ни одного придуманного. «Формируется» —
 * это RUNNING; числа рядом с ним нет, потому что сервер его не
 * присылает.
 */
export const STATUS_TITLE: Record<string, string> = {
  QUEUED: 'В очереди',
  RUNNING: 'Формируется',
  SUCCEEDED: 'Готов',
  FAILED: 'Ошибка',
  CANCELLED: 'Отменено',
};

/** Вкладки истории. «Все» включает и отменённые — они тоже существуют. */
export const TABS = [
  { key: 'all', title: 'Все', statuses: '' },
  { key: 'ready', title: 'Готовы', statuses: 'SUCCEEDED' },
  { key: 'work', title: 'В работе', statuses: 'QUEUED,RUNNING' },
  { key: 'bad', title: 'С ошибкой', statuses: 'FAILED' },
] as const;

export type TabKey = (typeof TABS)[number]['key'];

export function tabCount(
  tab: TabKey,
  counts: { total: number; QUEUED: number; RUNNING: number; SUCCEEDED: number; FAILED: number },
): number {
  if (tab === 'all') return counts.total;
  if (tab === 'ready') return counts.SUCCEEDED;
  if (tab === 'work') return counts.QUEUED + counts.RUNNING;
  return counts.FAILED;
}
