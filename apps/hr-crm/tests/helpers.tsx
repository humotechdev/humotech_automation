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

export function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider>
        <App />
      </SessionProvider>
    </MemoryRouter>,
  );
}
