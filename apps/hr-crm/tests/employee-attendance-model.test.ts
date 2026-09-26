/**
 * Расчёты вкладки «Посещаемость» одного сотрудника.
 *
 * Проверяется ровно то, чего требует задание и что легко сломать
 * незаметно: будущий день не считается пропуском, выходной не
 * соединяется ложной линией с соседними, а обед не смещает средний
 * приход к полудню.
 *
 * Ни одна проверка не повторяет правила учёта: время в офисе,
 * опоздание и состояние дня приходят готовыми и здесь только
 * раскладываются по координатам.
 */

import { describe, expect, it } from 'vitest';

import type { DailyRow, WorkScheduleDetail } from '../src/api/crm';
import {
  planByWeekday, points, segments, stats, histogram, usual, timeBounds,
} from '../src/features/employee/attendance-model';

const ZONE = 'Asia/Tashkent'; // UTC+5 круглый год

/** `09:06` по Ташкенту — это `04:06Z`. */
function at(day: string, clock: string): string {
  const [h, m] = clock.split(':').map(Number);
  const utc = (h as number) * 60 + (m as number) - 5 * 60;
  const hh = String(Math.floor(utc / 60)).padStart(2, '0');
  const mm = String(utc % 60).padStart(2, '0');
  return `${day}T${hh}:${mm}:00Z`;
}

function row(over: Partial<DailyRow> & { day: string }): DailyRow {
  return {
    timezone: ZONE,
    office_id: 'o-1',
    office_name: 'Ташкент',
    state: 'LEFT',
    first_entry_at: null,
    last_exit_at: null,
    seconds: 0,
    sessions: 0,
    open_session_id: null,
    late_minutes: 0,
    scheduled_start: '09:00:00',
    absence_code: null,
    absence_name: null,
    conflicting_marks: false,
    ...over,
  };
}

const SCHEDULE: WorkScheduleDetail = {
  id: 's-1',
  name: 'Пятидневка',
  timezone: ZONE,
  weekly_minutes: 2400,
  late_grace_minutes: 5,
  early_leave_grace_minutes: 5,
  is_flexible: false,
  status: 'ACTIVE',
  days: [1, 2, 3, 4, 5, 6, 7].map((weekday) => ({
    weekday,
    is_working_day: weekday <= 5,
    start_time: weekday <= 5 ? '09:00:00' : null,
    end_time: weekday <= 5 ? '18:00:00' : null,
  })),
};

const PLANS = planByWeekday(SCHEDULE);

describe('план по дням недели', () => {
  it('норма — недельное время на рабочие дни, а не длина смены', () => {
    // 09:00–18:00 — девять часов, но час из них обед: договорная норма
    // 2400 / 5 = 480 минут. Так же считает сервер.
    expect(PLANS.get(1)?.norm).toBe(8 * 3600);
    // Границы смены при этом остаются настоящими: по ним идут плановые
    // линии графика.
    expect(PLANS.get(1)?.start).toBe(9 * 60);
    expect(PLANS.get(1)?.end).toBe(18 * 60);
  });

  it('выходной — это норма ноль, а не отсутствие нормы', () => {
    expect(PLANS.get(6)?.norm).toBe(0);
    expect(PLANS.get(6)?.start).toBeNull();
  });
});

describe('точки графика', () => {
  // Понедельник 2026-09-07 … воскресенье 2026-09-13.
  const list = points(
    [
      row({ day: '2026-09-07', first_entry_at: at('2026-09-07', '09:06'),
            last_exit_at: at('2026-09-07', '18:20'), seconds: 8 * 3600, late_minutes: 6 }),
      row({ day: '2026-09-08', state: 'VACATION', absence_code: 'VAC' }),
      row({ day: '2026-09-09', first_entry_at: at('2026-09-09', '08:52'),
            last_exit_at: at('2026-09-09', '17:10'), seconds: 7 * 3600 }),
      row({ day: '2026-09-10', state: 'IN_OFFICE', first_entry_at: at('2026-09-10', '08:58'),
            last_exit_at: null, seconds: 4 * 3600, open_session_id: 'x-1' }),
      row({ day: '2026-09-11', state: 'NOT_COME', late_minutes: null }),
      row({ day: '2026-09-12', first_entry_at: at('2026-09-12', '09:00'),
            last_exit_at: at('2026-09-12', '18:00'), seconds: 9 * 3600 }),
      row({ day: '2026-09-13', state: 'DAY_OFF' }),
    ],
    PLANS,
    '2026-09-11', // «сегодня» — пятница: 12-е и 13-е ещё не наступили
  );

  it('отпуск, выходной и «нет графика» рабочими днями не считаются', () => {
    expect(list.map((one) => one.working)).toEqual([
      true, false, true, true, true, true, false,
    ]);
  });

  it('время читается в поясе офиса, а не браузера', () => {
    expect(list[0]?.entry).toBe(9 * 60 + 6);
    expect(list[0]?.exit).toBe(18 * 60 + 20);
  });

  it('у открытой сессии нет ни выхода, ни раннего ухода', () => {
    expect(list[3]?.open).toBe(true);
    expect(list[3]?.exit).toBeNull();
    expect(list[3]?.early).toBeNull();
  });

  it('ранний уход считается от планового конца', () => {
    expect(list[2]?.early).toBe(18 * 60 - (17 * 60 + 10));
  });

  it('будущий день отмечен как будущий', () => {
    expect(list.map((one) => one.future)).toEqual([
      false, false, false, false, false, true, true,
    ]);
  });

  it('отпуск и день без отметок разрывают линию, а не соединяются ею', () => {
    const runs = segments(list, (one) => one.entry);
    // 07-е | 09-е и 10-е подряд | 12-е. Отпуск 08-го, прогул 11-го и
    // выходной 13-го линию рвут.
    expect(runs.map((run) => run.map((one) => one.day))).toEqual([
      ['2026-09-07'],
      ['2026-09-09', '2026-09-10'],
      ['2026-09-12'],
    ]);
  });

  it('ось времени охватывает и график, и реальные отметки', () => {
    const { low, high } = timeBounds(list);
    expect(low).toBeLessThanOrEqual(8 * 60 + 52);
    expect(high).toBeGreaterThanOrEqual(18 * 60 + 20);
    expect(low % 60).toBe(0);
    expect(high % 60).toBe(0);
  });
});

describe('показатели', () => {
  const list = points(
    [
      row({ day: '2026-09-07', first_entry_at: at('2026-09-07', '09:10'),
            last_exit_at: at('2026-09-07', '18:00'), seconds: 9 * 3600, late_minutes: 10 }),
      row({ day: '2026-09-08', first_entry_at: at('2026-09-08', '08:50'),
            last_exit_at: at('2026-09-08', '17:00'), seconds: 7 * 3600 }),
      row({ day: '2026-09-09', state: 'NOT_COME', late_minutes: null }),
      row({ day: '2026-09-12', first_entry_at: at('2026-09-12', '09:00'),
            last_exit_at: at('2026-09-12', '18:00'), seconds: 9 * 3600 }),
      row({ day: '2026-09-13', state: 'DAY_OFF' }),
    ],
    PLANS,
    '2026-09-09',
  );
  const out = stats(list);

  it('будущие дни не портят ни средние, ни выполнение графика', () => {
    // 12-е ещё не наступило: в средние не входит.
    expect(out.averageEntry).toBe(Math.round((9 * 60 + 10 + (8 * 60 + 50)) / 2));
    expect(out.averageExit).toBe(Math.round((18 * 60 + 17 * 60) / 2));
    expect(out.workingDays).toBe(3); // 07, 08, 09 — без будущих и без выходного
  });

  it('день без отметки — не опоздание и не своевременный приход', () => {
    expect(out.lateDays).toBe(1);
    expect(out.onTime).toBe(1); // только 08-е: 09-е без входа не в счёт
  });

  it('выполнение графика считается по дням с нормой', () => {
    // Норма 8 часов: 07-е (9 ч) выполнено, 08-е (7 ч) нет, 09-е без
    // отметок тоже нет.
    expect(out.completion).toBe(Math.round((1 / 3) * 100));
  });

  it('ранний уход отделён от опоздания', () => {
    expect(out.earlyDays).toBe(1); // 08-е: ушёл в 17:00 при плане 18:00
  });
});

describe('когда обычно', () => {
  it('распределение строится по получасам и находит самый частый', () => {
    const values = [9 * 60 + 5, 9 * 60 + 12, 9 * 60 + 25, 8 * 60 + 40];
    const buckets = histogram(values, 30);
    const total = buckets.reduce((sum, one) => sum + one.count, 0);
    expect(total).toBe(values.length);
    expect(usual(buckets, 30)).toContain('09:00');
  });

  it('без данных распределения нет', () => {
    expect(histogram([], 30)).toEqual([]);
  });
});
