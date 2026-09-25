/**
 * Карточка сотрудника: решение по стажировке.
 *
 * Проверяется то, на чём кадровый учёт расходится с действительностью:
 *
 * — «Активен» как название состояния человека;
 * — решение по стажировке, предложенное тому, кто уже в штате;
 * — «принять в штат», отправленное без подтверждения;
 * — должность, ушедшая на сервер, когда её не меняли;
 * — увольнение одним щелчком, без окна.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const TRAINEE = {
  id: 'e-9',
  employee_number: 'HT-009',
  full_name: 'Мурадов Азизбек',
  first_name: 'Азизбек',
  last_name: 'Мурадов',
  phone: null,
  corporate_email: null,
  employment_status: 'PROBATION',
  employment_status_title: 'Стажировка',
  termination_reason: null,
  hire_date: '2026-09-01',
  termination_date: null,
  telegram_connected: true,
  photo: false,
  current_assignment: {
    id: 'a-9', office_id: 'o-1', office_name: 'Ташкент', region_id: 'r-1',
    region_name: 'Центр', department_id: 'd-1', department_name: 'Продажи',
    position_id: 'p-1', position_name: 'Менеджер',
    employment_type: 'FULL_TIME', work_mode: 'ONSITE', is_primary: true,
    valid_from: '2026-09-01', valid_to: null,
  },
  current_schedule: null,
  documents: [],
  assignment_history: [],
};

const STAFF = {
  ...TRAINEE,
  employment_status: 'ACTIVE',
  employment_status_title: 'Работает',
};

function network(
  person: Record<string, unknown> = TRAINEE,
  own: (path: string, method: string) => Response | null = () => null,
) {
  return fakeNetwork((path, call) => {
    const mine = own(path, call.method);
    if (mine) return mine;
    if (path.includes('/auth/')) {
      return json(200, {
        ...USER,
        permissions: ['employees.view', 'employees.manage', 'employees.read'],
      });
    }
    if (path.includes('/positions/')) {
      return json(200, {
        items: [{ id: 'p-2', name: 'Старший менеджер', status: 'ACTIVE' }],
        next_cursor: null, has_more: false,
      });
    }
    if (path.includes(`/employees/${person['id']}/`)) return json(200, person);
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

async function openCard(person: Record<string, unknown> = TRAINEE, own?: never) {
  const calls = network(person, own);
  renderApp(`/employees/${person['id']}`);
  await screen.findByRole('heading', { name: 'Мурадов Азизбек' });
  return calls;
}

describe('названия статусов', () => {
  test('человек «Работает», а не «Активен»', async () => {
    // «Активен» — это про учётную запись. Рядом со «Стажировкой» и
    // «Уволен» оно читается как слово из другого списка.
    await openCard(STAFF);

    expect(screen.getAllByText('Работает').length).toBeGreaterThan(0);
    expect(screen.queryByText('Активен')).toBeNull();
  });

  test('испытательный срок называется стажировкой', async () => {
    await openCard();

    expect(screen.getAllByText('Стажировка').length).toBeGreaterThan(0);
    expect(screen.queryByText('Испытательный срок')).toBeNull();
  });
});

describe('решение по стажировке', () => {
  test('полоса с действиями есть у стажёра', async () => {
    await openCard();

    const band = screen.getByLabelText('Стажировка');
    expect(within(band).getByText('Идёт стажировка')).toBeTruthy();
    expect(within(band).getByRole('button', { name: 'Принять в штат' })).toBeTruthy();
    expect(within(band).getByRole('button', { name: 'Завершить стажировку' }))
      .toBeTruthy();
  });

  test('у работающего в штате решать нечего', async () => {
    await openCard(STAFF);

    expect(screen.queryByLabelText('Стажировка')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Принять в штат' })).toBeNull();
  });

  test('приём в штат спрашивает подтверждение, а не срабатывает сразу', async () => {
    const calls = await openCard();

    fireEvent.click(screen.getByRole('button', { name: 'Принять в штат' }));

    expect(await screen.findByRole('dialog', { name: 'Принять в штат' })).toBeTruthy();
    // Ни одного запроса до подтверждения.
    expect(calls.some((one) => one.url.includes('/promote/'))).toBe(false);
  });

  test('без смены должности она не уходит на сервер', async () => {
    // Пустое поле означает «оставить прежнюю», а не «стереть».
    const calls = network(TRAINEE, ((path: string, method: string) =>
      path.includes('/promote/') && method === 'POST'
        ? json(200, STAFF)
        : null) as never);
    renderApp(`/employees/${TRAINEE.id}`);
    await screen.findByRole('heading', { name: 'Мурадов Азизбек' });

    fireEvent.click(screen.getByRole('button', { name: 'Принять в штат' }));
    const box = await screen.findByRole('dialog', { name: 'Принять в штат' });
    fireEvent.click(within(box).getByRole('button', { name: 'Принять в штат' }));

    await waitFor(() => {
      expect(calls.some((one) => one.url.includes('/promote/'))).toBe(true);
    });
    const sent = calls.find((one) => one.url.includes('/promote/'));
    expect(sent?.body).toEqual({});
  });

  test('выбранная должность уходит вместе с приёмом', async () => {
    const calls = network(TRAINEE, ((path: string, method: string) =>
      path.includes('/promote/') && method === 'POST'
        ? json(200, STAFF)
        : null) as never);
    renderApp(`/employees/${TRAINEE.id}`);
    await screen.findByRole('heading', { name: 'Мурадов Азизбек' });

    fireEvent.click(screen.getByRole('button', { name: 'Принять в штат' }));
    const box = await screen.findByRole('dialog', { name: 'Принять в штат' });
    fireEvent.click(within(box).getByLabelText(/^Должность/));
    fireEvent.click(await screen.findByRole('option', { name: 'Старший менеджер' }));
    fireEvent.click(within(box).getByRole('button', { name: 'Принять в штат' }));

    await waitFor(() => {
      const sent = calls.find((one) => one.url.includes('/promote/'));
      expect(sent?.body).toEqual({ position_id: 'p-2' });
    });
  });

  test('завершение стажировки — это увольнение, и оно спрашивает', async () => {
    const calls = network(TRAINEE, ((path: string, method: string) =>
      path.includes('/end-probation/') && method === 'POST'
        ? json(200, { ...TRAINEE, employment_status: 'TERMINATED' })
        : null) as never);
    renderApp(`/employees/${TRAINEE.id}`);
    await screen.findByRole('heading', { name: 'Мурадов Азизбек' });

    fireEvent.click(screen.getByRole('button', { name: 'Завершить стажировку' }));
    const box = await screen.findByRole('dialog', { name: 'Завершить стажировку' });
    // Человеку прямо сказано, что произойдёт: не «завершим», а «уволен».
    expect(within(box).getByText(/будет уволен/)).toBeTruthy();
    expect(calls.some((one) => one.url.includes('/end-probation/'))).toBe(false);

    fireEvent.click(within(box).getByRole('button', { name: 'Завершить стажировку' }));

    await waitFor(() => {
      expect(calls.some((one) => one.url.includes('/end-probation/'))).toBe(true);
    });
  });
});
