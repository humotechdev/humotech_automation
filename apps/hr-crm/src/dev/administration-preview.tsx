/**
 * Только для разработки: «Администрирование» на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. Нужен для снимка вёрстки и проверки движения
 * headless-браузером. Вкладка выбирается адресом: `?open=positions`.
 * Сохранение отвечает с задержкой, чтобы было видно состояние
 * «Сохраняем…»; `?fail=1` — сервер отказывает.
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import { App } from '../app/App';
import { SessionProvider } from '../features/auth/session';
import '../styles/app.css';
import '../styles/crm.css';

const HR = {
  id: 'u-1', email: 'hr@example.test', organization_id: 'org-1', organization_code: 'DEMO',
  employee_id: null, status: 'ACTIVE', timezone: 'Asia/Tashkent', roles: ['SUPER_ADMIN'],
  permissions: ['users.manage', 'roles.manage', 'audit.read', 'employees.read'],
};

const ADMINS = [
  {
    id: 'u-1', email: 'hr@example.test', status: 'ACTIVE', mfa_enabled: false, employee_id: 'e-1',
    full_name: 'Марина Петрова', last_login: null, created_at: '2026-09-01T05:00:00Z',
    updated_at: '2026-09-01T05:00:00Z', grants_visible: true,
    active_grants: [{ id: 'g-1', role_id: 'r-1', role_name: 'HR-администратор', role_code: 'HR_ADMIN',
                      region_id: null, region_name: null, office_id: null, office_name: null }],
  },
  {
    id: 'u-2', email: 'office.admin', status: 'INACTIVE', mfa_enabled: false, employee_id: null,
    full_name: null, last_login: null, created_at: '2026-09-01T05:00:00Z',
    updated_at: '2026-09-01T05:00:00Z', grants_visible: true,
    active_grants: [{ id: 'g-2', role_id: 'r-2', role_name: 'Администратор', role_code: 'OFFICE_ADMIN',
                      region_id: 'reg-1', region_name: 'Ташкент', office_id: 'o-1', office_name: 'Главный офис' }],
  },
];

const DEPARTMENTS = ['Отдел продаж', 'IT', 'HR', 'Бухгалтерия', 'Поддержка', 'Маркетинг',
  'Операционный отдел', 'Логистика', 'Юридический отдел', 'Административный отдел']
  .map((name, index) => ({
    id: `d-${index}`, office_id: null, office_name: index === 0 ? 'Главный офис' : null,
    parent_department_id: null, name, description: null, head_employee_id: null,
    head_employee_name: null, staff: index % 3, status: 'ACTIVE',
  }));

const POSITION_NAMES = ['Менеджер по продажам', 'Специалист поддержки', 'Бухгалтер', 'HR-специалист',
  'Разработчик', 'Системный администратор', 'Руководитель отдела', 'Офис-менеджер'];
const POSITIONS = Array.from({ length: 46 }, (_, index) => ({
  id: `p-${index}`,
  name: index < POSITION_NAMES.length ? POSITION_NAMES[index]! : `Должность ${index + 1}`,
  description: null, staff: index % 4, status: 'ACTIVE',
}));

const SCHEDULES = [
  { id: 's-1', name: 'Основной график', days: [1, 2, 3, 4, 5], from: '09:00', to: '18:00', rest: ['13:00', '14:00'] },
  { id: 's-2', name: 'Ранний график', days: [1, 2, 3, 4, 5], from: '08:00', to: '17:00', rest: ['12:00', '13:00'] },
  { id: 's-3', name: 'Поздний график', days: [1, 2, 3, 4, 5], from: '10:00', to: '19:00', rest: ['14:00', '15:00'] },
  { id: 's-4', name: 'Субботний график', days: [6], from: '09:00', to: '13:00', rest: null },
  { id: 's-5', name: 'Ночная смена', days: [1, 2, 3, 4, 5], from: '18:00', to: '03:00', rest: ['23:00', '00:00'] },
];

const card = (one: (typeof SCHEDULES)[number]) => ({
  id: one.id, name: one.name, timezone: 'Asia/Tashkent', weekly_minutes: 2400,
  late_grace_minutes: null, early_leave_grace_minutes: null, is_flexible: false, status: 'ACTIVE',
  days: [1, 2, 3, 4, 5, 6, 7].map((weekday) => {
    const on = one.days.includes(weekday);
    return {
      weekday, is_working_day: on,
      start_time: on ? `${one.from}:00` : null,
      end_time: on ? `${one.to}:00` : null,
      breaks: on && one.rest
        ? [{ id: `b-${weekday}`, name: 'Перерыв', start_time: `${one.rest[0]}:00`,
             end_time: `${one.rest[1]}:00`, is_paid: false }]
        : [],
    };
  }),
});

const answer = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const page = (items: unknown[]) => answer({ items, next_cursor: null, has_more: false });
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const search = new URLSearchParams(window.location.search);

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input).split('?')[0] ?? '';
  const method = init?.method ?? 'GET';
  if (url.includes('/auth/')) return answer(HR);
  if (method !== 'GET') {
    await wait(900);
    if (search.get('fail')) {
      return answer({ error: { code: 'conflict', message: 'Такое название уже есть' } }, 409);
    }
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    return answer({ id: 'x', status: 'ACTIVE', ...body });
  }
  if (url.endsWith('/users/')) return answer({ items: ADMINS, next_cursor: null, has_more: false });
  if (url.endsWith('/departments/')) return page(DEPARTMENTS);
  if (url.endsWith('/positions/')) return page(POSITIONS);
  if (url.endsWith('/work-schedules/')) {
    return page(SCHEDULES.map((one) => ({ id: one.id, name: one.name, timezone: 'Asia/Tashkent',
                                         status: 'ACTIVE', weekly_minutes: 2400 })));
  }
  const one = /\/work-schedules\/([^/]+)\/$/.exec(url);
  if (one) {
    const found = SCHEDULES.find((item) => item.id === one[1]);
    if (found) return answer(card(found));
  }
  const user = /\/users\/([^/]+)\/$/.exec(url);
  if (user) return answer(ADMINS.find((item) => item.id === user[1]) ?? ADMINS[0]);
  if (url.endsWith('/roles')) {
    return answer({ items: [{ id: 'r-1', code: 'HR_ADMIN', name: 'HR-администратор' },
                            { id: 'r-0', code: 'SUPER_ADMIN', name: 'Главный администратор' }] });
  }
  return page([]);
};

const open = search.get('open') ?? 'admins';
const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={[`/administration?open=${open}`]}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
