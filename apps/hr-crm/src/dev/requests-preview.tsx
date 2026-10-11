/**
 * Только для разработки: «Заявки» и страница одной заявки на подставных
 * данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Маршрут берётся из `?path=`: `/requests`,
 * `/requests/r-1` (больничный со справкой на проверке), `/requests/r-3`
 * (отпуск). Нужен для замеров вёрстки headless-браузером.
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
  permissions: ['absences.read', 'absences.decide', 'employees.read', 'attendance.read'],
};

const EMPLOYEE = { id: 'p-1', full_name: 'Muradov Azizbek', employee_number: 'HT-002' };
const OTHER = { id: 'p-2', full_name: 'Рахимова Мадина', employee_number: 'HT-003' };
const PLACE = { office_name: 'город Ташкент', department_name: 'Отдел продаж' };

const SICK = {
  id: 'r-1',
  kind: 'CREATE',
  employee: EMPLOYEE,
  absence_type: { code: 'SICK_LEAVE', name: 'Больничный' },
  status: 'SUBMITTED',
  stage: 'WAITING_DOCUMENTS',
  missing_for_approval: ['certificate', 'application', 'period'],
  first_day: null,
  last_day: null,
  submitted_at: '2026-09-21T11:24:00Z',
  comment: null,
  review_comment: null,
  requires_document: true,
  application_received_at: null,
  documents: [{
    id: 'd-1',
    document_type: 'SICK_NOTE',
    verification_status: 'PENDING',
    verified_at: null,
    verification_comment: null,
    file: {
      id: 'f-1', name: 'Справка_21-09-2026.pdf', mime_type: 'application/pdf',
      size_bytes: 1_887_436, uploaded_at: '2026-09-21T11:25:00Z', scan_status: 'CLEAN',
    },
  }],
  history: [
    { at: '2026-09-21T11:24:00Z', action: 'SUBMITTED', comment: null },
    { at: '2026-09-21T11:25:00Z', action: 'DOCUMENT_ATTACHED', comment: null },
  ],
};

const LEAVE = {
  ...SICK,
  id: 'r-3',
  employee: OTHER,
  absence_type: { code: 'ANNUAL_LEAVE', name: 'Ежегодный отпуск' },
  stage: 'PENDING',
  missing_for_approval: [],
  first_day: '2026-10-20',
  last_day: '2026-10-30',
  requires_document: false,
  documents: [],
  leave_balance: { needed_days: 9, available_days: 14, enough: true },
  overlap: null,
  history: [{ at: '2026-09-20T09:00:00Z', action: 'SUBMITTED', comment: null }],
};

const ROWS = [
  { kind: 'absence', id: 'r-1', created_at: SICK.submitted_at, place: PLACE, absence: SICK },
  { kind: 'absence', id: 'r-3', created_at: LEAVE.submitted_at, place: PLACE, absence: LEAVE },
];

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

window.fetch = async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.includes('/auth/')) return answer(HR);
  if (/\/absence-requests\/r-1\/?(\?|$)/.test(url)) return answer(SICK);
  if (/\/absence-requests\/r-3\/?(\?|$)/.test(url)) return answer(LEAVE);
  if (url.includes('/counts')) {
    return answer({ open: 12, leave: 4, sick: 5, fixes: 3, cancel: 1 });
  }
  if (/\/requests\/?(\?|$)/.test(url)) return answer({ items: ROWS, next_cursor: null, has_more: false });
  if (url.includes('/photo')) return new Response('', { status: 404 });
  return answer({ items: [], next_cursor: null, has_more: false });
};

const path = new URLSearchParams(window.location.search).get('path') ?? '/requests';

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={[path]}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
