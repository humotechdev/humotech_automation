/**
 * Только для разработки: «Обращения» на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Данные повторяют макет: четыре обращения в очереди и
 * переписка о командировке. Нужен для снимка вёрстки headless-браузером.
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
  email: 'hr@humotech.local',
  organization_id: 'org-1',
  organization_code: 'DEMO',
  employee_id: null,
  status: 'ACTIVE',
  timezone: 'Asia/Tashkent',
  roles: ['HR'],
  permissions: [
    'questions.read', 'questions.answer', 'knowledge.read', 'employees.read',
    'absences.read', 'attendance.read',
  ],
};

const day = new Date();
const at = (hh: number, mm: number, back = 0) => {
  const d = new Date(day);
  d.setDate(d.getDate() - back);
  d.setHours(hh, mm, 0, 0);
  return d.toISOString();
};

const person = (id: string, name: string) => ({ id, full_name: name, employee_number: null, has_photo: false });
const OFFICE = { id: 'o-1', name: 'Главный офис' };

const ROWS = [
  {
    id: 'q-1', number: 101, employee: person('e-1', 'Muradov Azizbek'), office: OFFICE,
    topic: 'Командировка', snippet: 'Здравствуйте! Нужно уточнить по командировке в Ташкент.',
    last_message_kind: 'EMPLOYEE', category: 'OTHER', priority: 'NORMAL', status: 'NEW',
    unread: false, awaiting_reply: true, due_at: null, overdue: false,
    last_message_at: at(10, 24), created_at: at(10, 12), assignee: { id: 'u-2', name: 'Aisha Karimova' },
  },
  {
    id: 'q-2', number: 102, employee: person('e-2', 'Karimova Sevara'), office: OFFICE,
    topic: 'Начисление зарплаты', snippet: 'Когда будет начислена зарплата за сентябрь?',
    last_message_kind: 'EMPLOYEE', category: 'SALARY', priority: 'NORMAL', status: 'NEW',
    unread: true, awaiting_reply: true, due_at: null, overdue: false,
    last_message_at: at(9, 15), created_at: at(9, 15), assignee: null,
  },
  {
    id: 'q-3', number: 99, employee: person('e-3', 'Tursunov Temur'), office: OFFICE,
    topic: 'Оформление отпуска', snippet: 'Подскажите, пожалуйста, какие документы нужны для отпуска?',
    last_message_kind: 'EMPLOYEE', category: 'VACATION', priority: 'URGENT', status: 'IN_PROGRESS',
    unread: false, awaiting_reply: true, due_at: null, overdue: false,
    last_message_at: at(16, 40, 1), created_at: at(16, 30, 1), assignee: null,
  },
  {
    id: 'q-4', number: 95, employee: person('e-4', 'Abdullaeva Nigora'), office: OFFICE,
    topic: 'Справка с места работы', snippet: 'Нужно получить справку для банка до пятницы.',
    last_message_kind: 'HR', category: 'DOCUMENTS', priority: 'NORMAL', status: 'WAITING_EMPLOYEE',
    unread: false, awaiting_reply: false, due_at: null, overdue: false,
    last_message_at: at(12, 5, 12), created_at: at(11, 50, 12), assignee: null,
  },
];

const HR_AUTHOR = { type: 'user', id: 'u-2', name: 'Aisha Karimova' };
const EMP_AUTHOR = { type: 'employee', id: 'e-1', name: 'Muradov Azizbek' };
const read = (iso: string) => ({ status: 'READ', sent_at: iso, read_at: iso, error: null });

const THREAD: [string, string, string][] = [
  ['E', '10:12', 'Здравствуйте! Нужно уточнить по командировке в Ташкент.\nС 22 по 25 апреля. Нужно ли какое-то дополнительное согласование со стороны HR?'],
  ['H', '10:14', 'Здравствуйте, Azizbek!\nДа, для командировки необходимо согласование с вашим руководителем и оформление заявки в системе.\nЯ подскажу детали.'],
  ['E', '10:16', 'Хорошо, подскажите, пожалуйста, какие документы нужно подготовить и в какие сроки их лучше подать?'],
  ['H', '10:18', 'Вам нужно заполнить заявку на командировку в разделе «Кадровый учёт» → «Командировки», приложить служебную записку и план поездки. Желательно подать заявку минимум за 3 рабочих дня до даты выезда.'],
  ['E', '10:20', 'Понял, спасибо! А суточные и проживание будут компенсироваться по стандартным условиям?'],
  ['H', '10:22', 'Да, всё по стандартным условиям компании. После утверждения заявки вам вышлют памятку с деталями по компенсациям.\nЕсли будут дополнительные вопросы — пишите, помогу.'],
];

const SOURCES = [
  { id: 's-1', title: 'Как оформить командировку', source_type: 'INSTRUCTION', version: 1, status: 'ACTIVE', published_at: at(9, 0, 30), updated_at: at(9, 0, 30) },
  { id: 's-2', title: 'Компенсации при командировках', source_type: 'POLICY', version: 2, status: 'ACTIVE', published_at: at(9, 0, 60), updated_at: at(9, 0, 60) },
  { id: 's-3', title: 'Образец служебной записки', source_type: 'TEMPLATE', version: 1, status: 'ACTIVE', published_at: at(9, 0, 90), updated_at: at(9, 0, 90) },
];

const QUESTION = {
  ...ROWS[0],
  question_text: THREAD[0]![2],
  channel: 'TELEGRAM',
  first_response_at: at(10, 14),
  closed_at: null,
  closed_by: null,
  close_reason: null,
  telegram: { connected: true, reason: null, status: 'ACTIVE' },
  messages: THREAD.map(([who, time, body], index) => {
    const [hh, mm] = time.split(':').map(Number);
    const iso = at(hh!, mm!);
    return {
      id: `m-${index}`,
      kind: who === 'E' ? 'EMPLOYEE' : 'HR',
      source: who === 'E' ? 'TELEGRAM' : 'CRM',
      body,
      event: null,
      details: null,
      author: who === 'E' ? EMP_AUTHOR : HR_AUTHOR,
      created_at: iso,
      delivery: who === 'E' ? null : read(iso),
      attachment: null,
    };
  }),
  draft: {
    status: 'READY', text: 'Да, всё по стандартным условиям компании.', confidence: '0.9',
    generated_at: at(10, 13), outdated: false, sources: SOURCES.slice(0, 2),
  },
  actions: {
    take: true, assign: true, priority: true, category: true, wait: true, start: true,
    close: true, reopen: false, reply: true, draft: true,
  },
};

const CONTEXT = {
  employee: {
    id: 'e-1', full_name: 'Muradov Azizbek', employee_number: null, employment_status: 'ACTIVE',
    has_photo: false, position: 'Sales Manager', department: 'Отдел продаж (Sales)', office: OFFICE,
    schedule: null, telegram: { connected: true, reason: null, status: 'ACTIVE', username: null },
  },
  links: { employee_card: true, attendance: true, requests: true },
  requests: [], balance: [], corrections: [], documents: [],
  history: { total: 1, closed: 0, open: 1, recent: [] },
  materials: SOURCES,
  today: { day: at(0, 0), state: 'IN_OFFICE', first_entry_at: at(9, 2), last_exit_at: null },
};

const COUNTS = {
  statuses: { NEW: 2, IN_PROGRESS: 3, WAITING_EMPLOYEE: 1, CLOSED: 24 },
  total: 30,
  quick: { all: 4, unanswered: 2, mine: 1, urgent: 1, unread: 1 },
};

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

window.fetch = async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.includes('/auth/')) return answer(HR);
  if (url.includes('/escalations/counts')) return answer(COUNTS);
  if (url.includes('/escalations/assignees')) return answer({ items: [{ id: 'u-2', name: 'Aisha Karimova' }, { id: 'u-3', name: 'Камилла Алимова' }] });
  if (url.includes('/context/')) return answer(CONTEXT);
  if (url.includes('/read/')) return answer({ id: 'q-1', unread: false });
  if (/\/escalations\/q-\d+\/?(\?|$)/.test(url)) return answer(QUESTION);
  if (/\/escalations\/?(\?|$)/.test(url)) return answer({ items: ROWS, next_cursor: null, has_more: false });
  if (url.includes('/offices')) return answer({ items: [{ ...OFFICE, status: 'ACTIVE', region_id: 'r-1' }] });
  if (url.includes('/photo')) return new Response('', { status: 404 });
  return answer({ items: [], next_cursor: null, has_more: false });
};

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={['/questions?id=q-1']}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
