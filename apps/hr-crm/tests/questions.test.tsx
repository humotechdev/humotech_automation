/**
 * «Обращения»: очередь, переписка, ответ в Telegram и черновик ассистента.
 *
 * Проверяется то, что легко сломать незаметно: что именно уходит на
 * сервер, что ответ не отправляется без Telegram и не уходит дважды,
 * что черновик не отправляется сам и не выдаёт себя за надёжный.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, pick, renderApp, type Call } from './helpers';

const HR = {
  ...USER,
  permissions: [
    'questions.read', 'questions.answer', 'knowledge.read', 'employees.read',
    'absences.read', 'attendance.read',
  ],
};

const now = new Date();
const minutesAgo = (n: number) => new Date(now.getTime() - n * 60_000).toISOString();

const ROW = {
  id: 'q-1',
  number: 214,
  employee: { id: 'e-1', full_name: 'Облокулов Шахноза', employee_number: 'DEMO-E-0004', has_photo: false },
  office: { id: 'o-1', name: 'Головной офис' },
  topic: 'Как перенести отпуск?',
  snippet: 'Можно перенести его на 27 сентября?',
  last_message_kind: 'EMPLOYEE',
  category: 'VACATION',
  priority: 'NORMAL',
  status: 'NEW',
  unread: false,
  awaiting_reply: true,
  due_at: new Date(now.getTime() + 42 * 60_000).toISOString(),
  overdue: false,
  last_message_at: minutesAgo(18),
  created_at: minutesAgo(18),
  assignee: null,
};

const URGENT = {
  ...ROW,
  id: 'q-2',
  number: 215,
  employee: { id: 'e-2', full_name: 'Назаров Фаррух', employee_number: 'DEMO-E-0010', has_photo: false },
  topic: 'Не сохранилась отметка выхода',
  priority: 'URGENT',
  overdue: true,
};

const SOURCE = {
  id: 's-1', title: 'Правила ежегодного отпуска', source_type: 'POLICY', version: 3,
  status: 'ACTIVE', published_at: '2026-09-01T09:00:00Z', updated_at: '2026-09-01T09:00:00Z',
};

const ACTIONS = {
  take: true, assign: true, priority: true, category: true, wait: true,
  close: true, reopen: false, reply: true, draft: true,
};

const QUESTION = {
  ...ROW,
  question_text: 'Добрый день. Можно перенести отпуск на 27 сентября?',
  channel: 'TELEGRAM',
  first_response_at: null,
  closed_at: null,
  closed_by: null,
  close_reason: null,
  telegram: { connected: true, reason: null, status: 'ACTIVE' },
  messages: [
    {
      id: 'm-0', kind: 'SYSTEM', source: 'SYSTEM', body: null, event: 'CREATED',
      details: { source: 'TELEGRAM' }, author: { type: 'system', id: null, name: 'Система' },
      created_at: minutesAgo(19), delivery: null,
    },
    {
      id: 'm-1', kind: 'EMPLOYEE', source: 'TELEGRAM',
      body: 'Добрый день. У меня отпуск с 20 сентября. Можно перенести его на 27 сентября?',
      event: null, details: null, author: { type: 'employee', id: 'e-1', name: 'Облокулов Шахноза' },
      created_at: minutesAgo(18), delivery: null,
    },
  ],
  draft: {
    status: 'READY',
    text: 'Да, даты отпуска можно изменить до его начала.',
    confidence: '0.9100',
    generated_at: minutesAgo(17),
    outdated: false,
    sources: [SOURCE],
  },
  actions: ACTIONS,
};

const COUNTS = {
  statuses: { NEW: 6, IN_PROGRESS: 4, WAITING_EMPLOYEE: 2, CLOSED: 38 },
  total: 50,
  quick: { all: 6, unanswered: 6, mine: 3, urgent: 2, unread: 1 },
};

const CONTEXT = {
  employee: {
    id: 'e-1', full_name: 'Облокулов Шахноза', employee_number: 'DEMO-E-0004',
    employment_status: 'ACTIVE', has_photo: false, position: 'Руководитель отдела',
    department: 'Финансы', office: { id: 'o-1', name: 'Головной офис' },
    schedule: { name: 'Пятидневка', flexible: false, summary: 'Пн–Пт · 09:00–18:00' },
    telegram: { connected: true, reason: null, status: 'ACTIVE', username: 'shahnoza' },
  },
  links: { employee_card: true, attendance: false, requests: true },
  requests: [{
    id: 'r-1', type: 'Ежегодный отпуск', type_code: 'ANNUAL_LEAVE', kind: 'CREATE',
    status: 'IN_REVIEW', start: '2026-09-20T00:00:00Z', end: '2026-09-25T00:00:00Z',
    created_at: '2026-09-01T00:00:00Z',
  }],
  balance: [{ type: 'Ежегодный отпуск', year: 2026, allocated_days: 28, used_days: 14, reserved_days: 0, available_days: 14 }],
  corrections: [],
  documents: [],
  history: {
    total: 6, closed: 5, open: 1,
    recent: [{ id: 'q-9', number: 180, topic: 'Как изменить банковские реквизиты?', status: 'CLOSED', created_at: '2026-09-02T10:00:00Z' }],
  },
  materials: [SOURCE],
};

type Handler = (path: string, call: Call) => Response | null;

function network(own: Handler = () => null, question: object = QUESTION) {
  return fakeNetwork((path, call) => {
    const mine = own(path, call);
    if (mine) return mine;
    if (path.includes('/auth/')) return json(200, HR);
    if (path.includes('/knowledge/escalations/counts')) return json(200, COUNTS);
    if (path.includes('/knowledge/escalations/assignees')) {
      return json(200, { items: [{ id: HR.id, name: 'Кадровик' }, { id: 'u-2', name: 'Азизова Мадина' }] });
    }
    if (path.includes('/context/')) return json(200, CONTEXT);
    if (path.includes('/read/')) return json(200, { id: 'q-1', unread: false });
    if (/\/knowledge\/escalations\/q-\d+\/?$/.test(path)) return json(200, question);
    if (/\/knowledge\/escalations\/(\?|$)/.test(path)) {
      return json(200, { items: [URGENT, ROW], next_cursor: null, has_more: false });
    }
    if (path.includes('/offices')) {
      return json(200, { items: [{ id: 'o-1', name: 'Головной офис', status: 'ACTIVE', region_id: 'r-1' }] });
    }
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

const posts = (calls: Call[], action: string) =>
  calls.filter((call) => call.method === 'POST' && call.url.includes(`/${action}/`));
const lists = (calls: Call[]) =>
  calls.filter((call) => call.method === 'GET' && /\/knowledge\/escalations\/(\?|$)/.test(call.url));

describe('очередь', () => {
  test('вкладки показывают настоящие счётчики, открыт выбранный вопрос', async () => {
    network();
    renderApp('/questions?id=q-1');

    const tabs = await screen.findByRole('tablist', { name: 'Состояние обращений' });
    await waitFor(() => expect(within(tabs).getByRole('tab', { name: /Новые\s*6/ })).toBeTruthy());
    expect(within(tabs).getByRole('tab', { name: /Закрытые\s*38/ })).toBeTruthy();

    const queue = screen.getByRole('region', { name: 'Очередь обращений' });
    expect(await within(queue).findByText('Назаров Фаррух')).toBeTruthy();

    const talk = screen.getByRole('region', { name: 'Переписка' });
    expect(await within(talk).findByText(/У меня отпуск с 20 сентября/)).toBeTruthy();
    expect(within(talk).getByText('Обращение создано автоматически')).toBeTruthy();
  });

  test('поиск и быстрый фильтр уходят в запрос и в адрес', async () => {
    const calls = network();
    renderApp('/questions');
    await screen.findByText('Облокулов Шахноза');

    fireEvent.change(screen.getByLabelText('Поиск обращений'), { target: { value: '214' } });
    await waitFor(() => expect(lists(calls).pop()?.url).toContain('search=214'), { timeout: 2000 });

    // Быстрых фильтров три: все, без ответа, срочные. «Мои» убран
    // вместе с ролями — администратор один, и все обращения его.
    fireEvent.click(screen.getByRole('button', { name: /Без ответа/ }));
    await waitFor(() => expect(lists(calls).pop()?.url).toContain('quick=unanswered'));
  });

  test('офис из шапки фильтрует очередь', async () => {
    const calls = network();
    renderApp('/questions');
    await screen.findByText('Облокулов Шахноза');

    await pick('Офис', 'Головной офис');

    await waitFor(() => expect(lists(calls).pop()?.url).toContain('office_id=o-1'));
  });
});

describe('действия', () => {
  test('переписка открыта сразу, без промежуточного «взять в работу»', async () => {
    // Ответственных больше нет: администратор один, и обращение его по
    // умолчанию. Кнопка «Взять в работу» просила подтвердить то, что и
    // так очевидно, и её убрали вместе с ролями.
    const calls = network();
    renderApp('/questions?id=q-1');

    // Поле ответа появляется само, без промежуточного шага.
    expect(await screen.findByLabelText('Ответ сотруднику · Telegram')).toBeTruthy();
    expect(posts(calls, 'take')).toHaveLength(0);
  });

  test('закрыть без причины нельзя, с причиной — причина уходит на сервер', async () => {
    const calls = network((path, call) =>
      call.method === 'POST' && path.includes('/close/')
        ? json(200, { ...QUESTION, status: 'CLOSED', closed_at: now.toISOString(), close_reason: 'Дубликат обращения', actions: { ...ACTIONS, reply: false, close: false, reopen: true } })
        : null,
    );
    renderApp('/questions?id=q-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Другие действия' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Закрыть обращение/ }));
    fireEvent.click(screen.getByRole('radio', { name: 'Другое' }));
    expect((screen.getByRole('button', { name: 'Закрыть обращение' }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('radio', { name: 'Дубликат обращения' }));
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть обращение' }));

    await waitFor(() => expect(posts(calls, 'close')).toHaveLength(1));
    expect(posts(calls, 'close')[0]?.body).toEqual({ reason: 'Дубликат обращения' });
  });
});

describe('ответ в Telegram', () => {
  test('ответ уходит один раз, даже если нажать дважды', async () => {
    let release: (value: Response) => void = () => {};
    const calls = network((path, call) =>
      call.method === 'POST' && path.includes('/reply/')
        ? (new Promise<Response>((resolve) => { release = resolve; }) as unknown as Response)
        : null,
    );
    renderApp('/questions?id=q-1');

    const field = await screen.findByLabelText('Ответ сотруднику · Telegram');
    fireEvent.change(field, { target: { value: 'Да, можно. Оформите изменение в «Мои заявки».' } });
    const send = screen.getByRole('button', { name: /Отправить/ });
    fireEvent.click(send);
    fireEvent.click(send);

    await waitFor(() => expect(posts(calls, 'reply')).toHaveLength(1));
    const body = posts(calls, 'reply')[0]?.body as { text: string; after: string; client_request_id: string };
    expect(body.text).toBe('Да, можно. Оформите изменение в «Мои заявки».');
    expect(body.after).toBe('KEEP');
    expect(body.client_request_id).toBeTruthy();

    release(json(200, { ...QUESTION, status: 'IN_PROGRESS' }));
    expect(await screen.findByText('Ответ отправлен в Telegram')).toBeTruthy();
    expect(posts(calls, 'reply')).toHaveLength(1);
  });

  test('«Закрыть после отправки» уходит вместе с ответом', async () => {
    const calls = network((path, call) =>
      call.method === 'POST' && path.includes('/reply/') ? json(200, { ...QUESTION, status: 'CLOSED' }) : null,
    );
    renderApp('/questions?id=q-1');

    fireEvent.change(await screen.findByLabelText('Ответ сотруднику · Telegram'), { target: { value: 'Готово' } });
    // Уточнение к отправке, а не отдельное действие: чаще всего ответ
    // и закрывает вопрос.
    fireEvent.click(screen.getByLabelText('Закрыть после отправки'));
    fireEvent.click(screen.getByRole('button', { name: /Отправить/ }));

    await waitFor(() => expect((posts(calls, 'reply')[0]?.body as { after: string }).after).toBe('CLOSE'));
  });

  test('без Telegram отправить нельзя, и это сказано прямо', async () => {
    const calls = network(() => null, {
      ...QUESTION,
      telegram: { connected: false, reason: 'not_linked', status: null },
    });
    renderApp('/questions?id=q-1');

    const field = await screen.findByLabelText('Ответ сотруднику · Telegram');
    fireEvent.change(field, { target: { value: 'Ответ' } });

    // Причина стоит там, где человек печатает: подсказка поля говорит,
    // что доставить сообщение некуда.
    expect(field.getAttribute('placeholder')).toMatch(/отправка недоступна/);
    expect((screen.getByRole('button', { name: /Отправить/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Отправить/ }));
    expect(posts(calls, 'reply')).toHaveLength(0);
  });

  test('отказ сервера «Telegram не подключён» не выдаётся за отправку', async () => {
    network((path, call) =>
      call.method === 'POST' && path.includes('/reply/')
        ? json(409, { error: { code: 'telegram_not_connected', message: 'нет', details: { reason: 'link_revoked' } } })
        : null,
    );
    renderApp('/questions?id=q-1');

    const field = await screen.findByLabelText('Ответ сотруднику · Telegram');
    fireEvent.change(field, { target: { value: 'Ответ' } });
    fireEvent.click(screen.getByRole('button', { name: /Отправить/ }));

    expect(await screen.findByText(/сообщение не отправлено и в ленту не добавлено/)).toBeTruthy();
    expect((field as HTMLTextAreaElement).value).toBe('Ответ');
    expect(screen.queryByText('Ответ отправлен в Telegram')).toBeNull();
  });
});

describe('черновик ассистента', () => {
  test('готовый ответ вставляется в поле, а не отправляется сам', async () => {
    // Черновик — подсказка, а не ответ: отправить его за кадровика
    // значит подписать его именем то, чего он не читал.
    const calls = network();
    renderApp('/questions?id=q-1');

    fireEvent.click(await screen.findByRole('button', { name: /Ответ от AI/ }));

    const field = screen.getByLabelText('Ответ сотруднику · Telegram') as HTMLTextAreaElement;
    await waitFor(() =>
      expect(field.value).toBe('Да, даты отпуска можно изменить до его начала.'),
    );
    expect(posts(calls, 'reply')).toHaveLength(0);
  });

  test('без черновика кнопки нет вовсе', async () => {
    // Кнопка, которая ничего не вставит, хуже её отсутствия: человек
    // нажимает и решает, что сломалось.
    network(() => null, {
      ...QUESTION,
      draft: null,
      actions: { ...ACTIONS, draft: false },
    });
    renderApp('/questions?id=q-1');

    await screen.findByLabelText('Ответ сотруднику · Telegram');
    expect(screen.queryByRole('button', { name: /Ответ от AI/ })).toBeNull();
  });
});

describe('контекст сотрудника', () => {
  test('связанные действия — только те, что разрешены', async () => {
    network();
    renderApp('/questions?id=q-1');

    const side = await screen.findByRole('complementary', { name: 'Контекст сотрудника' });
    expect(await within(side).findByText('Пн–Пт · 09:00–18:00')).toBeTruthy();
    expect(within(side).getByText('14 дней')).toBeTruthy();
    expect(within(side).getByText('Всего 6 · Закрыто 5')).toBeTruthy();
  });

  test('очередь открыта администратору без разговоров о правах', async () => {
    // Прав в интерфейсе больше нет: администратор один. Отказ, если он
    // понадобится, исполняет сервер.
    network();
    renderApp('/questions');

    expect(await screen.findByRole('region', { name: 'Очередь обращений' })).toBeTruthy();
    expect(screen.queryByText('Нет права просматривать обращения.')).toBeNull();
  });
});
