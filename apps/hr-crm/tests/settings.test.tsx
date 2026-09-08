/**
 * Страница настроек: черновик, сохранение и честность показанного.
 *
 * Проверяется не оформление, а три вещи, каждая из которых уже была
 * поводом для ошибки в этом проекте.
 *
 * **Черновик отделён от сохранённого.** Панель «есть несохранённые
 * изменения» на только что открытой странице — это неправда, а
 * отправка группы целиком отменяет правку соседа в поле, которого
 * не трогали.
 *
 * **Показанное соответствует возможному.** Формат, который хранилище
 * не умеет проверить, в списке не появляется; переключателя без
 * поведения на сервере нет вовсе.
 *
 * **Ошибка не превращается в успех.** Отказ сервера не заменяется
 * сообщением об успехе, а конфликт редакций объясняется, а не
 * затирает чужую работу.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { USER, crm, fakeNetwork, html, json, renderApp } from './helpers';
import { changedOnly, megabytes, zoneLabel } from '../src/features/settings/model';

const clean = (url: string) => url.split('?')[0] as string;

const EDITION = '2026-09-08T09:00:00Z';

const ORG = {
  key: 'organization.defaults',
  title: 'Организация',
  description: 'Название, описание и пояс, в котором CRM показывает время.',
  values: {
    name: 'HUMOTECH Демо',
    description: null,
    crm_timezone: null,
    default_timezone: 'Asia/Dushanbe',
  },
  defaults: {
    name: null, description: null, crm_timezone: null, default_timezone: 'UTC',
  },
  help: {
    name: 'Название рабочего пространства',
    description: 'Одна-две строки о том, чем занята организация',
    crm_timezone: 'Пояс, в котором CRM показывает время',
    default_timezone: 'Запасной пояс организации',
  },
  effective: { timezone: 'Asia/Dushanbe', code: 'DEMO' },
  updated_at: EDITION,
};

const POLICY = {
  key: 'absences.policy',
  title: 'Заявки и документы',
  description: 'Действуют на решения, принимаемые после изменения.',
  values: {
    require_hr_approval: true,
    document_required: false,
    document_required_from_day: 0,
    document_can_be_added_later: true,
    employee_may_cancel_pending: true,
    cancelling_approved_requires_hr: true,
    extensions_allowed: true,
    max_document_bytes: 10485760,
    allowed_document_types: ['application/pdf', 'image/jpeg'],
    allow_negative_leave_balance: false,
    backdating_days_allowed: 0,
    vacation_min_days_ahead: 0,
  },
  defaults: {},
  help: { require_hr_approval: 'Заявка ждёт решения HR' },
  effective: {
    storable_document_types: ['application/pdf', 'image/jpeg', 'image/png'],
  },
  updated_at: null,
};

const ALL = {
  items: [ORG, POLICY],
  elsewhere: [
    { name: 'late_grace_minutes', owner: 'work_schedules.late_grace_minutes',
      hint: 'Допуск опоздания задаётся графиком' },
    { name: 'geofence_radius_m', owner: 'offices.geofence_radius_m',
      hint: 'Радиус геозоны принадлежит офису' },
    { name: 'qr_token_ttl_seconds', owner: 'deployment',
      hint: 'Сроки жизни QR-кодов задаются развёртыванием' },
  ],
  last_change: null,
};

const LINKS = {
  items: [
    { key: 'telegram_bot', title: 'Telegram-бот', configured: false,
      state: 'off', note: 'Токен бота не задан развёртыванием',
      confirmed_at: null, queued: 0, link: '/notifications' },
    { key: 'mini_app', title: 'Mini App', configured: true, state: 'unknown',
      note: 'Доступность извне отсюда не проверяется',
      confirmed_at: null, queued: null, link: null },
    { key: 'ai_assistant', title: 'AI-ассистент', configured: false,
      state: 'off', note: 'Выключен рубильником', confirmed_at: null,
      queued: null, link: '/knowledge' },
  ],
};

function network(
  own: (url: string, call: { method: string }) => Response | null = () => null,
  options: { permissions?: string[]; all?: unknown } = {},
) {
  return fakeNetwork((url, call) => {
    const mine = own(url, call);
    if (mine) return mine;
    const bare = clean(url);
    if (bare.includes('/auth/')) {
      return json(200, {
        ...USER,
        permissions: options.permissions ?? ['settings.manage', 'audit.read'],
      });
    }
    if (bare.endsWith('/settings')) return json(200, options.all ?? ALL);
    if (bare.endsWith('/settings/integrations')) return json(200, LINKS);
    return crm(url) ?? json(200, { items: [] });
  });
}

const opened = () => screen.findByLabelText('Название рабочего пространства');

describe('загрузка', () => {
  test('поля показывают значения с сервера, а не умолчания', async () => {
    network();
    renderApp('/settings');

    const name = (await opened()) as HTMLInputElement;
    expect(name.value).toBe('HUMOTECH Демо');
    // Подзаголовок берётся из данных, а не из макета.
    expect(screen.getByText(/Параметры рабочего пространства HUMOTECH Демо/))
      .toBeTruthy();
  });

  test('ошибка загрузки не превращается в пустую форму', async () => {
    network((url) => (clean(url).endsWith('/settings') ? html(500) : null));
    renderApp('/settings');

    await screen.findByText(/Не удалось загрузить настройки/);
    expect(screen.queryByLabelText('Название рабочего пространства')).toBeNull();
  });

  test('без права раздел объясняет отказ и не притворяется пустым', async () => {
    network(undefined, { permissions: ['employees.read'] });
    renderApp('/settings');

    await screen.findByText(/Нет права на настройки организации/);
    expect(screen.getByText('settings.manage')).toBeTruthy();
  });

  test('личные предпочтения открыты и без права на настройки', async () => {
    network(undefined, { permissions: ['employees.read'] });
    renderApp('/settings?tab=me');

    await screen.findByText(USER.email);
    expect(screen.queryByText(/Нет права на настройки организации/)).toBeNull();
  });

  test('«Демо-данные» в обычном режиме не показываются', async () => {
    network();
    renderApp('/settings');
    await opened();
    expect(screen.queryByText('Демо-данные')).toBeNull();
  });
});

describe('черновик', () => {
  test('на только что открытой странице несохранённого нет', async () => {
    network();
    renderApp('/settings');
    await opened();

    expect(screen.getByText('Всё сохранено')).toBeTruthy();
    expect(screen.queryByText('Есть несохранённые изменения')).toBeNull();
  });

  test('панель появляется от фактического изменения значения', async () => {
    network();
    renderApp('/settings');
    const name = await opened();

    // Прикосновение к полю без правки — ещё не изменение.
    fireEvent.focus(name);
    expect(screen.queryByText('Есть несохранённые изменения')).toBeNull();

    fireEvent.change(name, { target: { value: 'HUMOTECH' } });
    expect(await screen.findByText('Есть несохранённые изменения')).toBeTruthy();
  });

  test('«Отменить» возвращает к сохранённому и ничего не отправляет',
    async () => {
      const calls = network();
      renderApp('/settings');
      const name = (await opened()) as HTMLInputElement;

      fireEvent.change(name, { target: { value: 'Другое имя' } });
      fireEvent.click(screen.getByRole('button', { name: 'Отменить' }));

      expect(name.value).toBe('HUMOTECH Демо');
      expect(screen.getByText('Всё сохранено')).toBeTruthy();
      expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
    });
});

describe('сохранение', () => {
  test('уходят только изменённые поля и виденная редакция', async () => {
    const calls = network((url, call) =>
      clean(url).includes('/settings/organization.defaults') && call.method === 'PATCH'
        ? json(200, { ...ORG, values: { ...ORG.values, name: 'HUMOTECH' },
                      updated_at: '2026-09-08T10:00:00Z' })
        : null,
    );
    renderApp('/settings');
    const name = await opened();

    fireEvent.change(name, { target: { value: 'HUMOTECH' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));

    await waitFor(() => {
      const sent = calls.find((c) => c.method === 'PATCH');
      expect(sent).toBeTruthy();
      const body = sent?.body as Record<string, unknown>;
      // Ровно одно поле: остальные не трогали, и записывать их поверх
      // чужой правки незачем.
      expect(body['values']).toEqual({ name: 'HUMOTECH' });
      expect(body['expected_updated_at']).toBe(EDITION);
      expect(body['check_expected']).toBe(true);
    });
  });

  test('новой сохранённой версией становится ответ сервера', async () => {
    // Сервер обрезает пробелы. Показывать надо сохранённое, а не
    // задуманное: иначе следующее сохранение отправит несуществующую разницу.
    network((_url, call) =>
      call.method === 'PATCH'
        ? json(200, { ...ORG, values: { ...ORG.values, name: 'Обрезано' } })
        : null,
    );
    renderApp('/settings');
    const name = (await opened()) as HTMLInputElement;

    fireEvent.change(name, { target: { value: '  Обрезано  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));

    await waitFor(() => expect(name.value).toBe('Обрезано'));
    expect(screen.getByText('Всё сохранено')).toBeTruthy();
  });

  test('успех показывается только после ответа сервера', async () => {
    let answer: (response: Response) => void = () => undefined;
    network((_url, call) =>
      call.method === 'PATCH'
        ? (new Promise<Response>((resolve) => { answer = resolve; }) as never)
        : null,
    );
    renderApp('/settings');
    const name = await opened();

    fireEvent.change(name, { target: { value: 'HUMOTECH' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));

    // Пока сервер молчит — кнопка занята, «сохранено» не написано.
    expect(await screen.findByRole('button', { name: 'Сохраняем…' })).toBeTruthy();
    expect(screen.queryByText('Всё сохранено')).toBeNull();

    answer(json(200, { ...ORG, values: { ...ORG.values, name: 'HUMOTECH' } }));
    await screen.findByText('Всё сохранено');
  });

  test('ошибка поля показывается рядом с полем', async () => {
    network((_url, call) =>
      call.method === 'PATCH'
        ? json(400, {
            error: {
              code: 'validation_failed',
              message: 'Неверно',
              details: { name: ['Поле «name» обязательно'] },
            },
          })
        : null,
    );
    renderApp('/settings');
    const name = await opened();

    fireEvent.change(name, { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));

    expect(await screen.findByText('Поле «name» обязательно')).toBeTruthy();
    // Черновик цел: править заново с нуля человек не должен.
    expect((name as HTMLInputElement).value).toBe('x');
  });

  test('сетевой отказ сохраняет черновик и позволяет повторить', async () => {
    let down = true;
    network((_url, call) => {
      if (call.method !== 'PATCH') return null;
      if (down) throw new TypeError('Failed to fetch');
      return json(200, { ...ORG, values: { ...ORG.values, name: 'Повтор' } });
    });
    renderApp('/settings');
    const name = (await opened()) as HTMLInputElement;

    fireEvent.change(name, { target: { value: 'Повтор' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));
    await screen.findByRole('alert');
    expect(name.value).toBe('Повтор');

    down = false;
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));
    await screen.findByText('Всё сохранено');
  });

  test('конфликт редакций объясняется, а не затирает чужую работу',
    async () => {
      network((_url, call) =>
        call.method === 'PATCH'
          ? json(409, {
              error: { code: 'conflict', message: 'Запись изменилась', details: null },
            })
          : null,
      );
      renderApp('/settings');
      const name = await opened();

      fireEvent.change(name, { target: { value: 'HUMOTECH' } });
      fireEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));

      expect(await screen.findByText(/изменил кто-то ещё/)).toBeTruthy();
      expect(screen.getByRole('button', { name: /Загрузить свежую версию/ }))
        .toBeTruthy();
      expect(screen.queryByText('Всё сохранено')).toBeNull();
    });
});

describe('заявки и документы', () => {
  test('предлагаются только форматы, которые хранилище умеет проверить',
    async () => {
      network();
      renderApp('/settings?tab=requests');

      await screen.findByLabelText('PDF');
      expect(screen.getByLabelText('PNG')).toBeTruthy();
      // Формата, которого нет в списке хранилища, нет и в выборе.
      expect(screen.queryByLabelText('DOC')).toBeNull();
      expect(screen.queryByText('application/msword')).toBeNull();
    });

  test('снятый формат уходит на сервер списком без него', async () => {
    const calls = network((_url, call) =>
      call.method === 'PATCH' ? json(200, POLICY) : null,
    );
    renderApp('/settings?tab=requests');

    fireEvent.click(await screen.findByLabelText('JPEG'));
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить изменения' }));

    await waitFor(() => {
      const sent = calls.find((c) => c.method === 'PATCH');
      const body = sent?.body as { values: Record<string, unknown> };
      expect(body.values['allowed_document_types']).toEqual(['application/pdf']);
    });
  });
});

describe('посещаемость и подключения', () => {
  test('вместо переключателей — владелец правила и путь к нему', async () => {
    network();
    renderApp('/settings?tab=attendance');

    await screen.findByText('late_grace_minutes');
    expect(screen.getByText('geofence_radius_m')).toBeTruthy();
    // Ни одного переключателя: у правила один владелец, и второй сделал бы
    // неоднозначным вопрос «какое значение сейчас работает».
    expect(screen.queryAllByRole('checkbox').length).toBe(0);
  });

  test('состояние подключения называется словом, а не цветом', async () => {
    network();
    renderApp('/settings?tab=integrations');

    await screen.findByText('Telegram-бот');
    expect(screen.getAllByText('Выключено').length).toBeGreaterThan(0);
    expect(screen.getByText('Не удалось проверить')).toBeTruthy();
  });

  test('в уведомлениях нет кнопки тестовой отправки', async () => {
    network();
    renderApp('/settings?tab=notifications');

    await screen.findByText('Telegram-бот');
    expect(screen.queryByRole('button', { name: /тестов/i })).toBeNull();
    expect(screen.queryAllByRole('checkbox').length).toBe(0);
  });
});

describe('вспомогательное', () => {
  test('на сервер уходит только изменённое', () => {
    expect(changedOnly({ a: 1, b: 2 }, { a: 1, b: 3 })).toEqual({ b: 3 });
    expect(changedOnly({ list: ['a'] }, { list: ['a'] })).toEqual({});
    expect(changedOnly({ list: ['a'] }, { list: ['a', 'b'] }))
      .toEqual({ list: ['a', 'b'] });
  });

  test('размер файла читается человеком', () => {
    expect(megabytes(10485760)).toBe('10 МБ');
    expect(megabytes(0)).toBe('—');
    expect(megabytes(null)).toBe('—');
  });

  test('подпись пояса содержит смещение, а не выдуманный город', () => {
    const label = zoneLabel('Asia/Dushanbe');
    expect(label.startsWith('Asia/Dushanbe')).toBe(true);
    expect(label).toContain('+05:00');
    expect(zoneLabel('')).toBe('');
  });
});

describe('адрес страницы', () => {
  test('открытый раздел живёт в адресе и переживает обновление', async () => {
    network();
    renderApp('/settings?tab=integrations');
    await screen.findByText('Telegram-бот');

    // Раздел из адреса, а не из внутреннего состояния по умолчанию.
    const menu = screen.getByLabelText('Разделы настроек');
    const active = within(menu).getByRole('button', { name: /Подключения/ });
    expect(active.getAttribute('aria-current')).toBe('page');
  });
});

vi.mock('../src/features/settings/model', async (original) => {
  // Демонстрационный режим в тестах выключен: бейдж «Демо-данные» не
  // должен появляться от того, что кто-то выставил переменную сборки.
  const real = await original<typeof import('../src/features/settings/model')>();
  return { ...real, demoMode: () => false };
});
