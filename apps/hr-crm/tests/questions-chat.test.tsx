/**
 * «Обращения»: поведение переписки при отправке и обновлении.
 *
 * Анимации здесь не видны — jsdom не рисует и не раскладывает страницу.
 * Проверяется то, на чём анимация держится и что она не должна сломать:
 * новое сообщение помечено один раз и только оно; статус доставки — ровно
 * тот, что прислал сервер; ошибка не теряет текст; повтор не даёт дубля;
 * чтение истории не прерывается прокруткой; итог отправки не уезжает в
 * другое обращение.
 */

import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp, type Call } from './helpers';

const HR = {
  ...USER,
  permissions: ['questions.read', 'questions.answer', 'knowledge.read', 'employees.read', 'absences.read'],
};

const now = new Date();
const minutesAgo = (n: number) => new Date(now.getTime() - n * 60_000).toISOString();

const ACTIONS = {
  take: true, assign: true, priority: true, category: true, wait: true,
  start: false, close: true, reopen: false, reply: true, draft: true,
};

const ROW_A = {
  id: 'q-1', number: 214,
  employee: { id: 'e-1', full_name: 'Облокулов Шахноза', employee_number: 'DEMO-E-0004', has_photo: false },
  office: { id: 'o-1', name: 'Головной офис' },
  topic: 'Как перенести отпуск?', snippet: 'Можно перенести?', last_message_kind: 'EMPLOYEE',
  category: 'VACATION', priority: 'NORMAL', status: 'NEW', unread: false, awaiting_reply: true,
  due_at: null, overdue: false, last_message_at: minutesAgo(18), created_at: minutesAgo(18), assignee: null,
};

const ROW_B = {
  ...ROW_A, id: 'q-2', number: 215,
  employee: { id: 'e-2', full_name: 'Назаров Фаррух', employee_number: 'DEMO-E-0010', has_photo: false },
  topic: 'Не сохранилась отметка выхода',
};

type Message = Record<string, unknown> & { id: string };

const employeeSays = (id: string, body: string, ago: number, row = ROW_A): Message => ({
  id, kind: 'EMPLOYEE', source: 'TELEGRAM', body, event: null, details: null,
  author: { type: 'employee', id: row.employee.id, name: row.employee.full_name },
  created_at: minutesAgo(ago), delivery: null,
});

const hrSays = (id: string, body: string, status: string, extra: Partial<Message> = {}): Message => ({
  id, kind: 'HR', source: 'CRM', body, event: null, details: null,
  author: { type: 'user', id: HR.id, name: 'Кадровик' },
  created_at: minutesAgo(1),
  delivery: { status, sent_at: null, read_at: null, error: null },
  ...extra,
});

const question = (row: typeof ROW_A, messages: Message[]) => ({
  ...row,
  question_text: 'Вопрос',
  channel: 'TELEGRAM',
  first_response_at: null, closed_at: null, closed_by: null, close_reason: null,
  telegram: { connected: true, reason: null, status: 'ACTIVE' },
  messages,
  draft: null,
  actions: ACTIONS,
});

const COUNTS = {
  statuses: { NEW: 2, IN_PROGRESS: 0, WAITING_EMPLOYEE: 0, CLOSED: 0 },
  total: 2,
  quick: { all: 2, unanswered: 2, mine: 0, urgent: 0, unread: 0 },
};

const CONTEXT = (row: typeof ROW_A) => ({
  employee: {
    id: row.employee.id, full_name: row.employee.full_name, employee_number: null,
    employment_status: 'ACTIVE', has_photo: false, position: null, department: null,
    office: row.office, schedule: null,
    telegram: { connected: true, reason: null, status: 'ACTIVE', username: null },
  },
  links: { employee_card: false, attendance: false, requests: false },
  requests: [], balance: [], corrections: [], documents: [],
  history: { total: 1, closed: 0, open: 1, recent: [] },
  materials: [],
});

/**
 * Сервер в памяти: по обращению — текущее состояние. Ответ на `reply`
 * решает тест; по умолчанию сервер добавляет ответ HR со статусом
 * «отправляется» — так и работает настоящий: Telegram доставляет позже.
 */
function server(initial: Record<string, ReturnType<typeof question>>) {
  const state = { ...initial };
  let reply: (id: string, call: Call) => Response | Promise<Response> = (id) => {
    const was = state[id]!;
    state[id] = { ...was, messages: [...was.messages, hrSays('m-new', 'Да, можно.', 'QUEUED')] };
    return json(200, state[id]);
  };
  const calls = fakeNetwork((path, call) => {
    if (path.includes('/auth/')) return json(200, HR);
    if (path.includes('/knowledge/escalations/counts')) return json(200, COUNTS);
    if (path.includes('/knowledge/escalations/assignees')) return json(200, { items: [] });
    const own = path.match(/\/knowledge\/escalations\/(q-\d+)\/([a-z]+)\//);
    if (own && own[2] === 'context') return json(200, CONTEXT(own[1] === 'q-2' ? ROW_B : ROW_A));
    if (own && own[2] === 'read') return json(200, { id: own[1], unread: false });
    if (own && own[2] === 'reply' && call.method === 'POST') return reply(own[1]!, call);
    const one = path.match(/\/knowledge\/escalations\/(q-\d+)\/?(\?|$)/);
    if (one) return json(200, state[one[1]!]);
    if (/\/knowledge\/escalations\/(\?|$)/.test(path)) {
      return json(200, { items: [ROW_A, ROW_B].filter((row) => state[row.id]), next_cursor: null, has_more: false });
    }
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
  return {
    calls,
    state,
    onReply(handler: typeof reply) { reply = handler; },
  };
}

const replies = (calls: Call[]) =>
  calls.filter((call) => call.method === 'POST' && call.url.includes('/reply/'));

const bubble = (id: string) => document.querySelector<HTMLElement>(`[data-message-id="${id}"]`);
const freshBubbles = () => document.querySelectorAll('.tk-msg--new');
const refresh = () => fireEvent.click(screen.getByRole('button', { name: 'Обновить' }));

async function openWithText(text: string, path = '/questions?id=q-1') {
  renderApp(path);
  const field = await screen.findByLabelText('Ответ сотруднику') as HTMLTextAreaElement;
  fireEvent.change(field, { target: { value: text } });
  return field;
}

const BASE_THREAD = [
  employeeSays('m-1', 'Добрый день. Можно перенести отпуск?', 18),
  employeeSays('m-2', 'На 27 сентября.', 17),
];

describe('отправка ответа', () => {
  test('новый пузырь — по ответу сервера, плавно и только он; статус — «отправляется», без галочек', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    const field = await openWithText('Да, можно.');
    // При открытии ничего не «влетает»: всё, что было, стоит на месте.
    expect(freshBubbles()).toHaveLength(0);
    const before = bubble('m-1');

    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(await screen.findByText('Ответ отправлен в Telegram')).toBeTruthy();
    const mine = await waitFor(() => {
      const node = bubble('m-new');
      expect(node).toBeTruthy();
      return node!;
    });
    expect(mine.classList.contains('tk-msg--new')).toBe(true);
    expect(mine.classList.contains('tk-msg--mine')).toBe(true);
    // Остальные строки те же самые узлы и без пометки — они не прыгают
    // и не анимируются заново.
    expect(bubble('m-1')).toBe(before);
    expect(freshBubbles()).toHaveLength(1);
    // Статус — тот, что прислал сервер: только «отправляется».
    expect(within(mine).getByRole('img', { name: 'Отправляется' })).toBeTruthy();
    expect(within(mine).queryByRole('img', { name: 'Доставлено' })).toBeNull();
    expect(within(mine).queryByRole('img', { name: 'Прочитано' })).toBeNull();
    // Поле очищено только после подтверждения.
    expect(field.value).toBe('');
    expect(replies(api.calls)).toHaveLength(1);
  });

  test('галочки появляются только когда их прислал сервер, а не по таймеру', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    await openWithText('Да, можно.');
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(bubble('m-new')).toBeTruthy());

    // Время идёт, анимации давно кончились — статус прежний.
    await act(() => new Promise((resolve) => setTimeout(resolve, 400)));
    expect(within(bubble('m-new')!).getByRole('img', { name: 'Отправляется' })).toBeTruthy();
    expect(within(bubble('m-new')!).queryByRole('img', { name: 'Доставлено' })).toBeNull();

    // Сервер сообщил «доставлено» — при следующем опросе значок меняется.
    const was = api.state['q-1']!;
    api.state['q-1'] = {
      ...was,
      messages: was.messages.map((one) => (one.id === 'm-new' ? hrSays('m-new', 'Да, можно.', 'DELIVERED') : one)),
    };
    refresh();
    const delivered = await within(bubble('m-new')!).findByRole('img', { name: 'Доставлено' });
    expect(delivered.classList.contains('tk-delivery--changed')).toBe(true);
    expect(within(bubble('m-new')!).queryByRole('img', { name: 'Прочитано' })).toBeNull();

    api.state['q-1'] = {
      ...was,
      messages: was.messages.map((one) => (one.id === 'm-new' ? hrSays('m-new', 'Да, можно.', 'READ') : one)),
    };
    refresh();
    expect(await within(bubble('m-new')!).findByRole('img', { name: 'Прочитано' })).toBeTruthy();
    // Пузырь — тот же, повторно не «влетал».
    expect(freshBubbles()).toHaveLength(1);
  });

  test('медленный ответ: видно, что идёт отправка, текст на месте, пузыря до подтверждения нет', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    let release: () => void = () => {};
    api.onReply((id) => new Promise<Response>((resolve) => {
      release = () => {
        const was = api.state[id]!;
        api.state[id] = { ...was, messages: [...was.messages, hrSays('m-new', 'Сейчас уточню.', 'QUEUED')] };
        resolve(json(200, api.state[id]));
      };
    }));
    const field = await openWithText('Сейчас уточню.');

    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));

    const busy = await screen.findByRole('button', { name: 'Отправляем…' });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('form', { name: 'Отправка ответа' }).getAttribute('aria-busy')).toBe('true');
    expect(field.value).toBe('Сейчас уточню.');
    expect(field.readOnly).toBe(true);
    expect(bubble('m-new')).toBeNull();

    await act(async () => { release(); });
    await waitFor(() => expect(bubble('m-new')).toBeTruthy());
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeTruthy();
    expect(field.readOnly).toBe(false);
    expect(field.value).toBe('');
    expect(replies(api.calls)).toHaveLength(1);
  });

  test('Enter и щелчок подряд — одно сообщение', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    let release: () => void = () => {};
    api.onReply((id) => new Promise<Response>((resolve) => {
      release = () => resolve(json(200, api.state[id]));
    }));
    const field = await openWithText('Один раз');

    fireEvent.keyDown(field, { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: /Отправ/ }));
    fireEvent.keyDown(field, { key: 'Enter' });

    await waitFor(() => expect(replies(api.calls)).toHaveLength(1));
    await act(async () => { release(); });
    expect(replies(api.calls)).toHaveLength(1);
  });

  test('Shift+Enter — новая строка, не отправка', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    const field = await openWithText('Первая строка');
    fireEvent.keyDown(field, { key: 'Enter', shiftKey: true });
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(replies(api.calls)).toHaveLength(0);
  });

  test('ошибка: сообщение не пропадает молча, текст остаётся, повтор — тем же ключом и без дубля', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    let fail = true;
    api.onReply((id) => {
      if (fail) {
        fail = false;
        return json(503, { error: { code: 'unavailable', message: 'Сервис недоступен', details: {} } });
      }
      const was = api.state[id]!;
      api.state[id] = { ...was, messages: [...was.messages, hrSays('m-new', 'Да, можно.', 'QUEUED')] };
      return json(200, api.state[id]);
    });
    const field = await openWithText('Да, можно.');

    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByText('Сообщение не отправлено — текст остался в поле.')).toBeTruthy();
    expect(field.value).toBe('Да, можно.');
    expect(bubble('m-new')).toBeNull();
    expect(screen.queryByText('Ответ отправлен в Telegram')).toBeNull();

    // Повтор — той же кнопкой. Ключ повтора прежний: если первый запрос
    // всё же дошёл, сервер не отправит сотруднику второе сообщение.
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    expect(await screen.findByText('Ответ отправлен в Telegram')).toBeTruthy();
    const [first, second] = replies(api.calls);
    expect((second?.body as { client_request_id: string }).client_request_id)
      .toBe((first?.body as { client_request_id: string }).client_request_id);
    expect(document.querySelectorAll('[data-message-id="m-new"]')).toHaveLength(1);
    expect(screen.queryByText('Сообщение не отправлено — текст остался в поле.')).toBeNull();
    expect(field.value).toBe('');
  });

  test('файл: ответ с вложением появляется пузырём с файлом', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    api.onReply((id) => {
      const was = api.state[id]!;
      api.state[id] = {
        ...was,
        messages: [...was.messages, hrSays('m-new', '', 'QUEUED', {
          body: null, attachment: { name: 'Бланк.pdf', mime_type: 'application/pdf', size_bytes: 2048 },
        })],
      };
      return json(200, api.state[id]);
    });
    renderApp('/questions?id=q-1');
    await screen.findByLabelText('Ответ сотруднику');
    const blank = new File(['%PDF-1.4'], 'Бланк.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByTestId('reply-file'), { target: { files: [blank] } });
    fireEvent.click(await screen.findByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(bubble('m-new')).toBeTruthy());
    expect(bubble('m-new')!.classList.contains('tk-msg--new')).toBe(true);
    expect(within(bubble('m-new')!).getByText('Бланк.pdf')).toBeTruthy();
    expect(replies(api.calls)[0]?.body).toBeInstanceOf(FormData);
  });
});

describe('обновление переписки', () => {
  test('новое сообщение сотрудника появляется плавно, без дублей, остальные не анимируются, набранный текст цел', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    const field = await openWithText('Черновик ответа');
    const old = bubble('m-2');

    api.state['q-1'] = question(ROW_A, [...BASE_THREAD, employeeSays('m-3', 'И ещё вопрос.', 0)]);
    refresh();

    await waitFor(() => expect(bubble('m-3')).toBeTruthy());
    expect(bubble('m-3')!.classList.contains('tk-msg--new')).toBe(true);
    expect(bubble('m-2')).toBe(old);
    expect(old!.classList.contains('tk-msg--new')).toBe(false);
    expect(field.value).toBe('Черновик ответа');

    // Тот же ответ сервера ещё раз — ни новых строк, ни новой анимации.
    const fresh = bubble('m-3');
    refresh();
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(document.querySelectorAll('[data-message-id]')).toHaveLength(3);
    expect(bubble('m-3')).toBe(fresh);
    expect(freshBubbles()).toHaveLength(1);
  });

  test('порядок — как у сервера', async () => {
    server({
      'q-1': question(ROW_A, [
        employeeSays('m-1', 'Первое', 30),
        hrSays('m-2', 'Ответ', 'READ'),
        employeeSays('m-3', 'Третье', 1),
      ]),
    });
    renderApp('/questions?id=q-1');
    await screen.findByText('Третье');
    const order = [...document.querySelectorAll('[data-message-id]')].map((node) => node.getAttribute('data-message-id'));
    expect(order).toEqual(['m-1', 'm-2', 'm-3']);
  });

  test('длинная переписка открывается без анимаций', async () => {
    const many = Array.from({ length: 300 }, (_, index) => (index % 2
      ? hrSays(`m-${index}`, `Ответ ${index}`, 'READ')
      : employeeSays(`m-${index}`, `Вопрос ${index}`, 300 - index)));
    server({ 'q-1': question(ROW_A, many) });
    renderApp('/questions?id=q-1');
    await screen.findByText('Ответ 299');
    expect(document.querySelectorAll('[data-message-id]')).toHaveLength(300);
    expect(freshBubbles()).toHaveLength(0);
  });
});

describe('прокрутка ленты', () => {
  /** jsdom ничего не раскладывает — размеры ленты задаются вручную. */
  function shapeThread(scrollTop: number) {
    const box = document.querySelector<HTMLElement>('.tk-thread')!;
    Object.defineProperty(box, 'scrollHeight', { configurable: true, value: 2000 });
    Object.defineProperty(box, 'clientHeight', { configurable: true, value: 400 });
    Object.defineProperty(box, 'scrollTop', { configurable: true, writable: true, value: scrollTop });
    const scrollTo = vi.fn();
    box.scrollTo = scrollTo as unknown as typeof box.scrollTo;
    fireEvent.scroll(box);
    return { box, scrollTo };
  }

  test('внизу ленты — плавно доезжаем до нового сообщения', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    renderApp('/questions?id=q-1');
    await screen.findByText('На 27 сентября.');
    const { scrollTo } = shapeThread(1600);

    api.state['q-1'] = question(ROW_A, [...BASE_THREAD, employeeSays('m-3', 'Новое', 0)]);
    refresh();
    await waitFor(() => expect(bubble('m-3')).toBeTruthy());

    expect(scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: 'smooth' });
    expect(screen.queryByRole('button', { name: 'К новым сообщениям' })).toBeNull();
  });

  test('читает историю — прокрутка не трогается, внизу кнопка «К новым сообщениям»', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    renderApp('/questions?id=q-1');
    await screen.findByText('На 27 сентября.');
    const { box, scrollTo } = shapeThread(100);

    api.state['q-1'] = question(ROW_A, [...BASE_THREAD, employeeSays('m-3', 'Новое', 0)]);
    refresh();
    await waitFor(() => expect(bubble('m-3')).toBeTruthy());

    expect(scrollTo).not.toHaveBeenCalled();
    expect(box.scrollTop).toBe(100);
    const jump = screen.getByRole('button', { name: 'К новым сообщениям' });
    fireEvent.click(jump);
    expect(scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: 'smooth' });
    expect(screen.queryByRole('button', { name: 'К новым сообщениям' })).toBeNull();
  });

  test('без анимаций в системе — прокрутка мгновенная', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('prefers-reduced-motion'), media: query,
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    }));
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    renderApp('/questions?id=q-1');
    await screen.findByText('На 27 сентября.');
    const { scrollTo } = shapeThread(1600);

    api.state['q-1'] = question(ROW_A, [...BASE_THREAD, employeeSays('m-3', 'Новое', 0)]);
    refresh();
    await waitFor(() => expect(bubble('m-3')).toBeTruthy());
    expect(scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: 'auto' });
  });
});

describe('переключение обращения во время отправки', () => {
  test('итог и «Отправляем…» остаются у своего обращения, черновики не путаются', async () => {
    const api = server({
      'q-1': question(ROW_A, BASE_THREAD),
      'q-2': question(ROW_B, [employeeSays('b-1', 'Отметка выхода пропала', 5, ROW_B)]),
    });
    let release: () => void = () => {};
    api.onReply((id) => new Promise<Response>((resolve) => {
      release = () => {
        const was = api.state[id]!;
        api.state[id] = { ...was, messages: [...was.messages, hrSays('m-new', 'Ответ первому', 'QUEUED')] };
        resolve(json(200, api.state[id]));
      };
    }));
    await openWithText('Ответ первому');
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    await screen.findByRole('button', { name: 'Отправляем…' });

    // Пока ответ в пути — открываем другое обращение.
    fireEvent.click(screen.getByText('Назаров Фаррух', { selector: '.tk-row__name' }));
    await screen.findByText('Отметка выхода пропала');
    const other = screen.getByLabelText('Ответ сотруднику') as HTMLTextAreaElement;
    expect(other.value).toBe('');
    expect(screen.queryByRole('button', { name: 'Отправляем…' })).toBeNull();
    fireEvent.change(other, { target: { value: 'Черновик второму' } });

    await act(async () => { release(); });
    await waitFor(() => expect(replies(api.calls)).toHaveLength(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));

    // Итог первого обращения не показан во втором, черновик второго цел.
    expect(screen.queryByText('Ответ отправлен в Telegram')).toBeNull();
    expect(bubble('m-new')).toBeNull();
    expect((screen.getByLabelText('Ответ сотруднику') as HTMLTextAreaElement).value).toBe('Черновик второму');
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeTruthy();

    // В первом — ответ на месте, а его черновик очищен.
    fireEvent.click(screen.getByText('Облокулов Шахноза', { selector: '.tk-row__name' }));
    await waitFor(() => expect(bubble('m-new')).toBeTruthy());
    expect((screen.getByLabelText('Ответ сотруднику') as HTMLTextAreaElement).value).toBe('');
    expect(replies(api.calls)).toHaveLength(1);
  });
});

describe('быстрые фильтры', () => {
  test('все три — в одном ряду фильтров и по-прежнему уходят в запрос', async () => {
    const api = server({ 'q-1': question(ROW_A, BASE_THREAD) });
    renderApp('/questions?id=q-1');
    await screen.findByLabelText('Ответ сотруднику');
    const row = document.querySelector('.tk-chips')!;
    const chips = within(row as HTMLElement).getAllByRole('button');
    expect(chips.map((one) => one.textContent?.replace(/\d+/g, ''))).toEqual(['Все', 'Без ответа', 'Срочные']);

    fireEvent.click(chips[2]!);
    await waitFor(() => expect(
      api.calls.some((call) => call.method === 'GET' && call.url.includes('quick=urgent')),
    ).toBe(true));
    expect(chips[2]!.getAttribute('aria-pressed')).toBe('true');
  });
});
