/**
 * Список сотрудников: адрес хранит состояние, счётчики не зависят от
 * выбранной вкладки, номер страницы превращается в сдвиг выборки.
 *
 * Имя выбранного сотрудника на странице дважды — в карточке списка и в
 * правой колонке, поэтому поиск по имени берёт все совпадения.
 */

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const PERSON = {
  id: 'e-1',
  employee_number: 'HT-001',
  full_name: 'Каримов Алишер',
  first_name: 'Алишер',
  last_name: 'Каримов',
  phone: null,
  corporate_email: null,
  employment_status: 'ACTIVE',
  hire_date: '2026-01-09',
  termination_date: null,
  telegram_connected: false,
  telegram_state: 'ACTIVE',
  telegram_username: null,
  photo: false,
  current_assignment: {
    id: 'a-1', office_id: 'o-1', office_name: 'Ташкент', region_id: 'r-1',
    region_name: 'Центр', department_id: 'd-1', department_name: 'Операционный отдел',
    position_id: 'p-1', position_name: 'Специалист поддержки',
    employment_type: 'FULL_TIME', work_mode: 'ONSITE', is_primary: true,
    valid_from: '2026-01-09', valid_to: null,
  },
  current_schedule: {
    id: 's-1', name: 'Пятидневка 09:00–18:00', timezone: 'Asia/Dushanbe',
    weekly_minutes: 2400, is_flexible: false,
  },
};

function network(handler: (path: string) => Response | null = () => null) {
  return fakeNetwork((path) => {
    const own = handler(path);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/employees/counts')) {
      return json(200, { total: 12, ACTIVE: 9, SUSPENDED: 1, TERMINATED: 2 });
    }
    if (path.includes('/employees/highlights')) {
      return json(200, {
        recent_hires: 0, recent: [], birthdays_today: 0, birthdays: [],
        without_schedule: 0, unscheduled: [],
      });
    }
    if (path.includes('/attendance/presence')) {
      return json(200, {
        date: '2026-03-12', timezone: 'Asia/Dushanbe', total: 12,
        counts: { IN_OFFICE: 9, NOT_COME: 2, DAY_OFF: 1 },
        truncated: false, items: [],
      });
    }
    if (path.includes('/employees/')) {
      return json(200, { items: [PERSON], next_cursor: null, has_more: false });
    }
    return crm(path) ?? json(200, { items: [] });
  });
}

const listCalls = (calls: { url: string }[]) =>
  calls.filter((c) => c.url.includes('/employees/?') && !c.url.includes('counts') && !c.url.includes('highlights'));

describe('список', () => {
  test('карточка показывает сведения из ответа сервера', async () => {
    network();
    renderApp('/employees');

    expect((await screen.findAllByText('Каримов Алишер')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Ташкент').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Специалист поддержки').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Операционный отдел').length).toBeGreaterThan(0);
    // В графике нет дней и часов — показывается его название, а не пустота.
    expect(screen.getAllByText('Пятидневка 09:00–18:00').length).toBeGreaterThan(0);
  });

  test('счётчики вкладок не зависят от выбранной вкладки', async () => {
    // Иначе, выбрав «Активные», человек видел бы нули у остальных.
    const calls = network();
    renderApp('/employees');
    await screen.findAllByText('Каримов Алишер');

    fireEvent.click(screen.getByRole('tab', { name: /Уволенные/ }));

    await waitFor(() =>
      expect(listCalls(calls).some((c) => c.url.includes('status=TERMINATED'))).toBe(true),
    );
    const counts = calls.filter((c) => c.url.includes('/employees/counts'));
    expect(counts.every((c) => !c.url.includes('status='))).toBe(true);
  });

  test('поиск попадает в адрес и оттуда в запрос', async () => {
    const calls = network();
    renderApp('/employees');
    await screen.findAllByText('Каримов Алишер');

    fireEvent.change(screen.getByLabelText('Поиск по ФИО, должности или Telegram'), {
      target: { value: 'Кар' },
    });

    await waitFor(
      () => expect(listCalls(calls).some((c) => c.url.includes('search='))).toBe(true),
      { timeout: 2000 },
    );
  });

  test('номер страницы уходит на сервер сдвигом выборки', async () => {
    // На странице шестнадцать человек, поэтому для второй страницы их
    // должно быть больше. Вторая запрашивается сдвигом, а не курсором:
    // на неё можно перейти сразу.
    const calls = network((path) =>
      path.includes('/employees/counts')
        ? json(200, { total: 20, ACTIVE: 20, SUSPENDED: 0, TERMINATED: 0 })
        : null,
    );
    renderApp('/employees');
    await screen.findAllByText('Каримов Алишер');

    fireEvent.click(screen.getByRole('button', { name: '2' }));

    await waitFor(() =>
      expect(listCalls(calls).some((c) => c.url.includes('offset=16'))).toBe(true),
    );
    expect(screen.queryByRole('button', { name: '3' })).toBeNull();
  });

  test('смена вкладки начинает выборку с первой страницы', async () => {
    // Страница прошлого набора указывает в чужие строки.
    const calls = network();
    renderApp('/employees?page=2');
    await screen.findAllByText('Каримов Алишер');

    fireEvent.click(screen.getByRole('tab', { name: /Активные/ }));

    await waitFor(() => {
      const last = listCalls(calls).pop();
      expect(last?.url).toContain('status=ACTIVE');
      expect(last?.url).not.toContain('offset=');
    });
  });

  test('ошибка загрузки не выдаётся за «сотрудников нет»', async () => {
    network((path) =>
      path.includes('/employees/?') && !path.includes('counts') && !path.includes('highlights')
        ? json(500, { error: {} })
        : null,
    );
    renderApp('/employees');

    expect(await screen.findByText(/Не удалось загрузить список/)).toBeTruthy();
  });
});

describe('карточка', () => {
  test('нажатие на карточку выбирает сотрудника, а не открывает карточку', async () => {
    // Открытие карточки — отдельное действие: случайное нажатие не должно
    // перекрывать список диалогом.
    network();
    renderApp('/employees');
    const [name] = await screen.findAllByText('Каримов Алишер');
    fireEvent.click(name as HTMLElement);

    expect((await screen.findAllByRole('button', { name: 'Открыть профиль' })).length).toBeGreaterThan(0);
    expect(screen.queryByRole('dialog', { name: 'Карточка сотрудника' })).toBeNull();
  });

  test('«Открыть профиль» открывает карточку сотрудника страницей', async () => {
    // Человек просит карточку — он и должен получить карточку, а не
    // окно поверх списка.
    network((path) =>
      /\/employees\/e-1\/$/.test(path)
        ? json(200, { ...PERSON, current_assignment: PERSON.current_assignment })
        : null,
    );
    renderApp('/employees');
    await screen.findAllByText('Каримов Алишер');
    const [open] = await screen.findAllByRole('button', { name: 'Открыть профиль' });
    fireEvent.click(open as HTMLElement);

    expect(await screen.findByRole('link', { name: /Все сотрудники/ })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Карточка сотрудника' })).toBeNull();
  });

  test('приглашение Telegram не создаётся само при открытии', async () => {
    // Открытие карточки не должно раздавать ссылки доступа.
    const calls = network((path) =>
      /\/employees\/e-1\/$/.test(path) ? json(200, PERSON) : null,
    );
    renderApp('/employees');
    await screen.findAllByText('Каримов Алишер');
    const [open] = await screen.findAllByRole('button', { name: 'Открыть профиль' });
    fireEvent.click(open as HTMLElement);
    await screen.findByRole('link', { name: /Все сотрудники/ });

    expect(calls.filter((c) => c.method === 'POST' && c.url.includes('invitations'))).toHaveLength(0);
  });
});
