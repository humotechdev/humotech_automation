/**
 * Аналитика: арифметика показателей и то, как она попадает на экран.
 *
 * Здесь проверяется именно математика — та, в которой ошибку не видно
 * глазами: веса при сложении долей, пункты против процентов, нулевой
 * знаменатель и повторные входы.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import {
  averagePerAttendedDay, formatPercent, formatPoints, points, relative,
  share, weighted,
} from '../src/features/analytics/metrics';
import { toPoints } from '../src/components/AttendanceChart';
import { USER, crm, fakeNetwork, json, pick, renderApp } from './helpers';

describe('доли и их сложение', () => {
  test('4 608 из 4 800 — это 96,0%', () => {
    expect(formatPercent(share(4608, 4800))).toBe('96,0%');
  });

  test('230 из 4 608 округляется до 5,0%', () => {
    expect(formatPercent(share(230, 4608))).toBe('5,0%');
  });

  test('общая доля двух разных по размеру офисов считается по суммам', () => {
    // Среднее из процентов дало бы 96,05%: большой офис весит больше
    // маленького, и без весов число было бы неверным.
    const big = { numerator: 795, denominator: 820 };
    const small = { numerator: 485, denominator: 510 };
    const total = weighted([big, small]);

    expect(total.numerator).toBe(1280);
    expect(total.denominator).toBe(1330);
    expect(formatPercent(total.percent)).toBe('96,2%');

    const naive = ((share(795, 820) ?? 0) + (share(485, 510) ?? 0)) / 2;
    expect(formatPercent(naive)).not.toBe(formatPercent(total.percent));
  });

  test('нулевой знаменатель даёт прочерк, а не ноль процентов', () => {
    expect(share(0, 0)).toBeNull();
    expect(formatPercent(share(0, 0))).toBe('—');
    // Но при известном знаменателе ноль числителя — честный ноль.
    expect(formatPercent(share(0, 21))).toBe('0,0%');
  });
});

describe('разницы', () => {
  test('96,0% и 94,5% различаются на 1,5 пункта, а не на 1,5 процента', () => {
    expect(formatPoints(points(96, 94.5))).toBe('+1,5 п.п.');
    expect(formatPoints(points(94.5, 96))).toBe('−1,5 п.п.');
    expect(formatPoints(points(96, 94.5))).not.toContain('%');
  });

  test('разница не определена, если одну сторону не измерили', () => {
    expect(points(96, null)).toBeNull();
    expect(formatPoints(points(null, 94.5))).toBe('—');
  });

  test('относительный рост от нуля не вычисляется', () => {
    // «Рост с нуля» — это не бесконечность, а отсутствие базы.
    expect(relative(5, 0)).toBeNull();
    expect(relative(96, 94.5)).toBeCloseTo(1.587, 2);
  });
});

describe('время', () => {
  test('среднее считается на день с явкой, а не на ожидаемый день', () => {
    // 4 608 дней с явкой, 129 600 минут в офисе -> 28 минут на день явки.
    const seconds = 4608 * 7 * 3600 + 4608 * 48 * 60;
    expect(averagePerAttendedDay(seconds, 4608)).toBeCloseTo(7 * 3600 + 48 * 60);
    expect(averagePerAttendedDay(seconds, 0)).toBeNull();
  });
});

describe('ряд графика', () => {
  test('день без ожиданий — разрыв, а не ноль', () => {
    const series = toPoints([
      { day: '2026-08-01', worked_seconds: 0, attended: 0, expected: 0, late: 0 },
      { day: '2026-08-02', worked_seconds: 1, attended: 222, expected: 230, late: 3 },
    ]);
    expect(series[0]?.value).toBeNull();
    expect(formatPercent(series[1]?.value ?? null)).toBe('96,5%');
  });

  test('повторные входы не увеличивают число дней явки', () => {
    // `attended` — это дни, а не события: сервер уже свернул повторные
    // входы одного человека в один день. Клиент их не пересчитывает.
    const point = { day: '2026-08-02', worked_seconds: 3, attended: 222, expected: 230, late: 3 };
    const twice = toPoints([point, point]);
    expect(twice.every((row) => row.attended === 222)).toBe(true);
  });
});

// --- страница --------------------------------------------------------------

const ratio = (numerator: number, denominator: number) => ({
  numerator, denominator, percent: denominator ? Math.round((numerator * 1000) / denominator) / 10 : null,
});

function day(date: string, weekday: number, over: Record<string, unknown> = {}) {
  const working = weekday <= 5;
  return {
    day: date, weekday, working, future: false, in_detail: true,
    expected: working ? 230 : 0, attended: working ? 222 : 0,
    percent: working ? 96.5 : null, on_time: working ? 200 : 0, on_time_percent: working ? 90.1 : null,
    late: working ? 22 : 0, missed: working ? 8 : 0, vacation: 0, sick_leave: 0, other_absence: 0,
    average_seconds: working ? 8 * 3600 : null,
    ...over,
  };
}

/** Понедельник 3 — воскресенье 9 августа 2026. */
const OVERVIEW = {
  period: { first: '2026-08-03', last: '2026-08-09', days: 7 },
  previous_period: { first: '2026-07-27', last: '2026-08-02' },
  weekday: null,
  generated_at: '2026-08-10T09:00:00Z',
  timezones: ['Asia/Tashkent'],
  summary: {
    attendance: ratio(4608, 4800),
    previous_attendance: ratio(4536, 4800),
    difference_points: 1.5,
    on_time: ratio(4378, 4608),
    late: ratio(230, 4608),
    average_seconds: 7 * 3600 + 48 * 60,
    open_sessions: 3,
    missed_days: 192,
    vacation_days: 0,
    sick_leave_days: 0,
    other_absence_days: 0,
  },
  days: [
    day('2026-08-03', 1), day('2026-08-04', 2), day('2026-08-05', 3),
    day('2026-08-06', 4), day('2026-08-07', 5, { percent: 91.3, attended: 210, missed: 20 }),
    day('2026-08-08', 6), day('2026-08-09', 7),
  ],
  previous_days: [],
  offices: [
    { id: 'o-1', name: 'Бухара', position: 1, attendance: ratio(97, 100), previous_attendance: ratio(91, 100), difference_points: 6 },
    { id: 'o-2', name: 'Самарканд', position: 2, attendance: ratio(94, 100), previous_attendance: ratio(96, 100), difference_points: -2 },
  ],
  arrivals: {
    bucket_minutes: 10, from_minutes: -60, to_minutes: 90,
    buckets: Array.from({ length: 15 }, (_, index) => ({
      from: -60 + index * 10, to: -50 + index * 10,
      early: index < 6 ? 10 : 0, grace: index === 6 ? 4 : 0, late: index > 6 ? 2 : 0,
    })),
    start_time: '09:00', uniform_start: true, median_minutes: 537, after_start: 20,
    late: ratio(16, 80),
  },
  weekdays: {
    days: [1, 2, 3, 4, 5].map((weekday) => ({
      weekday, attendance: ratio(90 + weekday, 100), on_time: ratio(75, 100), average_seconds: 8 * 3600,
    })),
    best: 5,
  },
};

const REGIONS = { items: [{ id: 'r-1', code: 'C', name: 'Центр', status: 'ACTIVE' }] };

const MOVEMENT = {
  current: { first: '2026-03-01', last: '2026-03-31', hired: 5, left: 2, difference: 3 },
  previous: { first: '2026-01-29', last: '2026-02-28', hired: 3, left: 4, difference: -1 },
  month_before: { first: '2026-02-01', last: '2026-02-28', hired: 2, left: 1, difference: 1 },
  year_before: { first: '2025-03-01', last: '2025-03-31', hired: 7, left: 7, difference: 0 },
  headcount: 42,
};

function network(handler: (path: string, method: string) => Response | null = () => null) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/analytics/overview')) return json(200, OVERVIEW);
    if (path.includes('/analytics/movement')) return json(200, MOVEMENT);
    if (path.includes('/regions/')) return json(200, REGIONS);
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

const PAGE = '/analytics?from=2026-08-03&to=2026-08-09';
const overviewCalls = (calls: { url: string }[]) => calls.filter((c) => c.url.includes('/analytics/overview'));

describe('движение сотрудников', () => {
  test('показывает принято, уволено и разницу со знаком', async () => {
    network();
    renderApp(PAGE);

    // Блок сначала считает: ждём текст, а не первый отрисованный кадр.
    // «Принято» есть и в карточке, и в заголовке столбца — берём оба.
    expect((await screen.findAllByText('Принято')).length).toBeGreaterThan(0);
    const block = screen.getByLabelText('Движение сотрудников');
    expect(within(block).getAllByText('5').length).toBeGreaterThan(0);
    // «3» и «−3» — противоположные новости, и различать их по цвету
    // одному нельзя.
    expect(within(block).getAllByText('+3').length).toBeGreaterThan(0);
    expect(within(block).getByText(/На конец периода: 42/)).toBeTruthy();
  });

  test('сравнивает с прошлым периодом, месяцем и годом', async () => {
    network();
    renderApp(PAGE);

    await screen.findAllByText('Принято');
    const block = screen.getByLabelText('Движение сотрудников');
    for (const title of [
      'Выбранный период', 'Предыдущий период', 'Месяцем раньше', 'Годом раньше',
    ]) {
      // Заголовок строки — это название и даты под ним, поэтому поиск
      // идёт по вхождению, а не по точному совпадению.
      expect(within(block).getByText(new RegExp(title))).toBeTruthy();
    }
    // Предыдущий период равен по длине, а не календарный: иначе
    // разница объяснялась бы длиной, а не событиями.
    expect(within(block).getAllByText(/2026-01-29/).length).toBeGreaterThan(0);
  });

  test('неполный ответ не роняет всю страницу', async () => {
    // Так бывает у старого сервера и у прокси, подменившего тело.
    network((path) =>
      path.includes('/analytics/movement') ? json(200, { headcount: 0 }) : null,
    );
    // Другой период — другой ключ загрузки: `useBlock` кэширует ответ
    // по ключу, и на том же периоде тест получил бы данные соседа.
    renderApp('/analytics?from=2026-07-06&to=2026-07-12');

    // Блок сначала считает, поэтому ждём именно текст, а не первый
    // отрисованный кадр.
    expect(await screen.findByText('Данных за период нет.')).toBeTruthy();
    // Остальные блоки при этом продолжают работать.
    expect(await screen.findByLabelText(/Явка/)).toBeTruthy();
  });
});

describe('сводка', () => {
  test('явка показана с сотрудника-днями и разницей в пунктах', async () => {
    network();
    renderApp(PAGE);

    expect(await screen.findByText('96,0%')).toBeTruthy();
    expect(screen.getByText(/4 608 из 4 800 сотрудника-дней/)).toBeTruthy();
    expect(screen.getByText('+1,5 п.п.')).toBeTruthy();
    // Опоздания и «вовремя» — доли первых входов, а не всех ожидаемых дней.
    expect(screen.getByText('5,0%')).toBeTruthy();
    expect(screen.getByText('7 ч 48 мин')).toBeTruthy();
  });

  test('ошибка не превращается в нулевые показатели', async () => {
    network((path) => (path.includes('/analytics/overview') ? json(500, { error: {} }) : null));
    renderApp(PAGE);

    expect((await screen.findAllByText(/Не удалось загрузить аналитику/)).length).toBeGreaterThan(0);
    expect(screen.queryByText('0,0%')).toBeNull();
  });
});

describe('фильтры', () => {
  test('быстрый выбор и регион уходят в запрос обзора', async () => {
    const calls = network();
    renderApp(PAGE);
    await screen.findByText('96,0%');

    fireEvent.click(screen.getByRole('button', { name: '7 дней' }));
    await waitFor(() => {
      const last = overviewCalls(calls).pop();
      expect(last?.url).toContain('date_to=');
      expect(last?.url).not.toContain('date_from=2026-08-03');
    });

    await pick('Регион', 'Центр');
    await waitFor(() => expect(overviewCalls(calls).pop()?.url).toContain('region_id=r-1'));
  });

  test('офис из рейтинга применяется ко всей странице', async () => {
    const calls = network();
    renderApp(PAGE);

    fireEvent.click(await screen.findByRole('button', { name: /Бухара/ }));

    await waitFor(() => expect(overviewCalls(calls).pop()?.url).toContain('office_id=o-1'));
  });

  test('экспорт заказывается с текущими фильтрами', async () => {
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/export-jobs/') ? json(201, { id: 'j-1', status: 'QUEUED' }) : null,
    );
    renderApp(`${PAGE}&office_id=o-1`);
    await screen.findByText('96,0%');

    fireEvent.click(screen.getByRole('button', { name: /Экспорт/ }));

    await waitFor(() =>
      expect(calls.filter((c) => c.method === 'POST' && c.url.includes('/export-jobs/'))).toHaveLength(1),
    );
    expect(await screen.findByText(/с текущими фильтрами/)).toBeTruthy();
  });
});

describe('календарь', () => {
  test('выходной не выбирается и не показывает процент', async () => {
    network();
    renderApp(PAGE);
    await screen.findByText('96,0%');

    // Выходной есть в календаре подписью, но не выбирается как рабочий день.
    // Та же дата может стоять и на оси графика, поэтому подписей несколько.
    expect(screen.queryByRole('gridcell', { name: /8 авг/ })).toBeNull();
    expect(screen.getAllByText('8 авг').length).toBeGreaterThan(0);
  });

  test('день открывает детали и ссылку в посещаемость с теми же фильтрами', async () => {
    network();
    renderApp(`${PAGE}&region_id=r-1`);

    fireEvent.click(await screen.findByRole('gridcell', { name: /7 авг/ }));

    const panel = await screen.findByRole('complementary', { name: 'Выбранный день' });
    await waitFor(() => expect(panel.textContent).toContain('91,3%'));
    const link = screen.getByRole('link', { name: /Открыть день в посещаемости/ });
    expect(link.getAttribute('href')).toBe('/attendance?date=2026-08-07&region_id=r-1');
  });

  test('«нет отметки» не называется прогулом', async () => {
    network();
    renderApp(PAGE);

    expect(await screen.findByText('Нет отметки')).toBeTruthy();
    expect(screen.queryByText(/[Пп]рогул/)).toBeNull();
  });
});
