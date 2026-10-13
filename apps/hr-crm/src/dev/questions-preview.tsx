/**
 * Только для разработки: «Обращения» на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Данные повторяют макет: четыре обращения в очереди и
 * переписка о командировке. Нужен для снимка вёрстки headless-браузером.
 *
 * Режимы для проверки поведения:
 *   ?delay=900          — ответы с задержкой (отмена запроса её прерывает);
 *   ?long=1             — длинная история переписки;
 *   ?replyfail=offline  — отправка ответа обрывается, ответ не сохранён;
 *   ?replyfail=lost     — обрывается, но сервер ответ сохранил.
 *   ?filter=1           — сразу с отбором «Высокий, Срочный» в адресе.
 *
 * Поток событий — подменённый EventSource: `window.__push(id, text)`
 * добавляет сообщение сотрудника и шлёт странице тот же сигнал
 * `question`, что и сервер. Страница обрабатывает его своим кодом.
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
    'absences.read', 'attendance.read', 'notifications.manage',
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

/**
 * `?delivery=mixed` — все состояния доставки ответа HR разом: первый ответ
 * не доставлен (с повтором), последний ещё отправляется, остальные
 * отправлены. Для снимка вёрстки строки состояния под текстом.
 */
const MIXED = new URLSearchParams(window.location.search).get('delivery') === 'mixed';
function delivery(iso: string, index: number, last: number) {
  if (!MIXED) return read(iso);
  if (index === 0) {
    return { status: 'FAILED', sent_at: null, read_at: null, error: 'blocked_by_user', notification_id: 'n-0', can_retry: true };
  }
  if (index === last) return { status: 'QUEUED', sent_at: null, read_at: null, error: null, notification_id: 'n-9', can_retry: false };
  return { status: 'DELIVERED', sent_at: iso, read_at: null, error: null, notification_id: `n-${index}`, can_retry: false };
}

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
      delivery: who === 'E' ? null : delivery(
        iso,
        THREAD.slice(0, index).filter(([one]) => one !== 'E').length,
        THREAD.filter(([one]) => one !== 'E').length - 1,
      ),
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
  // ?counts=12,14 — крупные счётчики быстрых фильтров (проверка ширины ряда).
  ...(new URLSearchParams(window.location.search).get('counts')
    ? (() => {
      const [unanswered, urgent] = new URLSearchParams(window.location.search).get('counts')!.split(',').map(Number);
      return { quick: { all: 4, unanswered: unanswered ?? 0, mine: 1, urgent: urgent ?? 0, unread: 1 } };
    })()
    : {}),
};

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const mode = new URLSearchParams(window.location.search);
const delay = Number(mode.get('delay') ?? 0);
const replyFail = mode.get('replyfail') ?? '';
const calls: string[] = [];

type Msg = (typeof QUESTION.messages)[number] & { client_request_id?: string | null };

/** Переписки по обращениям: у каждого своя, чтобы переключение было видно. */
const threads: Record<string, Msg[]> = {
  'q-1': [
    ...(mode.get('long') === '1'
      ? Array.from({ length: 28 }, (_, k): Msg => ({
        ...QUESTION.messages[0]!, id: `old-${k}`,
        kind: k % 3 ? 'EMPLOYEE' : 'HR', source: k % 3 ? 'TELEGRAM' : 'CRM',
        author: k % 3 ? EMP_AUTHOR : HR_AUTHOR,
        body: `Сообщение из истории №${k + 1}. ${'Подробности по вопросу командировки и компенсаций. '.repeat(1 + (k % 3))}`,
        created_at: at(8, k % 60, 1), delivery: null,
      }))
      : []),
    ...QUESTION.messages,
  ],
};
for (const row of ROWS.slice(1)) {
  threads[row.id] = [{
    ...QUESTION.messages[0]!, id: `${row.id}-m0`, body: row.snippet, created_at: at(9, 30),
    author: { ...EMP_AUTHOR, name: row.employee.full_name },
  }];
}

const questionOf = (id: string) => {
  const row = ROWS.find((one) => one.id === id) ?? ROWS[0]!;
  return { ...QUESTION, ...row, messages: threads[id] ?? [] };
};

function rowsNow() {
  return ROWS.map((row) => {
    const last = (threads[row.id] ?? []).at(-1);
    return last ? { ...row, snippet: last.body ?? row.snippet, last_message_at: last.created_at, last_message_kind: last.kind } : row;
  });
}

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = init?.method ?? 'GET';
  if (url.includes('/auth/')) return answer(HR);
  calls.push(`${method} ${url}`);
  if (delay > 0) {
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, delay);
      init?.signal?.addEventListener('abort', () => {
        window.clearTimeout(timer);
        reject(new DOMException('aborted', 'AbortError'));
      });
    });
  }
  if (url.includes('/escalations/counts')) return answer(COUNTS);
  if (url.includes('/escalations/assignees')) return answer({ items: [{ id: 'u-2', name: 'Aisha Karimova' }, { id: 'u-3', name: 'Камилла Алимова' }] });
  if (url.includes('/context/')) return answer(CONTEXT);
  if (url.includes('/read/')) return answer({ id: 'q-1', unread: false });
  const reply = url.match(/\/escalations\/(q-\d+)\/reply\/?/);
  if (reply && method === 'POST') {
    const id = reply[1]!;
    const body = JSON.parse(String(init?.body ?? '{}')) as { text: string; client_request_id: string };
    const list = threads[id] ?? (threads[id] = []);
    const saved = () => {
      if (!list.some((one) => one.client_request_id === body.client_request_id)) {
        list.push({
          ...QUESTION.messages[1]!, id: `r-${Date.now()}`, kind: 'HR', source: 'CRM', author: HR_AUTHOR,
          body: body.text, created_at: new Date().toISOString(), client_request_id: body.client_request_id,
          delivery: { status: 'QUEUED', sent_at: null, read_at: null, error: null } as never,
        });
      }
    };
    if (replyFail === 'offline') throw new TypeError('Failed to fetch');
    saved();
    if (replyFail === 'lost') throw new TypeError('Failed to fetch');
    return answer(questionOf(id));
  }
  // Приоритет и категория — как на сервере: значение меняется у строки
  // очереди, в ответ — обращение целиком.
  const change = url.match(/\/escalations\/(q-\d+)\/(priority|category)\/?$/);
  if (change && method === 'POST') {
    const row = ROWS.find((item) => item.id === change[1]);
    const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, string>;
    if (row) Object.assign(row, { [change[2]!]: body[change[2]!] });
    return answer(questionOf(change[1]!));
  }
  const one = url.match(/\/escalations\/(q-\d+)\/?(\?|$)/);
  if (one) return answer(questionOf(one[1]!));
  if (/\/escalations\/?(\?|$)/.test(url)) {
    // Фильтр «Приоритет» и «Категория»: несколько значений через запятую.
    const asked = new URL(url, window.location.origin).searchParams;
    const within = (key: 'priority' | 'category', value: string) => {
      const list = asked.get(key);
      return !list || list.split(',').includes(value);
    };
    const items = rowsNow().filter((row) => within('priority', row.priority) && within('category', row.category));
    return answer({ items, next_cursor: null, has_more: false });
  }
  if (url.includes('/offices')) return answer({ items: [{ ...OFFICE, status: 'ACTIVE', region_id: 'r-1' }] });
  if (url.includes('/photo')) return new Response('', { status: 404 });
  return answer({ items: [], next_cursor: null, has_more: false });
};

/** Подменённый поток событий: те же события, что шлёт сервер. */
class PreviewStream extends EventTarget {
  static last: PreviewStream | null = null;
  /** Сколько раз страница открывала поток: лишних подключений быть не должно. */
  static opened = 0;
  readyState = 1;
  onerror: ((event: Event) => void) | null = null;
  constructor() {
    super();
    PreviewStream.last = this;
    PreviewStream.opened += 1;
    window.setTimeout(() => this.dispatchEvent(new MessageEvent('ready', { data: '{}' })), 30);
  }
  close() { this.readyState = 2; }
  static CLOSED = 2;
}
(window as unknown as { EventSource: unknown }).EventSource = PreviewStream;
Object.assign(window, {
  __calls: calls,
  __streams: () => PreviewStream.opened,
  __push: (id: string, text: string) => {
    const list = threads[id] ?? (threads[id] = []);
    list.push({
      ...QUESTION.messages[0]!, id: `p-${Date.now()}`, kind: 'EMPLOYEE', source: 'TELEGRAM',
      author: { ...EMP_AUTHOR, name: ROWS.find((row) => row.id === id)?.employee.full_name ?? EMP_AUTHOR.name },
      body: text, created_at: new Date().toISOString(), delivery: null,
    });
    PreviewStream.last?.dispatchEvent(new MessageEvent('question', {
      data: JSON.stringify({ id, what: 'message', at: Date.now() }),
    }));
  },
});

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={[mode.get('filter') === '1' ? '/questions?id=q-1&priority=HIGH,URGENT' : '/questions?id=q-1']}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
