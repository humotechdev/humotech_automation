/**
 * Карточка сотрудника: подтверждение привязки Telegram.
 *
 * Согласие сотрудника в боте доступа не открывает — ссылку могли
 * переслать или перехватить. Рабочей привязку делает только HR, и
 * перед решением он видит, какой именно Telegram перешёл по ссылке.
 */

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const PERSON = {
  id: 'e-7',
  employee_number: 'HT-007',
  full_name: 'Каримов Алишер',
  first_name: 'Алишер',
  last_name: 'Каримов',
  phone: null,
  corporate_email: null,
  employment_status: 'ACTIVE',
  employment_status_title: 'Работает',
  termination_reason: null,
  hire_date: '2026-10-01',
  termination_date: null,
  telegram_connected: false,
  photo: false,
  current_assignment: null,
  current_schedule: null,
  documents: [],
  assignment_history: [],
};

const AWAITING = {
  state: 'PENDING',
  employee_id: 'e-7',
  account: { status: 'PENDING', telegram_username: 'alisher_k', connected_at: null },
  invitation: { id: 'inv-1', status: 'PENDING_CONFIRMATION', expires_at: null },
};

function network(link: Record<string, unknown>) {
  return fakeNetwork((path, call) => {
    if (path.includes('/auth/')) {
      return json(200, {
        ...USER,
        permissions: ['employees.view', 'employees.manage', 'employees.read',
          'telegram.read', 'telegram.manage'],
      });
    }
    if (path.includes('/telegram/invitations/inv-1/') && call.method === 'POST') {
      return json(200, { status: 'ACTIVE' });
    }
    if (path.includes('/employees/e-7/telegram')) return json(200, link);
    if (path.includes('/employees/e-7/onboarding')) return json(404, {});
    if (path.includes('/employees/e-7/')) return json(200, PERSON);
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

async function openCard(link: Record<string, unknown>) {
  const calls = network(link);
  renderApp('/employees/e-7');
  await screen.findByRole('heading', { name: 'Каримов Алишер' });
  return calls;
}

describe('подтверждение привязки Telegram', () => {
  test('HR видит, кто перешёл по ссылке, и подтверждает', async () => {
    const calls = await openCard(AWAITING);

    expect(await screen.findByText('@alisher_k')).toBeTruthy();
    expect(screen.getByText('Перешёл по ссылке — ждёт подтверждения HR')).toBeTruthy();
    // Пока ждём решения, новую ссылку не предлагаем: она бы спорила с этой.
    expect(screen.queryByRole('button', { name: 'Пригласить в Telegram' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));

    await waitFor(() => expect(calls.some((call) =>
      call.method === 'POST' && call.url.includes('/telegram/invitations/inv-1/confirm/'),
    )).toBe(true));
  });

  test('«Это не он» отклоняет привязку', async () => {
    const calls = await openCard(AWAITING);

    fireEvent.click(await screen.findByRole('button', { name: 'Это не он — отклонить' }));

    await waitFor(() => expect(calls.some((call) =>
      call.method === 'POST' && call.url.includes('/telegram/invitations/inv-1/reject/'),
    )).toBe(true));
  });

  test('рабочая привязка кнопок решения не показывает', async () => {
    await openCard({
      state: 'ACTIVE',
      employee_id: 'e-7',
      account: { status: 'ACTIVE', telegram_username: 'alisher_k', connected_at: null },
      invitation: { id: 'inv-1', status: 'USED', expires_at: null },
    });

    expect(await screen.findByText('Привязан')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Подтвердить' })).toBeNull();
  });
});
