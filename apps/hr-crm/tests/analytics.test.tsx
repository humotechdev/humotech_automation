/**
 * Аналитика: арифметика показателей и то, как она попадает на экран.
 *
 * Здесь проверяется именно математика — та, в которой ошибку не видно
 * глазами: веса при сложении долей, пункты против процентов, нулевой
 * знаменатель и повторные входы.
 */

import { screen, waitFor } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import {
  averagePerAttendedDay, formatPercent, formatPoints, points, relative,
  share, weighted,
} from '../src/features/analytics/metrics';
import { toPoints } from '../src/components/AttendanceChart';
import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

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

const TOTALS = {
  employees: 12,
  expected_working_days: 4800,
  attended_days: 4608,
  missed_days: 192,
  late_arrivals: 230,
  worked_seconds: 4608 * (7 * 3600 + 48 * 60),
  expected_seconds: 4800 * 8 * 3600,
};

function report(over: Partial<typeof TOTALS> = {}, name = 'Организация') {
  const totals = { ...TOTALS, ...over };
  return {
    scope: { kind: 'organization', id: null, name },
    period: { first: '2026-08-01', last: '2026-08-31', timezone: 'Asia/Tashkent' },
    generated_at: '2026-09-01T09:00:00Z',
    headcount: 12,
    coverage: {
      employees_total: 12, employees_with_schedule: 12, percent: 100,
      note: 'Сотрудники без графика в знаменатели не входят.',
    },
    totals,
    ratios: [
      {
        key: 'attendance', title: 'Посещаемость',
        percent: (totals.attended_days / totals.expected_working_days) * 100,
        numerator: totals.attended_days, denominator: totals.expected_working_days,
        formula: 'дни с отметками / рабочие дни по графику',
        unit: 'days',
      },
    ],
    series: [
      { day: '2026-08-01', worked_seconds: 1, attended: 222, expected: 230, late: 3 },
      { day: '2026-08-02', worked_seconds: 1, attended: 225, expected: 230, late: 2 },
    ],
  };
}

function network(handler: (path: string) => Response | null = () => null) {
  return fakeNetwork((path) => {
    const own = handler(path);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/analytics/compare')) {
      return json(200, {
        kind: 'office',
        left: report({}, 'Ташкент'),
        right: report({ expected_working_days: 510, attended_days: 485 }, 'Самарканд'),
        differences: [
          {
            key: 'attendance', title: 'Посещаемость',
            left: { key: 'attendance', title: 'Посещаемость', percent: 96, numerator: 4608,
                    denominator: 4800, formula: 'f', unit: 'days' },
            right: { key: 'attendance', title: 'Посещаемость', percent: 95.1, numerator: 485,
                     denominator: 510, formula: 'f', unit: 'days' },
            points: 0.9, comparable: true,
          },
          {
            key: 'punctuality', title: 'Приход вовремя',
            left: { key: 'punctuality', title: 'Приход вовремя', percent: null, numerator: 0,
                    denominator: 0, formula: 'f', unit: 'days' },
            right: { key: 'punctuality', title: 'Приход вовремя', percent: 90, numerator: 9,
                     denominator: 10, formula: 'f', unit: 'days' },
            points: null, comparable: false,
          },
        ],
      });
    }
    if (path.includes('/analytics')) return json(200, report());
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

describe('обзор', () => {
  test('процент показан вместе с исходными числами и единицей', async () => {
    network();
    renderApp('/analytics?date_from=2026-08-01&date_to=2026-08-31');

    expect(await screen.findByText('96,0%')).toBeTruthy();
    expect(screen.getAllByText(/4 608 из 4 800 сотрудник-дней/).length).toBeGreaterThan(0);
    expect(screen.getByText(/230 из 4 608 явок/)).toBeTruthy();
  });

  test('«нет отметки» не называется прогулом', async () => {
    network();
    renderApp('/analytics?date_from=2026-08-01&date_to=2026-08-31');

    expect(await screen.findByText('Нет отметки')).toBeTruthy();
    expect(screen.queryByText(/[Пп]рогул/)).toBeNull();
  });

  test('ошибка не превращается в нулевые показатели', async () => {
    network((path) =>
      path.includes('/analytics') && !path.includes('compare')
        ? json(500, { error: {} })
        : null,
    );
    renderApp('/analytics');

    expect(await screen.findByText(/Не удалось загрузить показатели/)).toBeTruthy();
    expect(screen.queryByText('0,0%')).toBeNull();
  });
});

describe('сравнение', () => {
  test('разница приходит в пунктах, а несравнимое так и подписано', async () => {
    network();
    renderApp('/analytics?tab=compare&date_from=2026-08-01&date_to=2026-08-31');

    await waitFor(() => expect(screen.queryByText(/Считаем сравнение/)).toBeNull(), {
      timeout: 3000,
    });
    expect(await screen.findByText('+0,9 п.п.')).toBeTruthy();
    expect(screen.getByText('Не сравнимо')).toBeTruthy();
  });
});
