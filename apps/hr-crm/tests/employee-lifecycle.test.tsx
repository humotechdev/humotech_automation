/**
 * Карточка сотрудника: название трудового статуса.
 *
 * «Активен» — это про учётную запись: она бывает активной или
 * отключённой. Про человека так не говорят, и в карточке работающего
 * сотрудника стоит «Работает», а не «Активен».
 */

import { screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const STAFF = {
  id: 'e-9',
  employee_number: 'HT-009',
  full_name: 'Мурадов Азизбек',
  first_name: 'Азизбек',
  last_name: 'Мурадов',
  phone: null,
  corporate_email: null,
  employment_status: 'ACTIVE',
  employment_status_title: 'Работает',
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

function network(person: Record<string, unknown> = STAFF) {
  return fakeNetwork((path) => {
    if (path.includes('/auth/')) {
      return json(200, {
        ...USER,
        permissions: ['employees.view', 'employees.manage', 'employees.read'],
      });
    }
    if (path.includes(`/employees/${person['id']}/`)) return json(200, person);
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

async function openCard(person: Record<string, unknown> = STAFF) {
  network(person);
  renderApp(`/employees/${person['id']}`);
  await screen.findByRole('heading', { name: 'Мурадов Азизбек' });
}

describe('названия статусов', () => {
  test('человек «Работает», а не «Активен»', async () => {
    // «Активен» — это про учётную запись. Рядом с «Уволен» и «В архиве»
    // оно читается как слово из другого списка.
    await openCard(STAFF);

    expect(screen.getAllByText('Работает').length).toBeGreaterThan(0);
    expect(screen.queryByText('Активен')).toBeNull();
  });
});
