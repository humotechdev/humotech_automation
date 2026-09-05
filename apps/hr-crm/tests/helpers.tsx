/** Общий каркас тестов: приложение целиком поверх поддельного `fetch`. */

import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';

import { App } from '../src/app/App';
import { SessionProvider } from '../src/features/auth/session';

export type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

export const json = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

export const empty = (status: number): Response => new Response(null, { status });

export const html = (status: number): Response =>
  new Response('<!doctype html><h1>Server Error (500)</h1>', {
    status,
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });

export const USER = {
  id: '8f14e45f-ceea-467a-9e2f-000000000001',
  email: 'hr@humotech.local',
  organization_id: '8f14e45f-ceea-467a-9e2f-000000000002',
  organization_code: 'DEMO',
  employee_id: null,
  status: 'ACTIVE',
  roles: ['HR'],
  permissions: ['employees.view'],
};

/**
 * Поддельная сеть.
 *
 * `handler` получает путь и метод и решает, чем ответить. Все вызовы
 * записываются: половина проверок — про то, что именно ушло на сервер.
 */
export function fakeNetwork(handler: (path: string, call: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const call: Call = {
      url,
      method: init?.method ?? 'GET',
      headers: (init?.headers as Record<string, string>) ?? {},
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    return handler(url, call);
  });
  vi.stubGlobal('fetch', fetchMock);
  return calls;
}

/**
 * Пустые, но правильные по форме ответы главной страницы.
 *
 * Возвращает `null` для чужого адреса — тест сам решает, чем ответить.
 * Пустой дашборд здесь намеренно: проверяется оболочка и маршрут, а не
 * числа, и выдуманные значения только маскировали бы разбор ответа.
 */
export function crm(path: string): Response | null {
  if (path.includes('/dashboard')) {
    return json(200, {
      date: '2026-09-05',
      timezone: 'Asia/Dushanbe',
      cards: [
        { key: 'active_employees', title: 'Активные сотрудники', value: 0,
          endpoint: null, params: {}, attention: false },
        { key: 'should_work_today', title: 'Должны работать сегодня', value: 0,
          endpoint: null, params: {}, attention: false },
        { key: 'in_office', title: 'Сейчас в офисе', value: 0,
          endpoint: null, params: {}, attention: false },
        { key: 'not_come', title: 'Не пришли', value: 0,
          endpoint: null, params: {}, attention: false },
        { key: 'vacation', title: 'В отпуске', value: 0,
          endpoint: null, params: {}, attention: false },
        { key: 'sick_leave', title: 'На больничном', value: 0,
          endpoint: null, params: {}, attention: false },
      ],
      warnings: [],
    });
  }
  if (path.includes('/analytics')) {
    return json(200, {
      period: { first: '2026-08-23', last: '2026-09-05', timezone: 'Asia/Dushanbe' },
      headcount: 0,
      series: [],
    });
  }
  if (path.includes('/absence-requests/pending')) return json(200, { requests: [] });
  // Вход и «кто я» сюда не относятся: ими распоряжается сам тест.
  if (path.includes('/auth/')) return null;
  if (path.includes('/api/v1/')) return json(200, { items: [] });
  return null;
}

export function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider>
        <App />
      </SessionProvider>
    </MemoryRouter>,
  );
}
