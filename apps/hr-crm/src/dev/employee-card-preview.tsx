/**
 * Только для разработки: карточка сотрудника на вымышленных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Все имена и числа вымышлены.
 *
 *   ?tab=overview|attendance|schedule|requests|history
 *   ?case=full   — обычный сотрудник со всем, что бывает;
 *   ?case=empty  — новичок: без назначения, графика, заявок и истории;
 *   ?case=error  — сервер отвечает ошибкой в отдельных секциях;
 *   ?case=long   — очень длинные имена, должности, офисы, файлы.
 *   ?open=1      — раскрыть первую строку (день, заявку, событие).
 *   ?start=/employees — начать со списка сотрудников;
 *   ?delay=700   — ответы с задержкой (отмена запроса её прерывает);
 *   ?save=offline — сохранение правки обрывается связью.
 *
 * Сотрудники e-1…e-8: у каждого своё имя, правка карточки меняет данные
 * так же, как сервер, — результат виден и в карточке, и в списке.
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import { App } from '../app/App';
import { SessionProvider } from '../features/auth/session';
import { monthStartOf, todayIn } from '../features/employee/card-model';
import '../styles/app.css';
import '../styles/crm.css';

const ZONE = 'Asia/Tashkent';
const search = new URLSearchParams(window.location.search);
const CASE = search.get('case') ?? 'full';
const TAB = search.get('tab') ?? 'overview';
const OPEN = search.get('open') === '1';
const LONG = CASE === 'long';

const HR = {
  id: 'u-1', email: 'hr@demo.local', organization_id: 'org-1', organization_code: 'DEMO',
  employee_id: null, status: 'ACTIVE', timezone: ZONE, roles: ['HR'],
  permissions: ['employees.read', 'employees.manage', 'attendance.read', 'attendance.correct',
    'absences.read', 'absences.approve', 'audit.read', 'telegram.read', 'telegram.manage',
    'onboarding.read', 'onboarding.manage', 'reports.export', 'schedules.read'],
};

const TODAY = todayIn(ZONE);
const pad = (n: number) => String(n).padStart(2, '0');
const shift = (day: string, by: number) => {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + by);
  return d.toISOString().slice(0, 10);
};
const at = (day: string, hh: number, mm: number) => `${day}T${pad(hh - 5)}:${pad(mm)}:00Z`;

const OFFICE = LONG ? 'Головной офис компании на проспекте Амира Темура, корпус Б, третий этаж' : 'Главный офис';
const POSITION = LONG ? 'Ведущий специалист по исследованию пользовательского опыта и аналитике данных' : 'UX-исследователь';

const PERSON = CASE === 'empty' ? {
  id: 'e-1', employee_number: 'HT-0301', full_name: 'Каримова Нигора', first_name: 'Нигора',
  last_name: 'Каримова', middle_name: null, phone: null, corporate_email: null, personal_email: null,
  birth_date: null, hire_date: shift(TODAY, 5), termination_date: null,
  employment_status: 'PENDING', employment_status_title: 'Ожидает выхода', gender: null,
  marital_status: null, photo: null, archived_at: null, documents: [], current_assignment: null,
  current_schedule: null, assignment_history: [],
} : {
  id: 'e-1', employee_number: 'HT-0101',
  full_name: LONG ? 'Абдурахмонова-Сайфуллаева Гулчехра Абдукаххоровна' : 'Соколов Алексей Игоревич',
  first_name: LONG ? 'Гулчехра' : 'Алексей', last_name: LONG ? 'Абдурахмонова-Сайфуллаева' : 'Соколов',
  middle_name: 'Игоревич', phone: '+998 00 000 00 00', corporate_email: 'a.sokolov@demo.local',
  personal_email: null, birth_date: '1991-04-12', hire_date: '2026-09-25', termination_date: null,
  employment_status: 'ACTIVE', employment_status_title: 'Работает', gender: 'MALE',
  marital_status: 'MARRIED', photo: null, archived_at: null,
  documents: [
    { id: 'd-1', kind: 'CONTRACT', title: LONG ? 'Трудовой договор № 2026/09/25-0101 с дополнительными соглашениями и приложениями.pdf' : 'Трудовой договор', status: 'UPLOADED', file_id: 'f-1', file: null },
    { id: 'd-2', kind: 'HIRE_ORDER', title: 'Приказ о приёме', status: 'GENERATED_LATER', file_id: null, file: null },
  ],
  current_assignment: {
    id: 'a-1', office_id: 'o-1', office_name: OFFICE, office_timezone: ZONE, region_id: 'r-1',
    region_name: 'Ташкент', department_id: 'dep-1', department_name: 'Отдел продаж',
    position_id: 'p-1', position_name: POSITION, employment_type: 'FULL_TIME', work_mode: 'ONSITE',
    is_primary: true, valid_from: '2026-09-25', valid_to: null,
  },
  current_schedule: {
    schedule_id: 's-1', name: 'Пятидневка', timezone: ZONE, status: 'ACTIVE', valid_from: '2026-09-25',
    valid_to: null, weekdays: [1, 2, 3, 4, 5], start_time: '09:00:00', end_time: '18:00:00',
  },
  assignment_history: [],
};

function dayRow(day: string, index: number) {
  const weekday = new Date(`${day}T12:00:00Z`).getUTCDay();
  const base = {
    day, timezone: ZONE, office_id: 'o-1', office_name: OFFICE, state: 'NOT_COME',
    first_entry_at: null as string | null, last_exit_at: null as string | null, seconds: 0,
    sessions: 0, open_session_id: null as string | null, late_minutes: null as number | null,
    scheduled_start: '09:00:00' as string | null, scheduled_end: '18:00:00' as string | null,
    absence_code: null as string | null, absence_name: null as string | null,
    conflicting_marks: false, field_work: null as unknown,
  };
  if (weekday === 0 || weekday === 6) return { ...base, state: 'DAY_OFF', scheduled_start: null, scheduled_end: null };
  if (day === TODAY) return base;
  if (index % 7 === 3) return { ...base, state: 'FIELD_WORK', seconds: 28_800, field_work: { id: 'fw-1', status: 'CONFIRMED' } };
  if (index % 5 === 1) return { ...base, state: 'NOT_COME' };
  const late = index % 4 === 2 ? 12 : 0;
  return {
    ...base, state: 'LEFT', first_entry_at: at(day, 9, late), last_exit_at: at(day, 18, index % 3),
    seconds: 32_400 - 3600 - late * 60, sessions: 1, late_minutes: late,
  };
}

function daily(first: string, last: string) {
  const days = [];
  for (let day = first, i = 0; day <= last; day = shift(day, 1), i += 1) days.push(dayRow(day, i));
  const past = days.filter((one) => one.day <= TODAY);
  return {
    employee_id: 'e-1', timezone: ZONE, first, last, days,
    totals: {
      seconds: past.reduce((s, one) => s + one.seconds, 0),
      days_with_marks: past.filter((one) => one.sessions > 0).length,
      working_days: past.filter((one) => one.scheduled_start).length,
      late_days: past.filter((one) => (one.late_minutes ?? 0) > 0).length,
      late_minutes: past.reduce((s, one) => s + (one.late_minutes ?? 0), 0),
      open_sessions: 0,
    },
    note: null,
  };
}

const SICK = {
  kind: 'absence', id: 'r-1', created_at: `${shift(TODAY, -1)}T05:00:00Z`,
  absence: {
    id: 'r-1', kind: 'CREATE', employee: { id: 'e-1', full_name: PERSON.full_name, employee_number: 'HT-0101' },
    absence_type: { code: 'SICK_LEAVE', name: 'Больничный' }, status: 'SUBMITTED', stage: 'HR_REVIEW',
    first_day: shift(TODAY, -1), last_day: TODAY, submitted_at: `${shift(TODAY, -1)}T05:00:00Z`,
    requires_document: true, missing_for_approval: [], comment: 'Температура, был у врача', review_comment: null,
    documents: [{ id: 'doc-1', document_type: 'CERTIFICATE', verification_status: 'PENDING', verified_at: null,
      file: { id: 'f-9', name: LONG ? 'справка_о_временной_нетрудоспособности_поликлиника_номер_семнадцать.pdf' : 'справка.pdf',
        mime_type: 'application/pdf', size_bytes: 120_000, uploaded_at: `${shift(TODAY, -1)}T05:00:00Z`, scan_status: 'CLEAN' } }],
  },
};

const VACATION = {
  kind: 'absence', id: 'r-2', created_at: '2026-10-01T05:00:00Z',
  absence: {
    id: 'r-2', kind: 'CREATE', employee: { id: 'e-1', full_name: PERSON.full_name, employee_number: 'HT-0101' },
    absence_type: { code: 'ANNUAL_LEAVE', name: 'Отпуск' }, status: 'APPROVED', stage: 'APPROVED',
    first_day: shift(TODAY, 12), last_day: shift(TODAY, 16), submitted_at: '2026-10-01T05:00:00Z',
    requires_document: false, missing_for_approval: [], comment: null, review_comment: 'Согласовано', documents: [],
  },
};

const SCHEDULE = {
  id: 's-1', name: 'Пятидневка', timezone: ZONE, weekly_minutes: 2400, late_grace_minutes: 10,
  early_leave_grace_minutes: 0, is_flexible: false, status: 'ACTIVE',
  days: [1, 2, 3, 4, 5, 6, 7].map((weekday) => ({
    weekday, is_working_day: weekday <= 5,
    start_time: weekday <= 5 ? '09:00:00' : null, end_time: weekday <= 5 ? '18:00:00' : null,
    breaks: weekday <= 5 ? [{ id: `b-${weekday}`, name: 'Обед', start_time: '13:00:00', end_time: '14:00:00', is_paid: false }] : [],
  })),
};

const HISTORY = [
  { id: 'h-1', action: 'employee.onboard', occurred_at: '2026-09-25T11:54:00Z', new_values: { hire_date: '2026-09-25', employment_status: 'ACTIVE' }, old_values: null },
  { id: 'h-2', action: 'employee.schedule.assign', occurred_at: '2026-09-25T11:54:00Z', new_values: { schedule_id: 's-1', valid_from: '2026-09-25' }, old_values: null },
  { id: 'h-3', action: 'employee.create', occurred_at: '2026-09-25T11:53:00Z', new_values: { first_name: 'Алексей', last_name: 'Соколов' }, old_values: null },
  { id: 'h-4', action: 'something.unknown', occurred_at: '2026-09-25T11:50:00Z', new_values: null, old_values: null },
].map((row) => ({
  ...row, entity_type: 'employees', entity_id: 'e-1', actor_user_id: 'u-2', actor_email: 'm.petrova@demo.local',
  actor_name: LONG ? 'Петрова-Хасанова Марина Александровна' : 'Петрова Марина', actor_employee_id: null,
  actor_employee_name: null, ip_address: null, user_agent: null,
}));

const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const fail = () => reply({ error: { code: 'server_error', message: 'Ошибка' } }, 500);

const DELAY = Number(search.get('delay') ?? 0);
const SAVE = search.get('save') ?? '';
const NAMES = ['', 'Соколов Алексей Игоревич', 'Ахмедова Дилноза Рустамовна', 'Юсупов Бахтиёр Каримович',
  'Ким Елена Викторовна', 'Рахимов Тимур Алишерович', 'Абдурахмонова-Сайфуллаева Гулчехра Абдукаххоровна',
  'Петров Дмитрий Олегович', 'Назарова Мадина Шавкатовна'];
type Someone = Record<string, unknown> & { full_name: string; last_name: unknown; first_name: unknown; middle_name: unknown };
const people: Record<string, Someone> = {};
for (let n = 1; n <= 8; n += 1) {
  const full = n === 1 ? PERSON.full_name : NAMES[n]!;
  const [last = '', first = '', middle = null] = full.split(' ');
  people[`e-${n}`] = { ...PERSON, id: `e-${n}`, employee_number: `HT-01${String(n).padStart(2, '0')}`,
    full_name: full, last_name: last, first_name: first, middle_name: middle };
}
const rowOf = (one: Someone) => ({
  ...one, photo: false, telegram_username: null, telegram_state: null,
});
const calls: string[] = [];
(window as unknown as { __calls: string[] }).__calls = calls;

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = new URL(String(input), 'http://x');
  const q = url.searchParams;
  const method = init?.method ?? 'GET';
  const empty = CASE === 'empty';
  if (url.pathname.includes('/auth/')) return reply(HR);
  calls.push(`${method} ${url.pathname}${url.search}`);
  if (DELAY > 0) {
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, DELAY);
      init?.signal?.addEventListener('abort', () => {
        window.clearTimeout(timer);
        reject(new DOMException('aborted', 'AbortError'));
      });
    });
  }
  // Любой e-N отвечает так же, как e-1, но своими данными.
  const who = url.pathname.match(/\/employees\/(e-\d+)/)?.[1] ?? 'e-1';
  const path = url.pathname.replace(/\/employees\/e-\d+/, '/employees/e-1');
  if (path === '/api/v1/employees/e-1/' || path === '/api/v1/employees/e-1') {
    if (method === 'PATCH') {
      if (SAVE === 'offline') throw new TypeError('Failed to fetch');
      const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, string>;
      const was = people[who]!;
      const next = { ...was, ...body };
      next.full_name = [next.last_name, next.first_name, next.middle_name].filter(Boolean).join(' ');
      people[who] = next;
      return reply(next);
    }
    return reply(people[who] ?? PERSON);
  }
  if (/\/employees\/counts\/?$/.test(path)) return reply({ total: 8, statuses: { ACTIVE: 8 } });
  if (/\/employees\/?$/.test(path)) {
    const term = (q.get('search') ?? '').toLowerCase();
    const all = Object.values(people).filter((one) => !term || one.full_name.toLowerCase().includes(term));
    const offset = Number(q.get('offset') ?? 0);
    const limit = Number(q.get('limit') ?? 15);
    return reply({ items: all.slice(offset, offset + limit).map(rowOf), next_cursor: null, has_more: offset + limit < all.length, total: all.length });
  }
  if (path.includes('/presence')) return reply({ items: [] });
  if (path.includes('/offices')) return reply({ items: [{ id: 'o-1', name: OFFICE, status: 'ACTIVE', region_id: 'r-1', region_name: 'Ташкент' }] });
  if (path.includes('/regions')) return reply({ items: [{ id: 'r-1', name: 'Ташкент' }] });
  if (path.includes('/departments')) return reply({ items: [{ id: 'dep-1', name: 'Отдел продаж' }] });
  if (path.includes('/positions')) return reply({ items: [{ id: 'p-1', name: POSITION }] });
  if (path.includes('/employees/e-1/telegram')) {
    return reply(empty ? { state: 'NOT_LINKED', account: null, invitation: null }
      : { state: 'ACTIVE', account: { status: 'ACTIVE', telegram_username: 'sokolov_demo', connected_at: null }, invitation: null });
  }
  if (path.includes('/employees/e-1/onboarding')) return reply({ error: { code: 'not_found' } }, 404);
  if (path.includes('/employees/e-1/assignments')) {
    return reply({ items: empty ? [] : [PERSON.current_assignment, {
      ...PERSON.current_assignment, id: 'a-0', office_name: 'Региональный офис', valid_from: '2026-01-10',
      valid_to: '2026-09-24', position_name: 'Стажёр-аналитик' }] });
  }
  if (path.includes('/employees/e-1/schedules')) {
    return reply({ items: empty ? [] : [{ id: 'sa-1', employee_id: 'e-1', schedule_id: 's-1', schedule_name: 'Пятидневка', valid_from: '2026-09-25', valid_to: null }] });
  }
  if (path.includes('/employees/e-1')) return reply(PERSON);
  if (/\/work-schedules\/?$/.test(path)) return reply({ items: [SCHEDULE], next_cursor: null, has_more: false });
  if (path.includes('/work-schedules/')) return reply(SCHEDULE);
  if (path.includes('/attendance/daily')) {
    if (CASE === 'error') return fail();
    if (empty) {
      return reply({ employee_id: 'e-1', timezone: ZONE, first: q.get('date_from'), last: q.get('date_to'), days: [],
        totals: { seconds: 0, days_with_marks: 0, working_days: 0, late_days: 0, late_minutes: 0, open_sessions: 0 },
        note: 'У сотрудника нет кадрового назначения за этот период' });
    }
    return reply(daily(q.get('date_from') ?? monthStartOf(TODAY), q.get('date_to') ?? TODAY));
  }
  if (path.includes('/attendance/events')) {
    const day = q.get('date_from') ?? TODAY;
    return reply({ items: [
      { id: 'ev-1', event_type: 'ENTRY', occurred_at: at(day, 9, 0), qr_point_name: 'Главный вход', office_name: OFFICE, inside_geofence: true, inside_office_network: null, anomalies: [] },
      { id: 'ev-2', event_type: 'EXIT', occurred_at: at(day, 18, 2), qr_point_name: 'Главный вход', office_name: OFFICE, inside_geofence: true, inside_office_network: null, anomalies: [] },
    ], next_cursor: null, has_more: false });
  }
  if (path.includes('/requests/counts')) {
    if (CASE === 'error') return fail();
    return reply(empty ? { open: 0, all: 0, leave: 0, sick: 0, fixes: 0, cancel: 0 } : { open: 1, all: 2, leave: 1, sick: 1, fixes: 0, cancel: 0 });
  }
  if (path.includes('/requests')) {
    if (empty) return reply({ items: [], next_cursor: null, has_more: false });
    const status = q.get('status') ?? '';
    const items = [SICK, VACATION].filter((one) => !status || status.split(',').includes(one.absence.status));
    return reply({ items, next_cursor: null, has_more: false });
  }
  if (path.includes('/audit-logs')) {
    if (CASE === 'error') return fail();
    return reply({ items: empty ? [] : HISTORY, next_cursor: null, has_more: false });
  }
  return reply({ items: [], next_cursor: null, has_more: false });
};

const extra = OPEN
  ? (TAB === 'attendance' ? `&day=${shift(TODAY, -2)}` : TAB === 'requests' ? '&request=r-1' : '')
  : '';
const start = search.get('start') ?? `/employees/e-1?tab=${TAB}${extra}`;
const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={[start]}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
