/**
 * Только для разработки: «Опросы» на подставных данных.
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
  permissions: ['surveys.read', 'surveys.manage'],
};

const q = (id: string) => ({ id, position: 1, kind: 'SCALE', is_required: true, text: 'Как дела?', options: null });
const template = (id: string, title: string, status: string, n: number) => ({
  id, title, description: 'Короткий опрос о работе', status, version: 1,
  published_at: status === 'PUBLISHED' ? '2026-09-10T05:00:00Z' : null,
  created_at: '2026-09-01T05:00:00Z', updated_at: '2026-09-1' + n + 'T05:00:00Z',
  questions: Array.from({ length: n }, (_, i) => q(`${id}-${i}`)), archived_at: null, campaigns_count: 0, author_name: 'Камилла Алимова',
});
const TEMPLATES = [
  template('t-1', 'Итоги стажировки', 'PUBLISHED', 7),
  template('t-2', 'Оценка адаптации', 'PUBLISHED', 5),
  template('t-3', 'Пульс команды', 'DRAFT', 4),
  template('t-4', 'Опрос о графике', 'DRAFT', 3),
];
const CAMPAIGNS = [1, 2, 3].map((i) => ({
  id: `c-${i}`, template_id: 't-1', template_title: 'Итоги стажировки', title: `Пульс-опрос ${i}`,
  status: 'ACTIVE', audience_kind: 'ALL', audience_ids: [], scheduled_at: null, repeat_months: null,
  remind_at: null, due_at: null, next_send_at: null, sent_at: '2026-09-17T06:00:00Z', template_version: 1,
  automation_id: null, total: 10, done: 3 + i, created_at: '2026-09-17T05:00:00Z',
}));
const RULES = [1, 2].map((i) => ({
  id: `a-${i}`, title: `Правило ${i}`, template_id: 't-1', template_title: 'Итоги стажировки',
  trigger_kind: 'PROBATION_END', offset_days: 1, send_hour: 10, send_minute: 0, repeat_months: null,
  scope: null, is_active: true, last_run_at: null, next_run_at: null, created_at: '2026-09-01T05:00:00Z',
}));

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

window.fetch = async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.includes('/auth/')) return answer(HR);
  if (url.includes('/surveys/templates/')) return answer({ items: url.includes('ARCHIVED') ? [] : TEMPLATES, next_cursor: null, has_more: false });
  if (url.includes('/surveys/campaigns/')) return answer({ items: CAMPAIGNS, next_cursor: null, has_more: false });
  if (url.includes('/surveys/automations/')) return answer({ items: RULES });
  return answer({ items: [], next_cursor: null, has_more: false });
};

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={['/surveys']}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
