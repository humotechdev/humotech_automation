/**
 * Аналитика: один лист, семь вкладок.
 *
 * Проверяется то, что легко сломать незаметно: лист не пересоздаётся при
 * смене вкладки, фильтры уходят в запросы, отсутствием считается только
 * подтверждённое, ошибка не превращается в нули, а действия («Напомнить»,
 * «Экспорт») делают ровно один запрос.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, pick, renderApp } from './helpers';

const ratio = (numerator: number, denominator: number) => ({
  numerator, denominator, percent: denominator ? Math.round((numerator * 1000) / denominator) / 10 : null,
});

function day(date: string, over: Record<string, unknown> = {}) {
  return {
    day: date, weekday: 1, working: true, future: false, in_detail: true,
    expected: 230, attended: 222, percent: 96.5, on_time: 200, on_time_percent: 90.1,
    late: 22, missed: 8, vacation: 1, sick_leave: 0, other_absence: 0, trip: 0, average_seconds: 8 * 3600,
    ...over,
  };
}

function overview(over: Record<string, unknown> = {}) {
  return {
    period: { first: '2026-08-03', last: '2026-08-09', days: 7 },
    previous_period: { first: '2026-07-27', last: '2026-08-02' },
    weekday: null, generated_at: '2026-08-10T09:00:00Z', timezones: ['Asia/Tashkent'],
    summary: {
      attendance: ratio(4608, 4800), previous_attendance: ratio(4536, 4800), difference_points: 1.5,
      on_time: ratio(4378, 4608), late: ratio(230, 4608), average_seconds: 7 * 3600 + 48 * 60,
      open_sessions: 3, missed_days: 192, vacation_days: 4, sick_leave_days: 2, other_absence_days: 1, trip_days: 3,
    },
    days: ['2026-08-03', '2026-08-04', '2026-08-05'].map((d) => day(d)),
    previous_days: [],
    offices: [
      { id: 'o-1', name: 'Бухара', position: 1, attendance: ratio(97, 100), previous_attendance: ratio(91, 100), difference_points: 6 },
    ],
    regions: [],
    departments: [
      { id: 'd-1', name: 'Продажи', attendance: ratio(61, 100), previous_attendance: ratio(70, 100), difference_points: -9 },
    ],
    positions: [], heads: [],
    employees: [
      { id: 'e-9', name: 'Саидова Дилноза', attendance: ratio(3, 5), late_days: 2, late_minutes: 35, missed_days: 2,
        seconds: 3 * 8 * 3600, vacation_days: 0, sick_days: 0, other_days: 0, office_id: 'o-1',
        missed_dates: ['2026-08-05'], late_dates: [] },
    ],
    arrivals: { bucket_minutes: 10, from_minutes: -60, to_minutes: 90, buckets: [], start_time: '09:00',
      uniform_start: true, median_minutes: 537, after_start: 20, late: ratio(16, 80) },
    weekdays: { days: [], best: null },
    ...over,
  };
}

const TEAM = {
  period: { first: '2026-08-03', last: '2026-08-09', days: 7 },
  previous_period: { first: '2026-07-27', last: '2026-08-02' },
  summary: { headcount: 48, headcount_start: 46, previous_headcount_start: 45, hired: 2, previous_hired: 1,
    promoted: 1, previous_promoted: 0, left: 1, previous_left: 2, probation_failed: 0 },
  series: [{ day: '2026-08-03', headcount: 46 }, { day: '2026-08-09', headcount: 48 }],
  by_department: [],
  departments: [{ id: 'd-1', name: 'Продажи', headcount: 8, previous_headcount: 7 }],
  offices: [{ id: 'o-1', name: 'Бухара', headcount: 48, previous_headcount: 46 }],
  hires: [{ id: 'e-5', name: 'Иванов Алексей', employee_number: null, status: 'PROBATION', hire_date: '2026-08-05',
    office: 'Бухара', department: 'Продажи', position: 'Менеджер' }],
  departures: [],
  transfers: [{ id: 'e-7', name: 'Собиров Умид', date: '2026-08-06', from_office: 'Бухара', to_office: 'Бухара',
    from_department: 'Продажи', to_department: 'Поддержка' }],
};

const PROBATION = {
  period: TEAM.period, previous_period: TEAM.previous_period,
  summary: { active: 2, due: 1, overdue: 0, started: 1, promoted: 3, failed: 1, previous_started: 0,
    previous_promoted: 2, previous_failed: 1, conversion_percent: 75, previous_conversion_percent: 66.7 },
  due_days: 7,
  trainees: [
    { id: 'e-5', name: 'Иванов Алексей', employee_number: null, status: 'PROBATION', hire_date: '2026-07-10',
      office: 'Бухара', department: 'Продажи', position: null, probation_from: '2026-07-10',
      probation_to: '2026-08-12', days_left: 3, days_total: 34 },
  ],
};

const OPEN = {
  items: [{
    kind: 'absence', id: 'q-1', created_at: '2026-08-01T08:00:00Z', place: null,
    absence: {
      id: 'q-1', employee: { id: 'e-3', full_name: 'Мурадов Азизбек', employee_number: null },
      absence_type: { code: 'SICK_LEAVE', name: 'Больничный' }, status: 'SUBMITTED',
      first_day: '2026-08-03', last_day: '2026-08-05', submitted_at: '2026-08-01T08:00:00Z',
      documents: [], requires_document: true, stage: 'NEEDS_FIX', missing_for_approval: ['certificate'],
      comment: null, review_comment: null,
    },
  }],
  next_cursor: null, has_more: false,
};

const CAMPAIGN = {
  id: 'c-1', template_id: 't', template_title: 'Оценка адаптации', title: 'Оценка адаптации', status: 'ACTIVE',
  audience_kind: 'ALL', audience_ids: null, scheduled_at: null, repeat_months: null, remind_at: null, due_at: null,
  next_send_at: null, sent_at: '2026-08-04T06:00:00Z', template_version: 1, automation_id: null, created_at: '2026-08-04T06:00:00Z',
};

const RECIPIENTS = {
  items: [
    { id: 'x-1', employee_id: 'e-1', full_name: 'Каримов Алишер', status: 'COMPLETED', sent_at: '2026-08-04T06:00:00Z',
      started_at: null, completed_at: '2026-08-05T06:00:00Z', skip_reason: null, office_name: 'Бухара', department_name: 'Продажи' },
    { id: 'x-2', employee_id: 'e-2', full_name: 'Рахимова Дилноза', status: 'SENT', sent_at: '2026-08-04T06:00:00Z',
      started_at: null, completed_at: null, skip_reason: null, office_name: 'Бухара', department_name: 'Продажи' },
  ],
};

const ONBOARDING = {
  items: [{
    employee_id: 'e-2', full_name: 'Рахимова Дилноза', employee_number: null, office_name: 'Бухара', department_name: 'Продажи',
    position_name: null, telegram_state: 'LINKED', status: 'NOT_STARTED', stage: 'SECTIONS', completed: false,
    sections_done: 0, sections_total: 5, policies_done: 0, policies_total: 2, invited_at: '2026-08-01T06:00:00Z',
    started_at: null, completed_at: null, last_reminder_at: null, invitation_status: null, invitation_expires_at: null,
  }],
  next_cursor: null, has_more: false,
};

function network(handler: (path: string, method: string) => Response | null = () => null) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/analytics/overview')) return json(200, overview());
    if (path.includes('/analytics/team')) return json(200, TEAM);
    if (path.includes('/analytics/probation')) return json(200, PROBATION);
    if (path.includes('/regions/')) return json(200, { items: [{ id: 'r-1', code: 'C', name: 'Центр', status: 'ACTIVE' }] });
    if (path.includes('/offices/')) return json(200, { items: [{ id: 'o-1', code: 'B', name: 'Бухара', status: 'ACTIVE', region_id: 'r-1', region_name: 'Центр' }] });
    if (path.includes('/departments/')) return json(200, { items: [{ id: 'd-1', name: 'Продажи' }], next_cursor: null, has_more: false });
    if (path.includes('/requests')) return json(200, path.includes('status=') ? OPEN : { items: [], next_cursor: null, has_more: false });
    if (path.includes('/recipients')) return json(200, RECIPIENTS);
    if (path.includes('/surveys/campaigns')) return json(200, { items: [CAMPAIGN], next_cursor: null, has_more: false });
    if (path.includes('/onboarding/progress')) return json(200, ONBOARDING);
    if (path.includes('/onboarding/documents')) return json(200, { items: [] });
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

const PAGE = '/analytics?from=2026-08-03&to=2026-08-09';
const urls = (calls: { url: string }[], part: string) => calls.filter((c) => c.url.includes(part)).map((c) => c.url);

describe('один лист', () => {
  test('смена вкладки не пересоздаёт лист и линию под вкладкой', async () => {
    network();
    renderApp(PAGE);
    await screen.findByText('48');

    const sheet = document.querySelector('.ax-sheet');
    const ink = document.querySelector('.ax-tabs__ink');
    fireEvent.click(screen.getByRole('tab', { name: 'Посещаемость' }));
    expect(await screen.findByText(/^Посещаемость и рабочее время/)).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'Аналитика' })).toBeTruthy();

    expect(document.querySelector('.ax-sheet')).toBe(sheet);
    expect(document.querySelector('.ax-tabs__ink')).toBe(ink);
    expect(screen.getByRole('tabpanel', { name: 'Посещаемость' })).toBeTruthy();
  });

  test('вкладка открывается по ссылке', async () => {
    network();
    renderApp(`${PAGE}&tab=team`);

    expect(await screen.findByRole('tab', { name: 'Команда', selected: true })).toBeTruthy();
    expect(await screen.findByText(/^Команда и кадровые изменения/)).toBeTruthy();
  });
});

describe('фильтры', () => {
  test('период, регион и отдел уходят в запрос сводки', async () => {
    const calls = network();
    renderApp(PAGE);
    await screen.findByText('48');

    await pick('Регион', 'Центр');
    await pick('Отдел', 'Продажи');
    await waitFor(() => {
      const last = urls(calls, '/analytics/overview').filter((u) => u.includes('people_limit=300')).pop() ?? '';
      expect(last).toContain('date_from=2026-08-03');
      expect(last).toContain('date_to=2026-08-09');
      expect(last).toContain('region_id=r-1');
      expect(last).toContain('department_id=d-1');
    });
  });

  test('разница считается с прошлым периодом той же длины', async () => {
    const calls = network();
    renderApp(PAGE);
    await screen.findByText('48');

    await waitFor(() => {
      const previous = urls(calls, '/analytics/overview').find((u) => u.includes('people_limit=1')) ?? '';
      expect(previous).toContain('date_from=2026-07-27');
      expect(previous).toContain('date_to=2026-08-02');
    });
  });

  test('экспорт заказывается с теми же фильтрами, включая отдел; у опросов своей выгрузки нет', async () => {
    const calls = network((path, method) => {
      if (method === 'POST' && path.includes('/export-jobs')) return json(201, { id: 'j-1', status: 'QUEUED' });
      if (path.includes('/reports/catalog')) {
        return json(200, { kinds: [{ key: 'attendance', title: 'Посещаемость', permission: 'x', fields: [
          { key: 'date', title: 'Дата', default: true, columns: [] }, { key: 'extra', title: 'Ещё', default: false, columns: [] },
        ] }], max_period_days: 366, xlsx_max_rows: 1, retention_hours: 1, preview_min_rows: 1, preview_max_rows: 1 });
      }
      return null;
    });
    renderApp(`${PAGE}&office_id=o-1&department_id=d-1&tab=attendance`);
    await screen.findByText(/^Посещаемость и рабочее время/);

    fireEvent.click(screen.getByRole('button', { name: /Экспорт/ }));
    await screen.findByRole('status');
    const order = calls.filter((c) => c.url.includes('/export-jobs')).map((c) => c.body as Record<string, unknown>);
    expect(order).toHaveLength(1);
    expect(order[0]).toMatchObject({
      kind: 'attendance', builder: true, date_from: '2026-08-03', date_to: '2026-08-09',
      office_ids: ['o-1'], department_ids: ['d-1'], fields: ['date'],
    });

    fireEvent.click(screen.getByRole('tab', { name: 'Опросы' }));
    expect(screen.getByRole('button', { name: /Экспорт/ }).hasAttribute('disabled')).toBe(true);
  });
});

describe('посещаемость', () => {
  test('явка и разница — с сервера, в пунктах', async () => {
    network();
    renderApp(`${PAGE}&tab=attendance`);

    expect(await screen.findByText('96%')).toBeTruthy();
    expect(screen.getByText(/\+1,5 п\.п\./)).toBeTruthy();
    expect(screen.getByText('7 ч 48 мин')).toBeTruthy();
    expect(screen.getByText('пришли вовремя')).toBeTruthy();
  });

  test('«Сравнить по» переключает разрез без второго правила явки', async () => {
    network();
    renderApp(`${PAGE}&tab=attendance`);
    await screen.findByText('Сравнение по офисам');

    await pick('Сравнить по', 'По отделам');
    expect(await screen.findByText('Сравнение по отделам')).toBeTruthy();
    expect(screen.getByText('61%')).toBeTruthy();
  });

  test('ошибка не превращается в нулевые показатели', async () => {
    network((path) => (path.includes('/analytics/overview') ? json(500, { error: {} }) : null));
    renderApp(`${PAGE}&tab=attendance`);

    expect((await screen.findAllByText(/Не удалось загрузить данные/)).length).toBeGreaterThan(0);
    expect(screen.queryByText('0%')).toBeNull();
  });
});

describe('отсутствия и заявки', () => {
  test('отсутствия — подтверждённые дни; видно, чьего действия ждёт заявка', async () => {
    network();
    renderApp(`${PAGE}&tab=absences`);

    // 4 отпуска + 2 больничных + 1 прочее + 3 командировки.
    await waitFor(() => expect(document.querySelector('.ax-kpis .ax-kpi__value')?.textContent).toBe('10'));
    const table = await screen.findByRole('table', { name: 'Заявки в работе' });
    const row = within(table).getByText('Мурадов Азизбек').closest('tr') as HTMLElement;
    // Нужны исправления — ход за сотрудником, а не за HR.
    expect(within(row).getByText('Сотрудник')).toBeTruthy();
  });

  test('заявка на дни уже подтверждённого отсутствия видна как пересечение', async () => {
    network((path) => (path.includes('/requests') && !path.includes('status=')
      ? json(200, { items: [{ ...OPEN.items[0], id: 'q-2', absence: { ...OPEN.items[0]!.absence, id: 'q-2', status: 'APPROVED',
        absence_type: { code: 'ANNUAL_LEAVE', name: 'Отпуск' }, first_day: '2026-08-04', last_day: '2026-08-08' } }], next_cursor: null, has_more: false })
      : null));
    renderApp(`${PAGE}&tab=absences`);

    const block = await screen.findByRole('region', { name: 'Пересечения' });
    expect(await within(block).findByText('Мурадов Азизбек')).toBeTruthy();
    expect(within(block).getByText(/накладывается на «Отпуск»/)).toBeTruthy();
  });
});

describe('команда и стажировки', () => {
  test('перевод показан с отдела на отдел, приём — в новых сотрудниках', async () => {
    network();
    renderApp(`${PAGE}&tab=team`);

    const transfers = await screen.findByRole('region', { name: 'Переводы' });
    expect(await within(transfers).findByText('Продажи → Поддержка')).toBeTruthy();
    expect(within(await screen.findByRole('table', { name: 'Новые сотрудники' })).getByText('Иванов Алексей')).toBeTruthy();
  });

  test('вывод о составе — только из настоящей разницы', async () => {
    network();
    renderApp(`${PAGE}&tab=team`);

    // Продажи: 8 сейчас против 7 — вырос на одного.
    expect(await screen.findByText('Отдел Продажи вырос на 1 сотрудника за период.')).toBeTruthy();
  });

  test('стажёр с решением через три дня — в ближайших, наставника не выдумываем', async () => {
    network();
    renderApp(`${PAGE}&tab=probation`);

    const table = await screen.findByRole('table', { name: 'Стажёры сейчас' });
    expect(within(table).getByText('3 дня')).toBeTruthy();
    expect(screen.queryByText('Наставник')).toBeNull();
    expect(screen.getByText(/Конверсия в найм — 75%/)).toBeTruthy();
  });
});

describe('опросы и ознакомления', () => {
  test('ждущие ответа считаются по идущим рассылкам', async () => {
    network();
    renderApp(`${PAGE}&tab=surveys`);

    const control = await screen.findByRole('table', { name: 'Нужен контроль' });
    expect(within(control).getByText('Рахимова Дилноза')).toBeTruthy();
    expect(screen.getByText('ждут ответа')).toBeTruthy();
  });

  test('«Напомнить» отправляет одно напоминание', async () => {
    const calls = network((path, method) => (method === 'POST' && path.includes('/onboarding/remind')
      ? json(200, { sent_at: '2026-08-09T10:00:00Z' }) : null));
    renderApp(`${PAGE}&tab=onboarding`);

    const table = await screen.findByRole('table', { name: 'Кому нужно напомнить' });
    fireEvent.click(within(table).getByRole('button', { name: /Напомнить/ }));
    expect(await within(table).findByText('Напомнили')).toBeTruthy();
    expect(calls.filter((c) => c.method === 'POST' && c.url.includes('/employees/e-2/onboarding/remind'))).toHaveLength(1);
  });
});
