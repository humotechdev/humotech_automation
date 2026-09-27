/**
 * Только для разработки: вкладка «Геолокация» офиса на подставных данных.
 *
 * Сеть подменяется целиком — ни одного запроса к серверу, ни входа, ни
 * учётных данных. `?shape=circle` — офис кругом 70 м, как до контура;
 * без параметра — сохранённый контур территории. Правка через «Сохранить»
 * отвечает тем, что прислали, как сервер после успешной записи.
 *
 * Контур — открытые данные OpenStreetMap (way 406552826): огороженная
 * территория южнее ул. Махтумкули в Ташкенте.
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
  permissions: ['offices.read', 'offices.manage', 'qr_points.read', 'qr_points.manage'],
};

const COMPOUND: [number, number][] = [
  [41.30495, 69.331616], [41.304857, 69.331617], [41.304812, 69.331057],
  [41.304803, 69.330988], [41.303657, 69.331005], [41.303354, 69.331019],
  [41.303334, 69.331781], [41.303395, 69.331784], [41.303364, 69.332934],
  [41.303436, 69.333213], [41.305025, 69.333295], [41.305025, 69.333208],
  [41.305036, 69.332542],
];

const CIRCLE = {
  id: 'o-1', organization_id: 'org-1', region_id: 'r-1', region_code: 'TK',
  region_name: 'город Ташкент', code: 'OFF-3', name: 'город Ташкент',
  address: 'Tashkent, Mahtumquli Street', timezone: 'Asia/Tashkent',
  latitude: '41.304290', longitude: '69.332414', geofence_radius_m: 70,
  geofence_kind: 'CIRCLE', geofence_polygon: null,
  status: 'ACTIVE', opened_at: null, closed_at: null,
};

const FENCED = {
  ...CIRCLE, geofence_kind: 'POLYGON', geofence_polygon: COMPOUND,
  latitude: '41.304174', longitude: '69.332151',
};

let office: Record<string, unknown> =
  new URLSearchParams(window.location.search).get('shape') === 'circle' ? CIRCLE : FENCED;

const answer = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const path = new URL(String(input), 'http://x').pathname;
  if (path.includes('/auth/')) return answer(HR);
  if (path.includes('/offices/o-1')) {
    if (init?.method === 'PATCH') office = { ...office, ...JSON.parse(String(init.body)) };
    return answer(office);
  }
  if (path.includes('/qr-points')) {
    return answer({
      items: [{
        id: 'q-1', office_id: 'o-1', office_name: 'город Ташкент', code: 'D1',
        name: 'Главный вход', direction_mode: 'BOTH', qr_mode: 'STATIC',
        rotation_seconds: 30, require_geolocation: true, require_office_network: false,
        is_active: true,
      }],
      next_cursor: null, has_more: false,
    });
  }
  return answer({ items: [], next_cursor: null, has_more: false });
};

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <MemoryRouter initialEntries={['/offices/o-1/setup?tab=geo']}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}
