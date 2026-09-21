/**
 * Расчёты вкладки «Посещаемость». Ни одного запроса — только разбор
 * того, что уже прислал сервер.
 *
 * Здесь нет ни одного правила учёта. Время в офисе, опоздание, границы
 * суток и состояние дня считает backend: у ночной смены, открытой
 * сессии и допуска опоздания должен быть один ответ, а не второй,
 * посчитанный в браузере. Этот модуль переводит готовые числа в
 * координаты точек и высоты столбцов — и только.
 *
 * Единственное, что считается здесь, — средние и распределения по
 * времени прихода. Их сервер не отдаёт, а складываются они из тех же
 * `first_entry_at` и `last_exit_at`, которые он уже посчитал сам.
 */

import type { DailyRow, ScheduleDay, WorkScheduleDetail } from '../../api/crm';

/** Состояния, в которых рабочего дня не было. Ложную линию по ним не ведут. */
export const OFF_STATES = new Set([
  'DAY_OFF',
  'NO_SCHEDULE',
  'VACATION',
  'SICK_LEAVE',
  'OTHER_ABSENCE',
]);

/** Минуты от полуночи в поясе офиса. `null` — момента нет. */
export function minutesInZone(at: string | null, zone: string): number | null {
  if (!at) return null;
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return null;
  const text = new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    ...(zone ? { timeZone: zone } : {}),
  }).format(date);
  const [hours, mins] = text.split(':').map((part) => Number.parseInt(part, 10));
  if (Number.isNaN(hours as number) || Number.isNaN(mins as number)) return null;
  return (hours as number) * 60 + (mins as number);
}

/** «09:07» из минут. Минус и переполнение суток сюда не приходят. */
export function hhmm(minutes: number | null): string {
  if (minutes === null) return '—';
  const whole = Math.max(0, Math.round(minutes));
  const h = Math.floor(whole / 60) % 24;
  const m = whole % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** «HH:MM:SS» или «HH:MM» из графика — в минуты от полуночи. */
export function fromClock(value: string | null | undefined): number | null {
  if (!value) return null;
  const [h, m] = value.split(':').map((part) => Number.parseInt(part, 10));
  if (Number.isNaN(h as number) || Number.isNaN(m as number)) return null;
  return (h as number) * 60 + (m as number);
}

/** День недели 1–7 (понедельник первый) для даты `ГГГГ-ММ-ДД`. */
export function weekdayOf(day: string): number {
  const date = new Date(`${day}T00:00:00`);
  const js = date.getDay();
  return js === 0 ? 7 : js;
}

/** План на один день: когда начинать, когда заканчивать, сколько норма. */
export type Plan = {
  start: number | null;
  end: number | null;
  /** Норма в секундах. `null` — графика на этот день нет. */
  norm: number | null;
};

/**
 * План по дням недели из назначенного графика.
 *
 * Норма — недельное договорное время, делённое на число рабочих дней.
 * Не «конец смены минус начало»: в эту разницу попадает обед, которого
 * график не описывает, и восьмичасовой день при смене 09:00–18:00
 * выглядел бы недоработкой на час. Так же и по той же причине считает
 * сервер (`attendance/statistics.py`), и второго правила на ту же
 * величину быть не должно.
 *
 * Начало и конец смены при этом остаются: плановые линии графика — это
 * действительно границы дня, а не норма.
 */
export function planByWeekday(
  schedule: WorkScheduleDetail | null,
): Map<number, Plan> {
  const plans = new Map<number, Plan>();
  if (!schedule) return plans;

  const working = schedule.days.filter((day) => day.is_working_day);
  const fallback =
    working.length > 0 && schedule.weekly_minutes
      ? Math.round(schedule.weekly_minutes / working.length) * 60
      : null;

  for (const day of schedule.days) {
    plans.set(day.weekday, dayPlan(day, fallback));
  }
  return plans;
}

function dayPlan(day: ScheduleDay, fallback: number | null): Plan {
  // Выходной — это норма ноль, а не отсутствие нормы: разница между
  // «сегодня не работают» и «графика нет» существенна.
  if (!day.is_working_day) return { start: null, end: null, norm: 0 };
  return { start: fromClock(day.start_time), end: fromClock(day.end_time), norm: fallback };
}

// --- точки графика «Приход и уход» ------------------------------------------

export type DayPoint = {
  row: DailyRow;
  day: string;
  /** Подпись оси: число месяца. */
  label: string;
  /** Первый вход и последний выход в минутах от полуночи. */
  entry: number | null;
  exit: number | null;
  plan: Plan;
  /** Минуты опоздания сверх допуска. Считает сервер. */
  late: number | null;
  /** Ушёл раньше планового конца, минуты. */
  early: number | null;
  /** Рабочий ли это день. Выходной, отпуск и болезнь — не рабочие. */
  working: boolean;
  /** День ещё не наступил. */
  future: boolean;
  /** Открытая сессия: выхода ещё нет, и рисовать его нечем. */
  open: boolean;
};

export function points(
  rows: DailyRow[],
  plans: Map<number, Plan>,
  today: string,
): DayPoint[] {
  return rows.map((row) => {
    const zone = row.timezone;
    const plan = plans.get(weekdayOf(row.day)) ?? { start: null, end: null, norm: null };
    const entry = minutesInZone(row.first_entry_at, zone);
    const exit = minutesInZone(row.last_exit_at, zone);
    const planned = fromClock(row.scheduled_start) ?? plan.start;
    // Ранний уход считается только от планового конца И только когда
    // выход действительно есть: у открытой сессии выхода нет, и
    // «ушёл раньше» про неё — выдумка.
    const early =
      exit !== null && plan.end !== null && exit < plan.end
        ? plan.end - exit
        : null;

    return {
      row,
      day: row.day,
      label: String(Number.parseInt(row.day.slice(8, 10), 10)),
      entry,
      exit,
      plan: { ...plan, start: planned ?? plan.start },
      late: row.late_minutes,
      early,
      working: !OFF_STATES.has(row.state),
      future: row.day > today,
      open: row.open_session_id !== null,
    };
  });
}

/** Отрезки непрерывных дней с данными. Пропуски линией не соединяются. */
export function segments(
  list: DayPoint[],
  pick: (point: DayPoint) => number | null,
): DayPoint[][] {
  const out: DayPoint[][] = [];
  let run: DayPoint[] = [];
  for (const point of list) {
    if (pick(point) === null) {
      if (run.length) out.push(run);
      run = [];
    } else {
      run.push(point);
    }
  }
  if (run.length) out.push(run);
  return out;
}

/** Границы оси времени: рабочий день плюс запас, но не уже реальных данных. */
export function timeBounds(list: DayPoint[]): { low: number; high: number } {
  const values: number[] = [];
  for (const point of list) {
    for (const value of [point.entry, point.exit, point.plan.start, point.plan.end]) {
      if (value !== null) values.push(value);
    }
  }
  if (values.length === 0) return { low: 6 * 60, high: 20 * 60 };
  const low = Math.min(...values) - 60;
  const high = Math.max(...values) + 60;
  // К целым часам: подписи оси иначе встают на 07:23 и читаются хуже.
  return {
    low: Math.max(0, Math.floor(low / 60) * 60),
    high: Math.min(24 * 60, Math.ceil(high / 60) * 60),
  };
}

// --- показатели --------------------------------------------------------------

export type Stats = {
  /** Средний приход и уход в минутах. `null` — считать не из чего. */
  averageEntry: number | null;
  averageExit: number | null;
  /** Дней, где пришёл без опоздания. */
  onTime: number;
  lateDays: number;
  earlyDays: number;
  /** Рабочих дней в периоде (по графику, а не по отметкам). */
  workingDays: number;
  /** Доля дней, где норма выполнена, в процентах. `null` — нормы нет. */
  completion: number | null;
};

/**
 * Средние и счётчики.
 *
 * Берутся только первый вход и последний выход рабочего дня: обед
 * между ними — это промежуточные отметки, и попади они в выборку,
 * «средний приход» сместился бы к полудню.
 *
 * Будущие дни не участвуют ни в одном счётчике: день, который ещё не
 * наступил, — это не «пропуск» и не «ноль часов».
 */
export function stats(list: DayPoint[]): Stats {
  const past = list.filter((point) => !point.future);
  const workdays = past.filter((point) => point.working);

  const entries = workdays.map((p) => p.entry).filter(isNumber);
  const exits = workdays.map((p) => p.exit).filter(isNumber);

  const withNorm = workdays.filter((p) => p.plan.norm !== null && p.plan.norm > 0);
  const met = withNorm.filter((p) => p.row.seconds >= (p.plan.norm as number));

  return {
    averageEntry: mean(entries),
    averageExit: mean(exits),
    lateDays: workdays.filter((p) => (p.late ?? 0) > 0).length,
    earlyDays: workdays.filter((p) => (p.early ?? 0) > 0).length,
    // «Вовремя» — только дни, когда человек ПРИШЁЛ: день без отметки
    // не опоздание, но и не своевременный приход.
    onTime: workdays.filter((p) => p.entry !== null && (p.late ?? 0) === 0).length,
    workingDays: workdays.length,
    completion: withNorm.length
      ? Math.round((met.length / withNorm.length) * 100)
      : null,
  };
}

const isNumber = (value: number | null): value is number => value !== null;

function mean(values: number[]): number | null {
  if (values.length === 0) return null;
  return Math.round(values.reduce((sum, one) => sum + one, 0) / values.length);
}

// --- распределение «когда обычно» -------------------------------------------

export type Bucket = { from: number; count: number };

/**
 * Распределение по получасам.
 *
 * Получас, а не час: разница между «приходит к 9:00» и «приходит
 * к 9:45» — это разные привычки, и час их бы склеил.
 */
export function histogram(values: number[], step = 30): Bucket[] {
  if (values.length === 0) return [];
  const low = Math.floor(Math.min(...values) / step) * step - step;
  const high = Math.ceil(Math.max(...values) / step) * step + step;
  const out: Bucket[] = [];
  for (let from = Math.max(0, low); from <= high; from += step) {
    out.push({
      from,
      count: values.filter((one) => one >= from && one < from + step).length,
    });
  }
  return out;
}

/** Самый населённый интервал: «08:45 – 09:15». Пусто — прочерк. */
export function usual(buckets: Bucket[], step = 30): string {
  const top = buckets.reduce<Bucket | null>(
    (best, one) => (best === null || one.count > best.count ? one : best),
    null,
  );
  if (top === null || top.count === 0) return '—';
  return `${hhmm(top.from)} – ${hhmm(top.from + step)}`;
}
