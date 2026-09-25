/**
 * Перевод ответов сервера в то, что читает человек.
 *
 * Считает всё сервер. Здесь только оформление: секунды в часы, коды
 * состояний в слова, метки времени — в часовой пояс офиса.
 *
 * Пояс приходит вместе с данными и применяется явно. Без этого браузер
 * показал бы время в поясе телефона: сотрудник в командировке увидел бы
 * свои московские отметки по местному времени и решил, что система врёт.
 */

const DAY_NAMES = [
  'воскресенье', 'понедельник', 'вторник', 'среда',
  'четверг', 'пятница', 'суббота',
];

const MONTHS = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

export const PRESENCE: Record<string, string> = {
  IN_OFFICE: 'В офисе',
  OUTSIDE: 'Вне офиса',
  SICK_LEAVE: 'Больничный',
  VACATION: 'Отпуск',
  OTHER_ABSENCE: 'Отсутствие',
  DAY_OFF: 'Выходной',
  WORKDAY_MISSED: 'Рабочий день без отметок',
};

/**
 * Длина периода в календарных днях, конец включён.
 *
 * Считается здесь, а не на сервере, и это не нарушение правила «клиент
 * ничего не считает»: рабочие дни зависят от графика и праздников —
 * их считает сервер, — а календарные это просто длина отрезка, и
 * второго мнения у неё быть не может.
 */
export function calendarDays(first: string, last: string): number {
  const from = new Date(first);
  const to = new Date(last);
  const days = Math.round((to.getTime() - from.getTime()) / 86400000) + 1;
  return days > 0 ? days : 0;
}

/** «30 календарных дней» — с правильным окончанием. */
export function calendarDaysText(first: string, last: string): string {
  const count = calendarDays(first, last);
  const tail = count % 10;
  const tens = count % 100;
  const word =
    tens >= 11 && tens <= 14
      ? 'календарных дней'
      : tail === 1
        ? 'календарный день'
        : tail >= 2 && tail <= 4
          ? 'календарных дня'
          : 'календарных дней';
  return `${count} ${word}`;
}

/** Секунды в «8 ч 30 мин». Единственная арифметика на клиенте. */
export function duration(seconds: number): string {
  if (!seconds) return '0 мин';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours && minutes) return `${hours} ч ${minutes} мин`;
  if (hours) return `${hours} ч`;
  return `${minutes} мин`;
}

/** Часы и минуты в поясе офиса. */
export function time(iso: string | null, timeZone: string): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone,
  });
}

/** «4 сентября» — без года: история смотрится за месяц, год очевиден. */
export function dayLabel(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

export function weekday(iso: string): string {
  return DAY_NAMES[new Date(`${iso}T00:00:00`).getDay()];
}

/** «01.09 — 30.09» либо одна дата, если период в один день. */
export function period(first: string, last: string): string {
  const short = (iso: string) => {
    const date = new Date(`${iso}T00:00:00`);
    return `${String(date.getDate()).padStart(2, '0')}.${String(
      date.getMonth() + 1,
    ).padStart(2, '0')}`;
  };
  return first === last ? dayLabel(first) : `${short(first)} — ${short(last)}`;
}

export function isoToday(): string {
  const now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('-');
}
