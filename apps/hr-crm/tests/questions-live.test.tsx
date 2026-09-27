/**
 * «Обращения» вживую: поток событий вместо опроса.
 *
 * Сервер шлёт только «какое обращение изменилось», а страница перечитывает
 * данные обычными запросами. Здесь закреплено, что из этого видит HR:
 * новая реплика в открытом чате без потери набранного ответа, строка
 * другого чата и счётчики без переключения, значок меню на другой
 * странице, «Отправляется → Отправлено → Не доставлено», повторная
 * отметка прочтения, фильтры, восстановление после обрыва.
 */

import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { resetLiveForTests } from '../src/features/realtime/events';
import { USER, crm, fakeNetwork, json, renderApp, type Call } from './helpers';

// --- поддельный EventSource --------------------------------------------------

class FakeSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static all: FakeSource[] = [];

  readyState = FakeSource.CONNECTING;
  onerror: ((event: Event) => void) | null = null;
  private handlers = new Map<string, ((event: MessageEvent) => void)[]>();

  constructor(public url: string, public init?: EventSourceInit) {
    FakeSource.all.push(this);
  }

  addEventListener(name: string, handler: (event: MessageEvent) => void) {
    this.handlers.set(name, [...(this.handlers.get(name) ?? []), handler]);
  }

  close() {
    this.readyState = FakeSource.CLOSED;
  }

  /** Сервер прислал именованное событие. */
  emit(name: string, data: Record<string, unknown> = {}) {
    if (name === 'ready') this.readyState = FakeSource.OPEN;
    for (const handler of this.handlers.get(name) ?? []) {
      handler(new MessageEvent(name, { data: JSON.stringify(data) }));
    }
  }

  /** Сервер ответил не 200 или соединение оборвалось насовсем. */
  fail() {
    this.readyState = FakeSource.CLOSED;
    this.onerror?.(new Event('error'));
  }
}

const latest = () => FakeSource.all[FakeSource.all.length - 1]!;

beforeEach(() => {
  FakeSource.all = [];
  vi.stubGlobal('EventSource', FakeSource);
});

afterEach(() => {
  resetLiveForTests();
  vi.useRealTimers();
});

// --- данные ------------------------------------------------------------------

const HR = {
  ...USER,
  permissions: ['questions.read', 'questions.answer', 'employees.read', 'notifications.manage'],
};

const now = Date.now();
const at = (minutes: number) => new Date(now - minutes * 60_000).toISOString();

const row = (id: string, name: string, minutes: number, extra: object = {}) => ({
  id, number: 200 + Number(id.slice(2)),
  employee: { id: `e-${id}`, full_name: name, employee_number: null, has_photo: false },
  office: { id: 'o-1', name: 'Головной офис' },
  topic: `Тема ${id}`, snippet: `Последнее ${id}`, last_message_kind: 'EMPLOYEE',
  category: 'OTHER', priority: 'NORMAL', status: 'NEW', unread: false,
  awaiting_reply: true, due_at: null, overdue: false,
  last_message_at: at(minutes), created_at: at(minutes), assignee: null,
  ...extra,
});

const employeeSays = (id: string, body: string, minutes: number) => ({
  id, kind: 'EMPLOYEE', source: 'TELEGRAM', body, event: null, details: null,
  author: { type: 'employee', id: 'e-q-1', name: 'Облокулов Шахноза' },
  created_at: at(minutes), delivery: null,
});

const hrSays = (id: string, body: string, delivery: object, key: string | null = null) => ({
  id, kind: 'HR', source: 'CRM', body, event: null, details: null,
  author: { type: 'user', id: USER.id, name: 'Кадровик' },
  created_at: at(0), client_request_id: key,
  delivery: {
    status: 'QUEUED', sent_at: null, read_at: null, error: null,
    notification_id: 'n-1', can_retry: false, ...delivery,
  },
});

const ACTIONS = {
  take: true, assign: true, priority: true, category: true, wait: true,
  start: false, close: true, reopen: false, reply: true, draft: false,
};

function question(messages: object[], extra: object = {}) {
  return {
    ...row('q-1', 'Облокулов Шахноза', 10), question_text: 'Вопрос', channel: 'TELEGRAM',
    first_response_at: null, closed_at: null, closed_by: null, close_reason: null,
    telegram: { connected: true, reason: null, status: 'ACTIVE' },
    messages, draft: null, actions: ACTIONS, ...extra,
  };
}

const CONTEXT = {
  employee: {
    id: 'e-q-1', full_name: 'Облокулов Шахноза', employee_number: null,
    employment_status: 'ACTIVE', has_photo: false, position: null, department: null,
    office: { id: 'o-1', name: 'Головной офис' }, schedule: null,
    telegram: { connected: true, reason: null, status: 'ACTIVE', username: null },
  },
  links: { employee_card: false, attendance: false, requests: false },
  requests: null, balance: null, corrections: null, documents: null,
  history: { total: 1, closed: 0, open: 1, recent: [] },
  materials: [], today: null,
};

const counts = (unanswered: number) => ({
  statuses: { NEW: unanswered, IN_PROGRESS: 0, WAITING_EMPLOYEE: 0, CLOSED: 0 },
  total: unanswered, quick: { all: unanswered, unanswered, mine: 0, urgent: 0, unread: 1 },
});

/** Сервер со сменяемым состоянием: тест меняет `state`, страница перечитывает. */
function server(initial: { rows: object[]; detail: object; unanswered: number }) {
  const state = { ...initial };
  const calls = fakeNetwork((path, call: Call) => {
    if (path.includes('/auth/')) return json(200, HR);
    if (path.includes('/knowledge/escalations/counts')) return json(200, counts(state.unanswered));
    if (path.includes('/knowledge/escalations/assignees')) return json(200, { items: [] });
    if (path.includes('/context/')) return json(200, CONTEXT);
    if (path.includes('/read/')) return json(200, { id: 'q-1', unread: false });
    if (path.includes('/notifications/') && path.includes('/retry/')) return json(200, {});
    if (call.method === 'POST' && path.includes('/reply/')) {
      const body = call.body as { text: string; client_request_id: string };
      state.detail = {
        ...(state.detail as object),
        messages: [...(state.detail as { messages: object[] }).messages,
          hrSays('m-hr', body.text, {}, body.client_request_id)],
      };
      return json(200, state.detail);
    }
    if (/\/knowledge\/escalations\/q-\d+\/?$/.test(path)) return json(200, state.detail);
    if (/\/knowledge\/escalations\/(\?|$)/.test(path)) {
      return json(200, { items: state.rows, next_cursor: null, has_more: false });
    }
    if (path.includes('/offices')) return json(200, { items: [] });
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
  return { state, calls };
}

const gets = (calls: Call[], pattern: RegExp) =>
  calls.filter((call) => call.method === 'GET' && pattern.test(call.url)).length;
const DETAIL = /\/knowledge\/escalations\/q-1\/?$/;
const LIST = /\/knowledge\/escalations\/(\?|$)/;
const COUNTS = /\/knowledge\/escalations\/counts/;

async function openLive() {
  await waitFor(() => expect(FakeSource.all.length).toBeGreaterThan(0));
  act(() => latest().emit('ready', { at: now / 1000 }));
}

function signal(id: string, what = 'message') {
  act(() => latest().emit('question', { id, what, at: Date.now() / 1000, sent: Date.now() / 1000 }));
}

// --- открытый чат ------------------------------------------------------------

describe('сообщение в открытом чате', () => {
  test('появляется без F5, набранный ответ и чат остаются', async () => {
    const { state, calls } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10)],
      detail: question([employeeSays('m-1', 'Первое сообщение', 10)]),
      unanswered: 1,
    });
    renderApp('/questions?id=q-1');
    await screen.findByText('Первое сообщение');
    await openLive();

    const field = screen.getByLabelText('Ответ сотруднику') as HTMLTextAreaElement;
    fireEvent.change(field, { target: { value: 'Черновик HR' } });
    const before = gets(calls, DETAIL);

    state.detail = question([
      employeeSays('m-1', 'Первое сообщение', 10),
      employeeSays('m-2', 'Уточнение от сотрудника', 0),
    ]);
    signal('q-1');

    expect(await screen.findByText('Уточнение от сотрудника')).toBeTruthy();
    expect(gets(calls, DETAIL)).toBeGreaterThan(before);
    // Набранное не потерялось, переписка та же.
    expect(field.value).toBe('Черновик HR');
    expect(screen.getByText('Первое сообщение')).toBeTruthy();
  });

  test('два сообщения подряд — оба в ленте, прочтение отмечается по каждому', async () => {
    const { state, calls } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10, { unread: true })],
      detail: question([employeeSays('m-1', 'Первое', 10)], { unread: true }),
      unanswered: 1,
    });
    renderApp('/questions?id=q-1');
    await screen.findByText('Первое');
    await openLive();
    await waitFor(() => expect(calls.filter((one) => one.url.includes('/read/'))).toHaveLength(1));

    state.detail = question([employeeSays('m-1', 'Первое', 10), employeeSays('m-2', 'Второе', 1)], { unread: true });
    signal('q-1');
    await screen.findByText('Второе');
    state.detail = question([
      employeeSays('m-1', 'Первое', 10), employeeSays('m-2', 'Второе', 1),
      employeeSays('m-3', 'Третье', 0),
    ], { unread: true });
    signal('q-1');
    await screen.findByText('Третье');

    // Прежнее `readOnce` отметило бы обращение один раз навсегда.
    await waitFor(() => {
      const seen = calls.filter((one) => one.url.includes('/read/'))
        .map((one) => (one.body as { seen_message_id?: string }).seen_message_id);
      expect(seen).toEqual(['m-1', 'm-2', 'm-3']);
    });
  });

  test('HR читает старое — лента не прыгает, появляется «Новые сообщения»', async () => {
    const { state } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10)],
      detail: question([employeeSays('m-1', 'Первое', 10)]),
      unanswered: 1,
    });
    renderApp('/questions?id=q-1');
    await screen.findByText('Первое');
    await openLive();

    // Прокрутили вверх: до нижнего края далеко.
    const thread = document.querySelector('.tk-thread') as HTMLElement;
    Object.defineProperty(thread, 'scrollHeight', { configurable: true, value: 2000 });
    Object.defineProperty(thread, 'clientHeight', { configurable: true, value: 400 });
    thread.scrollTop = 100;
    fireEvent.scroll(thread);

    state.detail = question([employeeSays('m-1', 'Первое', 10), employeeSays('m-2', 'Сообщение внизу ленты', 0)]);
    signal('q-1');
    await screen.findByText('Сообщение внизу ленты');

    // Кнопка появляется следующим рендером после самого сообщения.
    const newer = await screen.findByRole('button', { name: /Новые сообщения/ });
    expect(thread.scrollTop).toBe(100);
    fireEvent.click(newer);
    expect(thread.scrollTop).toBe(2000);
    expect(screen.queryByRole('button', { name: /Новые сообщения/ })).toBeNull();
  });
});

// --- другой чат и фильтры -------------------------------------------------------

describe('сообщение в другом чате', () => {
  test('обновляет строку и счётчики, но не переключает чат', async () => {
    const { state, calls } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10), row('q-2', 'Назаров Фаррух', 30)],
      detail: question([employeeSays('m-1', 'Первое', 10)]),
      unanswered: 2,
    });
    renderApp('/questions?id=q-1');
    await screen.findByText('Первое');
    await openLive();
    const detailBefore = gets(calls, DETAIL);
    const countsBefore = gets(calls, COUNTS);

    // Назаров написал: его строка поднялась наверх с новым текстом.
    state.rows = [
      row('q-2', 'Назаров Фаррух', 0, { snippet: 'Свежий вопрос', unread: true }),
      row('q-1', 'Облокулов Шахноза', 10),
    ];
    state.unanswered = 3;
    signal('q-2');

    const queue = screen.getByRole('region', { name: 'Очередь обращений' });
    expect(await within(queue).findByText('Свежий вопрос')).toBeTruthy();
    const rows = within(queue.querySelector('.tk-rows') as HTMLElement).getAllByRole('button');
    expect(rows[0]?.textContent).toContain('Назаров Фаррух');
    // Чат тот же, переписку не перечитывали.
    expect(screen.getByText('Первое')).toBeTruthy();
    expect(gets(calls, DETAIL)).toBe(detailBefore);
    expect(gets(calls, COUNTS)).toBeGreaterThan(countsBefore);
    // Значок меню — то же число, что «без ответа».
    await waitFor(() => {
      const nav = screen.getByRole('link', { name: /Обращения/ });
      expect(nav.textContent).toContain('3');
    });
  });

  test('обращение вне фильтра меняет числа, а список перечитывается с тем же фильтром', async () => {
    const { calls } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10)],
      detail: question([employeeSays('m-1', 'Первое', 10)]),
      unanswered: 1,
    });
    renderApp('/questions?id=q-1&status=IN_PROGRESS&quick=urgent');
    await screen.findByText('Первое');
    await openLive();
    const before = calls.length;

    signal('q-77');

    await waitFor(() => expect(calls.length).toBeGreaterThan(before));
    await waitFor(() => {
      const fresh = calls.slice(before).filter((one) => LIST.test(one.url));
      expect(fresh.length).toBeGreaterThan(0);
      // Страница ничего не вставляет из сигнала — перечитывает с фильтрами.
      expect(fresh.every((one) => one.url.includes('status=IN_PROGRESS') && one.url.includes('quick=urgent'))).toBe(true);
    });
    expect(calls.slice(before).some((one) => DETAIL.test(one.url))).toBe(false);
  });

  test('серия сигналов — одно перечитывание, а не десять', async () => {
    const { calls } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10)],
      detail: question([employeeSays('m-1', 'Первое', 10)]),
      unanswered: 1,
    });
    renderApp('/questions?id=q-1');
    await screen.findByText('Первое');
    await openLive();
    await new Promise((resolve) => setTimeout(resolve, 300));
    const before = gets(calls, LIST);

    for (let n = 0; n < 10; n += 1) signal(`q-${50 + n}`);
    await new Promise((resolve) => setTimeout(resolve, 500));

    expect(gets(calls, LIST) - before).toBe(1);
  });
});

// --- значок меню на другой странице ------------------------------------------------

describe('значок «Обращения» вне раздела', () => {
  test('растёт сразу и ведёт в обращение, из-за которого вырос', async () => {
    const { state } = server({ rows: [], detail: question([]), unanswered: 1 });
    renderApp('/');
    await openLive();
    const nav = await screen.findByRole('link', { name: /Обращения/ });
    await waitFor(() => expect(nav.textContent).toContain('1'));

    state.unanswered = 2;
    signal('q-9');

    await waitFor(() => expect(screen.getByRole('link', { name: /Обращения/ }).textContent).toContain('2'));
    expect(screen.getByRole('link', { name: /Обращения/ }).getAttribute('href')).toBe('/questions?id=q-9');
  });
});

// --- доставка ответа ------------------------------------------------------------

describe('ответ HR', () => {
  test('«Отправляется» сразу, «Отправлено» — когда бот отправил', async () => {
    const { state } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10)],
      detail: question([employeeSays('m-1', 'Первое', 10)]),
      unanswered: 1,
    });
    renderApp('/questions?id=q-1');
    await screen.findByText('Первое');
    await openLive();

    fireEvent.change(screen.getByLabelText('Ответ сотруднику'), { target: { value: 'Да, можно' } });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    const bubble = (await screen.findByText('Да, можно')).closest('article')!;
    expect(within(bubble).getByText('Отправляется')).toBeTruthy();
    // Одна реплика, а не «отправляемая» плюс сохранённая.
    await waitFor(() => expect(screen.getAllByText('Да, можно')).toHaveLength(1));

    // Бот отправил — сервер сообщил сигналом доставки.
    const detail = state.detail as { messages: { kind: string; delivery: object }[] };
    state.detail = {
      ...detail,
      messages: detail.messages.map((one) => (one.kind === 'HR'
        ? { ...one, delivery: { ...one.delivery, status: 'DELIVERED', sent_at: new Date().toISOString() } }
        : one)),
    };
    signal('q-1', 'delivery');

    await waitFor(() => {
      const mine = screen.getByText('Да, можно').closest('article')!;
      expect(within(mine).getByText('Отправлено')).toBeTruthy();
    });
    expect(screen.queryByText('Прочитано')).toBeNull();
  });

  test('«Не доставлено» с причиной и повтором отправки', async () => {
    const { calls } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10)],
      detail: question([
        employeeSays('m-1', 'Первое', 10),
        hrSays('m-2', 'Ответ', { status: 'FAILED', error: 'blocked_by_user', can_retry: true }),
      ]),
      unanswered: 0,
    });
    renderApp('/questions?id=q-1');

    expect(await screen.findByText(/Не доставлено: сотрудник заблокировал бота/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Отправить снова' }));

    await waitFor(() => expect(calls.some((one) => one.method === 'POST' && one.url.includes('/notifications/n-1/retry/'))).toBe(true));
  });
});

// --- обрыв и восстановление -----------------------------------------------------------

describe('потеря связи', () => {
  test('после обрыва поток переподключается и перечитывает всё', async () => {
    const { state, calls } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10)],
      detail: question([employeeSays('m-1', 'Первое', 10)]),
      unanswered: 1,
    });
    renderApp('/questions?id=q-1');
    await screen.findByText('Первое');
    await openLive();
    const first = latest();

    // Backend перезапускался: 502, EventSource сдался насовсем. Пока его не
    // было, сотрудник написал — сигнал этого потерян.
    state.detail = question([employeeSays('m-1', 'Первое', 10), employeeSays('m-2', 'Пока не было связи', 0)]);
    act(() => first.fail());

    // Переподключение — по паузе, сам, без F5.
    await waitFor(() => expect(FakeSource.all.length).toBe(2), { timeout: 4000 });
    const before = gets(calls, DETAIL);
    act(() => latest().emit('ready', { at: Date.now() / 1000 }));

    expect(await screen.findByText('Пока не было связи')).toBeTruthy();
    expect(gets(calls, DETAIL)).toBeGreaterThan(before);
  });

  test('возврат на вкладку перечитывает открытый чат', async () => {
    const { state } = server({
      rows: [row('q-1', 'Облокулов Шахноза', 10)],
      detail: question([employeeSays('m-1', 'Первое', 10)]),
      unanswered: 1,
    });
    renderApp('/questions?id=q-1');
    await screen.findByText('Первое');
    await openLive();

    state.detail = question([employeeSays('m-1', 'Первое', 10), employeeSays('m-2', 'Пока ноутбук спал', 0)]);
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });

    expect(await screen.findByText('Пока ноутбук спал')).toBeTruthy();
  });

  test('выход из системы на сервере — поток не переподключается', async () => {
    server({ rows: [], detail: question([]), unanswered: 0 });
    renderApp('/questions');
    await openLive();
    // Сессия кончилась: `/auth/me` теперь 401.
    fakeNetwork((path) => (path.includes('/auth/')
      ? json(401, { error: { code: 'not_authenticated', message: 'нет' } })
      : json(200, { items: [], next_cursor: null, has_more: false })));

    act(() => latest().emit('bye', { reason: 'session' }));
    await new Promise((resolve) => setTimeout(resolve, 1500));

    expect(FakeSource.all).toHaveLength(1);
  });
});

// --- права ----------------------------------------------------------------------

describe('права', () => {
  test('без права на обращения поток не открывается', async () => {
    fakeNetwork((path) => {
      if (path.includes('/auth/')) return json(200, { ...USER, permissions: ['employees.read'] });
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    renderApp('/');
    await screen.findAllByRole('link', { name: /Главная/ });
    await new Promise((resolve) => setTimeout(resolve, 100));

    expect(FakeSource.all).toHaveLength(0);
  });

  test('поток открывается с cookie сессии, один на приложение', async () => {
    server({ rows: [], detail: question([]), unanswered: 0 });
    renderApp('/questions');
    await openLive();

    expect(FakeSource.all).toHaveLength(1);
    expect(latest().url).toBe('/api/v1/events/stream');
    expect(latest().init?.withCredentials).toBe(true);
  });
});
