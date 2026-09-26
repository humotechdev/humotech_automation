/**
 * Только для разработки: «Ознакомления» на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Вкладка — из адреса страницы: `?tab=materials|sections`.
 * Данные выдуманы и живут только здесь: на стенд и в сборку они не попадают.
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
  permissions: ['onboarding.read', 'onboarding.manage', 'policies.publish', 'employees.read'],
};

const pad = (n: number) => String(n).padStart(2, '0');
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const TODAY = iso(new Date());
const addDays = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return iso(d); };

const PEOPLE: [string, string, string, string, string, number, number, number, number, string | null][] = [
  // имя, номер, офис, отдел, группа, разделы, документы(готово), документов, срок(дни), причина
  ['Muradov Azizbek', '1042', 'Ташкент', 'IT-отдел', 'attention', 10, 3, 4, -3, 'overdue'],
  ['Ashiraliyev Jandos', '0876', 'Алматы', 'Отдел продаж', 'attention', 10, 2, 3, 6, 'renewal'],
  ['Karimova Sevara', '0123', 'Ташкент', 'HR-отдел', 'attention', 4, 0, 3, 3, 'silent'],
  ['Abdullaeva Nigora', '0985', 'Бухара', 'Финансовый отдел', 'done', 10, 3, 3, -8, null],
  ['Ibragimov Sardor', '0678', 'Самарканд', 'Операционный отдел', 'in_progress', 6, 0, 3, 5, null],
  ['Tursunov Temur', '0457', 'Ташкент', 'IT-отдел', 'not_started', 0, 0, 3, 12, null],
  ['Rahimova Dilnoza', '0301', 'Бухара', 'Отдел продаж', 'waiting', 0, 0, 3, 13, null],
];

function row(p: typeof PEOPLE[number], i: number) {
  const [name, number, office, department, group, sections, done, total, due, reason] = p;
  return {
    employee_id: `e-${i}`, full_name: name, employee_number: number, office_name: office, department_name: department,
    position_name: null, telegram_state: group === 'waiting' ? 'NOT_LINKED' : 'ACTIVE',
    status: group === 'done' ? 'COMPLETED' : group === 'not_started' || group === 'waiting' ? 'NOT_STARTED' : 'IN_PROGRESS',
    stage: 'SECTIONS', completed: group === 'done', sections_done: sections, sections_total: 10, policies_done: done, policies_total: total,
    invited_at: `${addDays(-9)}T06:00:00Z`, started_at: sections ? `${addDays(-8)}T06:00:00Z` : null, completed_at: null,
    last_reminder_at: null, invitation_status: null, invitation_expires_at: null, enrolled_at: `${addDays(-10)}T06:00:00Z`,
    due_date: addDays(due), overdue: reason === 'overdue', reasons: reason ? [reason] : [], group,
    materials: [
      { document_id: 'd-0', title: 'Политика информационной безопасности', version: '1.5', state: reason === 'renewal' ? 'renewal' : done ? 'accepted' : 'pending', decided_at: null },
      { document_id: 'd-1', title: 'Кодекс этики и поведения', version: '1', state: 'pending', decided_at: null },
    ],
  };
}

const ROWS = PEOPLE.map(row);

const version = (id: string, v: string, status: string, day: string) => ({
  id, version: v, status, summary: 'Краткий текст', body: null, agree_label: 'Согласен', has_file: false, file_name: null,
  published_at: status === 'PUBLISHED' ? `${day}T06:00:00Z` : null, created_at: `${day}T06:00:00Z`,
});
const DOCS = [
  ['Вводный инструктаж по охране труда', 'Основные правила безопасной работы на рабочем месте.', 'c-0', '3.2', 48, 41, 0, -2],
  ['Пожарная безопасность', 'Правила поведения при пожаре и действия в чрезвычайных ситуациях.', 'c-0', '2.1', 36, 28, 0, -5],
  ['Политика защиты данных', 'Как мы обрабатываем, храним и защищаем персональные данные.', 'c-2', '1.5', 52, 49, 3, -7],
  ['Кодекс этики и поведения', 'Принципы делового поведения и ценности компании.', 'c-1', null, 0, 0, 0, -15],
  ['Регламент удалённой работы', 'Правила организации удалённой работы и коммуникации в команде.', 'c-2', '2', 37, 35, 0, -1],
].map(([title, description, cat, live, assigned, confirmed, renewal, ago], i) => ({
  id: `d-${i}`, code: `D${i}`, title, description, is_mandatory: true, position: i, archived_at: null,
  current_version: live ? version(`v-${i}`, String(live), 'PUBLISHED', addDays(Number(ago))) : null,
  versions: live ? [version(`v-${i}`, String(live), 'PUBLISHED', addDays(Number(ago))), ...(i === 2 ? [version('v-x', '1.6', 'DRAFT', addDays(-1))] : [])] : [version(`v-${i}`, '1', 'DRAFT', addDays(Number(ago)))],
  category: { id: String(cat), title: ['Охрана труда', 'HR и культура', 'Информационная безопасность'][Number(String(cat).slice(2))] },
  assigned, confirmed, declined: 0, renewal_pending: renewal, nearest_due: i === 1 ? addDays(4) : null,
  created_by: 'Karimova Sevara', changed_at: `${addDays(Number(ago))}T09:30:00Z`, changed_by: ['Muradov Azizbek', 'Ashiraliyev Jandos', 'Karimova Sevara', 'Abdullaeva Nigora', 'Ibragimov Sardor'][i],
}));

const CATEGORIES = [
  ['Охрана труда', 'Материалы по безопасной работе на рабочем месте и в чрезвычайных ситуациях', 'Muradov Azizbek', 'Специалист по ОТ', [0, 1]],
  ['HR и культура', 'Материалы о корпоративной культуре, адаптации и внутренних правилах', 'Abdullaeva Nigora', 'HR-менеджер', [3]],
  ['Информационная безопасность', 'Материалы по защите данных и безопасной работе с информацией', 'Karimova Sevara', 'Специалист по ИБ', [2, 4]],
].map(([title, description, owner, position, docs], i) => ({
  id: `c-${i}`, title, description, position: i + 1,
  owner: { id: `e-${i}`, full_name: owner, position_name: position },
  documents_count: (docs as number[]).length, documents: (docs as number[]).map((d) => ({ id: `d-${d}`, title: DOCS[d]!.title })),
  changed_at: `${addDays(-i * 5 - 1)}T12:30:00Z`, changed_by: 'Karimova Sevara',
}));

const CARDS = ['О компании', 'Миссия и ценности', 'Структура компании', 'Рабочий день'].map((title, i) => ({
  id: `s-${i}`, position: i + 1, title, body: 'Текст карточки о компании.', button_label: 'Я ознакомился', version: 1, updated_at: `${addDays(-20)}T06:00:00Z`,
}));

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const tally = () => {
  const groups: Record<string, number> = { all: ROWS.length, done: 0, attention: 0, waiting: 0, not_started: 0, in_progress: 0, overdue: 0 };
  for (const one of ROWS) { groups[one.group] = (groups[one.group] ?? 0) + 1; if (one.overdue) groups['overdue']! += 1; }
  return { all: ROWS.length, groups };
};

window.fetch = async (input: RequestInfo | URL) => {
  const url = new URL(String(input), 'http://x');
  const q = url.searchParams;
  const path = url.pathname;
  await new Promise((resolve) => setTimeout(resolve, 200));
  if (path.includes('/auth/')) return answer(HR);
  if (path.includes('/offices')) return answer({ items: ['Ташкент', 'Самарканд', 'Бухара', 'Алматы'].map((name, i) => ({ id: `o-${i}`, name, status: 'ACTIVE' })) });
  if (path.includes('/departments')) return answer({ items: ['IT-отдел', 'HR-отдел', 'Отдел продаж'].map((name, i) => ({ id: `dp-${i}`, name })), next_cursor: null, has_more: false });
  if (path.includes('/onboarding/counts')) return answer(tally());
  if (path.includes('/onboarding/progress')) {
    const group = q.get('group');
    return answer({ items: ROWS.filter((one) => !group || one.group === group), next_cursor: null, has_more: false });
  }
  if (path.includes('/onboarding/documents')) return answer({ items: DOCS });
  if (path.includes('/onboarding/categories')) return answer({ items: CATEGORIES });
  if (path.includes('/onboarding/sections')) return answer({ items: CARDS });
  return answer({ items: [], next_cursor: null, has_more: false });
};

void TODAY;
const start = `/onboarding${window.location.search}`;
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
