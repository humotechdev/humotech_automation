/**
 * Геозона офиса во вкладке «Геолокация»: круг или контур территории.
 *
 * Проверяется то, что HR делает руками: выбирает форму, обводит участок по
 * углам, отменяет лишний угол и сохраняет, — и что именно уходит на сервер.
 * «Настроен ли офис» решает одно правило на всех экранах: офис с контуром
 * без радиуса — настроен.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { geofenceLabel, geofenceReady } from '../src/api/crm';
import { needsSetup } from '../src/pages/OfficesPage';
import { USER, type Call, crm, fakeNetwork, json, renderApp } from './helpers';

/** Огороженная территория южнее ул. Махтумкули (OSM, way 406552826). */
const COMPOUND: [number, number][] = [
  [41.30495, 69.331616], [41.304857, 69.331617], [41.304812, 69.331057],
  [41.304803, 69.330988], [41.303657, 69.331005], [41.303354, 69.331019],
  [41.303334, 69.331781], [41.303395, 69.331784], [41.303364, 69.332934],
  [41.303436, 69.333213], [41.305025, 69.333295], [41.305025, 69.333208],
  [41.305036, 69.332542],
];

const OFFICE = {
  id: 'o-1', organization_id: 'org', region_id: 'r-1', region_code: 'C',
  region_name: 'Ташкент', code: 'HT-TAS-01', name: 'город Ташкент',
  address: 'Tashkent, Mahtumquli Street', timezone: 'Asia/Tashkent',
  latitude: '41.304290', longitude: '69.332414', geofence_radius_m: 70,
  geofence_kind: 'CIRCLE', geofence_polygon: null,
  status: 'ACTIVE', opened_at: null, closed_at: null,
};

const FENCED = {
  ...OFFICE, geofence_kind: 'POLYGON', geofence_polygon: COMPOUND,
  latitude: '41.304174', longitude: '69.332151', geofence_radius_m: null,
};

const POINT = {
  id: 'q-1', office_id: 'o-1', office_name: 'город Ташкент', code: 'D1',
  name: 'Главный вход', direction_mode: 'BOTH', qr_mode: 'STATIC',
  rotation_seconds: 30, require_geolocation: true, require_office_network: false,
  is_active: true,
};

function openGeo(office: Record<string, unknown>, answer?: (call: Call) => Response | null) {
  const calls = fakeNetwork((path, call) => {
    if (path.includes('/auth/')) {
      return json(200, { ...USER, permissions: ['offices.read', 'offices.manage'] });
    }
    if (call.method === 'PATCH' && path.includes('/offices/o-1')) {
      const own = answer?.(call);
      if (own) return own;
      const body = call.body as Record<string, unknown>;
      return json(200, { ...office, ...body });
    }
    if (path.includes('/offices/o-1')) return json(200, office);
    if (path.includes('/qr-points')) {
      return json(200, { items: [POINT], next_cursor: null, has_more: false });
    }
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
  renderApp('/offices/o-1/setup?tab=geo');
  return calls;
}

function patches(calls: Call[]): Record<string, unknown>[] {
  return calls
    .filter((call) => call.method === 'PATCH')
    .map((call) => call.body as Record<string, unknown>);
}

// --- одно правило «настроен» -----------------------------------------------

describe('настроен ли офис', () => {
  test('контур без радиуса — настроен, круг без радиуса — нет', () => {
    expect(geofenceReady({ ...FENCED, geofence_radius_m: null } as never)).toBe(true);
    expect(geofenceReady({ ...OFFICE, geofence_radius_m: null } as never)).toBe(false);
    expect(geofenceReady(OFFICE as never)).toBe(true);
  });

  test('выбран контур, но не нарисован — не настроен, даже при радиусе', () => {
    expect(geofenceReady({ ...OFFICE, geofence_kind: 'POLYGON' } as never)).toBe(false);
  });

  test('старый ответ без поля формы — это круг', () => {
    const legacy = { ...OFFICE } as Record<string, unknown>;
    delete legacy['geofence_kind'];
    delete legacy['geofence_polygon'];
    expect(geofenceReady(legacy as never)).toBe(true);
    expect(geofenceLabel(legacy as never)).toBe('70 м');
  });

  test('подпись называет форму', () => {
    expect(geofenceLabel(FENCED as never)).toBe('контур, 13 углов');
    expect(geofenceLabel({ ...OFFICE, latitude: null } as never)).toBe('не задана');
  });

  test('офис с контуром не требует настройки на странице офисов', () => {
    const row = { office: FENCED, points: [POINT], pointsKnown: true } as never;
    expect(needsSetup(row)).toBe(false);
    const empty = { office: { ...FENCED, geofence_polygon: [] }, points: [POINT], pointsKnown: true };
    expect(needsSetup(empty as never)).toBe(true);
  });
});

// --- редактор -----------------------------------------------------------------

describe('вкладка «Геолокация»', () => {
  test('сохранённый контур открывается контуром, с углами на карте', async () => {
    openGeo(FENCED);

    const shape = await screen.findByRole('radiogroup', { name: 'Форма зоны' });
    expect(within(shape).getByRole('radio', { name: 'Контур территории' })
      .getAttribute('aria-checked')).toBe('true');
    expect(await screen.findByText(/13 углов · около/)).toBeTruthy();
    // Радиуса у контура нет — ползунок не показывается.
    expect(screen.queryByLabelText('Радиус допуска, метров')).toBeNull();
    await waitFor(() => expect(document.querySelectorAll('.ofs-vertex')).toHaveLength(13));
  });

  test('«Отменить точку» убирает последний угол, сохраняется контур без него', async () => {
    const calls = openGeo(FENCED);

    await screen.findByText(/13 углов/);
    fireEvent.click(screen.getByRole('button', { name: 'Отменить точку' }));
    expect(await screen.findByText(/12 углов/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await screen.findByText('Контур территории сохранён');

    const [body] = patches(calls);
    expect(body!['geofence_kind']).toBe('POLYGON');
    expect(body!['geofence_polygon']).toEqual(COMPOUND.slice(0, -1));
    // Точку офиса для контура ставит сервер, клиент её не присылает.
    expect(body).not.toHaveProperty('latitude');
    expect(body).not.toHaveProperty('geofence_radius_m');
  });

  test('контур рисуется нажатиями по карте и не сохраняется, пока углов меньше трёх', async () => {
    const calls = openGeo(OFFICE);

    const shape = await screen.findByRole('radiogroup', { name: 'Форма зоны' });
    fireEvent.click(within(shape).getByRole('radio', { name: 'Контур территории' }));
    expect(await screen.findByText(/Нажмите на карту в углу участка/)).toBeTruthy();

    const canvas = document.querySelector('.ofs-map__canvas') as HTMLElement;
    fireEvent.click(canvas, { clientX: 10, clientY: 10 });
    fireEvent.click(canvas, { clientX: 60, clientY: 10 });
    expect(await screen.findByText(/Отмечено углов: 2/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Сохранить' }).hasAttribute('disabled')).toBe(true);

    fireEvent.click(canvas, { clientX: 60, clientY: 60 });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Сохранить' }).hasAttribute('disabled')).toBe(false));
    expect(document.querySelectorAll('.ofs-vertex')).toHaveLength(3);
    // Синяя полупрозрачная заливка появилась вместе с третьим углом.
    expect(document.querySelector('path.leaflet-interactive[fill="#1f6fe0"]')).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await screen.findByText('Контур территории сохранён');
    const [body] = patches(calls);
    expect(body!['geofence_kind']).toBe('POLYGON');
    expect(body!['geofence_polygon']).toHaveLength(3);
  });

  test('отказ сервера показывается под контуром, а не пропадает', async () => {
    openGeo(FENCED, () => json(400, {
      error: {
        code: 'validation_failed', message: 'Стороны контура пересекаются',
        details: { geofence_polygon: ['Стороны контура пересекаются. Обведите участок по порядку.'] },
      },
    }));

    await screen.findByText(/13 углов/);
    fireEvent.click(screen.getByRole('button', { name: 'Отменить точку' }));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText(/Обведите участок по порядку/)).toBeTruthy();
  });

  test('возврат к кругу сохраняет круг прежним видом', async () => {
    const calls = openGeo(FENCED);

    const shape = await screen.findByRole('radiogroup', { name: 'Форма зоны' });
    fireEvent.click(within(shape).getByRole('radio', { name: 'Круг' }));
    expect(await screen.findByLabelText('Радиус допуска, метров')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await screen.findByText('Расположение сохранено');
    const [body] = patches(calls);
    expect(body!['geofence_kind']).toBe('CIRCLE');
    expect(body!['latitude']).toBe('41.304174');
    expect(body!['geofence_radius_m']).toBe(100);
  });

  test('«Очистить» снимает и круг, и контур', async () => {
    const calls = openGeo(FENCED);

    await screen.findByText(/13 углов/);
    fireEvent.click(screen.getByRole('button', { name: 'Очистить' }));
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Очистить' }));
    await screen.findByText('Геолокация очищена');

    const [body] = patches(calls);
    expect(body).toEqual({
      geofence_kind: 'CIRCLE', geofence_polygon: null,
      latitude: null, longitude: null, geofence_radius_m: null,
    });
  });
});
