/**
 * Страница «Уведомления» после переделки: одна карточка, вкладки
 * «Лента событий» и «Очередь отправки», таблица событий с пагинацией.
 *
 * Проверяется то, что легче всего изобразить и труднее всего заметить:
 *
 * — итог, посчитанный по загруженной странице, а не взятый с сервера;
 * — переход на страницу без настоящего курсора;
 * — устаревший ответ, перезаписавший текущий отбор;
 * — погасшая точка «не прочитано» при отказе сервера;
 * — переход по адресу, который прислали, но который не ведёт в CRM;
 * — незнакомый вид события, роняющий страницу.
 *
 * Данные выдуманные.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, test } from 'vitest';

import type { FeedCounts, FeedEvent } from '../src/api/crm';
import {
  contextLine, dayLabel, initials, knownScope, pageNumbers, rangeLine,
  safeActionPath, scopeTotal, timeLabel, typeLabel,
} from '../src/features/notifications/feed-page';
import { forgetSticky } from '../src/features/shell/sticky';
import { USER, crm, fakeNetwork, json, pick, renderApp, type Call } from './helpers';

beforeEach(() => forgetSticky());

// --- чистые правила ---------------------------------------------------------

describe('правила страницы', () => {
  test('вид события словами, незнакомый — нейтрально', () => {
    expect(typeLabel({ type: 'sick_leave', group: 'requests' })).toBe('Заявка');
    expect(typeLabel({ type: 'attendance_correction', group: 'requests' })).toBe('Посещаемость');
    expect(typeLabel({ type: 'question', group: 'questions' })).toBe('Обращение');
    // Сервер уже присылает вид, которого нет в типах интерфейса.
    expect(typeLabel({ type: 'policy_declined' as FeedEvent['type'], group: 'documents' })).toBe('Документ');
    expect(typeLabel({ type: 'x_new' as FeedEvent['type'], group: 'zzz' as FeedEvent['group'] })).toBe('Событие');
  });

  test('адрес «Открыть» — только внутренний путь CRM', () => {
    expect(safeActionPath('/requests?request=1')).toBe('/requests?request=1');
    expect(safeActionPath('/questions?id=abc')).toBe('/questions?id=abc');
    expect(safeActionPath('/employees/5')).toBe('/employees/5');
    expect(safeActionPath('https://evil.example/x')).toBeNull();
    expect(safeActionPath('//evil.example/x')).toBeNull();
    expect(safeActionPath('javascript:alert(1)')).toBeNull();
    expect(safeActionPath('/\\evil')).toBeNull();
    expect(safeActionPath('/admin/secret')).toBeNull();
    expect(safeActionPath('')).toBeNull();
  });

  test('итог — из счётчиков сервера, неизвестный отбор — «Все»', () => {
    const counts: FeedCounts = { all: 45, unread: 7, action: 3, requests: 30, documents: 10, questions: 5 };
    expect(scopeTotal('unread', counts)).toBe(7);
    expect(scopeTotal('requests', counts)).toBe(30);
    expect(scopeTotal('what', counts)).toBe(45);
    expect(scopeTotal('all', null)).toBeNull();
    expect(knownScope('bogus')).toBe('all');
  });

  test('диапазон и номера страниц', () => {
    expect(rangeLine(1, 20, 45)).toBe('1–20 из 45 уведомлений');
    expect(rangeLine(3, 5, 45)).toBe('41–45 из 45 уведомлений');
    expect(rangeLine(1, 1, 1)).toBe('1–1 из 1 уведомления');
    expect(rangeLine(1, 0, 0)).toBe('');
    expect(pageNumbers(1, 3)).toEqual([1, 2, 3]);
    expect(pageNumbers(6, 12)).toEqual([1, null, 5, 6, 7, null, 12]);
  });

  test('дата и время — в поясе организации, без выдумок', () => {
    // 04:14 UTC — это 09:14 в Душанбе.
    expect(dayLabel('2026-09-25T04:14:00Z', 'Asia/Dushanbe')).toBe('25 сентября');
    expect(timeLabel('2026-09-25T04:14:00Z', 'Asia/Dushanbe')).toBe('09:14');
    expect(dayLabel('', 'Asia/Dushanbe')).toBe('');
    expect(dayLabel('not-a-date', 'Asia/Dushanbe')).toBe('');
  });

  test('инициалы и вторая строка без пустых частей', () => {
    expect(initials('Мирзаева Лола')).toBe('МЛ');
    expect(initials('')).toBe('');
    expect(contextLine({ employee_name: 'Мирзаева Лола', office_name: null, short_text: 'Отпуск, 5 дн.' }))
      .toBe('Мирзаева Лола · Отпуск, 5 дн.');
  });
});

// --- страница целиком ---------------------------------------------------------

function event(n: number, over: Partial<FeedEvent> = {}): FeedEvent {
  const id = `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
  return {
    id: `absence_request:${id}`,
    type: 'absence_request',
    group: 'requests',
    title: `Заявка на отпуск ${n}`,
    short_text: '18–22 сен',
    employee_id: `e-${n}`,
    employee_name: `Тестова Лола ${n}`,
    office_id: 'o-1',
    office_name: 'Тестовый офис',
    status: 'SUBMITTED',
    status_label: 'На рассмотрении',
    priority: 'HIGH',
    requires_action: true,
    created_at: '2026-09-25T04:14:00Z',
    read_at: '2026-09-25T05:00:00Z',
    related_entity_type: 'absence_requests',
    related_entity_id: id,
    action_url: `/requests?request=${id}`,
    action_title: 'Открыть заявку',
    ...over,
  };
}

const BASE_COUNTS: FeedCounts = { all: 45, unread: 1, action: 3, requests: 40, documents: 3, questions: 2 };

type Handler = (url: URL, call: Call) => Response | Promise<Response> | null;

/**
 * Сервер ленты с курсором `c<индекс>`: страница — срез списка, итог —
 * из `counts`, как у настоящего.
 */
function feedServer(options: {
  items?: (scope: string) => FeedEvent[];
  counts?: FeedCounts;
  own?: Handler;
} = {}) {
  const all = Array.from({ length: 45 }, (_, i) => event(i + 1, i === 0 ? { read_at: null } : {}));
  const itemsFor = options.items ?? (() => all);
  return fakeNetwork((path, call) => {
    const url = new URL(path, 'http://crm.test');
    const mine = options.own?.(url, call);
    if (mine) return mine;
    if (url.pathname.includes('/auth/me')) return json(200, USER);
    if (url.pathname.endsWith('/notification-feed/counts')) return json(200, options.counts ?? BASE_COUNTS);
    if (url.pathname.endsWith('/notification-feed/read-all')) {
      return json(200, { ...(options.counts ?? BASE_COUNTS), unread: 0, marked: 1 });
    }
    if (url.pathname.includes('/notification-feed/') && call.method === 'POST') {
      return json(200, { ...(options.counts ?? BASE_COUNTS), unread: 0 });
    }
    if (url.pathname.endsWith('/notification-feed')) {
      const scope = url.searchParams.get('scope') ?? 'all';
      const limit = Number(url.searchParams.get('limit') ?? '50');
      const cursor = url.searchParams.get('cursor');
      const start = cursor ? Number(cursor.slice(1)) : 0;
      const list = itemsFor(scope);
      const page = list.slice(start, start + limit);
      const more = list.length > start + limit;
      return json(200, {
        items: page,
        counts: options.counts ?? BASE_COUNTS,
        next_cursor: more ? `c${start + limit}` : null,
        has_more: more,
        window_days: 30,
      });
    }
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

/** Запросы самой страницы (колокольчик спрашивает `limit=1`). */
const pageCalls = (calls: Call[]) =>
  calls.filter((c) => {
    const url = new URL(c.url, 'http://crm.test');
    return url.pathname.endsWith('/notification-feed') && url.searchParams.get('limit') !== '1'
      && c.method === 'GET';
  }).map((c) => new URL(c.url, 'http://crm.test'));

const firstRow = async () => (await screen.findAllByTestId('feed-row'))[0]!;

describe('лента событий', () => {
  test('карточка с итогом сервера, вкладками и колонками', async () => {
    feedServer();
    renderApp('/notifications');

    const title = await screen.findByRole('heading', { name: /Уведомления/ });
    await waitFor(() => expect(title.textContent).toContain('45'));
    expect(screen.getByText('События по сотрудникам и заявкам')).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Лента событий' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tab', { name: 'Очередь отправки' })).toBeTruthy();

    const row = await firstRow();
    expect(within(row).getByText('Заявка на отпуск 1')).toBeTruthy();
    expect(within(row).getByText('Тестова Лола 1 · Тестовый офис · 18–22 сен')).toBeTruthy();
    expect(within(row).getByText('Заявка')).toBeTruthy();
    expect(within(row).getByText('25 сентября')).toBeTruthy();
    expect(within(row).getByText('09:14')).toBeTruthy();
    expect(within(row).getByRole('img', { name: 'Не прочитано' })).toBeTruthy();
    expect(within(row).getByText('Требует действия')).toBeTruthy();
    expect(row.className).toContain('ntf-row--unread');
    // На странице 20 строк, итог 45 — подпись говорит про весь набор.
    expect(screen.getAllByTestId('feed-row')).toHaveLength(20);
    expect(screen.getByText('1–20 из 45 уведомлений')).toBeTruthy();
  });

  test('следующая страница идёт по курсору сервера', async () => {
    const calls = feedServer();
    renderApp('/notifications');
    await firstRow();

    fireEvent.click(screen.getByRole('button', { name: 'Следующая страница' }));
    expect(await screen.findByText('21–40 из 45 уведомлений')).toBeTruthy();
    const last = pageCalls(calls).at(-1)!;
    expect(last.searchParams.get('cursor')).toBe('c20');
    expect(last.searchParams.get('limit')).toBe('20');
    expect(screen.getByText('Заявка на отпуск 21')).toBeTruthy();
  });

  test('дальняя страница — одним запросом с настоящим курсором', async () => {
    const calls = feedServer();
    renderApp('/notifications');
    await firstRow();

    fireEvent.click(screen.getByRole('button', { name: 'Страница 3' }));
    expect(await screen.findByText('41–45 из 45 уведомлений')).toBeTruthy();
    const last = pageCalls(calls).at(-1)!;
    // Курсор второй страницы уже известен — от него берутся две страницы
    // и показывается хвост; придуманного курсора нет.
    expect(last.searchParams.get('limit')).toBe('40');
    expect(last.searchParams.get('cursor')).toBe('c20');
    expect(screen.getAllByTestId('feed-row')).toHaveLength(5);
    expect(screen.getByText('Заявка на отпуск 41')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Следующая страница' })).toHaveProperty('disabled', true);
  });

  test('опустевшая последняя страница — шаг назад', async () => {
    let shrink = false;
    const all = Array.from({ length: 45 }, (_, i) => event(i + 1));
    feedServer({
      items: () => (shrink ? all.slice(0, 30) : all),
      own: (url) => {
        if (url.pathname.endsWith('/notification-feed') && url.searchParams.get('limit') === '40') {
          shrink = true;
        }
        return null;
      },
    });
    renderApp('/notifications');
    await firstRow();
    // Пока человек думал, часть событий закрыли: на третьей странице пусто.
    fireEvent.click(screen.getByRole('button', { name: 'Страница 3' }));
    expect(await screen.findByText(/^21–30 из 45/)).toBeTruthy();
  });

  test('отбор уходит на сервер; сегмент и вид исключают друг друга', async () => {
    const calls = feedServer();
    renderApp('/notifications');
    await firstRow();

    fireEvent.click(screen.getByRole('button', { name: 'Непрочитанные' }));
    await waitFor(() => expect(pageCalls(calls).at(-1)!.searchParams.get('scope')).toBe('unread'));
    expect(screen.getByRole('button', { name: 'Непрочитанные' }).getAttribute('aria-pressed')).toBe('true');

    await pick('Тип событий', 'Документы');
    await waitFor(() => expect(pageCalls(calls).at(-1)!.searchParams.get('scope')).toBe('documents'));
    // Сервер не умеет «непрочитанные документы» — сегмент снят.
    for (const name of ['Все', 'Непрочитанные', 'Требуют действия']) {
      expect(screen.getByRole('button', { name }).getAttribute('aria-pressed')).toBe('false');
    }

    fireEvent.click(screen.getByRole('button', { name: 'Требуют действия' }));
    await waitFor(() => expect(pageCalls(calls).at(-1)!.searchParams.get('scope')).toBe('action'));
  });

  test('устаревший ответ не перезаписывает текущий отбор', async () => {
    const gate: { open?: () => void } = {};
    const slowAll = Array.from({ length: 3 }, (_, i) => event(i + 1, { title: `Старое ${i + 1}` }));
    const unread = [event(99, { title: 'Свежее непрочитанное', read_at: null })];
    let first = true;
    feedServer({
      items: (scope) => (scope === 'unread' ? unread : slowAll),
      own: (url) => {
        if (url.pathname.endsWith('/notification-feed') && url.searchParams.get('limit') === '20'
          && (url.searchParams.get('scope') ?? 'all') === 'all' && first) {
          first = false;
          return new Promise<Response>((resolve) => {
            gate.open = () => resolve(json(200, {
              items: slowAll, counts: BASE_COUNTS, next_cursor: null, has_more: false, window_days: 30,
            }));
          });
        }
        return null;
      },
    });
    renderApp('/notifications');
    await screen.findByText('Загружаем ленту…');

    fireEvent.click(screen.getByRole('button', { name: 'Непрочитанные' }));
    expect(await screen.findByText('Свежее непрочитанное')).toBeTruthy();
    gate.open?.();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText('Свежее непрочитанное')).toBeTruthy();
    expect(screen.queryByText('Старое 1')).toBeNull();
  });

  test('«Открыть» отмечает прочтение и ведёт на запись', async () => {
    const calls = feedServer();
    renderApp('/notifications');
    const row = await firstRow();

    fireEvent.click(within(row).getByRole('button', { name: /^Открыть: / }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url.includes('/notification-feed/absence_request'))).toBe(true),
    );
    // Страница ленты ушла: открыт раздел записи.
    await waitFor(() => expect(screen.queryAllByTestId('feed-row')).toHaveLength(0));
  });

  test('запись исчезла — объяснение и обновление вместо перехода', async () => {
    feedServer({
      own: (url, call) => (call.method === 'POST' && url.pathname.includes('/notification-feed/absence_request')
        ? json(404, { error: { code: 'not_found', message: 'нет' } })
        : null),
    });
    renderApp('/notifications');
    const row = await firstRow();
    fireEvent.click(within(row).getByRole('button', { name: /^Открыть: / }));
    expect(await screen.findByText(/Событие больше недоступно/)).toBeTruthy();
    expect(screen.getAllByTestId('feed-row').length).toBeGreaterThan(0);
  });

  test('чужой адрес — кнопка выключена, перехода нет', async () => {
    feedServer({
      items: () => [event(1, { action_url: 'https://evil.example/steal' })],
      counts: { ...BASE_COUNTS, all: 1 },
    });
    renderApp('/notifications');
    const row = await firstRow();
    expect(within(row).getByRole('button', { name: /^Открыть: / })).toHaveProperty('disabled', true);
  });

  test('отметка из меню: точка гаснет только после ответа сервера', async () => {
    const calls = feedServer();
    renderApp('/notifications');
    const row = await firstRow();

    fireEvent.click(within(row).getByRole('button', { name: /^Ещё действия/ }));
    fireEvent.click(within(row).getByRole('menuitem', { name: 'Отметить прочитанным' }));
    await waitFor(() => expect(within(row).queryByRole('img', { name: 'Не прочитано' })).toBeNull());
    expect(calls.filter((c) => c.method === 'POST' && c.url.includes('/notification-feed/absence_request'))).toHaveLength(1);
  });

  test('отказ отметки не изображает успех', async () => {
    feedServer({
      own: (url, call) => (call.method === 'POST' && url.pathname.includes('/notification-feed/absence_request')
        ? json(500, { error: { code: 'server_error', message: 'x' } })
        : null),
    });
    renderApp('/notifications');
    const row = await firstRow();

    fireEvent.click(within(row).getByRole('button', { name: /^Ещё действия/ }));
    fireEvent.click(within(row).getByRole('menuitem', { name: 'Отметить прочитанным' }));
    expect(await screen.findByText(/Не удалось отметить прочитанным/)).toBeTruthy();
    expect(within(row).getByRole('img', { name: 'Не прочитано' })).toBeTruthy();
  });

  test('«Прочитать все» — один запрос на серию нажатий, затем перечитывание', async () => {
    const calls = feedServer({
      own: (url, call) => (call.method === 'POST' && url.pathname.endsWith('/read-all')
        ? new Promise<Response>((resolve) => setTimeout(() =>
          resolve(json(200, { ...BASE_COUNTS, unread: 0, marked: 1 })), 30))
        : null),
    });
    renderApp('/notifications');
    await firstRow();
    const before = pageCalls(calls).length;

    const button = screen.getByRole('button', { name: /Прочитать все/ });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(pageCalls(calls).length).toBeGreaterThan(before));
    expect(calls.filter((c) => c.url.includes('/read-all'))).toHaveLength(1);
  });

  test('незнакомый вид и событие без сотрудника рисуются безопасно', async () => {
    feedServer({
      items: () => [
        event(1, {
          type: 'policy_declined' as FeedEvent['type'], group: 'documents',
          title: 'Отказ от ознакомления', employee_id: null, employee_name: '',
          office_name: null, short_text: '',
        }),
        event(2, { type: 'brand_new' as FeedEvent['type'], group: 'other' as FeedEvent['group'], title: '' }),
      ],
      counts: { ...BASE_COUNTS, all: 2 },
    });
    renderApp('/notifications');
    const rows = await screen.findAllByTestId('feed-row');
    expect(within(rows[0]!).getByText('Отказ от ознакомления')).toBeTruthy();
    expect(within(rows[0]!).getByText('Документ')).toBeTruthy();
    // Без сотрудника — без кружка с инициалами.
    expect(rows[0]!.querySelector('.ntf-avatar:not(.ntf-avatar--none)')).toBeNull();
    expect(rows[0]!.querySelector('.ntf-row__context')).toBeNull();
    // Незнакомый вид без заголовка — нейтральное «Событие», не код.
    expect(within(rows[1]!).getAllByText('Событие').length).toBeGreaterThan(0);
    expect(rows[1]!.textContent).not.toContain('brand_new');
  });

  test('ссылка ?id= на событие не с этой страницы открывает подробности', async () => {
    const calls = feedServer({
      own: (url, call) => (call.method === 'GET' && url.pathname.includes('/notification-feed/question')
        ? json(200, {
          ...event(77), id: 'question:x', type: 'question', group: 'questions',
          employee: null, author: null, comment: 'Текст обращения', occurred_at: '2026-09-25T04:14:00Z',
          question: { topic: 'Зарплата', channel: 'TELEGRAM', category: 'SALARY', priority: 'NORMAL',
            last_message_at: '2026-09-25T04:14:00Z', assigned_to: null },
        })
        : null),
    });
    renderApp('/notifications?id=question:x');
    const panel = await screen.findByRole('region', { name: 'Подробности события' });
    expect(await within(panel).findByText('Текст обращения')).toBeTruthy();
    expect(within(panel).getByText('Зарплата')).toBeTruthy();
    // Чтение подробностей — это GET, отметки прочтения от него нет.
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  test('пусто совсем и пусто по отбору — разные слова', async () => {
    feedServer({ items: () => [], counts: { all: 0, unread: 0, action: 0, requests: 0, documents: 0, questions: 0 } });
    renderApp('/notifications');
    expect(await screen.findByText('За последние 30 дней событий не было.')).toBeTruthy();
  });

  test('пусто по отбору', async () => {
    feedServer({ items: (scope) => (scope === 'questions' ? [] : [event(1)]) });
    renderApp('/notifications');
    await firstRow();
    await pick('Тип событий', 'Обращения');
    expect(await screen.findByText('В этом отборе событий нет. Выберите другой отбор.')).toBeTruthy();
  });

  test('ошибка — не пустой список, и повтор работает', async () => {
    let fail = true;
    feedServer({
      own: (url) => (url.pathname.endsWith('/notification-feed') && url.searchParams.get('limit') === '20' && fail
        ? json(500, { error: { code: 'server_error', message: 'x' } })
        : null),
    });
    renderApp('/notifications');
    expect(await screen.findByText(/Не удалось загрузить ленту/)).toBeTruthy();
    expect(screen.queryByText(/событий не было/)).toBeNull();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(await firstRow()).toBeTruthy();
  });

  test('истёкшая сессия убирает показанное', async () => {
    let expired = false;
    feedServer({
      own: (url) => (url.pathname.endsWith('/notification-feed') && url.searchParams.get('limit') !== '1' && expired
        ? json(403, { error: { code: 'not_authenticated', message: 'x' } })
        : null),
    });
    renderApp('/notifications');
    await firstRow();
    expired = true;
    fireEvent.click(screen.getByRole('button', { name: 'Следующая страница' }));
    expect(await screen.findByText(/Сессия истекла или доступ к ленте закрыт/)).toBeTruthy();
    expect(screen.queryAllByTestId('feed-row')).toHaveLength(0);
  });

  test('разметка строки годится для телефона: событие, тип, дата и действие', async () => {
    feedServer();
    renderApp('/notifications');
    const row = await firstRow();
    // jsdom не считает медиазапросы: проверяется структура, на которую
    // опирается мобильная раскладка (`notifications.css`, ≤760 px).
    expect(row.querySelector('.ntf-row__event')).not.toBeNull();
    expect(row.querySelector('.ntf-row__type .ntf-row__label')?.textContent).toBe('Тип');
    expect(row.querySelector('.ntf-row__when .ntf-row__label')?.textContent).toBe('Создано');
    expect(row.querySelector('.ntf-row__actions .ntf-btn')).not.toBeNull();
    expect(document.querySelector('.ntf-page .ntf')).not.toBeNull();
  });
});

describe('очередь отправки в той же карточке', () => {
  test('вкладка переключает раздел и запрашивает очередь', async () => {
    const calls = feedServer({
      own: (url) => {
        if (url.pathname.endsWith('/notifications/counts/')) {
          return json(200, {
            total: 3, PENDING: 1, RUNNING: 0, SENT: 1, READ: 0, FAILED: 1, CANCELLED: 0,
            sent: 1, queued: 1, failed: 1, cancelled: 0, timezone: 'Asia/Dushanbe',
          });
        }
        if (url.pathname.endsWith('/notifications/')) {
          return json(200, { items: [], next_cursor: null, has_more: false });
        }
        return null;
      },
    });
    renderApp('/notifications');
    await firstRow();
    fireEvent.click(screen.getByRole('tab', { name: 'Очередь отправки' }));

    expect(await screen.findByLabelText('Сводка по состояниям')).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Очередь отправки' }).getAttribute('aria-selected')).toBe('true');
    await waitFor(() =>
      expect(calls.some((c) => new URL(c.url, 'http://crm.test').pathname.endsWith('/notifications/'))).toBe(true),
    );
    // Пустая очередь — словами, не нулями-выдумками в таблице.
    expect(await screen.findByText(/Уведомлений пока нет/)).toBeTruthy();
    const heading = screen.getByRole('heading', { name: /Уведомления/ });
    await waitFor(() => expect(heading.textContent).toContain('3'));
  });
});
