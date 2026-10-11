/**
 * Только для разработки: «Уведомления» на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Имена, офисы и события выдуманы. Нужен, чтобы снять
 * вёрстку headless-браузером: `?view=delivery` открывает очередь.
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import { App } from '../app/App';
import { SessionProvider } from '../features/auth/session';
import '../styles/app.css';
import '../styles/crm.css';

const HR = {
  id: 'u-1',
  email: 'hr@example.test',
  organization_id: 'org-1',
  organization_code: 'DEMO',
  employee_id: null,
  status: 'ACTIVE',
  timezone: 'Asia/Tashkent',
  roles: ['HR'],
  permissions: ['notifications.read', 'notifications.manage', 'employees.read'],
};

const now = Date.now();
const ago = (hours: number) => new Date(now - hours * 3600_000).toISOString();

const KINDS = [
  { type: 'sick_leave', group: 'requests', title: 'Заявка на больничный', short: '25–29 сентября' },
  { type: 'absence_request', group: 'requests', title: 'Заявка на отпуск', short: '6–17 октября' },
  { type: 'question', group: 'questions', title: 'Новое обращение', short: 'Вопрос о начислении зарплаты' },
  { type: 'absence_document', group: 'documents', title: 'Загружена справка', short: 'Справка о болезни, PDF' },
  { type: 'attendance_correction', group: 'requests', title: 'Исправление отметки', short: '24 сентября, вход 09:05' },
  { type: 'policy_declined', group: 'documents', title: 'Отказ от ознакомления с документом', short: 'Правила внутреннего распорядка' },
];
const PEOPLE = [
  ['Алиева Дилноза', 'Главный офис'],
  ['Каримов Бехзод', 'Офис Самарканд'],
  ['Рахимова Нигора', 'Главный офис'],
  ['Юсупов Шерзод', 'Офис Бухара'],
  ['Абдурахмонова Гульчехра Шавкатовна', 'Региональный офис Ферганской долины, филиал № 2'],
  ['', ''],
];

const EVENTS = Array.from({ length: 26 }, (_, i) => {
  const kind = KINDS[i % KINDS.length]!;
  const [name, office] = PEOPLE[i % PEOPLE.length]!;
  const id = `00000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`;
  return {
    id: `${kind.type}:${id}`,
    type: kind.type,
    group: kind.group,
    title: kind.title,
    short_text: kind.short,
    employee_id: name ? `e-${i}` : null,
    employee_name: name,
    office_id: office ? 'o-1' : null,
    office_name: office || null,
    status: 'SUBMITTED',
    status_label: 'На рассмотрении',
    priority: 'HIGH',
    requires_action: i % 3 !== 2,
    created_at: ago(i * 5 + 1),
    read_at: i < 3 ? null : ago(1),
    related_entity_type: 'absence_requests',
    related_entity_id: id,
    action_url: kind.group === 'questions' ? `/questions?id=${id}` : `/requests?request=${id}`,
    action_title: 'Открыть',
  };
});

const FEED_COUNTS = {
  all: EVENTS.length,
  unread: EVENTS.filter((e) => !e.read_at).length,
  action: EVENTS.filter((e) => e.requires_action).length,
  requests: EVENTS.filter((e) => e.group === 'requests').length,
  documents: EVENTS.filter((e) => e.group === 'documents').length,
  questions: EVENTS.filter((e) => e.group === 'questions').length,
};

const QUEUE_STATUSES = ['SENT', 'PENDING', 'FAILED', 'SENT', 'CANCELLED', 'READ', 'RUNNING', 'SENT'];
const QUEUE = Array.from({ length: 8 }, (_, i) => {
  const [name, office] = PEOPLE[i % 5]!;
  const status = QUEUE_STATUSES[i]!;
  return {
    id: `n-${i + 1}`,
    employee_id: `e-${i}`,
    employee: { id: `e-${i}`, full_name: name, employee_number: `HT-0${100 + i}` },
    office_id: 'o-1',
    office_name: office,
    region_name: 'Ташкентская область',
    channel: 'TELEGRAM',
    notification_type: 'absence.request.approved',
    title: ['Отпуск согласован', 'Напоминание об отметке', 'Справка принята', 'Новый документ для ознакомления'][i % 4],
    body: 'Текст уведомления сотруднику.',
    status,
    attempts: status === 'FAILED' ? 3 : 1,
    error_message: status === 'FAILED' ? 'blocked_by_user' : null,
    scheduled_at: null,
    next_attempt_at: null,
    sent_at: status === 'SENT' || status === 'READ' ? ago(i + 1) : null,
    read_at: null,
    related_entity_type: 'absence_requests',
    related_entity_id: `r-${i}`,
    created_at: ago(i * 3 + 1),
    updated_at: ago(i * 3),
    can_retry: status === 'FAILED',
    can_cancel: status === 'PENDING' || status === 'FAILED',
  };
});
const QUEUE_COUNTS = {
  total: 57, PENDING: 6, RUNNING: 1, SENT: 38, READ: 2, FAILED: 5, CANCELLED: 5,
  sent: 40, queued: 7, failed: 5, cancelled: 5, timezone: 'Asia/Tashkent',
};

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

window.fetch = async (input: RequestInfo | URL) => {
  const url = new URL(String(input), window.location.origin);
  const path = url.pathname;
  if (path.includes('/auth/')) return answer(HR);
  if (path.endsWith('/notification-feed/counts')) return answer(FEED_COUNTS);
  if (path.endsWith('/notification-feed')) {
    const scope = url.searchParams.get('scope') ?? 'all';
    const limit = Number(url.searchParams.get('limit') ?? '20');
    const start = Number((url.searchParams.get('cursor') ?? 'c0').slice(1));
    const list = EVENTS.filter((e) =>
      scope === 'all' ? true
        : scope === 'unread' ? !e.read_at
          : scope === 'action' ? e.requires_action
            : e.group === scope);
    const page = list.slice(start, start + limit);
    const more = list.length > start + limit;
    return answer({ items: page, counts: FEED_COUNTS, next_cursor: more ? `c${start + limit}` : null, has_more: more, window_days: 30 });
  }
  if (path.endsWith('/notifications/counts/')) return answer(QUEUE_COUNTS);
  if (/\/notifications\/n-\d+\/attempts\/$/.test(path)) return answer({ items: [], kept: true });
  if (path.endsWith('/notifications/')) return answer({ items: QUEUE, next_cursor: 'x', has_more: true });
  if (path.includes('/regions')) return answer({ items: [{ id: 'r-1', name: 'Ташкентская область', status: 'ACTIVE' }] });
  if (path.includes('/offices')) return answer({ items: [{ id: 'o-1', name: 'Главный офис', status: 'ACTIVE', region_id: 'r-1' }] });
  return answer({ items: [], next_cursor: null, has_more: false });
};

const search = new URLSearchParams(window.location.search);
const entry = search.get('view') === 'delivery' ? '/notifications?view=delivery' : '/notifications';

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={[entry]}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
