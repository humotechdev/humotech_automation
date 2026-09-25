/**
 * «Ознакомления»: один лист, три вкладки — сотрудники, материалы, разделы.
 *
 * Проверяется то, что легко сломать незаметно: лист и линия вкладок не
 * пересоздаются, числа на чипах берутся с сервера по тому же правилу,
 * что и строки, фильтры уходят в запрос и в выгрузку, «Напомнить всем»
 * называет итог по каждому, черновик никого не «назначает», а
 * опубликованную версию нельзя править — только выпустить новую.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, pick, renderApp } from './helpers';

const PERMISSIONS = ['onboarding.read', 'onboarding.manage', 'policies.publish', 'employees.read'];

function person(over: Record<string, unknown> = {}) {
  return {
    employee_id: 'e-1', full_name: 'Мурадов Азизбек', employee_number: '1042', office_name: 'Ташкент',
    department_name: 'IT-отдел', position_name: null, telegram_state: 'ACTIVE', status: 'IN_PROGRESS', stage: 'POLICIES',
    completed: false, sections_done: 10, sections_total: 10, policies_done: 1, policies_total: 3,
    invited_at: '2026-09-10T06:00:00Z', started_at: '2026-09-11T06:00:00Z', completed_at: null, last_reminder_at: null,
    invitation_status: null, invitation_expires_at: null, enrolled_at: '2026-09-10T06:00:00Z',
    due_date: '2026-09-20', overdue: true, reasons: ['overdue'], group: 'attention',
    materials: [{ document_id: 'd-1', title: 'Политика защиты данных', version: '1.5', state: 'pending', decided_at: null }],
    ...over,
  };
}

const PEOPLE = [
  person(),
  person({ employee_id: 'e-2', full_name: 'Абдуллаева Нигора', employee_number: '0985', completed: true, group: 'done',
           reasons: [], overdue: false, due_date: null, policies_done: 3, telegram_state: 'ACTIVE' }),
];

const COUNTS = { all: 2, groups: { all: 2, done: 1, attention: 1, waiting: 0, not_started: 0, in_progress: 0, overdue: 1 } };

const version = (id: string, v: string, status: string) => ({
  id, version: v, status, summary: 'Кратко', body: null, agree_label: 'Согласен', has_file: false, file_name: null,
  published_at: status === 'PUBLISHED' ? '2026-09-01T06:00:00Z' : null, created_at: '2026-09-01T06:00:00Z',
});

const DOCS = [
  { id: 'd-1', code: 'D1', title: 'Политика защиты данных', description: 'Как мы храним данные', is_mandatory: true, position: 1,
    archived_at: null, current_version: version('v-1', '1.5', 'PUBLISHED'), versions: [version('v-1', '1.5', 'PUBLISHED')],
    category: { id: 'c-1', title: 'Информационная безопасность' }, assigned: 52, confirmed: 49, declined: 0, renewal_pending: 3,
    nearest_due: null, created_by: 'Каримова Севара', changed_at: '2026-09-18T09:00:00Z', changed_by: 'Каримова Севара' },
  { id: 'd-2', code: 'D2', title: 'Кодекс этики и поведения', description: null, is_mandatory: true, position: 2,
    archived_at: null, current_version: null, versions: [version('v-2', '1', 'DRAFT')],
    category: null, assigned: 0, confirmed: 0, declined: 0, renewal_pending: 0,
    nearest_due: null, created_by: 'Каримова Севара', changed_at: '2026-09-10T09:00:00Z', changed_by: 'Каримова Севара' },
];

const CATEGORIES = [{
  id: 'c-1', title: 'Информационная безопасность', description: 'Защита данных', position: 1,
  owner: { id: 'e-3', full_name: 'Каримова Севара', position_name: 'Специалист по ИБ' },
  documents_count: 1, documents: [{ id: 'd-1', title: 'Политика защиты данных' }],
  changed_at: '2026-09-18T11:42:00Z', changed_by: 'Каримова Севара',
}];

const CARDS = [
  { id: 's-1', position: 1, title: 'О компании', body: 'Текст', button_label: 'Я ознакомился', version: 1, updated_at: '2026-09-01T06:00:00Z' },
];

function network(handler: (path: string, method: string, body: unknown) => Response | null = () => null) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method, call.body);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, { ...USER, permissions: PERMISSIONS });
    if (path.includes('/onboarding/counts')) return json(200, COUNTS);
    if (path.includes('/onboarding/progress')) {
      const group = new URL(path, 'http://x').searchParams.get('group');
      return json(200, { items: PEOPLE.filter((one) => !group || one.group === group), next_cursor: null, has_more: false });
    }
    if (path.includes('/onboarding/export')) return json(200, { items: [], total: 0 });
    if (path.includes('/onboarding/documents')) return json(200, { items: DOCS });
    if (path.includes('/onboarding/categories')) return json(200, { items: CATEGORIES });
    if (path.includes('/onboarding/sections')) return json(200, { items: CARDS });
    if (path.includes('/offices/')) return json(200, { items: [{ id: 'o-1', name: 'Ташкент', status: 'ACTIVE' }] });
    if (path.includes('/departments/')) return json(200, { items: [{ id: 'dp-1', name: 'IT-отдел' }], next_cursor: null, has_more: false });
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

const urls = (calls: { url: string }[], part: string) => calls.filter((c) => c.url.includes(part)).map((c) => c.url);

describe('один лист', () => {
  test('смена вкладки не пересоздаёт лист и линию; числа видны и у неактивных вкладок', async () => {
    network();
    renderApp('/onboarding');
    await screen.findByRole('table', { name: 'Ознакомления сотрудников' });

    const materials = screen.getByRole('tab', { name: /Материалы/ });
    await waitFor(() => expect(materials.textContent).toContain('2'));
    // Разделы: один раздел плюс знакомство с компанией.
    expect(screen.getByRole('tab', { name: /Разделы/ }).textContent).toContain('2');

    const sheet = document.querySelector('.on-sheet');
    const ink = document.querySelector('.on-tabs__ink');
    fireEvent.click(materials);
    expect(await screen.findByRole('table', { name: 'Материалы' })).toBeTruthy();
    expect(document.querySelector('.on-sheet')).toBe(sheet);
    expect(document.querySelector('.on-tabs__ink')).toBe(ink);
    expect(screen.getByRole('heading', { level: 1, name: 'Ознакомления' })).toBeTruthy();
  });

  test('старая ссылка на документы открывает «Материалы», ссылка на сотрудника — его ознакомление', async () => {
    network((path) => (path.includes('/employees/e-1/onboarding')
      ? json(200, { ...person(), timeline: [] }) : null));
    renderApp('/onboarding?tab=documents&employee=e-1');

    expect(await screen.findByRole('tab', { name: /Материалы/, selected: true })).toBeTruthy();
    expect(await screen.findByText('Срок ознакомления', { selector: 'h4' })).toBeTruthy();
  });
});

describe('сотрудники', () => {
  test('числа чипов и показателей — с сервера, группа уходит в запрос', async () => {
    const calls = network();
    renderApp('/onboarding');
    await screen.findByRole('table', { name: 'Ознакомления сотрудников' });

    const chip = screen.getByRole('button', { name: /Требуют внимания/ });
    expect(chip.textContent).toMatch(/1$/);
    fireEvent.click(chip);
    await waitFor(() => expect(urls(calls, '/onboarding/progress').some((u) => u.includes('group=attention') && u.includes('limit=25'))).toBe(true));
    await waitFor(() => expect(screen.queryByText('Абдуллаева Нигора')).toBeNull());
  });

  test('просрочка и срок показаны по данным сервера; без срока — «не назначен»', async () => {
    network();
    renderApp('/onboarding');

    const table = await screen.findByRole('table', { name: 'Ознакомления сотрудников' });
    const late = within(table).getByText('Мурадов Азизбек').closest('tr') as HTMLElement;
    expect(within(late).getByText('20.09.2026')).toBeTruthy();
    expect(within(late).getByText('Просрочено')).toBeTruthy();
    const done = within(table).getByText('Абдуллаева Нигора').closest('tr') as HTMLElement;
    expect(within(done).getByText('не назначен')).toBeTruthy();
    expect(within(done).getByText('Завершено')).toBeTruthy();
  });

  test('офис уходит и в список, и в счётчики, и в выгрузку', async () => {
    const calls = network();
    renderApp('/onboarding');
    await screen.findByRole('table', { name: 'Ознакомления сотрудников' });

    await pick('Офис', 'Ташкент');
    await waitFor(() => {
      expect(urls(calls, '/onboarding/progress').pop()).toContain('office_id=o-1');
      expect(urls(calls, '/onboarding/counts').pop()).toContain('office_id=o-1');
    });
    fireEvent.click(screen.getByRole('button', { name: /Выгрузить отчёт/ }));
    await waitFor(() => expect(urls(calls, '/onboarding/export').pop()).toContain('office_id=o-1'));
  });

  test('«Напомнить всем» — один запрос и итог по каждому', async () => {
    const calls = network((path, method) => (method === 'POST' && path.endsWith('/onboarding/remind')
      ? json(200, { items: [{ employee_id: 'e-1', outcome: 'no_telegram' }] }) : null));
    renderApp('/onboarding');

    const side = await screen.findByRole('complementary', { name: 'Требуют внимания' });
    expect(await within(side).findByText('Просрочен срок ознакомления')).toBeTruthy();
    fireEvent.click(within(side).getByRole('button', { name: /Напомнить всем/ }));

    expect(await within(side).findByText(/нет Telegram: 1/)).toBeTruthy();
    const sent = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/onboarding/remind'));
    expect(sent).toHaveLength(1);
    expect(sent[0]!.body).toEqual({ employee_ids: ['e-1'] });
  });

  test('ошибка не превращается в нули', async () => {
    network((path) => (path.includes('/onboarding/counts') ? json(500, { error: {} }) : null));
    renderApp('/onboarding');

    expect((await screen.findAllByText(/Не удалось загрузить данные/)).length).toBeGreaterThan(0);
    expect(screen.queryByText('назначено')).toBeNull();
  });
});

describe('материалы', () => {
  test('черновик никого не обязывает: назначено — прочерк', async () => {
    network();
    renderApp('/onboarding?tab=materials');

    const table = await screen.findByRole('table', { name: 'Материалы' });
    const draft = within(table).getByText('Кодекс этики и поведения').closest('tr') as HTMLElement;
    expect(within(draft).getByText('Черновик')).toBeTruthy();
    expect(within(draft).getAllByText('—').length).toBeGreaterThanOrEqual(2);
    const live = within(table).getByText('Политика защиты данных').closest('tr') as HTMLElement;
    expect(within(live).getByText('52')).toBeTruthy();
    expect(within(live).getByText('новая версия: 3')).toBeTruthy();
  });

  test('опубликованную версию не править — только новая версия', async () => {
    network();
    renderApp('/onboarding?tab=materials');

    fireEvent.click(within(await screen.findByRole('table', { name: 'Материалы' })).getByRole('button', { name: /^Политика защиты данных/ }));
    expect(await screen.findByRole('button', { name: /Новая версия/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Изменить версию|Редактировать версию/ })).toBeNull();
  });

  test('поиск и раздел сужают список', async () => {
    network();
    renderApp('/onboarding?tab=materials');
    await screen.findByRole('table', { name: 'Материалы' });

    await pick(/^Раздел: /, 'Без раздела');
    const table = screen.getByRole('table', { name: 'Материалы' });
    expect(within(table).queryByText('Политика защиты данных')).toBeNull();
    expect(within(table).getByText('Кодекс этики и поведения')).toBeTruthy();
  });
});

describe('разделы', () => {
  test('знакомство с компанией закреплено, раздел — с ответственным и примерами', async () => {
    network();
    renderApp('/onboarding?tab=sections');

    const table = await screen.findByRole('table', { name: 'Разделы материалов' });
    const rows = within(table).getAllByRole('row');
    expect(rows[1]!.textContent).toContain('Знакомство с компанией');
    const category = within(table).getByText('Информационная безопасность').closest('tr') as HTMLElement;
    expect(within(category).getAllByText('Каримова Севара').length).toBeGreaterThan(0);
    expect(within(category).getByText('Специалист по ИБ')).toBeTruthy();
    expect(within(category).getByText('Политика защиты данных')).toBeTruthy();
  });

  test('создание раздела уходит на сервер с названием и описанием', async () => {
    const calls = network((path, method) => (method === 'POST' && path.endsWith('/onboarding/categories/')
      ? json(201, { ...CATEGORIES[0], id: 'c-2', title: 'Охрана труда' }) : null));
    renderApp('/onboarding?tab=sections');
    await screen.findByRole('table', { name: 'Разделы материалов' });

    fireEvent.click(screen.getByRole('button', { name: /Создать раздел/ }));
    fireEvent.change(await screen.findByLabelText('Название'), { target: { value: 'Охрана труда' } });
    fireEvent.change(screen.getByLabelText('Описание'), { target: { value: 'Безопасная работа' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => {
      const made = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/onboarding/categories/'));
      expect(made).toHaveLength(1);
      expect(made[0]!.body).toMatchObject({ title: 'Охрана труда', description: 'Безопасная работа' });
    });
  });
});
