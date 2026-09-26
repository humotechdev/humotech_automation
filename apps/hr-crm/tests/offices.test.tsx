/**
 * Офисы и регионы: карта сети, лента, список, карточка офиса и регионы.
 *
 * Главное, что проверяется: статус офиса и состояние его настройки —
 * разные величины; офис без координат учтён отдельно («Без координат»);
 * офис попадает в область по региону, а не только по точке; отказ
 * сервера не выдаётся за ноль; привязанный экран не называется доступным.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
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

/** Тот же офис, но с настоящими координатами центра Ташкента. */
const PLACED = { ...OFFICE, latitude: '41.2995', longitude: '69.2401', geofence_radius_m: 150 };

const POINT = {
  id: 'q-1', office_id: 'o-1', office_name: 'Главный офис', code: 'D1',
  name: 'Главный вход', direction_mode: 'BOTH', qr_mode: 'ROTATING',
  rotation_seconds: 30, require_geolocation: true, require_office_network: false,
  is_active: true,
};

/**
 * Одна «область» — квадрат вокруг Ташкента. Обход против часовой, как в
 * настоящем GeoJSON: страница обязана развернуть кольцо сама.
 */
const GEO = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { shapeISO: 'UZ-TK', shapeName: 'Tashkent' },
      geometry: {
        type: 'Polygon',
        coordinates: [[[69, 41], [70, 41], [70, 42], [69, 42], [69, 41]]],
      },
    },
  ],
};

function network(
  handler: (path: string, method: string) => Response | null = () => null,
  office: Record<string, unknown> = OFFICE,
) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) {
      return json(200, { ...USER, permissions: ['offices.read', 'offices.manage'] });
    }
    if (path.includes('/geo/')) return json(200, GEO);
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
      return json(200, { items: [office], next_cursor: null, has_more: false });
    }
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

/** Карточка офиса в ленте внизу страницы. */
async function officeCard(id: string): Promise<HTMLElement> {
  await waitFor(() => expect(document.querySelector(`[data-office-id="${id}"]`)).not.toBeNull());
  return document.querySelector(`[data-office-id="${id}"]`) as HTMLElement;
}

/** Число дня в карточке ленты: «В офисе», «Отпуск», «Больничный». */
const dayNumber = (card: HTMLElement, title: string) =>
  within(card).getByText(title).parentElement?.querySelector('b')?.textContent;

describe('карта сети', () => {
  test('лента берёт числа дня с сервера', async () => {
    // Сервер: 32 в офисе, 2 ушли, 2 не пришли, 4 в отпуске, 2 на
    // больничном — за офисом числятся 42.
    network();
    renderApp('/offices');

    const card = await officeCard('o-1');
    await waitFor(() => expect(dayNumber(card, 'В офисе')).toBe('32'));
    expect(dayNumber(card, 'Отпуск')).toBe('4');
    expect(dayNumber(card, 'Больничный')).toBe('2');
    expect(card.textContent).toContain('Сотрудники: 42');
  });

  test('офис без координат на карту не ставится', async () => {
    // Точек офисов на карте нет (решение от 26.09): офис без координат
    // учитывается в легенде отдельно и помечен «Геопозиция: нет».
    network();
    renderApp('/offices');

    await waitFor(() => expect(screen.getByText('Без координат: 1')).toBeTruthy());
    expect((await officeCard('o-1')).textContent).toContain('Геопозиция: нет');
  });

  test('офис с координатами учтён на карте, карточка открывается из ленты', async () => {
    network(() => null, PLACED);
    renderApp('/offices');

    const card = await officeCard('o-1');
    await waitFor(() => expect(card.textContent).toContain('Геопозиция: есть'));
    // Все офисы с координатами — строки «Без координат» в легенде нет.
    expect(screen.queryByText(/Без координат/)).toBeNull();

    fireEvent.click(card);

    expect(await screen.findByRole('complementary', { name: 'Карточка офиса' })).toBeTruthy();
  });

  test('нажатие на область выбирает её, повторное — снимает', async () => {
    network(() => null, PLACED);
    renderApp('/offices');

    // У Ташкента две кнопки с одним названием: сама область и точка
    // города поверх неё (область слишком мала, чтобы в неё попадать).
    // Здесь — область.
    const [area] = await screen.findAllByRole('button', { name: 'город Ташкент' });
    if (!area) throw new Error('нет области «город Ташкент»');
    fireEvent.click(area);

    await waitFor(() => expect(area.getAttribute('aria-pressed')).toBe('true'));
    fireEvent.click(area);
    await waitFor(() => expect(area.getAttribute('aria-pressed')).toBe('false'));
  });

  test('активный офис может требовать настройки', async () => {
    // Статус и настройка — разные величины: точка требует геолокацию,
    // а координат у офиса нет, и проверка молча не работает. Офис стоит
    // в «город Ташкент» — под этой областью он и виден.
    network(() => null, { ...OFFICE, region_name: 'город Ташкент' });
    renderApp('/offices?area=UZ-TK');

    const panel = await screen.findByRole('complementary', { name: 'Выбранный регион' });
    await waitFor(() => expect(within(panel).getByText('1 офис без геозоны')).toBeTruthy());
    expect(within(panel).getByText('Активен')).toBeTruthy();
  });

  test('отказ в QR-точках не выдаётся за ноль', async () => {
    network((path) => (path.includes('/qr-points/') ? json(403, { error: {} }) : null));
    renderApp('/offices?office=o-1');

    // В ленте — прочерк, а не ноль; в карточке офиса — «нет доступа».
    const card = await officeCard('o-1');
    await waitFor(() => expect(card.textContent).toContain('QR: —'));
    expect(card.textContent).not.toContain('QR: 0');
    const aside = await screen.findByRole('complementary', { name: 'Карточка офиса' });
    expect(within(aside).getAllByText('нет доступа').length).toBeGreaterThan(0);
  });

  test('ошибка не превращается в «офисов нет»', async () => {
    network((path) => (path.includes('/offices/') ? json(500, { error: {} }) : null));
    renderApp('/offices');

    expect((await screen.findAllByText(/Не удалось загрузить офисы/)).length).toBeGreaterThan(0);
  });
});

describe('выбор области на карте', () => {
  /** Три офиса Бухарской области: с координатами, без них и чужой. */
  const BUKHARA = {
    ...OFFICE, id: 'o-b1', name: 'Бухара центр',
    region_id: 'r-b', region_name: 'Бухарская область',
    latitude: '39.7614', longitude: '64.4317', geofence_radius_m: 150,
  };
  const BUKHARA_NO_POINT = {
    ...OFFICE, id: 'o-b2', name: 'Бухара склад',
    region_id: 'r-b', region_name: 'Бухарская область',
    latitude: null, longitude: null, geofence_radius_m: null,
  };
  const TASHKENT = {
    ...OFFICE, id: 'o-t1', name: 'Ташкент офис',
    region_id: 'r-t', region_name: 'город Ташкент',
    latitude: '41.3111', longitude: '69.2406', geofence_radius_m: 100,
  };

  function manyOffices(handler: (path: string, method: string) => Response | null = () => null) {
    return fakeNetwork((path, call) => {
      const own = handler(path, call.method);
      if (own) return own;
      if (path.includes('/auth/')) {
        return json(200, { ...USER, permissions: ['offices.read', 'offices.manage'] });
      }
      if (path.includes('/geo/')) return json(200, GEO);
      if (path.includes('/qr-points/')) return json(200, { items: [] });
      if (path.includes('/attendance/presence')) {
        return json(200, {
          date: '2026-09-06', timezone: 'Asia/Tashkent',
          counts: { IN_OFFICE: 1 }, total: 1, truncated: false, items: [],
        });
      }
      if (path.includes('/employees/counts')) return json(200, { total: 3, ACTIVE: 3 });
      if (path.includes('/offices/')) {
        return json(200, {
          items: [BUKHARA, BUKHARA_NO_POINT, TASHKENT],
          next_cursor: null, has_more: false,
        });
      }
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
  }

  test('под областью видны все её офисы, включая те, что без точки на карте', async () => {
    // Офис получает регион при создании, а точку на карте — позже.
    // Считать принадлежность только по координатам значит терять из
    // выборки всё, что ещё не отметили на карте.
    manyOffices();
    renderApp('/offices?area=UZ-BU');

    // И в панели выбранной области, и в ленте — оба бухарских офиса;
    // чужой офис в выборку не попадает ни там, ни там.
    const panel = await screen.findByRole('complementary', { name: 'Выбранный регион' });
    const strip = screen.getByRole('region', { name: 'Офисы' });
    for (const place of [panel, strip]) {
      await waitFor(() => expect(within(place).getByText('Бухара центр')).toBeTruthy());
      expect(within(place).getByText('Бухара склад')).toBeTruthy();
      expect(within(place).queryByText('Ташкент офис')).toBeNull();
    }
  });

  test('без выбранной области видны все офисы', async () => {
    manyOffices();
    renderApp('/offices');

    expect(await screen.findByText('Бухара центр')).toBeTruthy();
    expect(screen.getByText('Ташкент офис')).toBeTruthy();
  });
});

describe('отбор ленты', () => {
  test('отбор не выглядит применённым до нажатия', async () => {
    // Отборы ленты — «Все / Активные / Неактивные» (69a2b0e); прежний
    // отдельный список с «Требует настройки» убран.
    network();
    renderApp('/offices');

    const button = await screen.findByRole('button', { name: 'Неактивные' });
    expect(button.getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('button', { name: 'Все' }).getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(button);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Неактивные' }).getAttribute('aria-pressed')).toBe('true'),
    );
    expect(screen.getByRole('button', { name: 'Все' }).getAttribute('aria-pressed')).toBe('false');
  });
});

describe('карточка офиса', () => {
  test('пустой адрес показывается как «Не указан»', async () => {
    // Адрес показывается в настройке офиса, где его и меняют (в карточке
    // на странице «Офисы» его нет намеренно — см. OfficeCard).
    network((path, method) =>
      method === 'GET' && /\/offices\/o-1\/$/.test(path) ? json(200, OFFICE) : null);
    renderApp('/offices/o-1/setup');

    const summary = await screen.findByRole('complementary', { name: 'Краткая карточка офиса' });
    const address = within(summary).getByText('Адрес', { selector: 'dt' }).parentElement;
    expect(address?.querySelector('dd')?.textContent).toBe('Не указан');
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

  test('кнопки не прячутся по правам: отказ даёт сервер', async () => {
    // Прав в интерфейсе нет — администратор один, и ему открыто всё.
    // Скрытая кнопка защитой никогда и не была: проверку исполняет
    // сервер, и прятать действие значит лишь спрятать причину отказа.
    network((path) =>
      path.includes('/auth/') ? json(200, { ...USER, permissions: ['offices.read'] }) : null,
    );
    renderApp('/offices?office=o-1');

    await screen.findAllByText('Главный офис');
    expect(screen.getByRole('link', { name: /Настроить/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: /Открыть офис/ })).toBeTruthy();
  });

  test('«Настроить» ведёт сразу к карте офиса', async () => {
    network();
    renderApp('/offices?office=o-1');

    const link = await screen.findByRole('link', { name: /Настроить/ });
    expect(link.getAttribute('href')).toBe('/offices/o-1/setup?tab=geo');
  });
});

describe('настройка офиса', () => {
  test('при ошибке сохранения введённое остаётся в форме', async () => {
    network((path, method) => {
      if (method === 'PATCH' && path.includes('/offices/o-1/')) {
        return json(400, { error: { code: 'invalid', message: 'нет',
                                    details: { name: ['Недопустимое значение.'] } } });
      }
      if (method === 'GET' && /\/offices\/o-1\/$/.test(path)) return json(200, OFFICE);
      return null;
    });
    renderApp('/offices/o-1/setup');

    const name = await screen.findByLabelText('Название офиса');
    fireEvent.change(name, { target: { value: 'Главный офис 2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));

    await screen.findByRole('alert');
    expect((name as HTMLInputElement).value).toBe('Главный офис 2');
    expect(screen.getByText('Недопустимое значение.')).toBeTruthy();
  });
});

describe('регионы', () => {
  test('таблица регионов показывает офисы и людей региона', async () => {
    // Прежняя вкладка регионов стала таблицей рядом с картой; старая
    // ссылка `?view=regions` открывает карту. Действия «Отключить» в
    // таблице нет (решение от 26.09).
    network((path) =>
      path.includes('/auth/')
        ? json(200, { ...USER, permissions: ['offices.read', 'regions.manage'] })
        : null,
    );
    renderApp('/offices?view=regions');

    expect(await screen.findByRole('region', { name: 'Карта сети' })).toBeTruthy();
    const table = await screen.findByRole('complementary', { name: 'Регионы сети' });
    await waitFor(() => expect(within(table).getByText('Центральный регион')).toBeTruthy());
    const cells = within(table).getByText('Центральный регион').closest('tr')?.querySelectorAll('td');
    // Офисов: 1; людей: 42 — те же числа, что в ленте.
    await waitFor(() => expect([...(cells ?? [])].map((one) => one.textContent)).toEqual(['1', '42']));
  });
});

describe('правило настройки', () => {
  test('необязательное пустое поле ошибкой настройки не считается', () => {
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

  test('неизвестные точки настройку не обвиняют', () => {
    expect(
      needsSetup({ office: OFFICE, counts: {}, points: [], pointsKnown: false } as never),
    ).toBe(false);
  });
});
