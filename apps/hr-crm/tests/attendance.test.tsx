/**
 * Посещаемость: один экран, четыре режима — за день, журнал, неделя,
 * сравнение.
 *
 * Проверяются обещания, которые легко нарушить незаметно: «нет отметки»
 * не прогул, отсутствие графика не опоздание, обрезанный ответ не выдаётся
 * за полный состав, а разница между первым входом и последним выходом не
 * подменяет время в офисе.
 *
 * Режимы — вкладки одного листа: лист и линия под вкладкой не
 * пересоздаются, меняется только содержимое.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, pick, renderApp } from './helpers';

const DAY = '2026-09-04';

const CARDS = [
  ['should_work_today', 'Должны работать сегодня', 214],
  ['in_office', 'Сейчас в офисе', 193],
  ['came', 'Пришли', 205],
  ['left', 'Уже ушли', 12],
  ['not_come', 'Не пришли', 9],
  ['late', 'Опоздали', 7],
  ['open_sessions', 'Незакрытые сессии', 3],
] as const;

function row(over: Partial<Record<string, unknown>> = {}) {
  return {
    employee_id: 'e-1',
    full_name: 'Каримов Алишер',
    employee_number: 'HT-001',
    office_id: 'o-1',
    office_name: 'Ташкент',
    state: 'IN_OFFICE',
    first_entry_at: `${DAY}T08:56:00Z`,
    last_exit_at: `${DAY}T12:04:00Z`,
    // Две сессии: разница между первым входом и последним выходом
    // (3 ч 8 мин) — НЕ время в офисе.
    seconds: 5 * 3600 + 8 * 60,
    open_session_id: 's-2',
    late_minutes: null,
    scheduled_start: '09:00:00',
    scheduled_end: '18:00:00',
    department_name: 'Операционный отдел',
    position_name: 'Специалист поддержки',
    absence_code: null,
    absence_name: null,
    notice_kind: null,
    notice_comment: null,
    conflicting_marks: false,
    outside_geofence: false,
    intervals: [
      { started_at: `${DAY}T08:56:00Z`, ended_at: `${DAY}T12:04:00Z`, seconds: 2 * 3600 + 30 * 60 },
      { started_at: `${DAY}T13:10:00Z`, ended_at: null, seconds: 2 * 3600 + 38 * 60 },
    ],
    ...over,
  };
}

const ROWS = [
  row(),
  row({ employee_id: 'e-2', full_name: 'Саидова Дилноза', employee_number: 'HT-004',
        state: 'NOT_COME', first_entry_at: null, last_exit_at: null, seconds: 0,
        open_session_id: null, intervals: [] }),
  row({ employee_id: 'e-3', full_name: 'Нурматов Жавохир', employee_number: 'HT-009',
        state: 'NO_SCHEDULE', scheduled_start: null, scheduled_end: null,
        late_minutes: null, last_exit_at: null, intervals: [] }),
  // Предупредил, что задерживается: это не «нет отметки», и разница
  // между написавшим и пропавшим должна быть видна в строке.
  row({ employee_id: 'e-5', full_name: 'Юсупова Камила', employee_number: 'HT-015',
        state: 'LATE', first_entry_at: null, last_exit_at: null, seconds: 0,
        open_session_id: null, intervals: [],
        notice_kind: 'LATE', notice_comment: 'Пробки' }),
  // Единственный опоздавший и единственный, у кого день закрыт.
  row({ employee_id: 'e-4', full_name: 'Рахимов Тимур', employee_number: 'HT-012',
        state: 'LEFT', late_minutes: 14, open_session_id: null,
        first_entry_at: `${DAY}T09:14:00Z`, last_exit_at: `${DAY}T18:02:00Z`,
        seconds: 8 * 3600 + 48 * 60,
        intervals: [
          { started_at: `${DAY}T09:14:00Z`, ended_at: `${DAY}T18:02:00Z`,
            seconds: 8 * 3600 + 48 * 60 },
        ] }),
];

const EVENTS = [
  { id: 'v-1', employee_id: 'e-1', employee: { id: 'e-1', full_name: 'Каримов Алишер', employee_number: 'HT-001' }, office_id: 'o-1', office_name: 'Главный офис',
    qr_point_id: 'q-1', qr_point_name: 'Главный вход', event_type: 'ENTRY',
    source: 'QR', verification_status: 'ACCEPTED',
    occurred_at: `${DAY}T08:56:00Z`, received_at: `${DAY}T08:56:00Z`,
    rejection_reason: null, inside_geofence: true, inside_office_network: true },
  { id: 'v-2', employee_id: 'e-1', employee: { id: 'e-1', full_name: 'Каримов Алишер', employee_number: 'HT-001' }, office_id: 'o-1', office_name: 'Главный офис',
    qr_point_id: 'q-2', qr_point_name: 'Служебный вход', event_type: 'EXIT',
    source: 'QR', verification_status: 'ACCEPTED',
    occurred_at: `${DAY}T12:04:00Z`, received_at: `${DAY}T12:04:00Z`,
    rejection_reason: null, inside_geofence: true, inside_office_network: true },
];

const OVERVIEW = {
  period: { first: DAY, last: DAY, days: 1 }, previous_period: { first: DAY, last: DAY },
  weekday: null, generated_at: `${DAY}T10:00:00Z`, timezones: ['Asia/Dushanbe'],
  summary: {
    attendance: { numerator: 18, denominator: 20, percent: 90 },
    previous_attendance: { numerator: 17, denominator: 20, percent: 85 },
    difference_points: 5, on_time: { numerator: 16, denominator: 18, percent: 88.9 },
    late: { numerator: 2, denominator: 18, percent: 11.1 }, average_seconds: 27720,
    open_sessions: 0, missed_days: 2, vacation_days: 3, sick_leave_days: 1, other_absence_days: 0,
  },
  days: [], previous_days: [], offices: [], regions: [],
  employees: [
    { id: 'e-1', name: 'Каримов Алишер', attendance: { numerator: 4, denominator: 5, percent: 80 },
      late_days: 1, late_minutes: 12, missed_days: 1, seconds: 4 * 8 * 3600, vacation_days: 0, sick_days: 0,
      other_days: 0, office_id: 'o-1', missed_dates: ['2026-09-02'], late_dates: [{ day: '2026-09-03', minutes: 12 }] },
    { id: 'e-2', name: 'Саидова Дилноза', attendance: { numerator: 5, denominator: 5, percent: 100 },
      late_days: 0, late_minutes: 0, missed_days: 0, seconds: 5 * 8 * 3600, vacation_days: 2, sick_days: 1,
      other_days: 0, office_id: 'o-1', missed_dates: [], late_dates: [] },
  ],
  arrivals: { bucket_minutes: 10, from_minutes: -60, to_minutes: 90, buckets: [], start_time: '09:00',
    uniform_start: true, median_minutes: 540, after_start: 0, late: { numerator: 0, denominator: 0, percent: null } },
  weekdays: { days: [] },
};

function network(
  handler: (path: string, method: string) => Response | null = () => null,
  permissions: string[] = ['attendance.read', 'attendance.manual'],
) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, { ...USER, permissions });
    if (path.includes('/regions/')) {
      return json(200, {
        items: [
          { id: 'r-1', name: 'Ташкент', code: 'TASHKENT', status: 'ACTIVE' },
          { id: 'r-2', name: 'Бухара', code: 'BUKHARA', status: 'ACTIVE' },
        ],
        next_cursor: null, has_more: false,
      });
    }
    if (path.includes('/departments/')) {
      return json(200, {
        items: [
          { id: 'd-1', name: 'Операционный отдел', office_id: 'o-1',
            office_name: 'Главный офис', status: 'ACTIVE', staff: 4 },
        ],
        next_cursor: null, has_more: false,
      });
    }
    if (path.includes('/dashboard')) {
      return json(200, {
        date: DAY,
        timezone: 'Asia/Dushanbe',
        cards: CARDS.map(([key, title, value]) => ({
          key, title, value, endpoint: null, params: {}, attention: false,
        })),
        warnings: [],
      });
    }
    if (path.includes('/attendance/presence')) {
      return json(200, {
        date: DAY, timezone: 'Asia/Dushanbe',
        counts: { IN_OFFICE: 1, NOT_COME: 1, NO_SCHEDULE: 1, LEFT: 1 },
        total: ROWS.length, truncated: false, items: ROWS,
      });
    }
    if (path.includes('/analytics/overview')) return json(200, OVERVIEW);
    if (path.includes('/attendance/events')) {
      return json(200, { items: EVENTS, next_cursor: null, has_more: false });
    }
    if (path.includes('/attendance/sessions')) {
      return json(200, {
        items: [{ id: 's-2', employee_id: 'e-1', office_id: 'o-1', office_name: 'Главный офис',
                  started_at: `${DAY}T13:10:00Z`, ended_at: null, duration_seconds: null,
                  status: 'OPEN', is_open: true }],
        next_cursor: null, has_more: false,
      });
    }
    return crm(path) ?? json(200, { items: [] });
  });
}

const tableRow = (name: string) => (screen.getByText(name).closest('[role="row"]') as HTMLElement);

describe('один экран', () => {
  test('смена режима не пересоздаёт лист и линию под вкладкой', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Каримов Алишер');

    const sheet = document.querySelector('.at-sheet');
    const ink = document.querySelector('.at-tabs__ink');
    fireEvent.click(screen.getByRole('tab', { name: 'Журнал отметок' }));
    expect(await screen.findByRole('heading', { name: 'Журнал отметок' })).toBeTruthy();

    expect(document.querySelector('.at-sheet')).toBe(sheet);
    expect(document.querySelector('.at-tabs__ink')).toBe(ink);
    expect(screen.getByRole('tabpanel', { name: 'Журнал отметок' })).toBeTruthy();
  });

  test('вкладки «Период» больше нет: старые ссылки ведут в «Сравнение» с теми же датами', async () => {
    network();
    renderApp(`/attendance?period=range&from=2026-09-01&to=${DAY}`);

    expect(await screen.findByRole('tab', { name: 'Сравнение', selected: true })).toBeTruthy();
    expect(screen.queryByRole('tab', { name: 'Период' })).toBeNull();
    expect(await screen.findByText(/1–4 сентября 2026 · Сравнение: все сотрудники/)).toBeTruthy();
  });

  test('старая ссылка на неделю по-прежнему открывает неделю', async () => {
    network();
    renderApp(`/attendance?period=week&date=${DAY}`);

    expect(await screen.findByRole('tab', { name: 'Неделя', selected: true })).toBeTruthy();
  });
});

describe('за день', () => {
  test('для HR строка без отметки открывает подтверждение выездной работы', async () => {
    network(() => null, ['attendance.read', 'attendance.correct']);
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Саидова Дилноза');
    fireEvent.click(within(tableRow('Саидова Дилноза')).getByRole('button', { name: 'Выездная работа' }));
    // Одна правая панель, форма встроена в неё — без второго окна.
    const card = await screen.findByRole('complementary', { name: 'Отметка за день' });
    expect(await within(card).findByLabelText(/Объект или место/)).toBeTruthy();
    expect(within(card).getByText(/До его ответа день не засчитывается/)).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  test('предупредившему об опоздании тоже можно запросить выездную работу; предупреждение остаётся видимым', async () => {
    network(() => null, ['attendance.read', 'attendance.correct']);
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Юсупова Камила');
    const line = tableRow('Юсупова Камила');
    expect(within(line).getByText('Пробки')).toBeTruthy();
    fireEvent.click(within(line).getByRole('button', { name: 'Выездная работа' }));
    const card = await screen.findByRole('complementary', { name: 'Отметка за день' });
    expect(await within(card).findByLabelText(/Объект или место/)).toBeTruthy();
    expect(screen.getAllByText('Пробки').length).toBeGreaterThanOrEqual(2);
  });

  test('числа полосы приходят с сервера, а не считаются по строкам', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);

    // 205 из 214 — это карточки дашборда, строк в таблице всего пять.
    expect(await screen.findByText('205 из 214 по графику (205 в офисе, 0 на выезде)')).toBeTruthy();
    expect(screen.getByText('96%')).toBeTruthy();
    expect(screen.getByText('+5 п.п. к вчера')).toBeTruthy();
  });

  test('для прошедшей даты полоса не говорит «сейчас» и «сегодня»', async () => {
    network();
    renderApp('/attendance?date=2020-01-02');

    expect(await screen.findByText('Статус дня')).toBeTruthy();
    expect(screen.queryByText('Статус на сейчас')).toBeNull();
    expect(screen.getByText('Явка за день')).toBeTruthy();
  });

  test('«Требуют внимания» — по одному правилу для чипа и строк', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Саидова Дилноза');

    // Число на чипе и строки таблицы считаются одним правилом. День
    // прошедший, поэтому незакрытая смена — тоже повод посмотреть.
    const chip = screen.getByRole('button', { name: /Требуют внимания/ });
    const rows = document.querySelectorAll('.at-table__body [role="row"]');
    expect(chip.textContent).toMatch(new RegExp(`${rows.length}$`));
    expect(screen.getByText('Рахимов Тимур')).toBeTruthy();
    expect(document.querySelectorAll('.at-row--warn, .at-row--bad').length).toBe(rows.length);
  });

  test('время в офисе берётся с сервера, а не разницей входа и выхода', async () => {
    network();
    renderApp(`/attendance?date=${DAY}&quick=all`);
    await screen.findByText('Каримов Алишер');

    expect(within(tableRow('Каримов Алишер')).getByText('5 ч 08 мин')).toBeTruthy();
  });

  test('«нет отметки» не называется прогулом, без графика нет опоздания', async () => {
    network();
    renderApp(`/attendance?date=${DAY}&quick=all`);
    await screen.findByText('Саидова Дилноза');

    expect(within(tableRow('Саидова Дилноза')).getByText('Нет отметки')).toBeTruthy();
    expect(screen.queryByText(/[Пп]рогул/)).toBeNull();
    const plain = tableRow('Нурматов Жавохир');
    expect(within(plain).getByText('Без графика')).toBeTruthy();
    expect(within(plain).queryByText(/Опоздал/)).toBeNull();
  });

  test('предупредивший виден отдельно, и его причина — рядом со статусом', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Юсупова Камила');

    const line = tableRow('Юсупова Камила');
    expect(within(line).getByText('Предупредил об опоздании')).toBeTruthy();
    expect(within(line).getByText('Пробки')).toBeTruthy();
  });

  test('обрезанный ответ не выдаётся за полный состав', async () => {
    network((path) => (path.includes('/attendance/presence')
      ? json(200, { date: DAY, timezone: 'Asia/Dushanbe', counts: {}, total: 900, truncated: true, items: ROWS })
      : null));
    renderApp(`/attendance?date=${DAY}`);

    expect(await screen.findByText(/Показаны не все сотрудники/)).toBeTruthy();
  });

  test('ошибка не превращается в пустой состав', async () => {
    network((path) => (path.includes('/attendance/presence') ? json(500, { error: {} }) : null));
    renderApp(`/attendance?date=${DAY}`);

    expect(await screen.findByText(/Не удалось загрузить сотрудников/)).toBeTruthy();
  });

  test('отдел уходит на сервер, день — выбранный', async () => {
    const calls = network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Каримов Алишер');

    await pick('Отдел', 'Операционный отдел');
    await waitFor(() => {
      const last = calls.filter((c) => c.url.includes('/attendance/presence')).pop()?.url ?? '';
      expect(last).toContain('department_id=d-1');
      expect(last).toContain(`date=${DAY}`);
    });
  });
});

describe('карточка дня', () => {
  test('ссылка на день сотрудника открывает его отметки', async () => {
    network();
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    const card = await screen.findByRole('complementary', { name: 'Отметка за день' });
    expect(await within(card).findByText('Каримов Алишер')).toBeTruthy();
    expect((await within(card).findAllByText(/Главный вход|Служебный вход/)).length).toBe(2);
  });

  test('«Исправить» открывает добавление отметки; без причины не сохранить', async () => {
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/attendance/manual')
        ? json(409, { error: { code: 'conflict', message: 'уже есть' } })
        : null,
    );
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Саидова Дилноза');

    fireEvent.click(within(tableRow('Саидова Дилноза')).getByRole('button', { name: 'Исправить' }));
    fireEvent.click(await screen.findByRole('button', { name: /Добавить отметку/ }));
    expect(screen.getByRole('button', { name: 'Сохранить' }).hasAttribute('disabled')).toBe(true);

    const reason = screen.getByLabelText('Причина');
    fireEvent.change(reason, { target: { value: 'Забыл отметиться на входе' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await screen.findByRole('alert');
    expect((reason as HTMLTextAreaElement).value).toBe('Забыл отметиться на входе');
    expect(calls.filter((c) => c.url.includes('/attendance/manual'))).toHaveLength(1);
  });
});

describe('журнал отметок', () => {
  test('показывает события дня с именами и ручной отметкой HR', async () => {
    network((path) => (path.includes('/attendance/events')
      ? json(200, {
        items: [...EVENTS, {
          ...EVENTS[0], id: 'v-3', source: 'MANUAL', author_name: 'Саидова Мадина',
          occurred_at: `${DAY}T10:00:00Z`, qr_point_name: null,
        }],
        next_cursor: null, has_more: false,
      })
      : null));
    renderApp(`/attendance?date=${DAY}&tab=log`);

    expect(await screen.findByText('HR (Саидова М.)')).toBeTruthy();
    expect(screen.getAllByText('Каримов Алишер').length).toBe(3);
  });

  test('весь день собирается по страницам, а не по первой', async () => {
    let pages = 0;
    network((path) => {
      if (!path.includes('/attendance/events')) return null;
      pages += 1;
      return json(200, path.includes('cursor=')
        ? { items: [EVENTS[1]], next_cursor: null, has_more: false }
        : { items: [EVENTS[0]], next_cursor: 'next', has_more: true });
    });
    renderApp(`/attendance?date=${DAY}&tab=log`);

    await waitFor(() => expect(screen.getAllByText('Каримов Алишер').length).toBe(2));
    expect(pages).toBe(2);
  });
});

describe('неделя', () => {
  test('день, который ещё не наступил, не запрашивается и не «без отметки»', async () => {
    const future = new Date();
    future.setDate(future.getDate() + 3);
    const pad = (n: number) => String(n).padStart(2, '0');
    const anchor = `${future.getFullYear()}-${pad(future.getMonth() + 1)}-${pad(future.getDate())}`;
    const now = new Date();
    const todayIso = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    const calls = network();
    renderApp(`/attendance?tab=week&date=${anchor}`);

    await screen.findByText(/Недельная сводка/);
    await waitFor(() => expect(calls.some((c) => c.url.includes('/analytics/overview'))).toBe(true));
    const asked = calls
      .filter((c) => c.url.includes('/attendance/presence'))
      .map((c) => new URL(c.url, 'http://x').searchParams.get('date') ?? '');
    expect(asked.every((one) => one <= todayIso)).toBe(true);
  });
});

describe('сравнение', () => {
  const OFFICES = [
    { id: 'o-1', code: 'HQ', name: 'Главный офис', status: 'ACTIVE', region_id: 'r-1', region_name: 'Ташкент' },
    { id: 'o-2', code: 'CH', name: 'Чиланзар', status: 'ACTIVE', region_id: 'r-1', region_name: 'Ташкент' },
    { id: 'o-3', code: 'BK', name: 'Бухара-центр', status: 'ACTIVE', region_id: 'r-2', region_name: 'Бухара' },
  ];

  /** Сводка одного объекта: своя явка и свои опоздания. */
  function summaryOf(percent: number, late: number, name?: string) {
    return {
      ...OVERVIEW,
      summary: {
        ...OVERVIEW.summary,
        attendance: { numerator: percent / 5, denominator: 20, percent },
        late: { numerator: late, denominator: 18, percent: null },
      },
      days: [
        { day: '2026-09-03', weekday: 4, working: true, future: false, in_detail: true, expected: 10, attended: 9,
          percent: percent - 2, on_time: 9, on_time_percent: null, late: 0, missed: 1, vacation: 0, sick_leave: 0,
          other_absence: 0, average_seconds: null },
        { day: DAY, weekday: 5, working: true, future: false, in_detail: true, expected: 10, attended: 9,
          percent, on_time: 9, on_time_percent: null, late: 0, missed: 1, vacation: 0, sick_leave: 0,
          other_absence: 0, average_seconds: null },
      ],
      employees: name ? [{ ...OVERVIEW.employees[0], name }] : OVERVIEW.employees,
    };
  }

  /** Сеть сравнения: офисы со своими регионами, сводка — по фильтру запроса. */
  function compareNetwork(fail: (query: URLSearchParams) => boolean = () => false) {
    return network((path) => {
      if (path.includes('/offices/')) return json(200, { items: OFFICES });
      if (!path.includes('/analytics/overview')) return null;
      const query = new URL(path, 'http://x').searchParams;
      if (fail(query)) return json(500, { error: {} });
      const office = query.get('office_id');
      const region = query.get('region_id');
      const employee = query.get('employee_id');
      if (employee) return json(200, summaryOf(employee === 'e-1' ? 80 : 100, employee === 'e-1' ? 1 : 0));
      if (query.get('people_limit') === '2000') return json(200, OVERVIEW);
      if (office === 'o-1') return json(200, summaryOf(90, 2));
      if (office === 'o-2') return json(200, summaryOf(70, 5));
      if (office === 'o-3') return json(200, summaryOf(60, 0));
      if (region === 'r-1') return json(200, summaryOf(85, 7));
      if (region === 'r-2') return json(200, summaryOf(60, 0));
      return json(200, OVERVIEW);
    });
  }

  const overviewCalls = (calls: { url: string }[], key: string) => calls
    .filter((c) => c.url.includes('/analytics/overview'))
    .map((c) => new URL(c.url, 'http://x').searchParams)
    .filter((q) => q.get(key))
    .map((q) => q.get(key));

  const cmpRow = async (name: string) => {
    const table = await screen.findByRole('table', { name: 'Сравнение по цифрам' });
    return (await within(table).findByText(name)).closest('[role="row"]') as HTMLElement;
  };

  test('бывший «Период» — все сотрудники за те же даты, числа из сводки сервера', async () => {
    network();
    renderApp(`/attendance?tab=period&from=2026-09-01&to=${DAY}`);

    const table = await screen.findByRole('table', { name: 'Сотрудники за период' });
    const row = (await within(table).findByText('Саидова Дилноза')).closest('[role="row"]') as HTMLElement;
    const cells = within(row).getAllByRole('cell').map((cell) => cell.textContent);
    expect(cells).toEqual(['СДСаидова Дилноза', '5', '5', '100%', '40 ч 00 мин', '0', '0', '3']);
  });

  test('несколько офисов — отдельный запрос на каждый, за выбранный период', async () => {
    const calls = compareNetwork();
    renderApp(`/attendance?tab=compare&cmp=office&cmp_ids=o-1,o-2,o-3&from=2026-09-01&to=${DAY}`);

    expect(await within(await cmpRow('Главный офис')).findByText('90%')).toBeTruthy();
    expect(await within(await cmpRow('Чиланзар')).findByText('70%')).toBeTruthy();
    expect(await within(await cmpRow('Бухара-центр')).findByText('60%')).toBeTruthy();
    expect(new Set(overviewCalls(calls, 'office_id'))).toEqual(new Set(['o-1', 'o-2', 'o-3']));
    const periods = calls.filter((c) => c.url.includes('office_id=')).map((c) => new URL(c.url, 'http://x').searchParams);
    expect(periods.every((q) => q.get('date_from') === '2026-09-01' && q.get('date_to') === DAY)).toBe(true);

    // Полоса: выше всех, ниже всех и разрыв — из тех же ответов.
    expect(await screen.findByText('30 п.п.')).toBeTruthy();
    const chart = screen.getByRole('region', { name: 'График явки' });
    expect(within(chart).getByRole('img').getAttribute('aria-label')).toContain('Главный офис — 90%');
  });

  test('ошибка одного офиса не обнуляет остальные и не выглядит нулём', async () => {
    compareNetwork((query) => query.get('office_id') === 'o-2');
    renderApp(`/attendance?tab=compare&cmp=office&cmp_ids=o-1,o-2&from=2026-09-01&to=${DAY}`);

    expect(await within(await cmpRow('Главный офис')).findByText('90%')).toBeTruthy();
    const failed = await cmpRow('Чиланзар');
    expect(await within(failed).findByText(/Не удалось загрузить/)).toBeTruthy();
    expect(within(failed).queryByText('0%')).toBeNull();
    expect(screen.getByRole('button', { name: 'Чиланзар: не загрузилось' })).toBeTruthy();
    // Полоса считается по тому, что пришло, а не прячется целиком.
    expect(screen.queryByText(/Не удалось загрузить сравнение/)).toBeNull();
  });

  test('офисы одного региона: выбор региона сравнивает только его офисы', async () => {
    const calls = compareNetwork();
    renderApp(`/attendance?tab=compare&cmp=office&from=2026-09-01&to=${DAY}`);
    await cmpRow('Бухара-центр');

    await pick('Регион', 'Ташкент');
    await waitFor(() => expect(screen.queryByText('Бухара-центр')).toBeNull());
    expect(await within(await cmpRow('Чиланзар')).findByText('70%')).toBeTruthy();
    expect(await within(await cmpRow('Главный офис')).findByText('90%')).toBeTruthy();
    const table = screen.getByRole('table', { name: 'Сравнение по цифрам' });
    // Строка заголовка и два офиса региона.
    expect(within(table).getAllByRole('row')).toHaveLength(3);
    expect(new URL(calls.filter((c) => c.url.includes('office_id=o-1')).pop()!.url, 'http://x').searchParams.get('date_from')).toBe('2026-09-01');
  });

  test('регионы — по одному запросу на регион из доступных офисов', async () => {
    const calls = compareNetwork();
    renderApp(`/attendance?tab=compare&cmp=region&from=2026-09-01&to=${DAY}`);

    expect(await within(await cmpRow('Ташкент')).findByText('85%')).toBeTruthy();
    expect(await within(await cmpRow('Бухара')).findByText('60%')).toBeTruthy();
    expect(new Set(overviewCalls(calls, 'region_id'))).toEqual(new Set(['r-1', 'r-2']));
  });

  test('несколько сотрудников: выбор в списке — запрос на каждого', async () => {
    const calls = compareNetwork();
    renderApp(`/attendance?tab=compare&cmp=employee&cmp_office=o-1&from=2026-09-01&to=${DAY}`);

    const trigger = await screen.findByRole('button', { name: /^Сотрудники:/ });
    await waitFor(() => expect((trigger as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole('checkbox', { name: /Каримов Алишер/ }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /Саидова Дилноза/ }));

    expect(await within(await cmpRow('Каримов Алишер')).findByText('80%')).toBeTruthy();
    expect(await within(await cmpRow('Саидова Дилноза')).findByText('100%')).toBeTruthy();
    expect(new Set(overviewCalls(calls, 'employee_id'))).toEqual(new Set(['e-1', 'e-2']));
  });

  test('не больше восьми объектов, даже если в ссылке больше', async () => {
    const calls = compareNetwork();
    const ids = Array.from({ length: 11 }, (_, i) => `x-${i}`).join(',');
    renderApp(`/attendance?tab=compare&cmp=employee&cmp_ids=${ids}&from=2026-09-01&to=${DAY}`);

    await waitFor(() => expect(overviewCalls(calls, 'employee_id').length).toBe(8));
    expect(new Set(overviewCalls(calls, 'employee_id')).has('x-8')).toBe(false);
  });

  test('«Сравнить всех сотрудников офиса» — одна кнопка, столбцы по каждому', async () => {
    const calls = compareNetwork();
    renderApp(`/attendance?tab=compare&cmp=employee&cmp_office=o-1&from=2026-09-01&to=${DAY}`);

    fireEvent.click(await screen.findByRole('button', { name: 'Сравнить всех сотрудников офиса' }));

    const bars = await screen.findByRole('list', { name: 'Явка сотрудников' });
    const items = within(bars).getAllByRole('listitem').map((one) => one.textContent);
    // Сначала лучшие: 100% выше 80%.
    expect(items).toEqual(['Саидова Дилноза100%', 'Каримов Алишер80%']);
    const asked = calls.filter((c) => c.url.includes('/analytics/overview')).map((c) => new URL(c.url, 'http://x').searchParams);
    expect(asked.some((q) => q.get('office_id') === 'o-1' && q.get('people_limit') === '2000')).toBe(true);
    expect(screen.getByText(/Сравнение: все сотрудники, Главный офис/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Сначала худшие' }));
    await waitFor(() => {
      const again = within(screen.getByRole('list', { name: 'Явка сотрудников' })).getAllByRole('listitem');
      expect(again[0]!.textContent).toBe('Каримов Алишер80%');
    });
  });

  test('выгрузка «всех сотрудников» — за период сравнения и по его офису', async () => {
    const calls = compareNetwork();
    renderApp(`/attendance?tab=compare&cmp=staff&cmp_office=o-1&from=2026-09-01&to=${DAY}`);

    fireEvent.click(await screen.findByRole('button', { name: /Экспорт/ }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/export-jobs/'))).toBe(true));
    const body = calls.find((c) => c.url.includes('/export-jobs/'))!.body as Record<string, string>;
    expect(body).toMatchObject({ kind: 'attendance', date_from: '2026-09-01', date_to: DAY, office_id: 'o-1' });
  });

  test('ошибка состава «всех сотрудников» — словами, а не пустым списком', async () => {
    compareNetwork((query) => query.get('people_limit') === '2000');
    renderApp(`/attendance?tab=compare&cmp=staff&cmp_office=o-1&from=2026-09-01&to=${DAY}`);

    expect((await screen.findAllByText(/Не удалось загрузить сотрудников/)).length).toBeGreaterThan(0);
    expect(screen.queryByText('За период по графику никого не ждали.')).toBeNull();
  });
});
