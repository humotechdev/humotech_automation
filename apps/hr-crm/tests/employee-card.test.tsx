/**
 * Новая карточка сотрудника: данные сервера, состояния и действия.
 *
 * Проверяется то, что легко сломать незаметно: ошибка не выглядит нулём,
 * счётчик ожиданий берётся с сервера по всему набору, уведомление о
 * заявке появляется только при заявке, фильтр и догрузка уходят на
 * сервер, решение не отправляется дважды, история называет события
 * словами, файл документа открывает только кадровик.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { describe, expect, test } from 'vitest';

import { App } from '../src/app/App';
import { monthStartOf, todayIn } from '../src/features/employee/card-model';
import { SessionProvider } from '../src/features/auth/session';
import { USER, type Call, crm, fakeNetwork, json, pick, renderApp } from './helpers';

const ZONE = 'Asia/Tashkent';
const TODAY = todayIn(ZONE);

const PERSON = {
  id: 'e-1',
  employee_number: 'T-1',
  full_name: 'Тестов Тест Тестович',
  first_name: 'Тест',
  last_name: 'Тестов',
  middle_name: 'Тестович',
  phone: null,
  corporate_email: null,
  personal_email: null,
  birth_date: null,
  hire_date: '2026-09-25',
  termination_date: null,
  employment_status: 'ACTIVE',
  employment_status_title: 'Работает',
  gender: null,
  marital_status: null,
  photo: null,
  archived_at: null,
  documents: [
    { id: 'd-1', kind: 'OTHER', title: 'Договор', status: 'UPLOADED', file_id: 'f-1', file: null },
  ],
  current_assignment: {
    id: 'a-1', office_id: 'o-1', office_name: 'Главный офис', office_timezone: ZONE,
    region_id: 'r-1', region_name: 'Центр', department_id: 'dep-1',
    department_name: 'Продажи', position_id: 'p-1', position_name: 'Аналитик',
    employment_type: 'FULL_TIME', work_mode: 'ONSITE', is_primary: true,
    valid_from: '2026-09-25', valid_to: null,
  },
  current_schedule: {
    schedule_id: 's-1', name: 'Пятидневка', timezone: ZONE, status: 'ACTIVE',
    valid_from: '2026-09-25', valid_to: null, weekdays: [1, 2, 3, 4, 5],
    start_time: '09:00:00', end_time: '18:00:00',
  },
  assignment_history: [],
};

function day(date: string, extra: Record<string, unknown> = {}) {
  return {
    day: date, timezone: ZONE, office_id: 'o-1', office_name: 'Главный офис',
    state: 'NOT_COME', first_entry_at: null, last_exit_at: null, seconds: 0,
    sessions: 0, open_session_id: null, late_minutes: null,
    scheduled_start: '09:00:00', scheduled_end: '18:00:00',
    absence_code: null, absence_name: null, conflicting_marks: false, field_work: null,
    ...extra,
  };
}

const REPORT = {
  employee_id: 'e-1', timezone: ZONE, first: monthStartOf(TODAY), last: TODAY,
  days: [day(TODAY)],
  totals: { seconds: 85_800, days_with_marks: 3, working_days: 5, late_days: 1,
    late_minutes: 12, open_sessions: 0 },
  note: null,
};

const REQUEST = {
  kind: 'absence', id: 'r-1', created_at: '2026-10-06T05:00:00Z',
  absence: {
    id: 'r-1', employee: { id: 'e-1', full_name: 'Тестов Тест', employee_number: 'T-1' },
    absence_type: { code: 'SICK_LEAVE', name: 'Больничный' }, status: 'SUBMITTED',
    stage: 'HR_REVIEW', first_day: '2026-10-06', last_day: '2026-10-07',
    submitted_at: '2026-10-06T05:00:00Z', requires_document: true, missing_for_approval: [],
    comment: null, review_comment: null, documents: [],
  },
};

const ALL = ['employees.read', 'employees.manage', 'attendance.read', 'absences.read',
  'absences.approve', 'audit.read', 'telegram.read', 'onboarding.read', 'reports.export'];

type Handler = (url: string, call: Call) => Response | Promise<Response> | null;

function open(path: string, options: { perms?: string[]; handler?: Handler } = {}) {
  const calls = fakeNetwork((url, call) => {
    if (url.includes('/auth/')) {
      return json(200, { ...USER, timezone: ZONE, permissions: options.perms ?? ALL });
    }
    const own = options.handler?.(url, call);
    if (own) return own;
    if (url.includes('/employees/e-1/telegram')) {
      return json(200, { state: 'NOT_LINKED', account: null, invitation: null });
    }
    if (url.includes('/employees/e-1/onboarding')) return json(404, {});
    if (url.includes('/employees/e-1/')) return json(200, PERSON);
    if (url.includes('/attendance/daily')) return json(200, REPORT);
    if (url.includes('/requests/counts')) {
      return json(200, { open: 7, all: 9, leave: 2, sick: 5, fixes: 2, cancel: 0 });
    }
    if (url.includes('/requests')) {
      return json(200, { items: [REQUEST], next_cursor: null, has_more: false });
    }
    return crm(url) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
  renderApp(path);
  return calls;
}

const sent = (calls: Call[], part: string) => calls.filter((call) => call.url.includes(part));

describe('обзор', () => {
  test('числа — с сервера; ожидания — из счётчика по всему набору', async () => {
    open('/employees/e-1');
    await screen.findByRole('heading', { name: 'Тестов Тест' });
    // 85 800 с — ровно 23 ч 50 мин; ничего не пересчитано в браузере.
    expect(await screen.findByText('23 ч 50 мин')).toBeTruthy();
    // Семь из счётчика, хотя в списке одна строка.
    expect(await screen.findByText('7 заявок')).toBeTruthy();
    expect(screen.getByText('Больничный ожидает решения')).toBeTruthy();
  });

  test('сбой запроса — прочерк и «не удалось», а не ноль', async () => {
    open('/employees/e-1', {
      handler: (url) => (url.includes('/attendance/daily') || url.includes('/requests/counts')
        ? json(500, {}) : null),
    });
    await screen.findByRole('heading', { name: 'Тестов Тест' });
    expect((await screen.findAllByText('Не удалось получить')).length).toBeGreaterThan(0);
    expect(screen.queryByText('0 заявок')).toBeNull();
    expect(screen.queryByText('0 ч')).toBeNull();
  });

  test('без ожидающей заявки уведомления нет', async () => {
    open('/employees/e-1', {
      handler: (url) => (url.includes('/requests?') || url.endsWith('/requests')
        ? json(200, { items: [], next_cursor: null, has_more: false }) : null),
    });
    await screen.findByRole('heading', { name: 'Тестов Тест' });
    await screen.findByText('7 заявок');
    expect(screen.queryByText(/ожидает решения/)).toBeNull();
  });

  test('без назначения за период — прочерк, а не «0 ч»', async () => {
    open('/employees/e-1', {
      handler: (url) => (url.includes('/attendance/daily') ? json(200, {
        ...REPORT, days: [],
        totals: { seconds: 0, days_with_marks: 0, working_days: 0, late_days: 0,
          late_minutes: 0, open_sessions: 0 },
        note: 'У сотрудника нет кадрового назначения за этот период',
      }) : null),
    });
    expect(await screen.findByText('Нет назначения за этот период')).toBeTruthy();
    expect(screen.queryByText('0 ч')).toBeNull();
  });

  test('карточки нет или она вне области — понятный ответ', async () => {
    open('/employees/e-1', {
      handler: (url) => (url.includes('/employees/e-1/') ? json(404, {}) : null),
    });
    expect(await screen.findByText(/Сотрудник не найден/)).toBeTruthy();
  });
});

describe('посещаемость', () => {
  test('день раскрывается по нажатию, нет отметки — не прогул', async () => {
    const calls = open('/employees/e-1?tab=attendance');
    const button = await screen.findByRole('button', { name: /^Открыть \d/ });
    expect(screen.getByText('Нет отметки')).toBeTruthy();
    fireEvent.click(button);
    expect(await screen.findByText(/ещё не прогул/)).toBeTruthy();
    await waitFor(() => expect(sent(calls, '/attendance/events').length).toBeGreaterThan(0));
  });
});

describe('заявки', () => {
  test('фильтр уходит на сервер, а не режет загруженную страницу', async () => {
    const calls = open('/employees/e-1?tab=requests');
    await screen.findByText('Больничный');
    await pick('Статус заявок', 'Ждут решения');
    await waitFor(() => expect(sent(calls, 'status=SUBMITTED%2CIN_REVIEW').length
      + sent(calls, 'status=SUBMITTED,IN_REVIEW').length).toBeGreaterThan(0));
  });

  test('решение отправляется один раз, кнопки — только с правом', async () => {
    let answer: (value: Response) => void = () => undefined;
    const calls = open('/employees/e-1?tab=requests&request=r-1', {
      handler: (url, call) => (call.method === 'POST' && url.includes('/absence-requests/r-1/')
        ? new Promise<Response>((resolve) => { answer = resolve; }) : null),
    });
    const approve = await screen.findByRole('button', { name: 'Одобрить' });
    fireEvent.click(approve);
    fireEvent.click(approve);
    answer(json(200, {}));
    await waitFor(() => expect(
      calls.filter((call) => call.method === 'POST' && call.url.includes('/absence-requests/r-1/approve')),
    ).toHaveLength(1));
  });

  test('без права рассмотрения кнопок нет', async () => {
    open('/employees/e-1?tab=requests&request=r-1', {
      perms: ALL.filter((code) => code !== 'absences.approve'),
    });
    await screen.findByText(/Решение принимает тот, у кого есть право/);
    expect(screen.queryByRole('button', { name: 'Одобрить' })).toBeNull();
  });

  test('файл документа открывает только кадровик', async () => {
    open('/employees/e-1?tab=requests', {
      perms: ALL.filter((code) => code !== 'employees.manage'),
    });
    fireEvent.click(await screen.findByRole('button', { name: /Документы сотрудника/ }));
    expect(await screen.findByText('Файл доступен кадровой службе')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Открыть' })).toBeNull();
  });
});

describe('история', () => {
  test('событие словами, неизвестный код — нейтрально, подробности по нажатию', async () => {
    open('/employees/e-1?tab=history', {
      handler: (url) => (url.includes('/audit-logs') ? json(200, {
        items: [
          { id: 'h-1', action: 'employee.schedule.assign', entity_type: 'x', entity_id: 'e-1',
            occurred_at: '2026-09-25T11:54:00Z', actor_user_id: 'u-1', actor_email: 'hr@test.local',
            actor_name: 'Петрова Марина', actor_employee_id: null, old_values: null,
            new_values: { valid_from: '2026-09-25' }, ip_address: null, user_agent: null },
          { id: 'h-2', action: 'something.new', entity_type: 'x', entity_id: 'e-1',
            occurred_at: '2026-09-25T11:50:00Z', actor_user_id: null, actor_email: null,
            actor_employee_id: null, old_values: null, new_values: null,
            ip_address: null, user_agent: null },
        ],
        next_cursor: null, has_more: false,
      }) : null),
    });
    expect(await screen.findByText('Назначен рабочий график')).toBeTruthy();
    expect(screen.getByText('Изменение записи')).toBeTruthy();
    expect(screen.queryByText('employee.schedule.assign')).toBeNull();
    expect(screen.getByText('Петрова Марина')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Подробнее' })[0]!);
    expect(await screen.findByText('Действует с')).toBeTruthy();
  });
});

describe('переход к другому сотруднику', () => {
  /** Карточка e-1, затем переход на e-2 тем же маршрутом, без ухода со страницы. */
  function steer(second: () => Response | Promise<Response>) {
    fakeNetwork((url) => {
      if (url.includes('/auth/')) return json(200, { ...USER, timezone: ZONE, permissions: ALL });
      if (/\/employees\/e-2\/?$/.test(url)) return second();
      if (url.includes('/employees/e-2/')) return new Promise<Response>(() => undefined);
      if (url.includes('/employees/e-1/telegram')) {
        return json(200, { state: 'NOT_LINKED', account: null, invitation: null });
      }
      if (url.includes('/employees/e-1/onboarding')) return json(404, {});
      if (url.includes('/employees/e-1/')) return json(200, PERSON);
      if (url.includes('/attendance/daily')) return json(200, REPORT);
      return crm(url) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    let go: (to: string) => void = () => undefined;
    function Steer() {
      go = useNavigate();
      return null;
    }
    render(
      <MemoryRouter initialEntries={['/employees/e-1']}>
        <SessionProvider>
          <Steer />
          <App />
        </SessionProvider>
      </MemoryRouter>,
    );
    return (to: string) => act(() => go(to));
  }

  test('пока грузится новый, прошлого человека под его адресом нет', async () => {
    const go = steer(() => new Promise<Response>(() => undefined));
    expect(await screen.findByRole('heading', { name: 'Тестов Тест' })).toBeTruthy();

    go('/employees/e-2');

    expect(await screen.findByText('Открываем карточку…')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Тестов Тест' })).toBeNull();
  });

  test('сбой загрузки нового — ошибка, а не карточка прошлого', async () => {
    const go = steer(() => json(500, { error: { code: 'server_error', message: '…', details: null } }));
    expect(await screen.findByRole('heading', { name: 'Тестов Тест' })).toBeTruthy();

    go('/employees/e-2');

    expect(await screen.findByText(/Не удалось открыть карточку/)).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Тестов Тест' })).toBeNull();
  });
});
