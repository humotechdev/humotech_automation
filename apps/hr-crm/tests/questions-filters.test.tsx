/**
 * «Обращения»: меню «три точки» и «Фильтр» очереди.
 *
 * Меню: каждый пункт приоритета и категории нажимается и отправляет
 * значение, которое сервер принимает. Выбранный сейчас пункт не серый —
 * он просто закрывает меню, без запроса.
 *
 * Фильтр: несколько приоритетов и категорий уходят параметрами списка
 * через запятую (так их принимает сервер), очередь сужается, выбор живёт
 * в адресе, счётчик — на кнопке, Esc закрывает и возвращает фокус.
 *
 * Состояние обращения — цветной текст без плашки (в шапке и в строке).
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp, type Call } from './helpers';

// Значения, которые принимает сервер: humotech/core/enums.py,
// QUESTION_PRIORITIES и QUESTION_CATEGORIES (ChoiceField в сериализаторах
// и `_known` в фильтре очереди). Другого сервер не примет — ответит 400.
const SERVER_PRIORITIES = ['LOW', 'NORMAL', 'HIGH', 'URGENT'];
const SERVER_CATEGORIES = [
  'VACATION', 'SICK_LEAVE', 'ATTENDANCE', 'SCHEDULE', 'SALARY', 'DOCUMENTS', 'TELEGRAM', 'OTHER',
];

const HR = {
  ...USER,
  permissions: ['questions.read', 'questions.answer', 'knowledge.read', 'employees.read'],
};

const now = new Date();
const ago = (n: number) => new Date(now.getTime() - n * 60_000).toISOString();

const row = (id: string, name: string, priority: string, category: string, status = 'NEW') => ({
  id, number: 100 + Number(id.slice(2)),
  employee: { id: `e-${id}`, full_name: name, employee_number: null, has_photo: false },
  office: { id: 'o-1', name: 'Головной офис' },
  topic: `Тема ${name}`, snippet: 'Текст', last_message_kind: 'EMPLOYEE',
  category, priority, status, unread: false, awaiting_reply: true,
  due_at: null, overdue: false, last_message_at: ago(10), created_at: ago(20), assignee: null,
});

const ROWS = [
  row('q-1', 'Облокулов Шахноза', 'NORMAL', 'VACATION'),
  row('q-2', 'Назаров Фаррух', 'URGENT', 'ATTENDANCE'),
  row('q-3', 'Каримова Севара', 'HIGH', 'SALARY'),
  row('q-4', 'Турсунов Темур', 'LOW', 'SALARY'),
];

const ACTIONS = {
  take: true, assign: true, priority: true, category: true, wait: true,
  start: false, close: true, reopen: false, reply: true, draft: true,
};

const question = (base = ROWS[0]!) => ({
  ...base,
  question_text: 'Вопрос', channel: 'TELEGRAM', first_response_at: null,
  closed_at: null, closed_by: null, close_reason: null,
  telegram: { connected: true, reason: null, status: 'ACTIVE' },
  messages: [{
    id: 'm-1', kind: 'EMPLOYEE', source: 'TELEGRAM', body: 'Добрый день', event: null, details: null,
    author: { type: 'employee', id: base.employee.id, name: base.employee.full_name },
    created_at: ago(10), delivery: null,
  }],
  draft: null,
  actions: ACTIONS,
});

const COUNTS = {
  statuses: { NEW: 4, IN_PROGRESS: 0, WAITING_EMPLOYEE: 0, CLOSED: 0 },
  total: 4,
  quick: { all: 4, unanswered: 4, mine: 0, urgent: 1, unread: 0 },
};

/** Сеть как сервер: список сужается по `priority` и `category` (через запятую). */
function network() {
  return fakeNetwork((path, call) => {
    if (path.includes('/auth/')) return json(200, HR);
    if (path.includes('/knowledge/escalations/counts')) return json(200, COUNTS);
    if (path.includes('/knowledge/escalations/assignees')) return json(200, { items: [] });
    if (path.includes('/context/')) return json(404, { error: { code: 'not_found', message: 'нет' } });
    if (path.includes('/read/')) return json(200, { id: 'q-1', unread: false });
    const action = path.match(/\/knowledge\/escalations\/(q-\d+)\/(priority|category)\/$/);
    if (action && call.method === 'POST') {
      const body = call.body as Record<string, string>;
      const allowed = action[2] === 'priority' ? SERVER_PRIORITIES : SERVER_CATEGORIES;
      if (!allowed.includes(body[action[2]!]!)) {
        return json(400, { error: { code: 'validation_failed', message: 'Неизвестное значение' } });
      }
      return json(200, { ...question(), [action[2]!]: body[action[2]!] });
    }
    if (/\/knowledge\/escalations\/q-\d+\/?$/.test(path)) return json(200, question());
    if (/\/knowledge\/escalations\/(\?|$)/.test(path)) {
      const asked = new URL(path, 'http://x').searchParams;
      const within = (key: 'priority' | 'category', value: string) =>
        !asked.get(key) || asked.get(key)!.split(',').includes(value);
      const items = ROWS.filter((one) => within('priority', one.priority) && within('category', one.category));
      return json(200, { items, next_cursor: null, has_more: false });
    }
    if (path.includes('/offices')) return json(200, { items: [] });
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

const lists = (calls: Call[]) =>
  calls.filter((call) => call.method === 'GET' && /\/knowledge\/escalations\/(\?|$)/.test(call.url));
const counts = (calls: Call[]) =>
  calls.filter((call) => call.method === 'GET' && call.url.includes('/knowledge/escalations/counts'));
const posts = (calls: Call[], action: string) =>
  calls.filter((call) => call.method === 'POST' && call.url.includes(`/${action}/`));
const queueNames = () =>
  within(screen.getByRole('region', { name: 'Очередь обращений' }))
    .queryAllByRole('button')
    .map((one) => one.textContent ?? '')
    .filter((text) => ROWS.some((one) => text.includes(one.employee.full_name)));

async function openMenu() {
  fireEvent.click(await screen.findByRole('button', { name: 'Другие действия' }));
  return screen.findByRole('group', { name: 'Приоритет' });
}

describe('меню «три точки»', () => {
  test('каждый пункт приоритета нажимается и отправляет значение, которое знает сервер', async () => {
    const calls = network();
    renderApp('/questions?id=q-1');
    const group = await openMenu();
    const names = within(group).getAllByRole('menuitemradio').map((one) => one.textContent);
    expect(names).toEqual(['Низкий', 'Обычный', 'Высокий', 'Срочный']);
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Приоритет' })).toBeNull());

    for (const [name, value] of [['Низкий', 'LOW'], ['Высокий', 'HIGH'], ['Срочный', 'URGENT']] as const) {
      const menu = await openMenu();
      const item = within(menu).getByRole('menuitemradio', { name });
      expect(item.hasAttribute('disabled')).toBe(false);
      const before = posts(calls, 'priority').length;
      fireEvent.click(item);
      await waitFor(() => expect(posts(calls, 'priority')).toHaveLength(before + 1));
      const body = posts(calls, 'priority').at(-1)!.body as { priority: string };
      expect(body).toEqual({ priority: value });
      expect(SERVER_PRIORITIES).toContain(body.priority);
      // Итог виден сразу: приоритет в строке очереди не отображается.
      expect(await screen.findByText(`Приоритет: ${name}`)).toBeTruthy();
    }
  });

  test('выбранный сейчас пункт не серый: закрывает меню без запроса', async () => {
    const calls = network();
    renderApp('/questions?id=q-1');
    const group = await openMenu();
    const current = within(group).getByRole('menuitemradio', { name: 'Обычный' });
    expect(current.getAttribute('aria-checked')).toBe('true');
    expect(current.hasAttribute('disabled')).toBe(false);
    fireEvent.click(current);
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Приоритет' })).toBeNull());
    expect(posts(calls, 'priority')).toHaveLength(0);
  });

  test('каждый пункт категории — значение сервера', async () => {
    const calls = network();
    renderApp('/questions?id=q-1');
    await openMenu();
    const group = screen.getByRole('group', { name: 'Категория' });
    const items = within(group).getAllByRole('menuitemradio');
    expect(items).toHaveLength(SERVER_CATEGORIES.length);
    expect(items.every((one) => !one.hasAttribute('disabled'))).toBe(true);
    fireEvent.click(within(group).getByRole('menuitemradio', { name: 'Зарплата' }));
    await waitFor(() => expect(posts(calls, 'category')).toHaveLength(1));
    expect(posts(calls, 'category')[0]!.body).toEqual({ category: 'SALARY' });
  });
});

describe('фильтр очереди', () => {
  test('один приоритет: уходит параметром, очередь сужается, счётчик на кнопке', async () => {
    const calls = network();
    renderApp('/questions');
    await screen.findAllByText('Назаров Фаррух');
    expect(queueNames()).toHaveLength(4);

    fireEvent.click(screen.getByRole('button', { name: 'Фильтр' }));
    const dialog = await screen.findByRole('dialog', { name: 'Фильтр обращений' });
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Срочный' }));

    await waitFor(() => expect(lists(calls).at(-1)?.url).toContain('priority=URGENT'));
    // Счётчики вкладок считаются с тем же отбором.
    await waitFor(() => expect(counts(calls).at(-1)?.url).toContain('priority=URGENT'));
    await waitFor(() => expect(queueNames()).toHaveLength(1));
    expect(queueNames()[0]).toContain('Назаров Фаррух');
    expect(screen.getByRole('button', { name: 'Фильтр, выбрано: 1' })).toBeTruthy();
  });

  test('несколько приоритетов и категория — через запятую, в порядке сервера; сброс', async () => {
    const calls = network();
    renderApp('/questions');
    await screen.findAllByText('Назаров Фаррух');

    fireEvent.click(screen.getByRole('button', { name: 'Фильтр' }));
    const dialog = await screen.findByRole('dialog', { name: 'Фильтр обращений' });
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Срочный' }));
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Высокий' }));
    await waitFor(() => expect(lists(calls).at(-1)?.url).toContain(`priority=${encodeURIComponent('HIGH,URGENT')}`));
    await waitFor(() => expect(queueNames()).toHaveLength(2));

    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Зарплата' }));
    await waitFor(() => expect(lists(calls).at(-1)?.url).toContain('category=SALARY'));
    await waitFor(() => expect(queueNames()).toHaveLength(1));
    expect(queueNames()[0]).toContain('Каримова Севара');
    expect(screen.getByRole('button', { name: 'Фильтр, выбрано: 3' })).toBeTruthy();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Сбросить' }));
    await waitFor(() => expect(queueNames()).toHaveLength(4));
    const last = lists(calls).at(-1)!.url;
    expect(last).not.toContain('priority=');
    expect(last).not.toContain('category=');
  });

  test('выбор из адреса; неизвестное значение отбрасывается, а не ломает очередь', async () => {
    const calls = network();
    renderApp('/questions?priority=HIGH,BOGUS&category=SALARY');
    await waitFor(() => expect(lists(calls).length).toBeGreaterThan(0));
    const url = lists(calls).at(-1)!.url;
    expect(url).toContain('priority=HIGH');
    expect(url).not.toContain('BOGUS');
    expect(url).toContain('category=SALARY');
    expect(await screen.findByRole('button', { name: 'Фильтр, выбрано: 2' })).toBeTruthy();
  });

  test('клавиатура: фокус в окне, Esc закрывает и возвращает фокус на кнопку', async () => {
    network();
    renderApp('/questions');
    await screen.findAllByText('Назаров Фаррух');
    const button = screen.getByRole('button', { name: 'Фильтр' });
    fireEvent.click(button);
    const dialog = await screen.findByRole('dialog', { name: 'Фильтр обращений' });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    expect(button.getAttribute('aria-expanded')).toBe('true');

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Фильтр обращений' })).toBeNull());
    expect(document.activeElement).toBe(button);
  });

  test('клик мимо окна закрывает его', async () => {
    network();
    renderApp('/questions');
    await screen.findAllByText('Назаров Фаррух');
    fireEvent.click(screen.getByRole('button', { name: 'Фильтр' }));
    await screen.findByRole('dialog', { name: 'Фильтр обращений' });
    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Фильтр обращений' })).toBeNull());
  });
});

describe('состояние обращения', () => {
  test('в шапке и в строке — цветной текст без плашки', async () => {
    network();
    renderApp('/questions?id=q-1');
    const talk = await screen.findByRole('region', { name: 'Переписка' });
    const head = await within(talk).findByText('Новое', { selector: '.tk-status' });
    expect(head.className).toContain('tk-status--NEW');

    const css = readFileSync(join(__dirname, '..', 'src', 'styles', 'questions.css'), 'utf8');
    const rules = [...css.matchAll(/([^{}]*\.tk-status[^{}]*)\{([^}]*)\}/g)];
    expect(rules.length).toBeGreaterThan(0);
    for (const [, selector, body] of rules) {
      const fills = [...body!.matchAll(/background(?:-color)?\s*:\s*([^;]+)/g)].map((one) => one[1]!.trim());
      expect(fills.every((one) => one === 'none'), `${selector!.trim()} — ${fills.join(', ')}`).toBe(true);
    }
  });
});
