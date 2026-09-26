/**
 * Только для разработки: «Аналитика» на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Вкладка — из адреса страницы: `?tab=attendance|…`.
 * Данные выдуманы и живут только здесь: на стенд они не попадают.
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
  permissions: ['attendance.read', 'analytics.read', 'employees.read', 'reports.export', 'absences.read', 'surveys.read', 'onboarding.read'],
};

const pad = (n: number) => String(n).padStart(2, '0');
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const TODAY = iso(new Date());
const addDays = (day: string, n: number) => { const d = new Date(`${day}T00:00:00`); d.setDate(d.getDate() + n); return iso(d); };
const range = (from: string, to: string) => { const out: string[] = []; for (let d = from; d <= to; d = addDays(d, 1)) out.push(d); return out; };
const wave = (i: number, base: number, amp: number) => Math.round((base + Math.sin(i * 0.9) * amp + Math.cos(i * 0.37) * amp * 0.6) * 10) / 10;

const NAMES = ['Каримов Бехзод', 'Рахимова Дилноза', 'Ибрагимов Сардор', 'Мурадов Азизбек', 'Собиров Умид', 'Ашуралиев Жандос', 'Ахмедова Нигора', 'Насридинов Тимур', 'Юсупова Камила', 'Тураев Шохрух'];
const DEPTS = ['Разработка', 'Продажи', 'Поддержка', 'Продукт', 'Маркетинг', 'HR', 'Финансы'];
const OFFICES = [
  { id: 'o-1', code: 'TAS', name: 'Ташкент', status: 'ACTIVE', region_id: 'r-1', region_name: 'Ташкент' },
  { id: 'o-2', code: 'SAM', name: 'Самарканд', status: 'ACTIVE', region_id: 'r-2', region_name: 'Самарканд' },
  { id: 'o-3', code: 'BUH', name: 'Бухара', status: 'ACTIVE', region_id: 'r-3', region_name: 'Бухара' },
  { id: 'o-4', code: 'NAM', name: 'Наманган', status: 'ACTIVE', region_id: 'r-4', region_name: 'Наманган' },
  { id: 'o-5', code: 'FER', name: 'Фергана', status: 'ACTIVE', region_id: 'r-4', region_name: 'Наманган' },
];
const share = (p: number) => ({ numerator: Math.round(p), denominator: 100, percent: p });

function overview(from: string, to: string) {
  const days = range(from, to).map((day, i) => {
    const weekend = [0, 6].includes(new Date(`${day}T00:00:00`).getDay());
    const percent = weekend ? null : wave(i, 84, 5);
    return {
      day, weekday: 1, working: !weekend, future: day > TODAY, in_detail: true, expected: weekend ? 0 : 48,
      attended: weekend ? 0 : Math.round(((percent ?? 0) * 48) / 100), percent, on_time: 40, on_time_percent: 88, late: 3,
      missed: 2, vacation: weekend ? 0 : i % 4, sick_leave: weekend ? 0 : i % 3 === 0 ? 2 : 0, other_absence: 0, trip: i % 9 === 0 ? 1 : 0, average_seconds: 27000,
    };
  });
  const previous_days = days.map((d, i) => ({ day: d.day, attended: 0, expected: 0, percent: d.percent === null ? null : wave(i + 3, 79, 4) }));
  const groups = (names: string[]) => names.map((name, i) => ({ id: `g-${i}`, name, position: i + 1, attendance: share(92 - i * 7), previous_attendance: share(88 - i * 5), difference_points: 4 - i * 2 }));
  return {
    period: { first: from, last: to, days: days.length }, previous_period: { first: from, last: to }, weekday: null,
    generated_at: `${TODAY}T10:00:00Z`, timezones: ['Asia/Tashkent'],
    summary: {
      attendance: share(86), previous_attendance: share(80), difference_points: 6, on_time: share(88), late: { numerator: 9, denominator: 400, percent: 2.2 },
      average_seconds: 27720, open_sessions: 1, missed_days: 3, vacation_days: 5, sick_leave_days: 3, other_absence_days: 1, trip_days: 2,
    },
    days, previous_days, offices: groups(OFFICES.map((o) => o.name)), regions: groups(['Ташкент', 'Самарканд', 'Бухара', 'Наманган']),
    departments: groups(DEPTS), positions: groups(['Разработчик', 'Менеджер', 'Оператор']), heads: groups(['Каримов Бехзод', 'Ахмедова Нигора']),
    employees: NAMES.slice(0, 7).map((name, i) => ({
      id: `e-${i}`, name, attendance: share(40 + i * 7), late_days: 5 - (i % 5), late_minutes: 40, missed_days: Math.max(0, 2 - i), seconds: (20 - i) * 3600 * 4,
      vacation_days: 0, sick_days: 0, other_days: 0, office_id: OFFICES[i % 5]!.id, missed_dates: i < 2 ? [addDays(TODAY, -i - 1)] : [], late_dates: [],
    })),
    arrivals: { bucket_minutes: 10, from_minutes: -60, to_minutes: 90, buckets: [], start_time: '09:00', uniform_start: true, median_minutes: 540, after_start: 0, late: share(2) },
    weekdays: { days: [], best: null },
  };
}

function team(from: string, to: string) {
  const series = range(from, to).filter((d) => d <= TODAY).map((day, i) => ({ day, headcount: 46 + Math.round(Math.sin(i / 3)) + (i > 20 ? 2 : 0) }));
  const person = (i: number, hire: string, status = 'ACTIVE') => ({ id: `e-${i}`, name: NAMES[i]!, employee_number: `HT-${i}`, status, hire_date: hire, office: OFFICES[i % 5]!.name, department: DEPTS[i % 7]!, position: 'Специалист' });
  return {
    period: { first: from, last: to, days: 30 }, previous_period: { first: from, last: to },
    summary: { headcount: 48, headcount_start: 46, previous_headcount_start: 45, hired: 2, previous_hired: 1, promoted: 1, previous_promoted: 0, left: 1, previous_left: 2, probation_failed: 0 },
    series,
    by_department: [{ id: 'd-0', name: 'Разработка', hired: 1, left: 0, difference: 1 }],
    departments: DEPTS.map((name, i) => ({ id: `d-${i}`, name, headcount: [14, 8, 7, 6, 5, 4, 3][i]!, previous_headcount: [13, 8, 7, 6, 5, 4, 3][i]! })),
    offices: OFFICES.map((o, i) => ({ id: o.id, name: o.name, headcount: [20, 10, 8, 6, 4][i]!, previous_headcount: [19, 10, 8, 6, 3][i]! })),
    hires: [person(5, addDays(TODAY, -7), 'PROBATION'), person(8, addDays(TODAY, -4), 'PROBATION')],
    departures: [{ ...person(7, '2023-02-01', 'TERMINATED'), termination_date: addDays(TODAY, -5), reason: 'По собственному желанию' }],
    transfers: [{ id: 'e-4', name: NAMES[4]!, date: addDays(TODAY, -3), from_office: 'Ташкент', to_office: 'Ташкент', from_department: 'Продажи', to_department: 'Поддержка' }],
  };
}

function probation() {
  const trainees = [3, 5, 12, 20, -1].map((left, i) => ({
    id: `e-${i}`, name: NAMES[i]!, employee_number: null, status: 'PROBATION', hire_date: addDays(TODAY, -40), office: OFFICES[i % 5]!.name, department: DEPTS[i]!, position: null,
    probation_from: addDays(TODAY, -40 + i), probation_to: addDays(TODAY, left), days_left: left, days_total: 60,
  })).sort((a, b) => a.days_left - b.days_left);
  return {
    period: { first: TODAY, last: TODAY, days: 30 }, previous_period: { first: TODAY, last: TODAY },
    summary: { active: 5, due: 2, overdue: 1, started: 3, promoted: 3, failed: 1, previous_started: 2, previous_promoted: 2, previous_failed: 1, conversion_percent: 75, previous_conversion_percent: 66.7 },
    due_days: 7, trainees,
  };
}

function queue(status: string | null) {
  const open = status !== null && status !== 'APPROVED';
  const statuses = status === 'APPROVED' ? ['APPROVED'] : open ? ['SUBMITTED', 'IN_REVIEW'] : ['APPROVED', 'APPROVED', 'REJECTED', 'SUBMITTED'];
  const stages = ['WAITING_DOCUMENTS', 'HR_REVIEW', 'NEEDS_FIX', 'HR_REVIEW', 'PENDING'];
  return {
    items: NAMES.slice(0, open ? 5 : 10).map((name, i) => ({
      kind: 'absence', id: `r-${i}`, created_at: `${addDays(TODAY, -i - 1)}T08:00:00Z`, place: null,
      absence: {
        id: `r-${i}`, employee: { id: `e-${i}`, full_name: name, employee_number: null },
        absence_type: i % 2 ? { code: 'SICK_LEAVE', name: 'Больничный' } : { code: 'ANNUAL_LEAVE', name: 'Отпуск' },
        status: statuses[i % statuses.length], first_day: addDays(TODAY, i), last_day: addDays(TODAY, i + 3),
        submitted_at: `${addDays(TODAY, -i * 2 - 1)}T08:00:00Z`, reviewed_at: open ? null : `${addDays(TODAY, -i * 2)}T12:00:00Z`,
        documents: [], requires_document: i % 2 === 1, stage: open ? stages[i % 5] : 'APPROVED',
        missing_for_approval: open && i % 2 ? (i === 1 ? ['certificate'] : ['certificate', 'application']) : [], comment: null, review_comment: null,
      },
    })),
    next_cursor: null, has_more: false,
  };
}

const STATES = ['IN_OFFICE', 'NOT_COME', 'IN_OFFICE', 'LEFT', 'VACATION', 'SICK_LEAVE', 'IN_OFFICE', 'LATE', 'IN_OFFICE', 'NOT_COME'];
function presence() {
  return {
    date: TODAY, timezone: 'Asia/Tashkent', counts: {}, total: 10, truncated: false,
    items: NAMES.map((name, i) => ({
      employee_id: `e-${i}`, full_name: name, employee_number: null, office_id: OFFICES[i % 5]!.id, office_name: OFFICES[i % 5]!.name,
      state: STATES[i], late_minutes: i === 2 ? 14 : null, open_session_id: null, notice_kind: null, notice_comment: null,
      conflicting_marks: false, outside_geofence: i === 6, absence_name: null,
    })),
  };
}

const CAMPAIGNS = ['Оценка адаптации', 'Удовлетворённость работой', 'Итоги стажировки'].map((title, i) => ({
  id: `c-${i}`, template_id: 't', template_title: title, title, status: i === 0 ? 'ACTIVE' : 'FINISHED', audience_kind: 'ALL', audience_ids: null,
  scheduled_at: null, repeat_months: null, remind_at: null, due_at: null, next_send_at: null, sent_at: `${addDays(TODAY, -20 + i * 6)}T06:00:00Z`,
  template_version: 1, automation_id: null, created_at: `${addDays(TODAY, -21)}T06:00:00Z`,
}));

function recipients(id: string) {
  const n = Number(id.slice(2));
  return {
    items: Array.from({ length: 24 }, (_, i) => ({
      id: `${id}-${i}`, employee_id: `e-${i % 10}`, full_name: NAMES[i % 10]!, status: (i + n) % 4 === 0 ? 'SENT' : 'COMPLETED',
      sent_at: `${addDays(TODAY, -20 + n * 6)}T06:00:00Z`, started_at: null,
      completed_at: (i + n) % 4 === 0 ? null : `${addDays(TODAY, -19 + n * 6 + (i % 6))}T06:00:00Z`, skip_reason: null,
      office_name: OFFICES[i % 5]!.name, department_name: DEPTS[i % 5]!,
    })),
  };
}

const ONB = ['NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'COMPLETED', 'POLICIES_IN_PROGRESS', 'COMPLETED', 'BLOCKED_BY_DECLINED_POLICY', 'COMPLETED', 'UPDATE_REQUIRED', 'COMPLETED'];
const onboarding = {
  items: NAMES.map((name, i) => ({
    employee_id: `e-${i}`, full_name: name, employee_number: null, office_name: OFFICES[i % 5]!.name, department_name: DEPTS[i % 5]!, position_name: null,
    telegram_state: 'LINKED', status: ONB[i], stage: 'SECTIONS', completed: ONB[i] === 'COMPLETED', sections_done: i % 5, sections_total: 5, policies_done: i % 3, policies_total: 3,
    invited_at: `${addDays(TODAY, -30 + i)}T06:00:00Z`, started_at: null, completed_at: null, last_reminder_at: null, invitation_status: null, invitation_expires_at: null,
  })),
  next_cursor: null, has_more: false,
};
const version = (v: string, day: string) => ({ id: `v-${v}`, version: v, status: 'PUBLISHED', summary: '', body: null, agree_label: '', has_file: false, file_name: null, published_at: `${day}T06:00:00Z`, created_at: `${day}T06:00:00Z` });
const DOCS = ['Вводный инструктаж по охране труда', 'Пожарная безопасность', 'Правила внутреннего распорядка', 'Политика защиты данных'].map((title, i) => ({
  id: `p-${i}`, code: `P${i}`, title, description: null, is_mandatory: true, position: i, archived_at: null,
  current_version: version(`${i + 1}.${i}`, addDays(TODAY, -i * 9)), versions: i % 2 ? [version(`${i + 1}.${i}`, addDays(TODAY, -i * 9)), version('1.0', '2025-01-10')] : [version(`${i + 1}.${i}`, addDays(TODAY, -i * 9))],
}));

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

window.fetch = async (input: RequestInfo | URL) => {
  const url = new URL(String(input), 'http://x');
  const q = url.searchParams;
  const path = url.pathname;
  const from = q.get('date_from') ?? addDays(TODAY, -29);
  const to = q.get('date_to') ?? TODAY;
  // Медленная сеть — видно заглушки и то, что лист при этом не прыгает.
  await new Promise((resolve) => setTimeout(resolve, 250));
  if (path.includes('/auth/')) return answer(HR);
  if (path.includes('/regions')) return answer({ items: ['Ташкент', 'Самарканд', 'Бухара', 'Наманган'].map((name, i) => ({ id: `r-${i + 1}`, code: `R${i}`, name, status: 'ACTIVE' })) });
  if (path.includes('/offices')) return answer({ items: OFFICES });
  if (path.includes('/departments')) return answer({ items: DEPTS.map((name, i) => ({ id: `d-${i}`, name })), next_cursor: null, has_more: false });
  if (path.includes('/analytics/overview')) return answer(overview(from, to));
  if (path.includes('/analytics/team')) return answer(team(from, to));
  if (path.includes('/analytics/probation')) return answer(probation());
  if (path.endsWith('/requests')) return answer(queue(q.get('status')));
  if (path.includes('/attendance/presence')) return answer(presence());
  if (path.includes('/reports/catalog')) return answer({ kinds: [], max_period_days: 366, xlsx_max_rows: 1, retention_hours: 1, preview_min_rows: 1, preview_max_rows: 1 });
  if (path.includes('/recipients')) return answer(recipients(path.split('/').filter(Boolean).slice(-2)[0]!));
  if (path.includes('/surveys/campaigns')) return answer({ items: CAMPAIGNS, next_cursor: null, has_more: false });
  if (path.includes('/onboarding/progress')) return answer(onboarding);
  if (path.includes('/pending')) return answer({ items: onboarding.items.filter((one) => one.status !== 'COMPLETED').slice(0, 4).map((one, i) => ({ employee_id: one.employee_id, full_name: one.full_name, employee_number: null, declined_at: i === 3 ? `${TODAY}T06:00:00Z` : null })), total: 4 });
  if (path.includes('/onboarding/documents')) return answer({ items: DOCS });
  return answer({ items: [], next_cursor: null, has_more: false });
};

const start = `/analytics${window.location.search}`;
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
