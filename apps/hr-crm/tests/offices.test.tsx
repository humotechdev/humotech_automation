/**
 * Офисы и регионы.
 *
 * Главное, что проверяется: статус офиса и состояние его настройки —
 * разные величины, привязанный экран не называется доступным, а
 * заполнение координат не включает обязательность геолокации у точки.
 */

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { needsSetup } from '../src/pages/OfficesPage';
import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const OFFICE = {
  id: 'o-1', organization_id: 'org', region_id: 'r-1', region_code: 'C',
  region_name: 'Центральный регион', code: 'HT-TAS-01', name: 'Главный офис',
  address: null, timezone: 'Asia/Tashkent',
  latitude: null, longitude: null, geofence_radius_m: null,
  status: 'ACTIVE', opened_at: null, closed_at: null,
};

const POINT = {
  id: 'q-1', office_id: 'o-1', office_name: 'Главный офис', code: 'D1',
  name: 'Главный вход', direction_mode: 'BOTH', qr_mode: 'ROTATING',
  rotation_seconds: 30, require_geolocation: true, require_office_network: false,
  is_active: true,
};

function network(handler: (path: string, method: string) => Response | null = () => null) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) {
      return json(200, { ...USER, permissions: ['offices.read', 'offices.manage'] });
    }
    if (path.includes('/qr-points/')) return json(200, { items: [POINT] });
    if (path.includes('/qr/devices')) {
      return json(200, [
        { id: 'd-1', qr_point_id: 'q-1', name: 'Планшет', status: 'ACTIVE',
          paired_at: '2026-09-04T09:31:00Z', last_seen_at: null },
      ]);
    }
    if (path.includes('/attendance/presence')) {
      return json(200, {
        date: '2026-09-06', timezone: 'Asia/Tashkent',
        counts: { IN_OFFICE: 32, LEFT: 2, NOT_COME: 2, VACATION: 4, SICK_LEAVE: 2 },
        total: 42, truncated: false, items: [],
      });
    }
    if (path.includes('/employees/counts')) return json(200, { total: 42, ACTIVE: 42 });
    if (path.includes('/dashboard')) {
      return json(200, {
        date: '2026-09-06', timezone: 'Asia/Tashkent',
        cards: [{ key: 'in_office', title: 'Сейчас в офисе', value: 32,
                  endpoint: null, params: {}, attention: false }],
        warnings: [],
      });
    }
    if (path.includes('/regions/')) {
      return json(200, {
        items: [{ id: 'r-1', code: 'C', name: 'Центральный регион', status: 'ACTIVE',
                  timezone: null }],
        next_cursor: null, has_more: false,
      });
    }
    if (path.includes('/offices/')) {
      return json(200, { items: [OFFICE], next_cursor: null, has_more: false });
    }
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

describe('список офисов', () => {
  test('сводка и строка берут числа с сервера', async () => {
    network();
    renderApp('/offices');

    expect(await screen.findByText('Главный офис')).toBeTruthy();
    expect(screen.getAllByText('42').length).toBeGreaterThan(0);
    expect(screen.getAllByText('32').length).toBeGreaterThan(0);
  });

  test('активный офис может требовать настройки', async () => {
    // Статус и состояние настройки — разные величины: точка требует
    // геолокацию, а координат у офиса нет, и проверка молча не работает.
    network();
    renderApp('/offices');

    expect(await screen.findByText(/Требует настройки · 1/)).toBeTruthy();
    expect(screen.getAllByText('Активен').length).toBeGreaterThan(0);
    expect(screen.getByText('Нет геозоны')).toBeTruthy();
  });

  test('кнопка «Требует настройки» не выглядит применённой до нажатия', async () => {
    network();
    renderApp('/offices');

    const button = await screen.findByRole('button', { name: /Требует настройки/ });
    expect(button.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(button);
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /Требует настройки/ }).getAttribute('aria-pressed'),
      ).toBe('true'),
    );
  });

  test('ошибка не превращается в «офисов нет»', async () => {
    network((path) =>
      path.includes('/offices/') ? json(500, { error: {} }) : null,
    );
    renderApp('/offices');

    expect(await screen.findByText(/Не удалось загрузить офисы/)).toBeTruthy();
  });
});

describe('карточка офиса', () => {
  test('пустой адрес показывается как «Не указан»', async () => {
    network();
    renderApp('/offices?office=o-1');

    expect(await screen.findByText('Не указан')).toBeTruthy();
  });

  test('состояние геолокации описывает фактические точки', async () => {
    network();
    renderApp('/offices?office=o-1');

    expect(await screen.findByText(/Обязательна для всех 1 точек/)).toBeTruthy();
  });

  test('привязанный экран не называется доступным', async () => {
    // Привязка — запись в базе, доступность — свежий сигнал от экрана.
    network();
    renderApp('/offices?office=o-1');
    fireEvent.click(await screen.findByRole('tab', { name: 'QR и геозона' }));

    expect(await screen.findByText(/Экран привязан, на связи не был/)).toBeTruthy();
    expect(screen.queryByText(/Онлайн/)).toBeNull();
  });

  test('без права управления кнопки правки нет', async () => {
    network((path) =>
      path.includes('/auth/')
        ? json(200, { ...USER, permissions: ['offices.read'] })
        : null,
    );
    renderApp('/offices?office=o-1');

    // Название есть и в строке списка, и в шапке карточки.
    await screen.findAllByText('Главный офис');
    expect(screen.queryByRole('button', { name: /Редактировать офис/ })).toBeNull();
  });

  test('при ошибке сохранения введённое остаётся в форме', async () => {
    network((path, method) =>
      method === 'PATCH' && path.includes('/offices/o-1/')
        ? json(400, { error: { code: 'invalid', message: 'нет',
                               details: { latitude: ['Недопустимое значение.'] } } })
        : null,
    );
    renderApp('/offices?office=o-1');

    fireEvent.click(await screen.findByRole('button', { name: /Редактировать офис/ }));
    const latitude = screen.getByLabelText('Широта');
    fireEvent.change(latitude, { target: { value: '999' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await screen.findByRole('alert');
    expect((latitude as HTMLInputElement).value).toBe('999');
    expect(screen.getByText('Недопустимое значение.')).toBeTruthy();
  });
});

describe('регионы', () => {
  test('вкладка показывает регионы и действие по правам', async () => {
    network((path) =>
      path.includes('/auth/')
        ? json(200, { ...USER, permissions: ['offices.read', 'regions.manage'] })
        : null,
    );
    renderApp('/offices?tab=regions');

    expect(await screen.findByText('Центральный регион')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Отключить' })).toBeTruthy();
  });
});

describe('правило настройки', () => {
  test('необязательное пустое поле ошибкой настройки не считается', () => {
    // Пустой адрес ничему не мешает: в «требует настройки» он не входит.
    const ready = {
      office: { ...OFFICE, latitude: '41.3', longitude: '69.2', geofence_radius_m: 100 },
      counts: {},
      points: [POINT],
    };
    expect(needsSetup(ready as never)).toBe(false);
    expect(needsSetup({ office: OFFICE, counts: {}, points: [POINT] } as never)).toBe(true);
    expect(
      needsSetup({ office: OFFICE, counts: {},
                   points: [{ ...POINT, require_geolocation: false }] } as never),
    ).toBe(false);
  });
});
