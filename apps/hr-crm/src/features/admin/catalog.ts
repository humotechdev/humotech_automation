/**
 * Четыре справочника «Администрирования» и общие правила работы с ними.
 *
 * Причин отсутствия здесь нет: отпуск, больничный и отгул — не то, что
 * компания придумывает сама, и держать их рядом с отделами значило бы
 * предлагать настраивать то, что настраивать не нужно.
 *
 * Правило одно на все: удаляют только то, на что никто не ссылался.
 * Как только значение попало в назначение сотрудника или в заявку, оно
 * перестаёт быть опечаткой и становится частью истории — стереть его
 * значит стереть ответ на вопрос «кем человек работал» или «почему его
 * не было». Такое значение архивируют.
 *
 * Отсюда и разница в подписи кнопки: «Удалить» у неиспользованного,
 * «Архивировать» у остального. Одна кнопка «Удалить», которая на самом
 * деле прячет, обманывает: человек считает, что убрал лишнее, а оно
 * осталось в отчётах.
 */

import type { AppIcon } from '../../components/AppIcon';

export type SectionKey =
  | 'admins'
  | 'departments'
  | 'positions'
  | 'schedules';

export type Section = {
  key: SectionKey;
  title: string;
  /** Одна фраза: что настраивают и где это потом используется. */
  about: string;
  icon: Parameters<typeof AppIcon>[0]['name'];
  /** Подпись кнопки добавления. */
  add: string;
  /** Чем меряется содержимое: «12 отделов». */
  unit: [one: string, few: string, many: string];
  /** Что написать, когда список пуст. */
  empty: string;
};

export const SECTIONS: Section[] = [
  {
    key: 'admins',
    title: 'Администраторы',
    about: 'Кто может входить в CRM',
    icon: 'admin',
    add: 'Добавить администратора',
    unit: ['администратор', 'администратора', 'администраторов'],
    empty: 'Кроме вас, в систему пока никто не входит.',
  },
  {
    key: 'departments',
    title: 'Отделы',
    about: 'Выбираются при добавлении сотрудника',
    icon: 'users',
    add: 'Добавить отдел',
    unit: ['отдел', 'отдела', 'отделов'],
    empty: 'Отделов пока нет. Они понадобятся при добавлении сотрудника.',
  },
  {
    key: 'positions',
    title: 'Должности',
    about: 'Выбираются при добавлении сотрудника',
    icon: 'key',
    add: 'Добавить должность',
    unit: ['должность', 'должности', 'должностей'],
    empty: 'Должностей пока нет. Они понадобятся при добавлении сотрудника.',
  },
  {
    key: 'schedules',
    title: 'Графики работы',
    about: 'Дни и часы работы сотрудника',
    icon: 'clock',
    add: 'Добавить график',
    unit: ['график', 'графика', 'графиков'],
    empty:
      'Графиков пока нет. Без графика система не знает, во сколько у человека '
      + 'начинается день, и не может посчитать опоздание.',
  },
];

/**
 * «1 отдел», «2 отдела», «5 отделов».
 *
 * Русский счёт нельзя свести к «одному или больше»: форма зависит от
 * двух последних цифр, и «11 отдела» читается как ошибка данных.
 */
export function counted(value: number, unit: Section['unit']): string {
  const [one, few, many] = unit;
  const tail = value % 100;
  if (tail >= 11 && tail <= 14) return `${value} ${many}`;
  switch (value % 10) {
    case 1:
      return `${value} ${one}`;
    case 2:
    case 3:
    case 4:
      return `${value} ${few}`;
    default:
      return `${value} ${many}`;
  }
}

/** Дни недели по ISO-8601: 1 — понедельник. */
export const WEEKDAYS: Array<{ n: number; short: string; full: string }> = [
  { n: 1, short: 'Пн', full: 'понедельник' },
  { n: 2, short: 'Вт', full: 'вторник' },
  { n: 3, short: 'Ср', full: 'среда' },
  { n: 4, short: 'Чт', full: 'четверг' },
  { n: 5, short: 'Пт', full: 'пятница' },
  { n: 6, short: 'Сб', full: 'суббота' },
  { n: 7, short: 'Вс', full: 'воскресенье' },
];

/**
 * Роли, которые предлагают при заведении администратора.
 *
 * Три вместо семи из каталога сервера: остальные — служебные и
 * переносные, и раздавать их выбором из списка незачем. Уже выданную
 * роль вне этого набора интерфейс всё равно показывает — иначе у живого
 * администратора роль выглядела бы пустой.
 */
export const OFFERED_ROLES: Array<{ code: string; title: string; about: string }> = [
  {
    code: 'SUPER_ADMIN',
    title: 'Главный администратор',
    about: 'Всё, включая учётные записи и настройки',
  },
  {
    code: 'HR_ADMIN',
    title: 'HR-администратор',
    about: 'Сотрудники, заявки, графики, отчёты',
  },
  {
    code: 'OFFICE_ADMIN',
    title: 'Администратор',
    about: 'Отметки и QR-точки своего офиса',
  },
];

/** Как показать роль: по коду, а не по названию из базы. */
export function roleTitle(code: string, fallback: string): string {
  return OFFERED_ROLES.find((one) => one.code === code)?.title ?? fallback;
}

/** «Пн–Пт 09:00–18:00» — как читают график в списке. */
export function scheduleLine(
  days: Array<{ weekday: number; is_working_day: boolean; start_time: string | null;
                end_time: string | null }>,
): string {
  const working = days
    .filter((day) => day.is_working_day)
    .sort((a, b) => a.weekday - b.weekday);
  if (working.length === 0) return 'рабочих дней нет';

  const names = working.map(
    (day) => WEEKDAYS.find((one) => one.n === day.weekday)?.short ?? '',
  );
  // Подряд идущие дни сворачиваются в диапазон: «Пн–Пт» читается, а
  // «Пн, Вт, Ср, Чт, Пт» занимает всю строку и не читается.
  const numbers = working.map((day) => day.weekday);
  const solid = numbers.every((n, index) => index === 0 || n === numbers[index - 1]! + 1);
  const span = solid && working.length > 1
    ? `${names[0]}–${names[names.length - 1]}`
    : names.join(', ');

  const first = working[0];
  const hours = first?.start_time && first?.end_time
    ? ` ${first.start_time.slice(0, 5)}–${first.end_time.slice(0, 5)}`
    : '';
  return `${span}${hours}`;
}
