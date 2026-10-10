/**
 * Только для разработки: раздел «Заявки» на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Нужен для снимков состояний и записи пути «список →
 * заявка → решение» headless-браузером. Режимы в адресе:
 *
 *   ?delay=1200        — ответы с задержкой (отмена запроса её прерывает);
 *   ?fail=1            — очередь отвечает ошибкой сервера;
 *   ?denied=1          — нет доступа к очереди;
 *   ?empty=1           — заявок нет вообще;
 *   ?decide=conflict   — решение: 409 «уже рассмотрел другой HR»;
 *   ?decide=offline    — решение: обрыв связи (но сервер его применил);
 *   ?start=/requests/r-3 — с какого адреса начать.
 *
 * Решение меняет данные так же, как сервер: одобренная заявка
 * возвращается одобренной и в очереди, и на своей странице.
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
  permissions: ['absences.read', 'absences.approve', 'absences.documents', 'employees.read', 'attendance.read'],
};

const mode = new URLSearchParams(window.location.search);
const delay = Number(mode.get('delay') ?? 0);
const decide = mode.get('decide') ?? 'ok';
const calls: string[] = [];
(window as unknown as { __calls: string[] }).__calls = calls;

const PEOPLE = [
  'Каримов Азиз Рустамович', 'Иванова Мария Сергеевна', 'Петров Дмитрий Олегович',
  'Абдуллаева-Ходжаева Гульнора Шавкатовна Нигматуллаевна', 'Юсупов Бахтиёр', 'Рахимова Мадина',
];
const OFFICES = ['Ташкент (HQ)', 'Самарканд', 'Бухара — региональный офис продаж и сервиса'];

const day = (offset: number) => {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return d.toISOString().slice(0, 10);
};
const at = (offset: number, hh = 10) => {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  d.setHours(hh, 24, 0, 0);
  return d.toISOString();
};

type Row = Record<string, unknown> & { id: string; absence: Record<string, unknown> };

const LONG_COMMENT = 'Сотрудник просит учесть, что справка выдана поликлиникой по месту жительства, '
  + 'а не по месту работы; оригинал передаст лично в отдел кадров после выхода. '.repeat(3);

function absence(n: number): Row {
  const id = `r-${n}`;
  const sick = n % 3 === 0;
  const name = PEOPLE[n % PEOPLE.length]!;
  const documents = sick && n % 2 === 0
    ? [{
      id: `d-${n}`, document_type: 'SICK_NOTE', verification_status: 'PENDING', verified_at: null,
      verification_comment: null,
      file: {
        id: `f-${n}`, name: n === 6 ? 'Справка_о_временной_нетрудоспособности_поликлиника_№_14_Мирзо-Улугбекского_района.pdf' : `Справка_${n}.pdf`,
        mime_type: 'application/pdf', size_bytes: 820_000, uploaded_at: at(-1, 12), scan_status: 'CLEAN',
      },
    }]
    : [];
  return {
    kind: 'absence', id, created_at: at(-n), place: { office_name: OFFICES[n % OFFICES.length], department_name: 'Продажи' },
    absence: {
      id, kind: 'CREATE', employee: { id: `p-${n % PEOPLE.length}`, full_name: name, employee_number: `HT-0${100 + n}` },
      absence_type: sick ? { code: 'SICK_LEAVE', name: 'Больничный' } : { code: 'ANNUAL_LEAVE', name: 'Ежегодный отпуск' },
      status: n > 20 ? (n % 2 ? 'APPROVED' : 'REJECTED') : 'SUBMITTED',
      stage: sick ? (documents.length ? 'HR_REVIEW' : 'WAITING_DOCUMENTS') : 'PENDING',
      missing_for_approval: sick ? ['certificate', 'application', 'period'] : [],
      first_day: sick ? null : day(n), last_day: sick ? null : day(n + 4),
      submitted_at: at(-n), comment: n === 3 ? LONG_COMMENT : null, review_comment: null,
      requires_document: sick, application_received_at: null, documents,
      leave_balance: sick ? null : { available_days: 18, needed_days: 5, enough: true },
      overlap: null,
      history: [
        { at: at(-n), action: 'SUBMITTED', comment: n === 3 ? LONG_COMMENT : null, actor: name },
        ...(n === 3 ? Array.from({ length: 9 }, (_, k) => ({ at: at(-n + 0.1 * k, 11 + k), action: 'COMMENTED', comment: `Уточнение ${k + 1}: ${LONG_COMMENT.slice(0, 120)}`, actor: 'HR Иванова' })) : []),
      ],
    },
  };
}

const store: Row[] = mode.get('empty') === '1' ? [] : Array.from({ length: 26 }, (_, k) => absence(k + 1));
const byId = (id: string) => store.find((one) => one.id === id);

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const refusal = (status: number, code: string, message: string) =>
  json(status, { error: { code, message, details: null } });

function queue(url: URL): Response {
  const p = url.searchParams;
  let rows = store.slice();
  const status = p.get('status');
  if (status) rows = rows.filter((one) => status.split(',').includes(String(one.absence['status'])));
  const type = p.get('type');
  if (type) rows = rows.filter((one) => type.split(',').includes(String((one.absence['absence_type'] as { code: string }).code)));
  if (p.get('kind') === 'correction' || p.get('request_kind') === 'CANCEL') rows = [];
  const search = (p.get('search') ?? '').toLowerCase();
  if (search) rows = rows.filter((one) => String((one.absence['employee'] as { full_name: string }).full_name).toLowerCase().includes(search));
  const offset = Number(p.get('cursor') ?? 0);
  const limit = Number(p.get('limit') ?? 20);
  const page = rows.slice(offset, offset + limit);
  const more = offset + limit < rows.length;
  return json(200, { items: page, next_cursor: more ? String(offset + limit) : null, has_more: more });
}

function counts(): Response {
  const open = store.filter((one) => ['SUBMITTED', 'IN_REVIEW'].includes(String(one.absence['status'])));
  const code = (one: Row) => (one.absence['absence_type'] as { code: string }).code;
  return json(200, {
    open: open.length,
    leave: store.filter((one) => code(one) === 'ANNUAL_LEAVE').length,
    sick: store.filter((one) => code(one) === 'SICK_LEAVE').length,
    fixes: 0,
    cancel: 0,
  });
}

function decideOn(row: Row, apply: () => void): Response {
  if (decide === 'conflict') {
    // Другой HR успел раньше: на сервере заявка уже решена.
    row.absence['status'] = 'APPROVED';
    return refusal(409, 'conflict', 'Заявку уже рассмотрел другой сотрудник HR.');
  }
  apply();
  if (decide === 'offline') throw new TypeError('Failed to fetch');
  return json(200, row.absence);
}

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const raw = String(input);
  const url = new URL(raw, window.location.origin);
  const method = init?.method ?? 'GET';
  if (raw.includes('/auth/')) return json(200, HR);
  calls.push(`${method} ${url.pathname}${url.search}`);
  if (delay > 0) {
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, delay);
      init?.signal?.addEventListener('abort', () => {
        window.clearTimeout(timer);
        reject(new DOMException('aborted', 'AbortError'));
      });
    });
  }
  const path = url.pathname;
  if (path.includes('/requests/counts')) return counts();
  if (/\/requests\/?$/.test(path)) {
    if (mode.get('denied') === '1') return refusal(403, 'permission_denied', 'Нет прав');
    if (mode.get('fail') === '1') return refusal(500, 'server_error', '…');
    return queue(url);
  }
  const one = path.match(/\/absence-requests\/([^/]+)(\/.*)?$/);
  if (one) {
    const row = byId(one[1]!);
    if (!row) return refusal(404, 'not_found', 'Заявка не найдена');
    const tail = one[2] ?? '';
    if (method === 'GET' && !tail) return json(200, row.absence);
    if (tail === '/approve') return decideOn(row, () => { row.absence['status'] = 'APPROVED'; row.absence['history'] = [...(row.absence['history'] as unknown[]), { at: new Date().toISOString(), action: 'APPROVED', comment: null, actor: 'HR Демо' }]; });
    if (tail === '/reject') return decideOn(row, () => { row.absence['status'] = 'REJECTED'; });
    if (tail.startsWith('/documents/')) {
      return decideOn(row, () => {
        const docs = row.absence['documents'] as { verification_status: string }[];
        for (const doc of docs) doc.verification_status = tail.endsWith('/accept') ? 'VERIFIED' : 'REJECTED';
        row.absence['stage'] = tail.endsWith('/accept') ? 'HR_REVIEW' : 'NEEDS_FIX';
        row.absence['missing_for_approval'] = (row.absence['missing_for_approval'] as string[]).filter((m) => m !== 'certificate');
      });
    }
    if (tail.startsWith('/application-received')) {
      return decideOn(row, () => {
        row.absence['application_received_at'] = url.searchParams.get('received') === 'true' ? new Date().toISOString() : null;
        row.absence['missing_for_approval'] = (row.absence['missing_for_approval'] as string[]).filter((m) => m !== 'application');
      });
    }
    if (tail === '/period') {
      return decideOn(row, () => {
        const body = JSON.parse(String(init?.body ?? '{}')) as { first_day: string; last_day: string };
        row.absence['first_day'] = body.first_day;
        row.absence['last_day'] = body.last_day;
        row.absence['missing_for_approval'] = (row.absence['missing_for_approval'] as string[]).filter((m) => m !== 'period');
        row.absence['history'] = [...(row.absence['history'] as unknown[]), { at: new Date().toISOString(), action: 'PERIOD_SET', comment: null, actor: 'HR Демо' }];
      });
    }
  }
  if (/\/employees\/[^/]+\/?$/.test(path)) {
    return json(200, { current_assignment: { region_name: 'Ташкент', office_name: 'Ташкент (HQ)' } });
  }
  if (path.includes('/regions')) return json(200, { items: [{ id: 'r-1', name: 'Ташкент' }] });
  if (path.includes('/offices')) return json(200, { items: OFFICES.map((name, k) => ({ id: `o-${k}`, name, status: 'ACTIVE', region_id: 'r-1' })) });
  return json(200, { items: [], next_cursor: null, has_more: false });
};

const start = mode.get('start') ?? '/requests';
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
