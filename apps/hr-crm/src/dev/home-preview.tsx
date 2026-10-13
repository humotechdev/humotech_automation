/**
 * Только для разработки: «Главная» на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Нужен для снимка вёрстки headless-браузером.
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
  permissions: ['attendance.read', 'absences.read', 'employees.read', 'questions.read'],
};

const iso = (d: Date) => d.toISOString().slice(0, 10);
const back = (days: number, hh = 10, mm = 0) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  d.setHours(hh, mm, 0, 0);
  return d;
};

const OFFICES = [
  { id: 'o-1', name: 'Ташкент (HQ)', status: 'ACTIVE', region_id: 'r-1' },
  { id: 'o-2', name: 'Самарканд', status: 'ACTIVE', region_id: 'r-2' },
  { id: 'o-3', name: 'Бухара', status: 'ACTIVE', region_id: 'r-3' },
];

const PRESENCE: Record<string, Record<string, number>> = {
  'o-1': { IN_OFFICE: 0, LEFT: 0, NOT_COME: 1, VACATION: 1, SICK_LEAVE: 1, DAY_OFF: 0 },
  'o-2': { IN_OFFICE: 0, LEFT: 0, NOT_COME: 0, VACATION: 1 },
  'o-3': { IN_OFFICE: 0, LEFT: 0, NOT_COME: 0, SICK_LEAVE: 1 },
};

const card = (key: string, value: number, state?: string) => ({
  key, title: key, value, attention: false,
  endpoint: key === 'active_employees' ? '/api/v1/employees' : '/api/v1/attendance/presence',
  params: { date: iso(new Date()), ...(state ? { state } : {}) },
});

const DASHBOARD = {
  date: iso(new Date()), timezone: 'Asia/Tashkent', warnings: [],
  cards: [
    card('active_employees', 5), card('should_work_today', 1), card('came', 0),
    card('in_office', 0, 'IN_OFFICE'), card('not_come', 1, 'NOT_COME'),
    card('vacation', 2, 'VACATION'), card('sick_leave', 2, 'SICK_LEAVE'),
  ],
};

const SERIES = [2, 3, 2, 1, 2, 1, 0].map((attended, i) => ({
  day: iso(back(6 - i)), worked_seconds: 0, attended, expected: 4 - (i === 6 ? 3 : 0), late: 0,
}));

/*
 * Ряд за запрошенный период: воскресенье — выходной (никого не ждали),
 * суббота через неделю рабочая — так видно, что ось пропускает дни по
 * данным, а не по дню недели.
 */
const ATTENDED = [4, 3, 4, 2, 4, 3, 1, 4, 4, 2, 3];
function seriesFor(url: string) {
  const params = new URL(url, 'http://x').searchParams;
  const from = params.get('date_from');
  const to = params.get('date_to');
  if (!from || !to) return SERIES;
  const out = [];
  for (let d = new Date(`${from}T00:00:00Z`), n = 0; iso(d) <= to; d.setUTCDate(d.getUTCDate() + 1), n += 1) {
    const weekday = d.getUTCDay();
    const week = Math.floor(d.getTime() / (7 * 86400000));
    const off = weekday === 0 || (weekday === 6 && week % 2 === 0);
    out.push({ day: iso(d), worked_seconds: 0, attended: off ? 0 : ATTENDED[n % ATTENDED.length]!, expected: off ? 0 : 4, late: 0 });
  }
  return out;
}

const absence = (id: string, name: string, code: string, typeName: string, stage: string, status = 'SUBMITTED', reviewed?: Date) => ({
  kind: 'absence', id, created_at: back(1).toISOString(),
  place: { office_name: 'Ташкент (HQ)', department_name: 'Sales' },
  absence: {
    id, employee: { id: `e-${id}`, full_name: name, employee_number: null },
    absence_type: { code, name: typeName }, status, stage, kind: 'CREATE',
    first_day: iso(back(2)), last_day: iso(back(-3)), submitted_at: back(1).toISOString(),
    reviewed_at: reviewed ? reviewed.toISOString() : null, requires_document: code === 'SICK_LEAVE',
    documents: [], comment: null, review_comment: null,
  },
});

const OPEN = [
  absence('a-1', 'Каримов Азиз', 'SICK_LEAVE', 'Больничный', 'WAITING_DOCUMENTS'),
  absence('a-2', 'Иванова Мария', 'SICK_LEAVE', 'Больничный', 'WAITING_DOCUMENTS'),
  absence('a-3', 'Петров Дмитрий', 'ANNUAL_LEAVE', 'Ежегодный отпуск', 'PENDING'),
];
const DECIDED = [absence('a-9', 'Каримов Азиз', 'SICK_LEAVE', 'Больничный', 'APPROVED', 'APPROVED', back(0, 10, 24))];

const feedItem = (id: string, type: string, title: string, name: string, text: string, at: Date) => ({
  id, type, group: 'requests', title, short_text: text, employee_id: 'e-1', employee_name: name,
  office_id: 'o-1', office_name: 'Ташкент (HQ)', status: 'NEW', status_label: 'Новое', priority: 'NORMAL',
  requires_action: true, created_at: at.toISOString(), read_at: null, related_entity_type: 'x',
  related_entity_id: id, action_url: '/requests', action_title: 'Открыть',
});
const FEED = {
  items: [
    feedItem('f-1', 'question', 'Новое обращение', 'Иванова М.', 'вопрос по графику', back(0, 9, 17)),
    feedItem('f-2', 'absence_request', 'Заявка на отпуск создана', 'Петров Д.', '05.10.2026 – 12.10.2026', back(1, 18, 32)),
    feedItem('f-3', 'attendance_correction', 'Исправление отметки', 'Сидоров К.', 'Ташкент (HQ)', back(1, 8, 59)),
  ],
  counts: { all: 3 }, next_cursor: null, has_more: false, window_days: 30,
};

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

/*
 * Режимы для проверки состояний: `?delay=1500` — ответ с задержкой (отмена
 * запроса её прерывает), `?fail=dashboard` — отказ этого адреса,
 * `?zero=1` — нули и пустые списки. `window.__calls` — журнал запросов.
 */
const mode = new URLSearchParams(window.location.search);
const delay = Number(mode.get('delay') ?? 0);
const failing = mode.get('fail') ?? '';
const zero = mode.get('zero') === '1';
const calls: string[] = [];
(window as unknown as { __calls: string[] }).__calls = calls;
const EMPTY = { items: [], next_cursor: null, has_more: false };

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url.includes('/auth/')) return answer(HR);
  calls.push(url);
  if (delay > 0) {
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, delay);
      init?.signal?.addEventListener('abort', () => {
        window.clearTimeout(timer);
        reject(new DOMException('aborted', 'AbortError'));
      });
    });
  }
  if (failing && url.includes(failing)) {
    return new Response('{"error":{"code":"server_error","message":"…","details":null}}', { status: 500, headers: { 'Content-Type': 'application/json' } });
  }
  if (zero) {
    if (url.includes('/dashboard')) return answer({ ...DASHBOARD, cards: DASHBOARD.cards.map((one) => ({ ...one, value: 0 })) });
    if (url.includes('/analytics')) return answer({ period: { first: SERIES[0]!.day, last: SERIES[6]!.day, timezone: 'Asia/Tashkent' }, headcount: 0, series: SERIES.map((one) => ({ ...one, attended: 0, expected: 0 })) });
    if (url.includes('/requests') || url.includes('/notification-feed')) return answer(EMPTY);
  }
  if (url.includes('/dashboard')) return answer(DASHBOARD);
  if (url.includes('/regions')) return answer({ items: [{ id: 'r-1', name: 'Ташкент' }, { id: 'r-2', name: 'Самарканд' }, { id: 'r-3', name: 'Бухара' }] });
  if (url.includes('/offices')) return answer({ items: OFFICES });
  if (url.includes('/analytics')) return answer({ period: { first: SERIES[0]!.day, last: SERIES[6]!.day, timezone: 'Asia/Tashkent' }, headcount: 5, series: seriesFor(url) });
  if (url.includes('/attendance/presence')) {
    const office = new URL(url, 'http://x').searchParams.get('office_id') ?? 'o-1';
    return answer({ date: iso(new Date()), timezone: 'Asia/Tashkent', counts: PRESENCE[office] ?? {}, total: 0, truncated: false, items: [] });
  }
  if (url.includes('/requests')) {
    const params = new URL(url, 'http://x').searchParams;
    if (params.get('kind') === 'correction') return answer({ items: [], next_cursor: null, has_more: false });
    if ((params.get('status') ?? '').includes('APPROVED')) return answer({ items: DECIDED, next_cursor: null, has_more: false });
    return answer({ items: OPEN, next_cursor: null, has_more: false });
  }
  if (url.includes('/notification-feed')) return answer(FEED);
  return answer({ items: [], next_cursor: null, has_more: false });
};

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={['/']}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
