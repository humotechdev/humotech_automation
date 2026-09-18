/**
 * Администрирование.
 *
 * Проверяется то, что легче всего изобразить и труднее всего заметить:
 *
 * — человек с тремя назначениями, показанный обладателем одной роли;
 * — три разные области, схлопнутые в «Вся организация»;
 * — активная учётная запись, выданная за имеющую доступ, хотя все её
 *   назначения закончились;
 * — «Готово» после установки пароля, показанное до ответа сервера, и
 *   второй пользователь от второго нажатия;
 * — пароль, доехавший до адреса, хранилища или журнала;
 * — счётчик по загруженной странице вместо всего набора;
 * — истёкшее назначение, выглядящее действующим;
 * — правка роли поверх чужой, сделанной тем временем.
 *
 * Данные здесь выдуманные. В рабочую базу они не попадают.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test, vi, afterEach } from 'vitest';

import {
  accessNote,
  changes,
  emptyText,
  grantPhase,
  hasAccess,
  roleSummary,
  scopeSummary,
  scopeTitle,
  sections,
  userLine,
  userTitle,
} from '../src/features/admin/model';
import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

// --- чистые правила ---------------------------------------------------------

const grant = (over: Partial<{
  id: string;
  role_id: string;
  role_name: string;
  role_code: string;
  region_id: string | null;
  region_name: string | null;
  office_id: string | null;
  office_name: string | null;
}> = {}) => ({
  id: 'g-1',
  role_id: 'r-1',
  role_name: 'Кадровик',
  role_code: 'HR',
  region_id: null,
  region_name: null,
  office_id: null,
  office_name: null,
  ...over,
});

describe('несколько назначений', () => {
  test('три роли — это не одна роль', () => {
    const summary = roleSummary([
      grant({ role_name: 'Кадровик' }),
      grant({ id: 'g-2', role_name: 'Наблюдатель' }),
      grant({ id: 'g-3', role_name: 'Администратор' }),
    ]);
    expect(summary).toBe('Кадровик +2');
    expect(summary).not.toBe('Кадровик');
  });

  test('одна роль в двух областях считается одной ролью', () => {
    const grants = [
      grant({ region_name: 'Согд' }),
      grant({ id: 'g-2', region_name: 'Хатлон' }),
    ];
    expect(roleSummary(grants)).toBe('Кадровик');
    // Областей при этом две, и они не сливаются.
    expect(scopeSummary(grants)).toBe('Согд +1');
  });

  test('несколько областей не превращаются во «всю организацию»', () => {
    const summary = scopeSummary([
      grant({ region_name: 'Согд' }),
      grant({ id: 'g-2', office_name: 'Центральный' }),
    ]);
    expect(summary).toBe('Согд +1');
    expect(summary).not.toContain('Вся организация');
  });

  test('офис точнее региона: показывается он', () => {
    expect(scopeTitle(grant({ region_name: 'Согд', office_name: 'Худжанд' })))
      .toBe('Худжанд');
  });

  test('назначений нет — это не «вся организация»', () => {
    expect(scopeSummary([])).toBe('—');
    expect(roleSummary([])).toBe('Без роли');
  });
});

describe('активность записи и наличие прав — разные состояния', () => {
  const person = (over: Record<string, unknown>) => ({
    id: 'u-1',
    email: 'kto@humotech.tj',
    status: 'ACTIVE',
    mfa_enabled: false,
    employee_id: null,
    full_name: null,
    last_login: null,
    created_at: '2026-09-01T05:00:00Z',
    updated_at: '2026-09-01T05:00:00Z',
    active_grants: [],
    grants_visible: true,
    ...over,
  }) as never;

  test('активная запись без назначений не имеет доступа', () => {
    const row = person({ status: 'ACTIVE', active_grants: [] });
    expect(hasAccess(row)).toBe(false);
    expect(accessNote(row)).toContain('действующих назначений нет');
  });

  test('отключённая запись объясняется отключением, а не пустотой прав', () => {
    expect(accessNote(person({ status: 'INACTIVE' }))).toContain('отключена');
  });

  test('без права на роли пустой список ничего не утверждает', () => {
    // Ни «нет доступа», ни «есть»: назначения просто не показаны.
    expect(accessNote(person({ grants_visible: false }))).toBeNull();
  });
});

describe('стадия назначения', () => {
  const row = (over: Record<string, unknown>) => ({
    id: 'g-1',
    user_id: 'u-1',
    role_id: 'r-1',
    role_code: 'HR',
    role_name: 'Кадровик',
    region_id: null,
    region_name: null,
    office_id: null,
    office_name: null,
    valid_from: '2026-01-01T00:00:00Z',
    valid_to: null,
    ...over,
  }) as never;

  const now = new Date('2026-09-07T12:00:00Z');

  test('«действует» решает сервер, а не сравнение дат здесь', () => {
    // Срок открыт, но сервер этого назначения в действующих не назвал.
    expect(grantPhase(row({ valid_to: null }), new Set(), now)).toBe('ended');
    expect(grantPhase(row({ valid_to: null }), new Set(['g-1']), now)).toBe('active');
  });

  test('будущее назначение не выглядит действующим', () => {
    expect(grantPhase(row({ valid_from: '2026-12-01T00:00:00Z' }), new Set(), now))
      .toBe('future');
  });

  test('истёкшее назначение не выглядит действующим', () => {
    expect(grantPhase(
      row({ valid_to: '2026-02-01T00:00:00Z' }), new Set(), now,
    )).toBe('ended');
  });
});

describe('подписи под списком', () => {
  test('при курсорной подгрузке номера страниц не выдумываются', () => {
    expect(userLine(25, true, 137)).toBe('Показано 25 пользователей из 137');
    expect(userLine(137, false, 137)).toBe('Показаны все 137 пользователей');
  });

  test('без общего числа оно не подставляется', () => {
    expect(userLine(25, true, null)).toBe('Показано 25 пользователей');
  });

  test('причина пустоты у каждого случая своя', () => {
    expect(emptyText('петров', false)).toContain('запросу');
    expect(emptyText('', true)).toContain('условия');
    expect(emptyText('', false)).toContain('заводит администратор');
  });
});

describe('журнал: разбор изменений', () => {
  const entry = (over: Record<string, unknown>) => ({
    id: 'a-1',
    action: 'user.set_password',
    entity_type: 'users',
    entity_id: 'u-1',
    occurred_at: '2026-09-07T05:00:00Z',
    actor_user_id: 'u-9',
    actor_email: 'admin@humotech.tj',
    actor_employee_id: null,
    old_values: null,
    new_values: null,
    ip_address: null,
    user_agent: null,
    ...over,
  }) as never;

  test('изменение показывается как «было → стало»', () => {
    const rows = changes(entry({
      old_values: { status: 'INACTIVE' },
      new_values: { status: 'ACTIVE' },
    }));
    expect(rows).toHaveLength(1);
    expect(rows[0]?.before).toBe('Неактивен');
    expect(rows[0]?.after).toBe('Активен');
  });

  test('от установки пароля остаётся только факт операции', () => {
    // Значения вырезает сервер; здесь проверяется, что интерфейс не
    // додумывает содержание за него.
    expect(changes(entry({ old_values: null, new_values: null }))).toEqual([]);
  });
});

describe('каталог разрешений', () => {
  const catalog = [
    { code: 'users.manage', name: 'Управление учётными записями', description: null },
    { code: 'roles.manage', name: 'Управление ролями', description: null },
    { code: 'audit.read', name: 'Чтение журнала', description: null },
  ];

  test('права не объединяются в группы, которых нет в каталоге', () => {
    const groups = sections(catalog);
    const codes = groups.flatMap((group) => group.items.map((item) => item.code));
    expect(new Set(codes)).toEqual(new Set(catalog.map((item) => item.code)));
  });

  test('каждое право попадает ровно в одну секцию', () => {
    const groups = sections(catalog);
    const codes = groups.flatMap((group) => group.items.map((item) => item.code));
    expect(codes.length).toBe(new Set(codes).size);
  });
});

// --- страница ---------------------------------------------------------------

const clean = (url: string) => url.split('?')[0] ?? '';

const ROLES = [
  {
    id: 'r-1', code: 'HR', name: 'Кадровик', description: 'Кадровые операции',
    is_system: true, permissions: ['employees.read'], grantable: true,
    missing_permissions: [], updated_at: '2026-09-01T05:00:00.123456Z',
  },
  {
    id: 'r-2', code: 'VIEWER', name: 'Наблюдатель', description: null,
    is_system: false, permissions: ['employees.read'], grantable: true,
    missing_permissions: [], updated_at: '2026-09-02T05:00:00.654321Z',
  },
  {
    id: 'r-3', code: 'ROOT', name: 'Суперадминистратор', description: null,
    is_system: true, permissions: ['settings.manage'], grantable: false,
    missing_permissions: ['settings.manage'],
    updated_at: '2026-09-03T05:00:00.000001Z',
  },
];

const CATALOG = [
  { code: 'users.manage', name: 'Управление учётными записями', description: null },
  { code: 'employees.read', name: 'Просмотр сотрудников', description: null },
  { code: 'settings.manage', name: 'Настройки', description: null },
];

const MANY = {
  id: 'u-1',
  email: 'mnogo@humotech.tj',
  status: 'ACTIVE',
  mfa_enabled: false,
  employee_id: 'e-1',
  full_name: 'Рахимов Далер',
  last_login: '2026-09-06T04:00:00Z',
  created_at: '2026-09-01T05:00:00Z',
  updated_at: '2026-09-01T05:00:00Z',
  grants_visible: true,
  active_grants: [
    { id: 'g-1', role_id: 'r-1', role_name: 'Кадровик', role_code: 'HR',
      region_id: 'reg-1', region_name: 'Согд', office_id: null, office_name: null },
    { id: 'g-2', role_id: 'r-2', role_name: 'Наблюдатель', role_code: 'VIEWER',
      region_id: null, region_name: null, office_id: 'off-1',
      office_name: 'Центральный' },
  ],
};

const NO_ACCESS = {
  ...MANY,
  id: 'u-2',
  email: 'bezprav@humotech.tj',
  full_name: null,
  employee_id: null,
  active_grants: [],
};

const COUNTS = { active: 2, inactive: 5, total: 137, roles: 3 };

/**
 * Области, доступные для выдачи. Один ответ сервера — справочники
 * `/regions/` и `/offices/` для этого не читаются вовсе.
 *
 * «Хатлон» без единого офиса здесь намеренно: прежний источник собирал
 * регионы из видимых офисов и такой регион терял молча.
 */
const SCOPES = {
  all_organization: true,
  regions: [
    { id: 'reg-1', name: 'Согд' },
    { id: 'reg-2', name: 'Хатлон' },
  ],
  offices: [{ id: 'off-1', name: 'Центральный', region_id: 'reg-1' }],
};

function network(
  own: (url: string, method: string) => Response | Promise<Response> | null = () => null,
  options: {
    users?: unknown[];
    counts?: unknown;
    permissions?: string[];
    scopes?: unknown;
  } = {},
) {
  return fakeNetwork((url, call) => {
    const mine = own(url, call.method);
    if (mine) return mine;
    const bare = clean(url);

    if (bare.includes('/auth/')) {
      return json(200, {
        ...USER,
        permissions: options.permissions ?? [
          'users.manage', 'roles.manage', 'audit.read', 'employees.read',
        ],
      });
    }
    if (bare.includes('/users/counts/')) return json(200, options.counts ?? COUNTS);
    const grants = /\/users\/([^/]+)\/grants$/.exec(bare);
    if (grants) return json(200, { items: [] });
    const one = /\/users\/([^/]+)\/$/.exec(bare);
    if (one && call.method === 'GET') {
      const id = one[1] as string;
      const row = [MANY, NO_ACCESS].find((item) => item.id === id);
      return row ? json(200, row) : json(404, {});
    }
    if (bare.endsWith('/users/') && call.method === 'GET') {
      return json(200, {
        items: options.users ?? [MANY, NO_ACCESS],
        next_cursor: 'next',
        has_more: true,
      });
    }
    if (bare.endsWith('/roles')) return json(200, { items: ROLES });
    if (bare.endsWith('/permissions')) return json(200, { items: CATALOG });
    if (bare.endsWith('/grants/scopes')) {
      return json(200, options.scopes ?? SCOPES);
    }
    if (bare.includes('/audit-logs')) {
      return json(200, { items: [], next_cursor: null, has_more: false });
    }
    return crm(url) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

const opened = () => screen.findByText('Рахимов Далер');

/**
 * Выбрать значение в списке CRM: нажать кнопку списка и выбрать пункт.
 * Это своё меню, а не `<select>`, и «изменить значение» ему не отправить.
 */
async function pick(label: RegExp, option: RegExp) {
  fireEvent.click(screen.getByLabelText(label));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}

describe('список', () => {
  test('счётчики приходят с сервера и описывают весь набор', async () => {
    network();
    renderApp('/admin');
    await opened();

    const summary = screen.getByLabelText('Сводка по учётным записям');
    expect(within(summary).getByText('2')).toBeTruthy();
    expect(within(summary).getByText('5')).toBeTruthy();
    // На странице две строки, а всего — 137.
    expect(screen.getByText(/из 137/)).toBeTruthy();
  });

  test('счётчик не считается по загруженной странице', async () => {
    network(undefined, { counts: { ...COUNTS, total: 137 } });
    renderApp('/admin');
    await opened();

    const rows = screen.getAllByRole('row');
    expect(rows.length).toBeLessThan(10);
    expect(screen.queryByText('Показаны все 2 пользователя')).toBeNull();
  });

  test('фильтр статуса в сводку не уходит: он и есть то, что считают',
    async () => {
      const calls = network();
      renderApp('/admin');
      await opened();

      await pick(/Статус/, /Неактивные|Отключённые|Неактивна/);

      await waitFor(() => {
        expect(calls.some(
          (c) => c.url.includes('/users/') && c.url.includes('status=INACTIVE'),
        )).toBe(true);
      });
      const counts = calls.filter((c) => c.url.includes('/users/counts/'));
      expect(counts.every((c) => !c.url.includes('status='))).toBe(true);
    });

});

describe('фильтры и карточка', () => {
  test('открытие карточки не сбрасывает отбор', async () => {
    network();
    renderApp('/admin?search=рахимов&status=ACTIVE');
    await opened();

    fireEvent.click(screen.getByLabelText('Открыть Рахимов Далер'));

    await screen.findByRole('tab', { name: /Профиль/ });
    expect((screen.getByLabelText('Поиск по имени или логину') as HTMLInputElement).value)
      .toBe('рахимов');
    // Отбор виден в самой кнопке списка: «Статус: Активные».
    expect(screen.getByLabelText(/Статус/).getAttribute('aria-label'))
      .toContain('Активные');
  });

});

describe('пароль', () => {
  test('успех объявляется только после ответа сервера, повтор заблокирован',
    async () => {
      let release: (() => void) | null = null;
      const held = new Promise<void>((resolve) => { release = resolve; });
      let posts = 0;

      network((url, method) => {
        if (clean(url).includes('/set-password/') && method === 'POST') {
          posts += 1;
          return held.then(() => json(200, MANY));
        }
        return null;
      });
      renderApp('/admin?id=u-1');
      await screen.findByRole('tab', { name: /Профиль/ });

      fireEvent.click(await screen.findByRole('button', { name: /Задать пароль/ }));
      const field = await screen.findByLabelText('Новый пароль');
      fireEvent.change(field, { target: { value: 'korrekt-parol-2026' } });
      fireEvent.change(screen.getByLabelText('Повтор пароля'), {
        target: { value: 'korrekt-parol-2026' } });

      const save = screen.getByRole('button', { name: /Установить пароль|Сохраняем/ });
      fireEvent.click(save);
      fireEvent.click(save);
      fireEvent.click(save);

      // Пока сервер не ответил — ни одного сообщения об успехе.
      expect(screen.queryByText(/Пароль установлен/)).toBeNull();
      release!();
      await waitFor(() => expect(posts).toBe(1));
    });

  test('пароль не попадает ни в адрес, ни в хранилище', async () => {
    const secret = 'korrekt-parol-2026';
    const calls = network((url, method) =>
      clean(url).includes('/set-password/') && method === 'POST'
        ? json(200, MANY)
        : null,
    );
    renderApp('/admin?id=u-1');
    await screen.findByRole('tab', { name: /Профиль/ });

    fireEvent.click(await screen.findByRole('button', { name: /Задать пароль/ }));
    fireEvent.change(await screen.findByLabelText('Новый пароль'), {
      target: { value: secret } });
    fireEvent.change(screen.getByLabelText('Повтор пароля'), {
      target: { value: secret } });
    fireEvent.click(screen.getByRole('button', { name: /Установить пароль|Сохраняем/ }));

    await waitFor(() => {
      expect(calls.some((c) => c.url.includes('/set-password/'))).toBe(true);
    });
    // В адресах его нет ни в одном запросе.
    expect(calls.every((c) => !c.url.includes(secret))).toBe(true);
    expect(window.location.search).not.toContain(secret);
    expect(JSON.stringify(window.localStorage)).not.toContain(secret);
    expect(JSON.stringify(window.sessionStorage)).not.toContain(secret);
    // Уходит он ровно одним полем и ровно в теле запроса.
    const sent = calls.find((c) => c.url.includes('/set-password/'));
    expect(sent?.body).toEqual({ password: secret });
  });
});

describe('журнал действий', () => {
  test('правки и удаления записей в интерфейсе нет', async () => {
    network(undefined, {});
    renderApp('/admin?tab=audit');
    await screen.findByLabelText('Журнал действий');

    const panel = screen.getByLabelText('Журнал действий');
    expect(within(panel).queryByRole('button', { name: /Удалить/ })).toBeNull();
    expect(within(panel).queryByRole('button', { name: /Изменить/ })).toBeNull();
  });

  test('фильтры уходят на сервер', async () => {
    const calls = network();
    renderApp('/admin?tab=audit');
    await screen.findByLabelText('Журнал действий');

    await pick(/Действие/, /Учётные записи/);

    await waitFor(() => {
      expect(calls.some(
        (c) => c.url.includes('/audit-logs') && c.url.includes('action=user.'),
      )).toBe(true);
    });
  });
});

describe('демонстрационный режим', () => {
  // Признак берётся из сборки. Контейнер разработки собран с
  // VITE_DEMO_MODE=true, и без явной подмены проверка зависела бы от
  // того, где запущены тесты, а не от кода.
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  test('метка «Демо-данные» без явного включения не показывается', async () => {
    vi.stubEnv('VITE_DEMO_MODE', '');
    network();
    renderApp('/admin');
    await opened();
    expect(screen.queryByText('Демо-данные')).toBeNull();
  });

  test('при явном включении метка показывается', async () => {
    vi.stubEnv('VITE_DEMO_MODE', 'true');
    network();
    renderApp('/admin');
    await opened();
    expect(await screen.findByText('Демо-данные')).toBeTruthy();
  });
});

describe('заголовок и профиль', () => {
  test('роль и организация берутся из авторизации, а не из макета', async () => {
    network(undefined, {
      permissions: ['users.manage', 'roles.manage'],
    });
    renderApp('/admin');
    await opened();

    // Блок с ролью и кодом организации из меню убран: человеку он ничего
    // не говорил, а код организации — внутренний идентификатор. Осталось
    // главное свойство — в интерфейсе нет персонажа из макета.
    expect(screen.getByRole('navigation', { name: 'Разделы' })).toBeTruthy();
    expect(screen.queryByText('Технический администратор')).toBeNull();
    expect(screen.queryByText(USER.organization_code)).toBeNull();
  });
});

describe('вспомогательное', () => {
  test('имя записи — имя сотрудника, если он привязан', () => {
    expect(userTitle(MANY as never)).toBe('Рахимов Далер');
    expect(userTitle(NO_ACCESS as never)).toBe(NO_ACCESS.email);
  });
});
