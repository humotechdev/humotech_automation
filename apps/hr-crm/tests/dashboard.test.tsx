/**
 * Главная: оперативная картина дня.
 *
 * Проверяется не вёрстка, а обещания страницы:
 *
 * — числа берутся с сервера и ведут в отфильтрованные списки, ноль — не ссылка;
 * — ошибка блока не превращается в ноль и не гасит соседние блоки;
 * — регион и офис доходят до каждого запроса;
 * — переключение периода трогает только «Ритм дня», и прежняя линия не исчезает;
 * — «Отсутствуют» — только подтверждённые отпуск, больничный и иное;
 * — «Фокус HR» собран по стадиям заявок, а пустой — говорит об этом словами;
 * — лента событий сужается выбранным офисом.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { today } from '../src/features/dashboard/data';
import { USER, crm, fakeNetwork, json, renderApp, type Call } from './helpers';

const DAY = today();

const card = (key: string, value: number, state?: string) => ({
  key,
  title: key,
  value,
  attention: false,
  endpoint: key === 'active_employees' ? '/api/v1/employees' : '/api/v1/attendance/presence',
  params: { date: DAY, ...(state ? { state } : {}) },
});

function dashboard(overrides: Record<string, number> = {}) {
  const value = (key: string, fallback: number) => overrides[key] ?? fallback;
  return {
    date: DAY,
    timezone: 'Asia/Tashkent',
    warnings: [],
    cards: [
      card('active_employees', value('active_employees', 214)),
      card('should_work_today', value('should_work_today', 200)),
      card('came', value('came', 190)),
      card('in_office', value('in_office', 180), 'IN_OFFICE'),
      card('not_come', value('not_come', 9), 'NOT_COME'),
    ],
  };
}

const OFFICES = {
  items: [
    { id: 'o-1', name: 'Ташкент (HQ)', status: 'ACTIVE', region_id: 'r-1' },
    { id: 'o-2', name: 'Бухара', status: 'ACTIVE', region_id: 'r-2' },
  ],
};

const PRESENCE: Record<string, Record<string, number>> = {
  'o-1': { IN_OFFICE: 5, LEFT: 1, NOT_COME: 2, VACATION: 1, SICK_LEAVE: 1, OTHER_ABSENCE: 1, DAY_OFF: 3 },
  'o-2': { IN_OFFICE: 0, LEFT: 0, NOT_COME: 0 },
};

const absence = (id: string, code: string, stage: string, extra: Record<string, unknown> = {}) => ({
  kind: 'absence',
  id,
  created_at: '2026-09-20T05:00:00Z',
  absence: {
    id,
    employee: { id: `e-${id}`, full_name: 'Каримов Азиз Нодирович', employee_number: null },
    absence_type: { code, name: code === 'SICK_LEAVE' ? 'Больничный' : 'Ежегодный отпуск' },
    status: 'SUBMITTED',
    stage,
    kind: 'CREATE',
    first_day: '2026-09-22',
    last_day: '2026-09-27',
    submitted_at: '2026-09-20T05:00:00Z',
    reviewed_at: null,
    requires_document: code === 'SICK_LEAVE',
    documents: [],
    comment: null,
    review_comment: null,
    ...extra,
  },
});

const OPEN = [
  absence('a-1', 'SICK_LEAVE', 'WAITING_DOCUMENTS'),
  absence('a-2', 'SICK_LEAVE', 'NEEDS_FIX'),
  absence('a-3', 'ANNUAL_LEAVE', 'PENDING'),
];

const feed = (id: string, office: string | null, title: string, at: string) => ({
  id, type: 'question', group: 'questions', title, short_text: 'вопрос по графику',
  employee_id: 'e-1', employee_name: 'Иванова М.', office_id: office,
  office_name: office === 'o-2' ? 'Бухара' : 'Ташкент (HQ)', status: 'NEW', status_label: 'Новое',
  priority: 'NORMAL', requires_action: true, created_at: at, read_at: null,
  related_entity_type: 'employee_questions', related_entity_id: id,
  action_url: `/questions?id=${id}`, action_title: 'Открыть',
});

type Handler = (path: string, call: Call) => Response | Promise<Response> | null;

function network(own: Handler = () => null) {
  return fakeNetwork((path, call) => {
    const mine = own(path, call);
    if (mine) return mine;
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/dashboard')) return json(200, dashboard());
    if (path.includes('/regions')) return json(200, { items: [{ id: 'r-1', name: 'Ташкент' }, { id: 'r-2', name: 'Бухара' }] });
    if (path.includes('/offices')) return json(200, OFFICES);
    if (path.includes('/analytics')) {
      return json(200, {
        period: { first: DAY, last: DAY, timezone: 'Asia/Tashkent' },
        headcount: 5,
        series: [
          { day: '2026-09-22', worked_seconds: 0, attended: 3, expected: 4, late: 0 },
          { day: '2026-09-23', worked_seconds: 0, attended: 0, expected: 0, late: 0 },
          { day: '2026-09-24', worked_seconds: 0, attended: 1, expected: 4, late: 0 },
        ],
      });
    }
    if (path.includes('/attendance/presence')) {
      const office = new URL(path, 'http://x').searchParams.get('office_id') ?? 'o-1';
      return json(200, { date: DAY, timezone: 'Asia/Tashkent', counts: PRESENCE[office] ?? {}, total: 0, truncated: false, items: [] });
    }
    if (path.includes('/requests')) {
      const params = new URL(path, 'http://x').searchParams;
      if (params.get('kind') === 'correction') return json(200, { items: [], next_cursor: null, has_more: false });
      if ((params.get('status') ?? '').includes('APPROVED')) {
        return json(200, {
          items: [absence('a-9', 'SICK_LEAVE', 'APPROVED', { status: 'APPROVED', reviewed_at: '2026-09-24T05:24:00Z' })],
          next_cursor: null, has_more: false,
        });
      }
      return json(200, { items: OPEN, next_cursor: null, has_more: false });
    }
    if (path.includes('/notification-feed')) {
      return json(200, {
        items: [
          feed('f-1', 'o-1', 'Новое обращение', '2026-09-24T04:17:00Z'),
          feed('f-2', 'o-2', 'Обращение из Бухары', '2026-09-24T03:00:00Z'),
        ],
        counts: { all: 2 }, next_cursor: null, has_more: false, window_days: 30,
      });
    }
    return crm(path) ?? json(200, { items: [] });
  });
}

const count = (calls: Call[], part: string) => calls.filter((c) => c.url.includes(part)).length;
const today_ = () => screen.getByRole('list', { name: 'Сегодня' });

describe('строка «Сегодня»', () => {
  test('четыре показателя с сервера, число ведёт в отфильтрованный список', async () => {
    network();
    renderApp('/');

    await screen.findByText('сейчас в офисе');
    const row = today_();
    expect(within(row).getByText('214')).toBeTruthy();
    expect(within(row).getByText('200')).toBeTruthy();
    expect(within(row).getByText('180')).toBeTruthy();
    const missing = within(row).getByText('без отметки').closest('a') as HTMLAnchorElement;
    expect(missing.getAttribute('href')).toContain('/attendance');
    expect(missing.getAttribute('href')).toContain('state=NOT_COME');
  });

  test('ноль — не ссылка: открывать пустой список незачем', async () => {
    network((path) => (path.includes('/dashboard') ? json(200, dashboard({ not_come: 0 })) : null));
    renderApp('/');

    await screen.findByText('без отметки');
    expect(within(today_()).getByText('без отметки').closest('a')).toBeNull();
  });

  test('для прошлой даты подпись не обещает настоящее время', async () => {
    network();
    renderApp('/');
    await screen.findByText('сейчас в офисе');

    const field = screen.getByLabelText('Дата');
    fireEvent.change(field, { target: { value: '02.01.2020' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    expect(await screen.findByText('в офисе')).toBeTruthy();
    expect(screen.queryByText('сейчас в офисе')).toBeNull();
    expect(screen.getByText('было по графику')).toBeTruthy();
  });

  test('подпись под графиком — словами и в родительном падеже', async () => {
    network((path) => (path.includes('/dashboard') ? json(200, dashboard({ came: 0, should_work_today: 1 })) : null));
    renderApp('/');

    const note = await screen.findByText(/Сегодня отметился/);
    expect(note.textContent).toBe('Сегодня отметился 0 из 1 сотрудника.');
  });

  test('без людей по графику — объяснение, а не голые нули', async () => {
    network((path) => (path.includes('/dashboard') ? json(200, dashboard({ came: 0, should_work_today: 0 })) : null));
    renderApp('/');

    expect(await screen.findByText('Сегодня по выбранным условиям нет сотрудников по графику.')).toBeTruthy();
  });
});

describe('состояния блоков', () => {
  test('ошибка блока не превращается в ноль', async () => {
    network((path) => (path.includes('/dashboard') ? json(500, { error: {} }) : null));
    renderApp('/');

    expect(await screen.findByText(/Не удалось загрузить показатели/)).toBeTruthy();
    expect(screen.queryByRole('list', { name: 'Сегодня' })).toBeNull();
  });

  test('сбой одного блока не гасит соседние', async () => {
    network((path) => (path.includes('/attendance/presence') ? json(500, { error: {} }) : null));
    renderApp('/');

    expect(await screen.findByText(/Не удалось загрузить офисы/)).toBeTruthy();
    expect(within(today_()).getByText('214')).toBeTruthy();
  });
});

describe('фильтры', () => {
  test('смена даты — новый запрос показателей на эту дату', async () => {
    const calls = network();
    renderApp('/');
    await screen.findByText('сейчас в офисе');

    const before = count(calls, '/dashboard');
    const field = screen.getByLabelText('Дата');
    fireEvent.change(field, { target: { value: '01.09.2026' } });
    fireEvent.keyDown(field, { key: 'Enter' });

    await waitFor(() => expect(count(calls, '/dashboard')).toBeGreaterThan(before));
    expect(calls.filter((c) => c.url.includes('/dashboard')).pop()?.url).toContain('date=2026-09-01');
  });

  test('офис доходит до показателей, очереди заявок и ленты событий', async () => {
    const calls = network();
    renderApp('/');
    await screen.findByText('Новое обращение');

    fireEvent.click(screen.getByLabelText(/^Офис:/));
    fireEvent.click(await screen.findByRole('option', { name: 'Ташкент (HQ)' }));

    await waitFor(() => {
      expect(calls.filter((c) => c.url.includes('/dashboard')).pop()?.url).toContain('office_id=o-1');
    });
    expect(calls.filter((c) => c.url.includes('/requests')).pop()?.url).toContain('office_id=o-1');
    // Событие другого офиса из ленты уходит.
    await waitFor(() => expect(screen.queryByText('Обращение из Бухары')).toBeNull());
    expect(screen.getByText('Новое обращение')).toBeTruthy();
  });
});

describe('ритм дня', () => {
  test('период меняет только график', async () => {
    const calls = network();
    renderApp('/');
    await screen.findByRole('img', { name: /Явка по дням/ });

    const before = {
      dashboard: count(calls, '/dashboard'),
      presence: count(calls, '/attendance/presence'),
      chart: count(calls, '/analytics'),
    };
    fireEvent.click(screen.getByRole('button', { name: 'Месяц' }));

    await waitFor(() => expect(count(calls, '/analytics')).toBeGreaterThan(before.chart));
    expect(count(calls, '/dashboard')).toBe(before.dashboard);
    expect(count(calls, '/attendance/presence')).toBe(before.presence);
    expect(screen.getByRole('button', { name: 'Месяц' }).getAttribute('aria-pressed')).toBe('true');
  });

  test('прежняя линия видна, пока грузится новая, и сбой её не стирает', async () => {
    let chartCalls = 0;
    network((path) => {
      if (!path.includes('/analytics')) return null;
      chartCalls += 1;
      return chartCalls > 1 ? json(500, { error: {} }) : null;
    });
    renderApp('/');
    const chart = await screen.findByRole('img', { name: /Явка по дням/ });

    fireEvent.click(screen.getByRole('button', { name: '14 дней' }));

    expect(await screen.findByText(/Не удалось обновить явку/)).toBeTruthy();
    expect(screen.getByRole('img', { name: /Явка по дням/ })).toBe(chart);
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeTruthy();
  });

  test('день без графика — разрыв, а не ноль процентов', async () => {
    network();
    renderApp('/');

    const chart = await screen.findByRole('img', { name: /Явка по дням/ });
    const label = chart.getAttribute('aria-label') ?? '';
    expect(label).toContain('22.09 — 75 %');
    expect(label).toContain('23.09 — нет графика');
    expect(label).toContain('24.09 — 25 %');
  });
});

describe('фокус HR', () => {
  test('строки собраны по стадиям заявок', async () => {
    network();
    renderApp('/');

    const focus = await screen.findByRole('region', { name: 'Фокус HR' });
    expect(await within(focus).findByText('2 больничных ждут справку')).toBeTruthy();
    expect(within(focus).getByText('1 отпуск ждёт решения')).toBeTruthy();
    expect(within(focus).getByText('9 сотрудников без отметки')).toBeTruthy();
    expect(within(focus).queryByText(/проверки/)).toBeNull();
  });

  test('пустая очередь говорит об этом словами', async () => {
    network((path) => {
      if (!path.includes('/requests')) return path.includes('/dashboard') ? json(200, dashboard({ not_come: 0 })) : null;
      return json(200, { items: [], next_cursor: null, has_more: false });
    });
    renderApp('/');

    expect(await screen.findByText('Сейчас ничего не ждёт решения HR.')).toBeTruthy();
  });
});

describe('офисы сегодня', () => {
  test('«Отсутствуют» — только оформленное, строка ведёт в посещаемость офиса', async () => {
    network();
    renderApp('/');

    const table = await screen.findByRole('table', { name: 'Офисы' });
    const row = (await within(table).findByText('Ташкент (HQ)')).closest('a') as HTMLAnchorElement;
    const cells = within(row).getAllByRole('cell').map((cell) => cell.textContent);
    // По графику 5+1+2, в офисе 5, нет отметки 2, отсутствуют 1+1+1 (выходной — нет).
    expect(cells).toEqual(['Ташкент (HQ)', '8', '5', '2', '3']);
    expect(row.getAttribute('href')).toContain('office_id=o-1');
  });
});

describe('последние события', () => {
  test('решение по заявке и события ленты — одной лентой, новые сверху', async () => {
    network();
    renderApp('/');

    const events = await screen.findByRole('region', { name: 'Последние события' });
    const titles = (await within(events).findAllByRole('link')).map((link) => link.querySelector('b')?.textContent);
    expect(titles).toEqual(['Больничный подтверждён', 'Новое обращение', 'Обращение из Бухары']);
  });
});
