/**
 * «Заявки»: вкладки и фильтр «Статус» делят очередь на непересекающиеся
 * группы.
 *
 * Жалоба: под «На рассмотрении» стояли вместе больничные без справки,
 * со справкой на проверке и с возвращённой справкой; вкладка «Отмена»
 * не показывала отклонённую заявку (она отбирала заявки на отмену
 * отпуска); «Требуют решения» показывала возвращённые на исправление.
 *
 * Сервер подменён (`queue-fake.ts`) — отбор по тем же параметрам, что у
 * настоящего; совпадение с сервером проверяет
 * `django_tests/test_requests_groups.py`.
 */

import { screen, waitFor, within, fireEvent } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, fakeNetwork, json, pick, renderApp, type Call } from './helpers';
import { ROWS, serveQueue, type Row } from './queue-fake';

// К общей доске — отклонённый больничный и отозванный отпуск.
const BOARD: Row[] = [
  ...ROWS,
  { id: 'sick-rejected', name: 'Больничный Отклонён', kind: 'absence', type: 'SICK_LEAVE', requestKind: 'CREATE',
    status: 'REJECTED', stage: 'REJECTED', paper: 'REJECTED', office: 'o-1' },
  { id: 'leave-cancelled', name: 'Отпуск Отозван', kind: 'absence', type: 'ANNUAL_LEAVE', requestKind: 'CREATE',
    status: 'CANCELLED', stage: 'CANCELLED', office: 'o-1' },
  { id: 'corr-rejected', name: 'Исправление Отклонено', kind: 'correction', status: 'REJECTED', office: 'o-1' },
];

function network() {
  return fakeNetwork((path) => {
    const url = new URL(path, 'http://x');
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/requests/counts')) return json(200, { open: 0, all: 0, leave: 0, sick: 0, fixes: 0, rejected: 0 });
    if (url.pathname.endsWith('/requests')) return json(200, serveQueue(url, BOARD));
    return json(200, { items: [], next_cursor: null, has_more: false });
  });
}

const queue = () => screen.getByRole('region', { name: 'Очередь заявок' });
const names = () => [...queue().querySelectorAll('.rq-row__who b')].map((one) => one.textContent).sort();
const rowOf = (name: string) => within(queue()).getByText(name).closest('.rq-row') as HTMLElement;
const sentQueue = (calls: Call[]) =>
  calls.filter((c) => new URL(c.url, 'http://x').pathname.endsWith('/requests'));

async function settle(expected: string[]) {
  await waitFor(() => expect(names()).toEqual([...expected].sort()));
}

describe('вкладки', () => {
  test('«Требуют решения» — только то, что ждёт кадровика', async () => {
    network();
    renderApp('/requests');

    await settle(['Справка Напроверке', 'Справка Принята', 'Отпуск Ждёт', 'Отпуск Бухара', 'Исправление Ждёт']);
    // Без справки и с возвращённой справкой ждут сотрудника, а не HR.
    expect(within(queue()).queryByText('Ждёт Справку')).toBeNull();
    expect(within(queue()).queryByText('Вернули Справку')).toBeNull();
  });

  test('«Исправления» — все, где надо исправить: возвращённая справка и исправления отметок', async () => {
    network();
    renderApp('/requests?tab=fixes');

    await settle(['Вернули Справку', 'Исправление Ждёт', 'Исправление Рассмотрено', 'Исправление Отклонено']);
    // Возвращённая справка подписана как есть — не «Отклонён» и не «Отменён».
    expect(within(rowOf('Вернули Справку')).getByText('Нужны исправления')).toBeTruthy();
  });

  test('«Отклонённые» — окончательный отказ; исправление и отзыв сюда не попадают', async () => {
    const calls = network();
    renderApp('/requests?tab=rejected');

    await settle(['Отпуск Решён', 'Больничный Отклонён', 'Исправление Отклонено']);
    expect(within(queue()).queryByText('Вернули Справку')).toBeNull();
    expect(within(queue()).queryByText('Отпуск Отозван')).toBeNull();
    expect(new URL(sentQueue(calls).pop()!.url, 'http://x').searchParams.get('status')).toBe('REJECTED');
  });

  test('вкладка подписана «Отклонённые», старая ссылка tab=cancel ведёт туда же', async () => {
    network();
    renderApp('/requests?tab=cancel');

    await settle(['Отпуск Решён', 'Больничный Отклонён', 'Исправление Отклонено']);
    const selected = within(screen.getByRole('tablist', { name: 'Вид заявок' })).getAllByRole('tab')
      .filter((tab) => tab.getAttribute('aria-selected') === 'true');
    expect(selected.map((tab) => tab.textContent?.replace(/\d+/g, ''))).toEqual(['Отклонённые']);
  });
});

describe('фильтр «Статус» во вкладке «Больничные»', () => {
  const SICK: Record<string, string[]> = {
    'Ожидают документа': ['Ждёт Справку'],
    'На рассмотрении': ['Справка Напроверке', 'Справка Принята'],
    'Исправление': ['Вернули Справку'],
    'Одобрена': ['Больничный Подтверждён'],
    'Отклонена': ['Больничный Отклонён'],
  };

  test.each(Object.entries(SICK))('«%s» — только своя группа', async (option, expected) => {
    network();
    renderApp('/requests?tab=sick');
    await waitFor(() => expect(names().length).toBeGreaterThan(0));

    await pick('Статус', option);
    await settle(expected);
  });

  test('группы не пересекаются и вместе дают всю вкладку', async () => {
    network();
    renderApp('/requests?tab=sick');
    await settle(['Ждёт Справку', 'Вернули Справку', 'Справка Напроверке', 'Справка Принята', 'Больничный Подтверждён', 'Больничный Отклонён']);

    const seen: string[] = [];
    for (const option of Object.keys(SICK)) {
      await pick('Статус', option);
      await settle(SICK[option]!);
      seen.push(...names());
    }
    expect(seen.sort()).toEqual(Object.values(SICK).flat().sort());
  });

  test('пункт уходит серверу стадией, а не статусами базы', async () => {
    const calls = network();
    renderApp('/requests?tab=sick');
    await waitFor(() => expect(names().length).toBeGreaterThan(0));

    await pick('Статус', 'Исправление');
    await settle(['Вернули Справку']);
    const sent = new URL(sentQueue(calls).pop()!.url, 'http://x').searchParams;
    expect(sent.get('stage')).toBe('NEEDS_FIX');
    expect(sent.get('status')).toBeNull();
  });
});

describe('фильтр «Статус» во вкладке «Отпуска»', () => {
  test.each([
    ['На рассмотрении', ['Отпуск Ждёт', 'Отпуск Бухара']],
    ['Отклонена', ['Отпуск Решён']],
    ['Отозвана', ['Отпуск Отозван']],
  ] as const)('«%s»', async (option, expected) => {
    network();
    renderApp('/requests?tab=leave');
    await waitFor(() => expect(names().length).toBeGreaterThan(0));

    await pick('Статус', option);
    await settle([...expected]);
  });

  test('у отпуска нет ни «ожидают документа», ни «исправления»', async () => {
    network();
    renderApp('/requests?tab=leave');
    await waitFor(() => expect(names().length).toBeGreaterThan(0));

    await pick('Статус', 'Ожидают документа');
    expect(await screen.findByText('По выбранным условиям заявок нет')).toBeTruthy();
    await pick('Статус', 'Исправление');
    expect(await screen.findByText('По выбранным условиям заявок нет')).toBeTruthy();
  });
});

describe('вкладка и пункт фильтра согласованы', () => {
  test('«Требуют решения» + «Исправление» — пусто, без запроса за чужой группой', async () => {
    const calls = network();
    renderApp('/requests');
    await waitFor(() => expect(names().length).toBeGreaterThan(0));
    const before = sentQueue(calls).length;

    await pick('Статус', 'Исправление');
    expect(await screen.findByText('По выбранным условиям заявок нет')).toBeTruthy();
    expect(sentQueue(calls).length).toBe(before);
  });

  test('ручная смена вкладки сбрасывает пункт фильтра', async () => {
    const calls = network();
    renderApp('/requests?tab=sick&status=fix');
    await settle(['Вернули Справку']);

    fireEvent.click(screen.getByRole('tab', { name: /Отклонённые/ }));
    await settle(['Отпуск Решён', 'Больничный Отклонён', 'Исправление Отклонено']);
    expect(new URL(sentQueue(calls).pop()!.url, 'http://x').searchParams.get('stage')).toBeNull();
  });
});
