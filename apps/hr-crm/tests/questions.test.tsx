/**
 * «Обращения»: очередь, переписка, ответ в Telegram, управление и подсказка.
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
  start: false, close: true, reopen: false, reply: true, draft: true,
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
  today: { day: '2026-09-23', state: 'IN_OFFICE', first_entry_at: '2026-09-23T04:02:00Z', last_exit_at: null },
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

const CLOSED = {
  ...QUESTION,
  status: 'CLOSED',
  closed_at: now.toISOString(),
  close_reason: 'Вопрос решён',
  actions: { ...ACTIONS, reply: false, close: false, wait: false, assign: false, reopen: true },
};

describe('очередь', () => {
  test('вкладки показывают настоящие счётчики, открыт выбранный вопрос', async () => {
    network();
    renderApp('/questions?id=q-1');

    const tabs = await screen.findByRole('tablist', { name: 'Состояние обращений' });
    await waitFor(() => expect(within(tabs).getByRole('tab', { name: /Новые\s*6/ })).toBeTruthy());
    expect(within(tabs).getByRole('tab', { name: /Ждут сотрудника\s*2/ })).toBeTruthy();
    // У закрытых числа нет: оно ничего не требует от кадровика.
    expect(within(tabs).getByRole('tab', { name: 'Закрытые' })).toBeTruthy();

    const queue = screen.getByRole('region', { name: 'Очередь обращений' });
    expect(await within(queue).findByText('Назаров Фаррух')).toBeTruthy();
    expect(within(queue).getByText(/2 из 6/)).toBeTruthy();

    const talk = screen.getByRole('region', { name: 'Переписка' });
    expect(await within(talk).findByText(/У меня отпуск с 20 сентября/)).toBeTruthy();
    // История обращения остаётся в ленте мелкой строкой.
    expect(within(talk).getByText('Обращение создано')).toBeTruthy();
  });

  test('поиск и быстрый фильтр уходят в запрос и в адрес', async () => {
    const calls = network();
    renderApp('/questions');
    await screen.findAllByText('Облокулов Шахноза');

    fireEvent.change(screen.getByLabelText('Поиск обращений'), { target: { value: '214' } });
    await waitFor(() => expect(lists(calls).pop()?.url).toContain('search=214'), { timeout: 2000 });

    fireEvent.click(screen.getByRole('button', { name: /Без ответа/ }));
    await waitFor(() => expect(lists(calls).pop()?.url).toContain('quick=unanswered'));
  });

  test('офис и ответственный из шапки фильтруют очередь', async () => {
    const calls = network();
    renderApp('/questions');
    await screen.findAllByText('Облокулов Шахноза');

    await pick('Офис', 'Головной офис');
    await waitFor(() => expect(lists(calls).pop()?.url).toContain('office_id=o-1'));

    await pick(/^Ответственный: Ответственный: Все/, 'Азизова Мадина');
    await waitFor(() => expect(lists(calls).pop()?.url).toContain('assignee=u-2'));
  });

  test('пустая вкладка говорит об этом спокойно', async () => {
    network((path) => (/\/knowledge\/escalations\/(\?|$)/.test(path)
      ? json(200, { items: [], next_cursor: null, has_more: false })
      : null));
    renderApp('/questions?status=WAITING_EMPLOYEE');

    expect(await screen.findByText('В этой очереди обращений нет')).toBeTruthy();
    expect(screen.getByText('Выберите обращение в очереди')).toBeTruthy();
    expect(screen.getByText('Контекст появится после выбора обращения')).toBeTruthy();
  });
});

describe('управление', () => {
  test('ответственный назначается выбором человека', async () => {
    const calls = network((path, call) =>
      call.method === 'POST' && path.includes('/assign/')
        ? json(200, { ...QUESTION, assignee: { id: 'u-2', name: 'Азизова Мадина' } })
        : null,
    );
    renderApp('/questions?id=q-1');

    const side = await screen.findByRole('complementary', { name: 'Контекст сотрудника' });
    fireEvent.click(await within(side).findByRole('button', { name: 'Ответственный: Не назначен' }));
    // «Не назначен» среди вариантов нет: снять ответственного сервер не умеет.
    const list = await screen.findByRole('listbox', { name: 'Ответственный' });
    expect(within(list).queryByRole('option', { name: /Не назначен/ })).toBeNull();
    fireEvent.click(within(list).getByRole('option', { name: 'Азизова Мадина' }));

    await waitFor(() => expect(posts(calls, 'assign')).toHaveLength(1));
    expect(posts(calls, 'assign')[0]?.body).toEqual({ to_user_id: 'u-2' });
    expect(await within(side).findByRole('button', { name: 'Ответственный: Азизова Мадина' })).toBeTruthy();
  });

  test('статус «В работе» не забирает обращение себе', async () => {
    const calls = network(
      (path, call) => (call.method === 'POST' && path.includes('/start/')
        ? json(200, { ...QUESTION, status: 'IN_PROGRESS', actions: { ...ACTIONS, start: false } })
        : null),
      { ...QUESTION, actions: { ...ACTIONS, start: true } },
    );
    renderApp('/questions?id=q-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Статус: Новое' }));
    const list = await screen.findByRole('listbox', { name: 'Статус' });
    // Вернуть в «Новое» нельзя — это лишь текущее значение.
    expect((within(list).getByRole('option', { name: /Новое/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(list).getByRole('option', { name: 'В работе' }));

    await waitFor(() => expect(posts(calls, 'start')).toHaveLength(1));
    expect(posts(calls, 'take')).toHaveLength(0);
  });

  test('«Ждёт сотрудника» — своё действие', async () => {
    const calls = network((path, call) =>
      call.method === 'POST' && path.includes('/wait/')
        ? json(200, { ...QUESTION, status: 'WAITING_EMPLOYEE' })
        : null,
    );
    renderApp('/questions?id=q-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Статус: Новое' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Ждёт сотрудника' }));

    await waitFor(() => expect(posts(calls, 'wait')).toHaveLength(1));
  });

  test('закрыть без причины нельзя, с причиной — причина уходит на сервер', async () => {
    const calls = network((path, call) =>
      call.method === 'POST' && path.includes('/close/') ? json(200, CLOSED) : null,
    );
    renderApp('/questions?id=q-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Закрыть обращение' }));
    const ask = await screen.findByRole('dialog', { name: 'Закрытие обращения' });
    fireEvent.click(within(ask).getByRole('radio', { name: 'Другое' }));
    expect((within(ask).getByRole('button', { name: 'Закрыть обращение' }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(within(ask).getByRole('radio', { name: 'Дубликат обращения' }));
    fireEvent.click(within(ask).getByRole('button', { name: 'Закрыть обращение' }));

    await waitFor(() => expect(posts(calls, 'close')).toHaveLength(1));
    expect(posts(calls, 'close')[0]?.body).toEqual({ reason: 'Дубликат обращения' });
  });

  test('у закрытого поле ответа отключено, переписка на месте, «Открыть снова» — по праву', async () => {
    const calls = network((path, call) =>
      call.method === 'POST' && path.includes('/reopen/') ? json(200, QUESTION) : null, CLOSED);
    renderApp('/questions?id=q-1');

    expect(await screen.findByText(/Обращение закрыто\. Чтобы ответить/)).toBeTruthy();
    expect(screen.queryByLabelText('Ответ сотруднику')).toBeNull();
    expect(screen.getByText(/У меня отпуск с 20 сентября/)).toBeTruthy();
    const side = screen.getByRole('complementary', { name: 'Контекст сотрудника' });
    expect(within(side).getByRole('button', { name: 'Статус: Закрыто' })).toBeTruthy();
    expect(within(side).getAllByText('Закрыто').length).toBeGreaterThan(1);
    expect(within(side).queryByRole('button', { name: 'Закрыть обращение' })).toBeNull();

    fireEvent.click(within(side).getByRole('button', { name: /Открыть снова/ }));
    await waitFor(() => expect(posts(calls, 'reopen')).toHaveLength(1));
  });

  test('без права переоткрывать «Открыть снова» нет', async () => {
    network(() => null, { ...CLOSED, actions: { ...CLOSED.actions, reopen: false } });
    renderApp('/questions?id=q-1');

    await screen.findByText(/Обращение закрыто\. Чтобы ответить/);
    expect(screen.queryByRole('button', { name: /Открыть снова/ })).toBeNull();
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

    const field = await screen.findByLabelText('Ответ сотруднику');
    fireEvent.change(field, { target: { value: 'Да, можно. Оформите изменение в «Мои заявки».' } });
    const send = screen.getByRole('button', { name: 'Отправить' });
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

  test('пустое сообщение отправить нельзя', async () => {
    network();
    renderApp('/questions?id=q-1');

    const field = await screen.findByLabelText('Ответ сотруднику');
    fireEvent.change(field, { target: { value: '   ' } });
    expect((screen.getByRole('button', { name: 'Отправить' }) as HTMLButtonElement).disabled).toBe(true);
  });

  test('файл без текста — тоже ответ, и уходит файлом', async () => {
    const calls = network((path, call) =>
      call.method === 'POST' && path.includes('/reply/') ? json(200, QUESTION) : null,
    );
    renderApp('/questions?id=q-1');

    await screen.findByLabelText('Ответ сотруднику');
    const blank = new File(['%PDF-1.4'], 'Бланк.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByTestId('reply-file'), { target: { files: [blank] } });
    expect(await screen.findByText('Бланк.pdf')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(posts(calls, 'reply')).toHaveLength(1));
    const form = posts(calls, 'reply')[0]?.body as FormData;
    expect(form).toBeInstanceOf(FormData);
    expect((form.get('file') as File).name).toBe('Бланк.pdf');
    expect(form.get('client_request_id')).toBeTruthy();
  });

  test('чужой формат файла не прикладывается', async () => {
    network();
    renderApp('/questions?id=q-1');

    await screen.findByLabelText('Ответ сотруднику');
    const doc = new File(['x'], 'report.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    fireEvent.change(screen.getByTestId('reply-file'), { target: { files: [doc] } });

    expect(await screen.findByText('Можно приложить PDF, PNG или JPEG')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Отправить' }) as HTMLButtonElement).disabled).toBe(true);
  });

  test('без Telegram отправить нельзя, и это сказано прямо', async () => {
    const calls = network(() => null, {
      ...QUESTION,
      telegram: { connected: false, reason: 'not_linked', status: null },
    });
    renderApp('/questions?id=q-1');

    const field = await screen.findByLabelText('Ответ сотруднику');
    fireEvent.change(field, { target: { value: 'Ответ' } });

    expect(field.getAttribute('placeholder')).toMatch(/отправка недоступна/);
    expect((screen.getByRole('button', { name: 'Отправить' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    expect(posts(calls, 'reply')).toHaveLength(0);
  });

  test('отказ сервера «Telegram не подключён» не выдаётся за отправку', async () => {
    network((path, call) =>
      call.method === 'POST' && path.includes('/reply/')
        ? json(409, { error: { code: 'telegram_not_connected', message: 'нет', details: { reason: 'link_revoked' } } })
        : null,
    );
    renderApp('/questions?id=q-1');

    const field = await screen.findByLabelText('Ответ сотруднику');
    fireEvent.change(field, { target: { value: 'Ответ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(await screen.findByText(/сообщение не отправлено и в ленту не добавлено/)).toBeTruthy();
    expect((field as HTMLTextAreaElement).value).toBe('Ответ');
    expect(screen.queryByText('Ответ отправлен в Telegram')).toBeNull();
  });
});

describe('похожие статьи из базы знаний', () => {
  test('статьи показаны, готовый ответ вставляется в поле, но сам не отправляется', async () => {
    // Подсказка — не ответ: отправить её за кадровика значит подписать
    // его именем то, чего он не читал.
    const calls = network();
    renderApp('/questions?id=q-1');

    const hint = await screen.findByRole('region', { name: 'Похожие статьи из базы знаний' });
    // Одна статья и в черновике, и в материалах — показана один раз.
    expect(within(hint).getAllByText('Правила ежегодного отпуска')).toHaveLength(1);
    fireEvent.click(within(hint).getByRole('button', { name: 'Вставить готовый ответ' }));

    const field = screen.getByLabelText('Ответ сотруднику') as HTMLTextAreaElement;
    await waitFor(() => expect(field.value).toBe('Да, даты отпуска можно изменить до его начала.'));
    expect(posts(calls, 'reply')).toHaveLength(0);
  });

  test('без готового черновика вставлять нечего', async () => {
    network(() => null, {
      ...QUESTION,
      draft: { ...QUESTION.draft, status: 'LOW_CONFIDENCE' },
    });
    renderApp('/questions?id=q-1');

    await screen.findByLabelText('Ответ сотруднику');
    expect(screen.queryByRole('button', { name: /Вставить готовый ответ/ })).toBeNull();
  });

  test('без статей блока нет', async () => {
    network(
      (path) => (path.includes('/context/') ? json(200, { ...CONTEXT, materials: [] }) : null),
      { ...QUESTION, draft: null },
    );
    renderApp('/questions?id=q-1');

    await screen.findByLabelText('Ответ сотруднику');
    expect(screen.queryByRole('region', { name: 'Похожие статьи из базы знаний' })).toBeNull();
  });
});

describe('контекст сотрудника', () => {
  test('отдел, офис, сегодня на работе и открытые обращения — из данных', async () => {
    network();
    renderApp('/questions?id=q-1');

    const side = await screen.findByRole('complementary', { name: 'Контекст сотрудника' });
    expect(await within(side).findByText('Финансы')).toBeTruthy();
    expect(within(side).getByText('Руководитель отдела')).toBeTruthy();
    expect(within(side).getByTitle(/^Пришёл в /).textContent).toBe('Сегодня на работе');
    const open = within(side).getByRole('listitem', { name: 'Открытых обращений' });
    expect(within(open).getByText('1')).toBeTruthy();
  });

  test('без права на посещаемость строки «сегодня» нет', async () => {
    network((path) => (path.includes('/context/') ? json(200, { ...CONTEXT, today: null }) : null));
    renderApp('/questions?id=q-1');

    const side = await screen.findByRole('complementary', { name: 'Контекст сотрудника' });
    await within(side).findByText('Финансы');
    expect(within(side).queryByText('Сегодня на работе')).toBeNull();
  });
});
