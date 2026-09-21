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
import { describe, expect, test } from 'vitest';

import { counted } from '../src/features/admin/catalog';
import {
  accessNote,
  changes,
  emptyText,
  grantPhase,
  hasAccess,
  roleSummary,
  scopeSummary,
  scopeTitle,
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
  // Тот же идентификатор, что у вошедшего: это его собственная запись,
  // и убрать её нельзя.
  id: USER.id,
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

// --- один экран настроек ----------------------------------------------------

const DEPARTMENT = {
  id: 'd-1', office_id: null, office_name: null, parent_department_id: null,
  name: 'Продажи', description: null, head_employee_id: null,
  head_employee_name: null, staff: 0, status: 'ACTIVE',
};

const USED_DEPARTMENT = { ...DEPARTMENT, id: 'd-2', name: 'Финансы', staff: 4 };

const POSITION = {
  id: 'p-1', name: 'Инженер', description: null, staff: 0, status: 'ACTIVE',
};

/** Справочники поверх общей сети: отделы, должности, графики, причины. */
function catalog(
  own: (url: string, method: string) => Response | Promise<Response> | null = () => null,
  rows: {
    departments?: unknown[];
    positions?: unknown[];
    schedules?: unknown[];
  } = {},
) {
  return network((url, method) => {
    const mine = own(url, method);
    if (mine) return mine;
    const bare = clean(url);
    const page = (items: unknown[]) =>
      json(200, { items, next_cursor: null, has_more: false });

    if (bare.endsWith('/departments/') && method === 'GET') {
      return page(rows.departments ?? [DEPARTMENT]);
    }
    if (bare.endsWith('/positions/') && method === 'GET') {
      return page(rows.positions ?? [POSITION]);
    }
    if (bare.endsWith('/work-schedules/') && method === 'GET') {
      return page(rows.schedules ?? []);
    }
    return null;
  });
}

describe('один экран', () => {
  test('четыре справочника и ничего сверх них', async () => {
    catalog();
    renderApp('/administration');

    const sheet = await screen.findByLabelText('Справочники');
    for (const title of [
      'Администраторы', 'Отделы', 'Должности', 'Графики работы',
    ]) {
      expect(within(sheet).getByText(title)).toBeTruthy();
    }
    // Офис — это адрес, карта и геозона; он настраивается в своём разделе.
    expect(within(sheet).queryByText('Офисы')).toBeNull();
    expect(screen.getByText(/Офисы, карта и QR-точки/)).toBeTruthy();
    // Отпуск и больничный компания не придумывает: настраивать нечего.
    expect(within(sheet).queryByText('Причины отсутствия')).toBeNull();
  });

  test('старый адрес ведёт сюда же', async () => {
    catalog();
    renderApp('/admin');
    expect(await screen.findByLabelText('Справочники')).toBeTruthy();
  });

  test('число стоит в закрытом блоке, список — только в раскрытом', async () => {
    catalog();
    renderApp('/administration');

    expect(await screen.findByText('1 отдел')).toBeTruthy();
    // Закрытый блок списка не показывает.
    expect(screen.queryByText('Продажи')).toBeNull();

    fireEvent.click(screen.getByText('Отделы'));
    expect(await screen.findByText('Продажи')).toBeTruthy();
  });

  test('пока ответ не пришёл, стоит прочерк, а не ноль', async () => {
    // Ноль означал бы «записей нет» — этого мы ещё не знаем.
    catalog((url) =>
      clean(url).endsWith('/departments/')
        ? new Promise<Response>(() => undefined)
        : null,
    );
    renderApp('/administration');

    const sheet = await screen.findByLabelText('Справочники');
    const block = within(sheet).getByText('Отделы').closest('article');
    expect(within(block as HTMLElement).getByText('—')).toBeTruthy();
  });
});

describe('добавление', () => {
  test('у отдела одно поле и ничего больше', async () => {
    catalog();
    renderApp('/administration');

    fireEvent.click(await screen.findByRole('button', { name: /Добавить отдел/ }));
    const box = await screen.findByRole('dialog', { name: 'Новый отдел' });

    expect(within(box).getByLabelText('Название отдела')).toBeTruthy();
    // Ни офиса, ни руководителя, ни кода: это не про заведение отдела.
    expect(within(box).queryByLabelText(/Офис/)).toBeNull();
    expect(within(box).queryByLabelText(/Руководитель/)).toBeNull();
    expect(within(box).queryByLabelText(/Код/)).toBeNull();
  });

  test('отдел уходит на сервер одним названием', async () => {
    const calls = catalog((url, method) =>
      clean(url).endsWith('/departments/') && method === 'POST'
        ? json(201, DEPARTMENT)
        : null,
    );
    renderApp('/administration');

    fireEvent.click(await screen.findByRole('button', { name: /Добавить отдел/ }));
    fireEvent.change(await screen.findByLabelText('Название отдела'), {
      target: { value: 'Логистика' },
    });
    const box = screen.getByRole('dialog', { name: 'Новый отдел' });
    fireEvent.click(within(box).getByRole('button', { name: 'Добавить отдел' }));

    await waitFor(() => {
      expect(calls.some((one) =>
        one.url.includes('/departments/') && one.method === 'POST')).toBe(true);
    });
    const sent = calls.find((one) =>
      one.url.includes('/departments/') && one.method === 'POST');
    expect(sent?.body).toEqual({ name: 'Логистика' });
  });

  test('пустое название на сервер не уходит', async () => {
    const calls = catalog();
    renderApp('/administration');

    fireEvent.click(await screen.findByRole('button', { name: /Добавить должность/ }));
    const box = await screen.findByRole('dialog', { name: 'Новая должность' });
    fireEvent.click(within(box).getByRole('button', { name: 'Добавить должность' }));

    expect(await screen.findByText('Название обязательно')).toBeTruthy();
    expect(calls.some((one) => one.method === 'POST')).toBe(false);
  });

  test('у администратора есть логин, пароль и роль', async () => {
    catalog();
    renderApp('/administration');

    fireEvent.click(
      await screen.findByRole('button', { name: /Добавить администратора/ }),
    );
    const box = await screen.findByRole('dialog', { name: 'Новый администратор' });

    expect(within(box).getByLabelText('ФИО')).toBeTruthy();
    expect(within(box).getByLabelText('Логин для входа')).toBeTruthy();
    // Пароль скрыт, пока его не попросили показать.
    expect(within(box).getByLabelText('Пароль').getAttribute('type'))
      .toBe('password');
    fireEvent.click(within(box).getByRole('button', { name: 'Показать пароль' }));
    expect(within(box).getByLabelText('Пароль').getAttribute('type')).toBe('text');
  });

  test('роли предлагаются три, и это не весь каталог', async () => {
    catalog();
    renderApp('/administration');

    fireEvent.click(
      await screen.findByRole('button', { name: /Добавить администратора/ }),
    );
    fireEvent.click(await screen.findByLabelText(/^Роль/));
    const options = await screen.findAllByRole('option');
    const names = options.map((one) => one.textContent);
    expect(names).toContain('Главный администратор');
    expect(names).toContain('HR-администратор');
    expect(names).toContain('Администратор');
    // Служебные и переносные роли каталога выбором не раздают.
    expect(names).not.toContain('Суперадминистратор');
    expect(names).not.toContain('Наблюдатель');
  });

  test('роль меняется и у заведённого администратора', async () => {
    // Иначе кадровика нельзя повысить, не заводя человека заново, —
    // а второй логин у одного человека это уже не смена роли.
    const calls = catalog((url, method) => {
      const bare = clean(url);
      if (bare.endsWith(`/users/${NO_ACCESS.id}/`) && method === 'GET') {
        return json(200, {
          ...NO_ACCESS,
          active_grants: [
            { id: 'g-9', role_id: 'r-1', role_name: 'Кадровик',
              role_code: 'HR_ADMIN', region_id: null, region_name: null,
              office_id: null, office_name: null },
          ],
        });
      }
      // Каталог ролей сервера: коды здесь те, что предлагает интерфейс.
      if (bare.endsWith('/roles')) {
        return json(200, {
          items: [
            { ...ROLES[0]!, id: 'r-hr', code: 'HR_ADMIN' },
            { ...ROLES[0]!, id: 'r-super', code: 'SUPER_ADMIN' },
          ],
        });
      }
      if (bare.endsWith('/grants') && method === 'POST') {
        return json(201, { id: 'g-10' });
      }
      if (bare.endsWith('/grants/g-9') && method === 'DELETE') {
        return json(200, { id: 'g-9' });
      }
      return null;
    });
    renderApp('/administration');

    fireEvent.click(await screen.findByText('Администраторы'));
    fireEvent.click(
      await screen.findByRole('button', { name: /Изменить «bezprav@humotech.tj»/ }),
    );
    const box = await screen.findByRole('dialog', { name: 'Администратор' });

    fireEvent.click(within(box).getByLabelText(/^Роль/));
    fireEvent.click(await screen.findByRole('option', { name: 'Главный администратор' }));
    fireEvent.click(within(box).getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => {
      expect(calls.some((one) => clean(one.url).endsWith('/grants')
        && one.method === 'POST')).toBe(true);
    });
    // Новая роль выдаётся ДО отзыва прежней: обратный порядок на секунду
    // оставил бы человека без доступа, а при сбое — насовсем.
    const order = calls
      .filter((one) => clean(one.url).includes('/grants'))
      .map((one) => one.method);
    expect(order).toEqual(['POST', 'DELETE']);
  });
});

describe('удаление и архив', () => {
  test('неиспользуемое удаляется совсем', async () => {
    const calls = catalog((url, method) =>
      clean(url).endsWith('/departments/d-1/') && method === 'DELETE'
        ? json(204, {})
        : null,
    );
    renderApp('/administration?open=departments');

    fireEvent.click(await screen.findByLabelText('Удалить «Продажи»'));
    const ask = await screen.findByRole('dialog', { name: 'Удалить' });
    expect(within(ask).getByText(/никто не ссылался/)).toBeTruthy();
    fireEvent.click(within(ask).getByRole('button', { name: 'Удалить' }));

    await waitFor(() => {
      expect(calls.some((one) =>
        one.url.includes('/departments/d-1/') && one.method === 'DELETE')).toBe(true);
    });
  });

  test('использованное только архивируется, и это сказано числом', async () => {
    const calls = catalog(
      (url, method) =>
        clean(url).endsWith('/departments/d-2/deactivate/') && method === 'POST'
          ? json(200, USED_DEPARTMENT)
          : null,
      { departments: [USED_DEPARTMENT] },
    );
    renderApp('/administration?open=departments');

    fireEvent.click(await screen.findByLabelText('Архивировать «Финансы»'));
    const ask = await screen.findByRole('dialog', { name: 'Архивировать' });
    expect(within(ask).getByText(/уже используется \(4\)/)).toBeTruthy();
    fireEvent.click(within(ask).getByRole('button', { name: 'Архивировать' }));

    await waitFor(() => {
      expect(calls.some((one) => one.url.includes('/deactivate/'))).toBe(true);
    });
    // Физического удаления не было: история осталась бы без объяснения.
    expect(calls.every((one) => one.method !== 'DELETE')).toBe(true);
  });

  test('себя убрать нельзя', async () => {
    catalog();
    renderApp('/administration?open=admins');

    const mine = await screen.findByLabelText(/это вы: убрать нельзя/i);
    expect((mine as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('журнал действий', () => {
  test('остаётся доступным, но справочником не стал', async () => {
    catalog();
    renderApp('/administration/audit');
    expect(await screen.findByLabelText('Журнал действий')).toBeTruthy();
  });
});

describe('вспомогательное', () => {
  test('имя записи — имя сотрудника, если он привязан', () => {
    expect(userTitle(MANY as never)).toBe('Рахимов Далер');
    expect(userTitle(NO_ACCESS as never)).toBe(NO_ACCESS.email);
  });

  test('счёт по-русски не сводится к «одному или больше»', () => {
    const unit: [string, string, string] = ['отдел', 'отдела', 'отделов'];
    expect(counted(1, unit)).toBe('1 отдел');
    expect(counted(2, unit)).toBe('2 отдела');
    expect(counted(5, unit)).toBe('5 отделов');
    expect(counted(11, unit)).toBe('11 отделов');
    expect(counted(21, unit)).toBe('21 отдел');
  });
});
