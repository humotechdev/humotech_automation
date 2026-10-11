/**
 * Геозона офиса во вкладке «Геолокация»: круг или контур территории.
 *
 * Проверяется то, что HR делает руками: выбирает форму, обводит участок по
 * углам, отменяет лишний угол и сохраняет, — и что именно уходит на сервер.
 * «Настроен ли офис» решает одно правило на всех экранах: офис с контуром
 * без радиуса — настроен.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { describe, expect, test } from 'vitest';

import { geofenceLabel, geofenceReady } from '../src/api/crm';
import { rejectionTitle } from '../src/pages/AttendancePage';
import { needsSetup } from '../src/pages/OfficesPage';
import { App } from '../src/app/App';
import { SessionProvider } from '../src/features/auth/session';
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

  test('«Очистить» нет: офис без места не остаётся', async () => {
    openGeo(FENCED);

    await screen.findByText(/13 углов/);
    expect(screen.queryByRole('button', { name: 'Очистить' })).toBeNull();
  });

  test('новое место ждёт подтверждения, прежнее названо действующим', async () => {
    const calls = openGeo(OFFICE, (call) => json(200, {
      ...OFFICE,
      pending_location: {
        geofence_kind: 'CIRCLE', latitude: '41.305000', longitude: '69.332414',
        geofence_radius_m: (call.body as { geofence_radius_m: number }).geofence_radius_m,
        geofence_polygon: null,
      },
      pending_location_by_name: 'Мадина',
    }));

    const slider = await screen.findByRole('slider', { name: 'Радиус допуска, метров' });
    fireEvent.change(slider, { target: { value: '120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText(/ждёт подтверждения\. Пока действует прежнее/)).toBeTruthy();
    const region = screen.getByRole('region', { name: 'Новое местоположение ждёт подтверждения' });
    // Действующая зона названа: прежние 70 м.
    expect(within(region).getByText(/70 м/)).toBeTruthy();
    expect(within(region).getByText(/Мадина/)).toBeTruthy();

    fireEvent.click(within(region).getByRole('button', { name: 'Подтвердить' }));
    await waitFor(() => expect(calls.some((call) =>
      call.method === 'POST' && call.url.includes('/offices/o-1/location/confirm/'),
    )).toBe(true));
  });
});

describe('новый офис', () => {
  test('создаётся только вместе с местом, одним запросом', async () => {
    const calls = fakeNetwork((path, call) => {
      if (path.includes('/auth/')) {
        return json(200, { ...USER, permissions: ['offices.read', 'offices.manage'] });
      }
      if (call.method === 'POST' && /\/offices\/?$/.test(new URL(path, 'http://x').pathname)) {
        return json(201, { ...OFFICE, ...(call.body as object), id: 'o-new' });
      }
      if (path.includes('/offices/o-new')) return json(200, OFFICE);
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    renderApp('/offices/new?region=r-1&name=Самарканд');

    const create = await screen.findByRole('button', { name: 'Создать офис' });
    // Без точки создать нельзя.
    expect((create as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByPlaceholderText('41.311081'), { target: { value: '39.6542' } });
    fireEvent.change(screen.getByPlaceholderText('69.240562'), { target: { value: '66.9597' } });
    await waitFor(() => expect((create as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(create);

    await waitFor(() => expect(calls.some((call) => call.method === 'POST')).toBe(true));
    const sent = calls.find((call) => call.method === 'POST')!.body as Record<string, unknown>;
    expect(sent).toMatchObject({
      region_id: 'r-1', name: 'Самарканд', geofence_kind: 'CIRCLE',
      latitude: '39.654200', longitude: '66.959700', geofence_radius_m: 100,
    });
  });
});

describe('старый сервер', () => {
  test('контур, отброшенный сервером, не объявляется сохранённым', async () => {
    // Сервер без поддержки контура отвечает офисом-кругом, как было.
    openGeo(FENCED, () => json(200, OFFICE));

    await screen.findByText(/13 углов/);
    fireEvent.click(screen.getByRole('button', { name: 'Отменить точку' }));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText(/Сервер не сохранил контур/)).toBeTruthy();
    expect(screen.queryByText('Контур территории сохранён')).toBeNull();
    // Нарисованное не пропало.
    expect(screen.getByText(/12 углов/)).toBeTruthy();
  });
});

// --- журнал --------------------------------------------------------------------

describe('отказ по месту в журнале', () => {
  test('HR видит, что место не установлено, а не одно «Отклонено»', () => {
    // Сотруднику сказали: «сообщите в отдел кадров, попытка там видна».
    expect(rejectionTitle('LOCATION_AMBIGUOUS')).toBe('Отклонено · у границы, место не установлено');
    expect(rejectionTitle('LOCATION_STALE')).toBe('Отклонено · устаревшая геопозиция');
    expect(rejectionTitle('OUTSIDE_GEOFENCE')).toBe('Отклонено · вне геозоны');
    expect(rejectionTitle('ЧТО-ТО_НОВОЕ')).toBe('Отклонено');
    expect(rejectionTitle(null)).toBe('Отклонено');
  });
});

// --- надёжность редактора места ---------------------------------------------------

describe('редактор места: отправка и состояния', () => {
  test('двойное нажатие «Сохранить» — одно сохранение', async () => {
    let release: (value: Response) => void = () => undefined;
    const calls = openGeo(FENCED, () => new Promise<Response>((resolve) => { release = resolve; }) as unknown as Response);
    await screen.findByText(/13 углов/);
    fireEvent.click(screen.getByRole('button', { name: 'Отменить точку' }));
    const save = screen.getByRole('button', { name: 'Сохранить' });
    fireEvent.click(save);
    fireEvent.click(save);
    release(json(200, FENCED));
    await waitFor(() => expect(patches(calls)).toHaveLength(1));
  });

  test('обрыв при сохранении: «неизвестно», офис перечитан, повтора нет', async () => {
    const calls = openGeo(FENCED, () => { throw new TypeError('Failed to fetch'); });
    await screen.findByText(/13 углов/);
    fireEvent.click(screen.getByRole('button', { name: 'Отменить точку' }));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText(/неизвестно, сохранилось ли место/)).toBeTruthy();
    expect(patches(calls)).toHaveLength(1);
    await waitFor(() => expect(calls.filter((call) => call.method === 'GET' && /\/offices\/o-1\/?$/.test(call.url)).length)
      .toBeGreaterThanOrEqual(2));
    // Черновик на месте: двенадцать углов, а не снова тринадцать.
    expect(screen.getByText(/12 углов/)).toBeTruthy();
  });

  test('пустые координаты — точки нет, а не 0,0', async () => {
    openGeo({ ...OFFICE, latitude: '', longitude: '' });
    const lat = await screen.findByLabelText('Широта');
    expect((lat as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText('Долгота') as HTMLInputElement).value).toBe('');
  });

  test('сбой поиска адреса — «недоступен», а не «ничего не нашлось»', async () => {
    fakeNetwork((path) => {
      if (path.includes('/auth/')) return json(200, { ...USER, permissions: ['offices.read', 'offices.manage'] });
      if (path.includes('photon')) return json(503, {});
      if (path.includes('/offices/o-1')) return json(200, OFFICE);
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    renderApp('/offices/o-1/setup?tab=geo');
    fireEvent.change(await screen.findByLabelText('Поиск адреса'), { target: { value: 'Махтумкули' } });
    expect(await screen.findByText(/Поиск адреса сейчас недоступен/, undefined, { timeout: 3000 })).toBeTruthy();
  });
});

describe('переход к другому офису', () => {
  test('пока грузится новый, название и место прежнего не показываются', async () => {
    fakeNetwork((path) => {
      if (path.includes('/auth/')) return json(200, { ...USER, permissions: ['offices.read', 'offices.manage'] });
      if (path.includes('/offices/o-2')) return new Promise<Response>(() => undefined);
      if (path.includes('/offices/o-1')) return json(200, OFFICE);
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    let go: (to: string) => void = () => undefined;
    function Steer() {
      go = useNavigate();
      return null;
    }
    render(
      <MemoryRouter initialEntries={['/offices/o-1/setup']}>
        <SessionProvider>
          <Steer />
          <App />
        </SessionProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { name: 'город Ташкент' })).toBeTruthy();

    act(() => go('/offices/o-2/setup'));

    expect(await screen.findByText('Загружаем офис…')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'город Ташкент' })).toBeNull();
    expect(screen.queryByText('Tashkent, Mahtumquli Street')).toBeNull();
  });
});
