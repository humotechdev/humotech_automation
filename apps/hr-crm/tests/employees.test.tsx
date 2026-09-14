/**
 * Список сотрудников: адрес хранит состояние, счётчики не зависят от
 * выбранной вкладки, а курсорная пагинация не рисует номера страниц.
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
    if (path.includes('/attendance/presence')) {
      return json(200, {
        date: '2026-03-12', timezone: 'Asia/Dushanbe', total: 12,
        counts: { IN_OFFICE: 9, NOT_COME: 2, DAY_OFF: 1 },
        truncated: false, items: [],
      });
    }
    if (path.includes('/regions/')) {
      return json(200, { items: [{ id: 'r-1', code: 'C', name: 'Центр', status: 'ACTIVE' }] });
    }
    if (path.includes('/employees/')) {
      return json(200, { items: [PERSON], next_cursor: 'c2', has_more: true });
    }
    return crm(path) ?? json(200, { items: [] });
  });
}

describe('список', () => {
  test('строка показывает две строки сведений из ответа сервера', async () => {
    network();
    renderApp('/employees');

    expect(await screen.findByText('Каримов Алишер')).toBeTruthy();
    expect(screen.getByText('HT-001')).toBeTruthy();
    expect(screen.getByText('Специалист поддержки')).toBeTruthy();
    expect(screen.getByText('Операционный отдел')).toBeTruthy();
    expect(screen.getByText('Пятидневка 09:00–18:00')).toBeTruthy();
  });

  test('счётчики вкладок не зависят от выбранной вкладки', async () => {
    // Иначе, выбрав «Активные», человек видел бы нули у остальных.
    const calls = network();
    renderApp('/employees');
    await screen.findByText('Каримов Алишер');

    fireEvent.click(screen.getByRole('tab', { name: /Уволенные/ }));

    await waitFor(() =>
      expect(
        calls.some((c) => c.url.includes('/employees/?') && c.url.includes('status=TERMINATED')),
      ).toBe(true),
    );
    const counts = calls.filter((c) => c.url.includes('/employees/counts'));
    expect(counts.every((c) => !c.url.includes('status='))).toBe(true);
  });

  test('поиск попадает в адрес и оттуда в запрос', async () => {
    // Проверяем по запросу: адрес — не украшение, из него собирается
    // выборка, и возврат из карточки поэтому ничего не теряет.
    const calls = network();
    renderApp('/employees');
    await screen.findByText('Каримов Алишер');

    fireEvent.change(screen.getByLabelText('Поиск по ФИО, должности или Telegram'), {
      target: { value: 'Кар' },
    });

    await waitFor(
      () =>
        expect(
          calls.some((c) => c.url.includes('/employees/?') && c.url.includes('search=')),
        ).toBe(true),
      { timeout: 2000 },
    );
  });

  test('страница листается курсором, номеров страниц нет', async () => {
    // Сервер не считает общее число строк: «страница 25» была бы
    // нарисованной кнопкой, ведущей неизвестно куда.
    const calls = network();
    renderApp('/employees');
    await screen.findByText('Каримов Алишер');

    expect(screen.queryByRole('button', { name: '25' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('cursor=c2'))).toBe(true),
    );
  });

  test('смена региона сбрасывает несовместимый офис', async () => {
    const calls = network();
    renderApp('/employees?office_id=o-9');
    await screen.findByText('Каримов Алишер');

    fireEvent.change(screen.getByLabelText('Регион'), { target: { value: 'r-1' } });

    await waitFor(() => {
      const last = calls.filter((c) => c.url.includes('/employees/?')).pop();
      expect(last?.url).toContain('region_id=r-1');
      expect(last?.url).not.toContain('office_id');
    });
  });

  test('ошибка загрузки не выдаётся за «сотрудников нет»', async () => {
    network((path) =>
      path.includes('/employees/?') || path.endsWith('/employees/')
        ? json(500, { error: {} })
        : null,
    );
    renderApp('/employees');

    expect(await screen.findByText(/Не удалось загрузить список/)).toBeTruthy();
  });
});

describe('карточка', () => {
  test('нажатие на строку выбирает сотрудника, а не открывает карточку', async () => {
    // Выбор показывает человека в правой колонке. Открытие карточки —
    // отдельное действие: список из двухсот строк не должен перекрываться
    // диалогом от случайного попадания курсором.
    network();
    renderApp('/employees');
    fireEvent.click(await screen.findByText('Каримов Алишер'));

    expect(await screen.findByRole('button', { name: 'Открыть профиль' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Карточка сотрудника' })).toBeNull();
  });

  test('открывается кнопкой «Открыть профиль»', async () => {
    network();
    renderApp('/employees');
    fireEvent.click(await screen.findByText('Каримов Алишер'));
    fireEvent.click(await screen.findByRole('button', { name: 'Открыть профиль' }));

    expect(await screen.findByRole('dialog', { name: 'Карточка сотрудника' })).toBeTruthy();
  });

  test('приглашение Telegram не создаётся само при открытии', async () => {
    // Открытие карточки не должно раздавать ссылки доступа.
    const calls = network();
    renderApp('/employees');
    fireEvent.click(await screen.findByText('Каримов Алишер'));
    fireEvent.click(await screen.findByRole('button', { name: 'Открыть профиль' }));
    await screen.findByRole('dialog', { name: 'Карточка сотрудника' });

    expect(calls.filter((c) => c.method === 'POST' && c.url.includes('invitations'))).toHaveLength(0);
  });
});
