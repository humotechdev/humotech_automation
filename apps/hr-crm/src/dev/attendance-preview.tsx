/**
 * Только для разработки: «Посещаемость» на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Режим — из адреса страницы: `?tab=log|week|compare`.
 * Сравнение: `?tab=compare&cmp=office|region|employee|staff`; у каждого офиса,
 * региона и сотрудника своя явка, чтобы линии расходились. `?fail=o-2` —
 * ответ по этому объекту падает (видно, что ошибка не обнуляет соседей).
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import { App } from '../app/App';
import { SessionProvider } from '../features/auth/session';
import '../styles/app.css';
import '../styles/crm.css';

const HR = {
  id: 'u-1', email: 'hr@humotech.local', organization_id: 'org-1', organization_code: 'DEMO',
  employee_id: null, status: 'ACTIVE', timezone: 'Asia/Tashkent', roles: ['HR'],
  permissions: ['attendance.read', 'attendance.manual', 'analytics.read', 'employees.read', 'reports.export'],
};

const pad = (n: number) => String(n).padStart(2, '0');
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const TODAY = iso(new Date());
const at = (day: string, hh: number, mm: number) => `${day}T${pad(hh - 5)}:${pad(mm)}:00Z`;

const PEOPLE = [
  ['Ashiraliyev Jandos', 'Главный офис', 'Sales'], ['Karimova Sevara', 'Главный офис', 'Sales'],
  ['Muradov Azizbek', 'Главный офис', 'Sales'], ['Abdullaeva Nigora', 'Главный офис', 'Sales'],
  ['Tursunov Temur', 'Главный офис', 'Support'], ['Rahimov Davron', 'Региональный офис', 'Logistics'],
  ['Saidova Malika', 'Главный офис', 'HR'], ['Ismoilova Dilnoza', 'Главный офис', 'Finance'],
  ['Abdullaev Kamron', 'Бухарская область', 'IT'], ['Yusupov Bekzod', 'Главный офис', 'Sales'],
  ['Nazarova Madina', 'Региональный офис', 'Logistics'], ['Qodirov Sherzod', 'Главный офис', 'IT'],
];

type Plan = [string, number | null, number | null, number | null, number];
/** Состояние, приход, уход (часы*100+мин), опоздание — по номеру человека и дню. */
function plan(i: number, dayIndex: number): Plan {
  const weekend = dayIndex % 7 >= 5;
  if (weekend) return ['DAY_OFF', null, null, null, 0];
  if (i === 4) return ['VACATION', null, null, null, 0];
  if (i === 7) return ['SICK_LEAVE', null, null, null, 0];
  if (i === 3) return ['NO_SCHEDULE', null, null, null, 0];
  if (i === 0 && dayIndex % 3 === 0) return ['NOT_COME', null, null, null, 0];
  if (i === 1 && dayIndex % 2 === 0) return ['LEFT', 918, 1805, 0, 18];
  if (i === 5) return ['LEFT', 801, 1704, 0, 0];
  return ['IN_OFFICE', 900 + (i % 5) * 2, null, null, 0];
}

function row(i: number, day: string, dayIndex: number) {
  const [name, office, department] = PEOPLE[i]!;
  const [state, came, left, , late] = plan(i, dayIndex);
  const isToday = day === TODAY;
  const finalState = !isToday && state === 'IN_OFFICE' ? 'LEFT' : state;
  const exit = left ?? (!isToday && came ? 1800 + i : null);
  return {
    employee_id: `e-${i}`, full_name: name, employee_number: null, office_id: 'o-1', office_name: office,
    department_name: department, position_name: null, state: finalState,
    first_entry_at: came ? at(day, Math.floor(came / 100), came % 100) : null,
    last_exit_at: exit ? at(day, Math.floor(exit / 100), exit % 100) : null,
    seconds: came ? ((exit ?? 1930) - came) * 36 : 0, open_session_id: isToday && state === 'IN_OFFICE' ? 's' : null,
    late_minutes: late || (came ? 0 : null), scheduled_start: state === 'NO_SCHEDULE' ? null : i === 5 ? '08:00' : '09:00',
    scheduled_end: state === 'NO_SCHEDULE' ? null : i === 5 ? '17:00' : '18:00',
    absence_code: null, absence_name: null, notice_kind: null, notice_comment: null,
    conflicting_marks: false, intervals: [], outside_geofence: false,
  };
}

function dayIndexOf(day: string) {
  const d = new Date(`${day}T12:00:00`);
  return (d.getDay() + 6) % 7 + 7 * Math.floor(d.getDate() / 7);
}

function presence(day: string) {
  const index = dayIndexOf(day);
  const items = PEOPLE.map((_, i) => row(i, day, index));
  const counts: Record<string, number> = {};
  items.forEach((one) => { counts[one.state] = (counts[one.state] ?? 0) + 1; });
  return { date: day, timezone: 'Asia/Tashkent', counts, total: items.length, truncated: false, items };
}

function dashboard(day: string) {
  const { counts } = presence(day);
  const card = (key: string, value: number, state?: string) => ({
    key, title: key, value, attention: false, endpoint: '/api/v1/attendance/presence',
    params: { date: day, ...(state ? { state } : {}) },
  });
  const came = (counts['IN_OFFICE'] ?? 0) + (counts['LEFT'] ?? 0);
  return {
    date: day, timezone: 'Asia/Tashkent', warnings: [],
    cards: [
      card('active_employees', 12), card('should_work_today', came + (counts['NOT_COME'] ?? 0)), card('came', came),
      card('in_office', counts['IN_OFFICE'] ?? 0, 'IN_OFFICE'), card('not_come', counts['NOT_COME'] ?? 0, 'NOT_COME'),
      card('late', 1), card('left', counts['LEFT'] ?? 0, 'LEFT'),
      card('vacation', counts['VACATION'] ?? 0, 'VACATION'), card('sick_leave', counts['SICK_LEAVE'] ?? 0, 'SICK_LEAVE'),
    ],
  };
}

const OFFICES = [
  { id: 'o-1', code: 'HQ', name: 'Главный офис', status: 'ACTIVE', region_id: 'r-1', region_name: 'город Ташкент' },
  { id: 'o-2', code: 'RG', name: 'Региональный офис', status: 'ACTIVE', region_id: 'r-1', region_name: 'город Ташкент' },
  { id: 'o-3', code: 'CH', name: 'Чиланзар', status: 'ACTIVE', region_id: 'r-1', region_name: 'город Ташкент' },
  { id: 'o-4', code: 'BK', name: 'Бухара-центр', status: 'ACTIVE', region_id: 'r-2', region_name: 'Бухарская область' },
  { id: 'o-5', code: 'SM', name: 'Самарканд', status: 'ACTIVE', region_id: 'r-3', region_name: 'Самаркандская область' },
];

/** Своя «явка» у каждого объекта — линии сравнения не совпадают. */
function biasOf(id: string | null): number {
  if (!id) return 0;
  let n = 0;
  for (const ch of id) n = (n * 31 + ch.charCodeAt(0)) % 997;
  return (n % 7) - 3;
}

function overview(from: string, to: string, q: URLSearchParams = new URLSearchParams()) {
  const days: string[] = [];
  for (let d = new Date(`${from}T12:00:00`); iso(d) <= to; d.setDate(d.getDate() + 1)) days.push(iso(d));
  const who = q.get('employee_id') ?? q.get('office_id') ?? q.get('region_id');
  const bias = biasOf(who);
  const single = Boolean(q.get('employee_id'));
  const dayRows = days.map((day, n) => {
    const weekend = [0, 6].includes(new Date(`${day}T12:00:00`).getDay());
    const expected = weekend ? 0 : single ? 1 : 30;
    const wobble = ((n * 7 + bias * 3) % 5) - 2;
    const attended = weekend ? 0 : single ? ((n + bias + 9) % 6 === 0 ? 0 : 1) : Math.max(0, Math.min(30, 26 + bias + wobble));
    return {
      day, weekday: 1, working: !weekend, future: day > TODAY, in_detail: true, expected, attended,
      percent: expected ? Math.round((attended / expected) * 1000) / 10 : null, on_time: attended, on_time_percent: null,
      late: n % 4 === 0 ? 1 : 0, missed: expected - attended, vacation: 1, sick_leave: 1, other_absence: 0, average_seconds: 27700,
    };
  });
  const roster = q.get('employee_id') ? PEOPLE.map((one, i) => [one, i] as const).filter(([, i]) => `e-${i}` === q.get('employee_id'))
    : PEOPLE.map((one, i) => [one, i] as const);
  const employees = roster.map(([[name], i]) => ({
    id: `e-${i}`, name, attendance: { numerator: 17 - ((i * 5) % 7), denominator: 17, percent: Math.round(((17 - ((i * 5) % 7)) / 17) * 1000) / 10 },
    late_days: i % 3 === 1 ? 2 : 0, late_minutes: 30, missed_days: i % 4 === 3 ? 1 : 0, seconds: (16 - (i % 4)) * 27700,
    vacation_days: i === 4 ? 4 : 0, sick_days: i === 7 ? 2 : 0, other_days: 0, office_id: 'o-1',
    missed_dates: i % 4 === 3 ? [days[Math.max(0, days.length - 4 - i)] ?? from] : [],
    late_dates: i % 3 === 1 ? [{ day: days[Math.max(0, days.length - 2 - i)] ?? from, minutes: 18 }] : [],
  }));
  return {
    period: { first: from, last: to, days: days.length }, previous_period: { first: from, last: to }, weekday: null,
    generated_at: new Date().toISOString(), timezones: ['Asia/Tashkent'],
    summary: {
      attendance: (() => {
        const expected = dayRows.reduce((sum, one) => sum + one.expected, 0);
        const attended = dayRows.reduce((sum, one) => sum + one.attended, 0);
        return { numerator: attended, denominator: expected, percent: expected ? Math.round((attended / expected) * 1000) / 10 : null };
      })(), previous_attendance: { numerator: 1, denominator: 1, percent: 84 },
      difference_points: Math.round((bias * 1.3 + 0.8) * 10) / 10, on_time: { numerator: 129, denominator: 138, percent: 93.5 }, late: { numerator: Math.max(0, 6 + bias * 2), denominator: 138, percent: 6.5 },
      average_seconds: 27720 + bias * 600, open_sessions: 0, missed_days: Math.max(0, 3 - bias), vacation_days: 4, sick_leave_days: 2, other_absence_days: 0,
    },
    days: dayRows, previous_days: [], offices: [], regions: [], employees,
    arrivals: { bucket_minutes: 10, from_minutes: -60, to_minutes: 90, buckets: [], start_time: '09:00', uniform_start: true, median_minutes: 540, after_start: 0, late: { numerator: 0, denominator: 0, percent: null } },
    weekdays: { days: [] },
  };
}

function events(day: string) {
  const items = presence(day).items.flatMap((one, i) => {
    const out = [];
    if (one.first_entry_at) {
      out.push({ id: `in-${i}`, employee_id: one.employee_id, employee: { id: one.employee_id, full_name: one.full_name, employee_number: null },
        office_id: 'o-1', office_name: one.office_name, qr_point_id: null, qr_point_name: i === 0 ? '1 этаж' : null, event_type: 'ENTRY',
        source: i === 6 ? 'MANUAL' : 'QR', verification_status: 'ACCEPTED', occurred_at: one.first_entry_at, received_at: one.first_entry_at,
        rejection_reason: null, inside_geofence: i === 2 ? false : true, inside_office_network: null, created_at: one.first_entry_at,
        author_name: i === 6 ? 'Саидова Мадина' : null });
    }
    if (one.last_exit_at) {
      out.push({ id: `out-${i}`, employee_id: one.employee_id, employee: { id: one.employee_id, full_name: one.full_name, employee_number: null },
        office_id: 'o-1', office_name: one.office_name, qr_point_id: null, qr_point_name: null, event_type: 'EXIT', source: 'QR',
        verification_status: 'ACCEPTED', occurred_at: one.last_exit_at, received_at: one.last_exit_at, rejection_reason: null,
        inside_geofence: true, inside_office_network: null, created_at: one.last_exit_at, author_name: null });
    }
    return out;
  });
  return { items, next_cursor: null, has_more: false };
}

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

// `?delay=1200` — данные режимов приходят с задержкой, чтобы видеть заготовки.
const DELAY = Number(new URLSearchParams(window.location.search).get('delay') ?? '0');
const FAIL = (new URLSearchParams(window.location.search).get('fail') ?? '').split(',').filter(Boolean);

window.fetch = async (input: RequestInfo | URL) => {
  const url = new URL(String(input), 'http://x');
  const q = url.searchParams;
  const path = url.pathname;
  if (path.includes('/auth/')) return answer(HR);
  if (DELAY > 0) await new Promise((done) => window.setTimeout(done, DELAY));
  if (path.includes('/offices')) return answer({ items: OFFICES });
  if (path.includes('/departments')) return answer({ items: ['Sales', 'Support', 'HR', 'Finance', 'IT', 'Logistics'].map((name, i) => ({ id: `d-${i}`, name })), next_cursor: null, has_more: false });
  if (path.includes('/dashboard')) return answer(dashboard(q.get('date') ?? TODAY));
  if (path.includes('/analytics/overview')) {
    const one = q.get('employee_id') ?? q.get('office_id') ?? q.get('region_id');
    if (one && FAIL.includes(one)) return new Response('{}', { status: 500, headers: { 'Content-Type': 'application/json' } });
    return answer(overview(q.get('date_from') ?? TODAY, q.get('date_to') ?? TODAY, q));
  }
  if (path.includes('/attendance/presence')) return answer(presence(q.get('date') ?? TODAY));
  if (path.includes('/attendance/events')) return answer(events(q.get('date_from') ?? TODAY));
  return answer({ items: [], next_cursor: null, has_more: false });
};

const start = `/attendance${window.location.search}`;
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
