/**
 * Только для разработки: страница входа на подменённой сети.
 *
 * `/auth/me` всегда отвечает «не вошли», а ответ на вход выбирается
 * параметром `?answer=`: credentials, throttled, denied, offline, html,
 * slow (не отвечает вовсе — видно состояние загрузки). Учётных данных
 * и запросов к серверу здесь нет.
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import { App } from '../app/App';
import { SessionProvider } from '../features/auth/session';
import '../styles/app.css';
import '../styles/crm.css';

const answer = new URLSearchParams(window.location.search).get('answer') ?? 'credentials';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const refusal = (status: number, code: string) =>
  json(status, { error: { code, message: '…', details: code === 'too_many_attempts' ? { retry_after: 300 } : null } });

document.cookie = 'csrftoken=preview';

window.fetch = async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.endsWith('/auth/me')) return refusal(403, 'not_authenticated');
  switch (answer) {
    case 'throttled': return refusal(429, 'too_many_attempts');
    case 'denied': return refusal(403, 'permission_denied');
    case 'offline': throw new TypeError('Failed to fetch');
    case 'html': return new Response('<!doctype html><title>502</title>', { status: 502 });
    case 'slow': return new Promise<Response>(() => undefined);
    default: return refusal(401, 'invalid_credentials');
  }
};

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={['/login']}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
