/**
 * Помощники новой карточки сотрудника: даты в поясе офиса и подписи
 * журнала изменений. Расчётов учёта здесь нет — только слова и формат.
 */

import { todayIso } from './model';

const MONTHS_OF = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

/** «в октябре» — для подписи «Отработано в октябре». */
const MONTHS_IN = [
  'январе', 'феврале', 'марте', 'апреле', 'мае', 'июне',
  'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре',
];

/**
 * Сегодня в `YYYY-MM-DD` в поясе офиса.
 *
 * Не по часам браузера: кадровик в другом поясе, открывший карточку
 * после полуночи у себя, видел бы «сегодня» не тот день, который
 * считает сервер. Пояс берётся из текущего назначения сотрудника.
 */
export function todayIn(zone: string | null | undefined, now = new Date()): string {
  if (zone) {
    try {
      const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
      }).formatToParts(now);
      const pick = (type: string) => parts.find((one) => one.type === type)?.value ?? '';
      const day = `${pick('year')}-${pick('month')}-${pick('day')}`;
      if (/^\d{4}-\d{2}-\d{2}$/.test(day)) return day;
    } catch {
      // Неизвестный пояс — не повод падать: ниже — часы браузера.
    }
  }
  return todayIso(now);
}

/** Первое число месяца того же дня. */
export const monthStartOf = (day: string): string => `${day.slice(0, 8)}01`;

function split(day: string): [number, number, number] {
  const [y = 0, m = 1, d = 1] = day.split('-').map(Number);
  return [y, m, d];
}

/** «6 октября». */
export function dayMonth(day: string): string {
  const [, m, d] = split(day);
  return `${d} ${MONTHS_OF[m - 1] ?? ''}`;
}

/** «6 октября 2026». */
export function dayMonthYear(day: string): string {
  return `${dayMonth(day)} ${split(day)[0]}`;
}

/** «1–7 октября», «28 сентября – 4 октября», «30 декабря 2025 – 3 января 2026». */
export function dayRange(first: string, last: string): string {
  if (first === last) return dayMonth(first);
  const [y1, m1, d1] = split(first);
  const [y2, m2, d2] = split(last);
  if (y1 !== y2) return `${dayMonth(first)} ${y1} – ${dayMonth(last)} ${y2}`;
  if (m1 === m2) return `${d1}–${d2} ${MONTHS_OF[m2 - 1] ?? ''}`;
  return `${d1} ${MONTHS_OF[m1 - 1] ?? ''} – ${d2} ${MONTHS_OF[m2 - 1] ?? ''}`;
}

/** «в октябре» по дню. */
export const monthIn = (day: string): string => `в ${MONTHS_IN[split(day)[1] - 1] ?? ''}`;

const WEEKDAY_SHORT = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

/** «7 окт., ср» — строка журнала по дням. */
export function dayCellShort(day: string): string {
  const [, m, d] = split(day);
  const short = (MONTHS_OF[m - 1] ?? '').slice(0, 3);
  const weekday = WEEKDAY_SHORT[new Date(`${day}T12:00:00Z`).getUTCDay()] ?? '';
  return `${d} ${short}., ${weekday}`;
}

const DAY_NAMES = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/** «Пн–Пт, 09:00–18:00» из краткой сводки графика в карточке. */
export function scheduleSummary(schedule: {
  weekdays?: number[]; start_time?: string | null; end_time?: string | null;
} | null | undefined): string | null {
  if (!schedule) return null;
  const days = [...(schedule.weekdays ?? [])].sort((a, b) => a - b);
  let span = '';
  if (days.length) {
    const solid = days.every((one, at) => at === 0 || one === (days[at - 1] ?? 0) + 1);
    span = solid && days.length > 2
      ? `${DAY_NAMES[(days[0] ?? 1) - 1]}–${DAY_NAMES[(days[days.length - 1] ?? 1) - 1]}`
      : days.map((one) => DAY_NAMES[one - 1]).join(', ');
  }
  const hours = schedule.start_time && schedule.end_time
    ? `${schedule.start_time.slice(0, 5)}–${schedule.end_time.slice(0, 5)}` : '';
  return [span, hours].filter(Boolean).join(', ') || null;
}

/** «1 заявка», «2 заявки», «5 заявок». */
export function plural(n: number, one: string, few: string, many: string): string {
  const tens = n % 100;
  const ones = n % 10;
  if (tens >= 11 && tens <= 14) return many;
  if (ones === 1) return one;
  if (ones >= 2 && ones <= 4) return few;
  return many;
}

// --- журнал изменений -------------------------------------------------------

/**
 * Что случилось — словами. Код события — для разбора, не для чтения:
 * основным текстом строки он не показывается.
 */
export const AUDIT_ACTION: Record<string, string> = {
  'employee.create': 'Создана карточка сотрудника',
  'employee.onboard': 'Сотрудник принят на работу',
  'employee.update': 'Изменены сведения о сотруднике',
  'employee.terminate': 'Сотрудник уволен',
  'employee.assignment.change': 'Изменено назначение',
  'employee.schedule.assign': 'Назначен рабочий график',
  'employee.photo.set': 'Обновлена фотография',
  'employee.document.attach': 'Добавлен документ',
  'employee.document.detach': 'Удалён документ',
  'absence.request.create': 'Подана заявка на отсутствие',
  'absence.request.approve': 'Заявка подтверждена',
  'absence.request.reject': 'Заявка отклонена',
  'absence.request.cancel': 'Заявка отменена',
  'absence.request.extend': 'Запрошено продление',
  'absence.request.set_period': 'Проставлены фактические даты',
  'absence.request.application_received': 'Получено подписанное заявление',
  'attendance.event.manual': 'Внесена ручная отметка',
  'attendance.correction.reviewed': 'Рассмотрено исправление отметки',
  'field_work.requested': 'Запрошена выездная работа',
  'field_work.confirmed': 'Выездная работа подтверждена',
  'field_work.cancelled': 'Выездная работа отменена',
  'field_work.cancelled_by_qr': 'Выездная работа снята отметкой в офисе',
  'field_work.expired': 'Запрос выездной работы истёк',
  'telegram.link.consume': 'Сотрудник перешёл по ссылке Telegram',
  'telegram.link.accept_terms': 'Сотрудник принял условия в Telegram',
  'telegram.link.confirm': 'Привязка Telegram подтверждена',
  'telegram.link.reject': 'Привязка Telegram отклонена',
  'telegram.link.disconnect': 'Telegram отключён',
  'telegram.invitation.create': 'Выдана ссылка привязки Telegram',
  'telegram.invitation.revoke': 'Ссылка привязки отозвана',
  'telegram.invitation.confirm': 'Привязка Telegram подтверждена',
  'telegram.invitation.reject': 'Привязка Telegram отклонена',
  'onboarding.enrol': 'Назначено ознакомление',
  'onboarding.invite': 'Отправлено приглашение на ознакомление',
  'onboarding.remind': 'Отправлено напоминание об ознакомлении',
  'onboarding.start': 'Начато ознакомление',
  'onboarding.section.acknowledge': 'Подтверждён раздел ознакомления',
};

/**
 * Неизвестный код — нейтральное название, а не код и не падение: сервер
 * научится новому действию раньше, чем этот словарь.
 */
export function auditTitle(action: string): string {
  return AUDIT_ACTION[action] ?? 'Изменение записи';
}

/** Названия полей в «было / стало». Неизвестное поле — как есть. */
export const AUDIT_FIELD: Record<string, string> = {
  first_name: 'Имя',
  last_name: 'Фамилия',
  middle_name: 'Отчество',
  phone: 'Телефон',
  corporate_email: 'Рабочая почта',
  personal_email: 'Личная почта',
  birth_date: 'Дата рождения',
  hire_date: 'Дата приёма',
  termination_date: 'Дата увольнения',
  termination_reason: 'Причина увольнения',
  employment_status: 'Статус',
  gender: 'Пол',
  marital_status: 'Семейное положение',
  preferred_language: 'Язык',
  employee_id: 'Сотрудник',
  office_id: 'Офис',
  department_id: 'Отдел',
  position_id: 'Должность',
  employment_type: 'Тип занятости',
  work_mode: 'Формат работы',
  valid_from: 'Действует с',
  valid_to: 'Действует по',
  schedule_id: 'График',
  status: 'Состояние',
  first_day: 'Первый день',
  last_day: 'Последний день',
  comment: 'Комментарий',
  review_comment: 'Комментарий к решению',
  telegram_username: 'Имя в Telegram',
};

export const auditField = (key: string): string => AUDIT_FIELD[key] ?? key;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Коды значений словами — по полю, потому что одно слово значит разное:
 * `ACTIVE` у сотрудника — «работает», у привязки — «активна».
 */
const AUDIT_ENUM: Record<string, Record<string, string>> = {
  employment_status: {
    ACTIVE: 'Работает', PENDING: 'Ожидает выхода', ONBOARDING: 'Оформляется',
    ON_LEAVE: 'В отпуске', SUSPENDED: 'Приостановлен', TERMINATED: 'Уволен', ARCHIVED: 'В архиве',
  },
  status: {
    DRAFT: 'Черновик', SUBMITTED: 'На рассмотрении', IN_REVIEW: 'На рассмотрении',
    APPROVED: 'Подтверждена', REJECTED: 'Отклонена', CANCELLED: 'Отменена',
    ACTIVE: 'Активна', PENDING: 'Ожидает', REVOKED: 'Отозвана', USED: 'Использована',
    PENDING_CONFIRMATION: 'Ждёт подтверждения', EXPIRED: 'Истекла',
  },
  employment_type: {
    FULL_TIME: 'Полная занятость', PART_TIME: 'Частичная занятость', CONTRACT: 'Договор',
    INTERN: 'Стажёр (прежний тип)',
  },
  work_mode: { ONSITE: 'В офисе', REMOTE: 'Удалённо', HYBRID: 'Гибрид', FIELD: 'Выездной' },
  gender: { MALE: 'Мужской', FEMALE: 'Женский' },
};

/** Значение поля в журнале: пусто — словами, идентификатор — коротко. */
export function auditValue(value: unknown, field?: string): string {
  if (value === null || value === undefined || value === '') return 'не указано';
  if (field && typeof value === 'string' && AUDIT_ENUM[field]?.[value]) {
    return AUDIT_ENUM[field][value] as string;
  }
  if (typeof value === 'boolean') return value ? 'да' : 'нет';
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return 'значение';
    }
  }
  const text = String(value);
  if (UUID.test(text)) return `запись …${text.slice(-6)}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return dayMonthYear(text);
  return text;
}
