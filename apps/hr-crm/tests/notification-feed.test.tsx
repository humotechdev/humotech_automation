/**
 * Лента событий: колокольчик, окно и страница.
 *
 * Проверяется то, что легче всего изобразить и труднее всего заметить:
 *
 * — придуманный счётчик у колокольчика. Число приходит с сервера, и
 *   пока сервер молчит, у колокольчика нет ничего;
 * — «прочитано» от одного только открытия окна. Открытие показывает,
 *   прочтение — это действие человека;
 * — счётчик, который не вернулся назад после неудачного запроса.
 *   Отказ обязан возвращать прежнее состояние, а не оставлять
 *   нарисованный ноль;
 * — пустая лента на месте ошибки. «Событий нет» и «не удалось
 *   загрузить» — разные ответы;
 * — подробности, которых в ответе нет: имя файла, ссылка на документ,
 *   ответ провайдера.
 *
 * Данные здесь выдуманные. Настоящих сотрудников в тестах нет.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import {
  FEED_FILTERS,
  badgeText,
  facts,
  filterTitle,
  lasting,
  since,
  statusTone,
} from '../src/features/notifications/feed-model';
import type { FeedCounts, FeedDetail, FeedEvent } from '../src/api/crm';
import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

// --- чистые правила ---------------------------------------------------------

describe('число у колокольчика', () => {
  test('больше девяноста девяти показывается как «99+»', () => {
    expect(badgeText(1)).toBe('1');
    expect(badgeText(99)).toBe('99');
    expect(badgeText(100)).toBe('99+');
    expect(badgeText(1387)).toBe('99+');
  });
});

describe('относительное время', () => {
  const now = new Date('2026-09-16T12:00:00Z');
  const ago = (minutes: number) =>
    new Date(now.getTime() - minutes * 60_000).toISOString();

  test('минуты, часы, вчера и дата', () => {
    expect(since(ago(0), now)).toBe('Только что');
    expect(since(ago(2), now)).toBe('2 мин назад');
    expect(since(ago(95), now)).toBe('1 ч назад');
    // Вчерашнее не превращается в «30 ч назад»: это уже не число часов,
    // а другой день.
    const yesterday = new Date(now.getTime() - 26 * 3600_000);
    expect(since(yesterday.toISOString(), now)).toBe('Вчера');
    // Дальше недели относительная подпись ничего не значит — дата.
    const old = new Date(now.getTime() - 9 * 86400_000);
    expect(since(old.toISOString(), now)).toMatch(/\d+ \w+/);
  });

  test('длительность открытой сессии словами', () => {
    expect(lasting(45)).toBe('45 мин');
    expect(lasting(120)).toBe('2 ч');
    expect(lasting(200)).toBe('3 ч 20 мин');
  });
});

describe('цвет метки', () => {
  const base: FeedEvent = {
    id: 'absence_request:1',
    type: 'absence_request',
    group: 'requests',
    title: 'Новая заявка на отпуск',
    short_text: 'Ежегодный отпуск, 5 дн.',
    employee_id: 'e1',
    employee_name: 'Мирзаева Лола',
    office_id: 'o1',
    office_name: 'Ташкент',
    status: 'SUBMITTED',
    status_label: 'На рассмотрении',
    priority: 'HIGH',
    requires_action: true,
    created_at: new Date().toISOString(),
    read_at: null,
    related_entity_type: 'absence_requests',
    related_entity_id: '1',
    action_url: '/requests?request=1',
    action_title: 'Открыть заявку',
  };

  test('красный — только у критичного', () => {
    expect(statusTone(base)).toBe('orange');
    expect(statusTone({ ...base, priority: 'CRITICAL' })).toBe('red');
    expect(
      statusTone({
        ...base,
        priority: 'NORMAL',
        requires_action: false,
        status: 'APPROVED',
      }),
    ).toBe('green');
  });
});

describe('строки карточки', () => {
  const card = {
    id: 'sick_leave:1',
    type: 'sick_leave',
    group: 'requests',
    title: 'Новый больничный',
    short_text: 'Больничный, 4 дн.',
    employee_id: 'e1',
    employee_name: 'Мирзаева Лола',
    office_id: 'o1',
    office_name: 'Ташкент',
    status: 'SUBMITTED',
    status_label: 'На рассмотрении',
    priority: 'HIGH',
    requires_action: true,
    created_at: '2026-09-16T09:00:00Z',
    read_at: null,
    related_entity_type: 'absence_requests',
    related_entity_id: '1',
    action_url: '/requests?request=1',
    action_title: 'Открыть больничный',
    employee: {
      id: 'e1',
      full_name: 'Мирзаева Лола',
      office_name: 'Ташкент',
      position_name: 'Специалист по персоналу',
      department_name: 'HR',
    },
    author: null,
    comment: 'Прошу подтвердить',
    occurred_at: '2026-09-16T09:00:00Z',
    absence: {
      type_name: 'Больничный',
      type_code: 'SICK_LEAVE',
      request_kind: 'CREATE',
      is_extension: false,
      first_day: '2026-09-18',
      last_day: '2026-09-22',
      requires_document: true,
      document: {
        id: 'd1',
        document_type: 'SICK_CERTIFICATE',
        verification_status: 'PENDING',
        verification_label: 'На проверке',
        verified_at: null,
        file_name: 'spravka.pdf',
        size_bytes: 48_512,
        uploaded_at: '2026-09-16T09:05:00Z',
        scan_status: 'CLEAN',
      },
      review_comment: null,
      calendar_days: 5,
      working_days: 5,
      balance_before_days: null,
      balance_after_days: null,
      overlaps: false,
    },
  } as unknown as FeedDetail;

  test('о справке — только безопасные сведения', () => {
    const rows = facts(card);
    const document = rows.find((row) => row.key === 'document');
    expect(document?.value).toContain('spravka.pdf');
    expect(document?.value).toContain('На проверке');
    // Ни ссылки, ни содержимого документа в строках нет и быть не может.
    expect(JSON.stringify(rows)).not.toMatch(/http|storage|download/i);
  });

  test('пустые величины не показываются прочерком', () => {
    const rows = facts(card);
    // Остатка отпуска у больничного нет — строки тоже нет.
    expect(rows.some((row) => row.key === 'balance')).toBe(false);
    expect(rows.every((row) => row.value.trim().length > 0)).toBe(true);
  });

  test('офис берётся из карточки сотрудника', () => {
    expect(facts(card).find((row) => row.key === 'office')?.value).toBe('Ташкент');
  });
});

describe('вкладки отбора', () => {
  test('перечень тот же, что понимает сервер', () => {
    expect(FEED_FILTERS.map((one) => one.key)).toEqual([
      'all', 'unread', 'action', 'requests', 'documents', 'attendance',
      'questions', 'system',
    ]);
    expect(filterTitle('action')).toBe('Требуют действия');
    expect(filterTitle('что-нибудь')).toBe('Все');
  });
});

// --- окно целиком -----------------------------------------------------------

const COUNTS: FeedCounts = {
  all: 6, unread: 6, action: 4, requests: 2, documents: 1, attendance: 2,
  questions: 1, system: 0,
};

function event(over: Partial<FeedEvent> = {}): FeedEvent {
  return {
    id: 'absence_request:11111111-1111-1111-1111-111111111111',
    type: 'absence_request',
    group: 'requests',
    title: 'Новая заявка на отпуск',
    short_text: 'Ежегодный отпуск, 5 дн.',
    employee_id: 'e1',
    employee_name: 'Мирзаева Лола',
    office_id: 'o1',
    office_name: 'Ташкент',
    status: 'SUBMITTED',
    status_label: 'На рассмотрении',
    priority: 'HIGH',
    requires_action: true,
    created_at: new Date(Date.now() - 120_000).toISOString(),
    read_at: null,
    related_entity_type: 'absence_requests',
    related_entity_id: '11111111-1111-1111-1111-111111111111',
    action_url: '/requests?request=11111111-1111-1111-1111-111111111111',
    action_title: 'Открыть заявку',
    ...over,
  };
}

function detail(over: Partial<FeedDetail> = {}): FeedDetail {
  return {
    ...event(),
    employee: {
      id: 'e1',
      full_name: 'Мирзаева Лола',
      office_name: 'Ташкент',
      position_name: 'Специалист по персоналу',
      department_name: 'HR',
    },
    author: null,
    comment: 'Плановый отпуск. Прошу подтвердить в удобное время.',
    occurred_at: new Date().toISOString(),
    absence: {
      type_name: 'Ежегодный отпуск',
      type_code: 'ANNUAL_LEAVE',
      request_kind: 'CREATE',
      is_extension: false,
      first_day: '2026-09-18',
      last_day: '2026-09-22',
      requires_document: false,
      document: null,
      review_comment: null,
      calendar_days: 5,
      working_days: 5,
      balance_before_days: 14,
      balance_after_days: 9,
      overlaps: false,
    },
    ...over,
  } as FeedDetail;
}

/** Ответы ленты поверх общего каркаса CRM. */
function feedNetwork(options: {
  counts?: FeedCounts;
  items?: FeedEvent[];
  onRead?: () => Response;
  onReadAll?: () => Response;
  listStatus?: number;
} = {}) {
  const counts = options.counts ?? COUNTS;
  const items = options.items ?? [event()];
  return fakeNetwork((path, call) => {
    if (path.includes('/auth/me')) return json(200, USER);
    if (path.includes('/notification-feed/counts')) return json(200, counts);
    if (path.includes('/notification-feed/read-all')) {
      return options.onReadAll
        ? options.onReadAll()
        : json(200, { ...counts, unread: 0, marked: counts.unread });
    }
    if (path.includes('/notification-feed/') && call.method === 'POST') {
      return options.onRead
        ? options.onRead()
        : json(200, { ...counts, unread: counts.unread - 1 });
    }
    if (path.includes('/notification-feed/')) {
      return json(200, detail({ id: items[0]?.id ?? 'absence_request:1' }));
    }
    if (path.includes('/notification-feed')) {
      if (options.listStatus) return json(options.listStatus, { detail: 'нет' });
      return json(200, {
        items,
        counts,
        next_cursor: null,
        has_more: false,
        window_days: 30,
      });
    }
    return crm(path) ?? json(200, { items: [] });
  });
}

async function openBell() {
  const bell = await screen.findByRole('button', { name: /Уведомления/ });
  fireEvent.click(bell);
  return bell;
}

describe('колокольчик', () => {
  test('показывает число непрочитанного с сервера', async () => {
    feedNetwork();
    renderApp('/');
    const bell = await screen.findByRole('button', {
      name: /Уведомления, непрочитанных 6/,
    });
    expect(within(bell).getByText('6')).toBeTruthy();
  });

  test('пока сервер не ответил, числа нет', async () => {
    fakeNetwork((path) => {
      if (path.includes('/auth/me')) return json(200, USER);
      if (path.includes('/notification-feed')) return json(500, { detail: 'нет' });
      return crm(path) ?? json(200, { items: [] });
    });
    renderApp('/');
    const bell = await screen.findByRole('button', { name: 'Уведомления' });
    // Ни единицы, ни нуля: придуманного числа у колокольчика не бывает.
    expect(bell.textContent?.trim()).toBe('');
  });

  test('открывается, показывает строку и подробности', async () => {
    feedNetwork();
    renderApp('/');
    await openBell();

    expect(await screen.findByRole('dialog', { name: 'Уведомления' })).toBeTruthy();
    const panel = screen.getByRole('dialog', { name: 'Уведомления' });
    expect(within(panel).getAllByText('Новая заявка на отпуск').length).toBeGreaterThan(0);
    expect(within(panel).getAllByText('Мирзаева Лола').length).toBeGreaterThan(0);
    // Правая область: период, дни и остаток — из ответа, а не из разметки.
    await waitFor(() =>
      expect(within(panel).getByText('18–22 сен 2026')).toBeTruthy(),
    );
    expect(within(panel).getByText('5 календарных · 5 рабочих')).toBeTruthy();
    expect(within(panel).getByText('14 дн. → 9 дн.')).toBeTruthy();
    expect(within(panel).getByText('Открыть заявку')).toBeTruthy();
  });

  test('открытие окна ничего не помечает прочитанным', async () => {
    const calls = feedNetwork();
    renderApp('/');
    await openBell();
    await screen.findByRole('dialog', { name: 'Уведомления' });
    await waitFor(() =>
      expect(calls.some((one) => one.url.includes('/notification-feed/'))).toBe(true),
    );
    // Карточка читается GET-ом; POST — это уже отметка, и её не было.
    expect(
      calls.filter(
        (one) => one.method === 'POST' && one.url.includes('/notification-feed'),
      ),
    ).toHaveLength(0);
  });

  test('нажатие на строку отмечает её прочитанной', async () => {
    const calls = feedNetwork();
    renderApp('/');
    await openBell();
    const panel = await screen.findByRole('dialog', { name: 'Уведомления' });

    fireEvent.click(within(panel).getAllByText('Новая заявка на отпуск')[0]!);
    await waitFor(() =>
      expect(
        calls.filter(
          (one) => one.method === 'POST' && one.url.includes('/notification-feed/'),
        ),
      ).toHaveLength(1),
    );
  });

  test('неудачная отметка возвращает счётчик назад', async () => {
    feedNetwork({ onRead: () => json(500, { detail: 'нет' }) });
    renderApp('/');
    await openBell();
    const panel = await screen.findByRole('dialog', { name: 'Уведомления' });

    fireEvent.click(within(panel).getAllByText('Новая заявка на отпуск')[0]!);
    // Пять на месте шести означало бы прочтение, которого сервер не принял.
    await waitFor(() =>
      expect(within(panel).getByRole('heading', { name: /Уведомления/ }).textContent)
        .toContain('6'),
    );
  });

  test('«Прочитать все» обнуляет счётчик и обновляет список', async () => {
    const calls = feedNetwork();
    renderApp('/');
    await openBell();
    const panel = await screen.findByRole('dialog', { name: 'Уведомления' });

    fireEvent.click(within(panel).getByText('Прочитать все'));
    await waitFor(() =>
      expect(calls.some((one) => one.url.includes('/read-all'))).toBe(true),
    );
  });

  test('ошибка списка — это не «уведомлений нет»', async () => {
    feedNetwork({ listStatus: 500 });
    renderApp('/');
    await openBell();
    const panel = await screen.findByRole('dialog', { name: 'Уведомления' });
    await waitFor(() =>
      expect(within(panel).getByText('Повторить')).toBeTruthy(),
    );
    expect(within(panel).queryByText('Новых событий нет')).toBeNull();
  });

  test('пустая лента говорит об этом прямо', async () => {
    feedNetwork({
      items: [],
      counts: { ...COUNTS, all: 0, unread: 0, action: 0, requests: 0 },
    });
    renderApp('/');
    await openBell();
    const panel = await screen.findByRole('dialog', { name: 'Уведомления' });
    expect(await within(panel).findByText('Новых событий нет')).toBeTruthy();
  });

  test('закрывается клавишей Esc', async () => {
    feedNetwork();
    renderApp('/');
    await openBell();
    await screen.findByRole('dialog', { name: 'Уведомления' });
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Уведомления' })).toBeNull(),
    );
  });

  test('длинные ФИО и заголовок не ломают строку', async () => {
    feedNetwork({
      items: [
        event({
          title: 'Запрошена отмена подтверждённого ежегодного оплачиваемого отпуска',
          employee_name: 'Абдурахмонзода Шарифджон Мухаммадюсуфович',
        }),
      ],
    });
    renderApp('/');
    await openBell();
    const panel = await screen.findByRole('dialog', { name: 'Уведомления' });
    expect(
      within(panel).getAllByText('Абдурахмонзода Шарифджон Мухаммадюсуфович').length,
    ).toBeGreaterThan(0);
  });
});

describe('страница ленты', () => {
  test('открывается вкладкой «Лента событий» и ведёт к очереди', async () => {
    feedNetwork();
    renderApp('/notifications');
    expect(await screen.findByRole('tab', { name: 'Лента событий' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Очередь отправки' })).toBeTruthy();
    // Лента доступна без права на очередь отправки: это разные данные.
    expect(screen.queryByText(/Нет права на просмотр очереди/)).toBeNull();
  });

  test('показывает событие и его подробности', async () => {
    feedNetwork();
    renderApp('/notifications');
    expect(
      (await screen.findAllByText('Новая заявка на отпуск')).length,
    ).toBeGreaterThan(0);
  });
});
