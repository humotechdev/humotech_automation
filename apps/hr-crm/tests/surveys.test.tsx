/**
 * Опросы в CRM: шаблоны, рассылки и именные ответы.
 *
 * Проверяется то, что легко сделать почти правильно:
 *
 * — опрос, показанный анонимным: сводка вместо ответов;
 * — «0 из 0» вместо «ещё не отправляли»;
 * — вопрос с одним вариантом, ушедший на сервер;
 * — варианты, оставшиеся у шкалы после смены типа;
 * — круг получателей, отправленный пустым;
 * — дата отправки в прошлом;
 * — повторная отправка от второго нажатия.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';
import { plural } from '../src/pages/SurveysPage';
import { show } from '../src/pages/SurveyCampaignPage';

const clean = (url: string) => url.split('?')[0] ?? '';

const TEMPLATE = {
  id: 't-1',
  title: 'Пульс-опрос',
  description: 'Короткий опрос о работе',
  created_at: '2026-09-01T05:00:00Z',
  updated_at: '2026-09-01T05:00:00Z',
  questions: [
    {
      id: 'q1', position: 1, kind: 'SCALE', is_required: true,
      text: 'Насколько комфортно в команде?', options: null,
    },
    {
      id: 'q2', position: 2, kind: 'MULTI', is_required: true,
      text: 'Что помогает в работе?',
      options: ['Коллеги', 'График', 'Задачи'],
    },
  ],
};

const CAMPAIGN = {
  id: 'c-1',
  template_id: 't-1',
  template_title: 'Пульс-опрос',
  title: 'Пульс-опрос',
  status: 'ACTIVE',
  audience_kind: 'OFFICE',
  audience_ids: ['off-1'],
  scheduled_at: null,
  repeat_months: null,
  next_send_at: null,
  sent_at: '2026-09-17T06:00:00Z',
  total: 4,
  done: 2,
  created_at: '2026-09-17T05:00:00Z',
};

const UNSENT = {
  ...CAMPAIGN,
  id: 'c-2',
  title: 'Опрос сентября',
  status: 'DRAFT',
  sent_at: null,
  total: 0,
  done: 0,
};

const FILLED = {
  id: 'r-1',
  employee_id: 'e-1',
  full_name: 'Рахимов Далер',
  status: 'COMPLETED',
  sent_at: '2026-09-17T06:00:00Z',
  started_at: '2026-09-17T07:00:00Z',
  completed_at: '2026-09-17T07:04:00Z',
  office_name: 'Центральный',
  department_name: 'Разработка',
  answers: [
    {
      question_id: 'q1', question_text: 'Насколько комфортно в команде?',
      kind: 'SCALE', text: null, number: 5, options: null,
    },
    {
      question_id: 'q2', question_text: 'Что помогает в работе?',
      kind: 'MULTI', text: null, number: null, options: ['Коллеги'],
    },
  ],
};

const SUMMARY = {
  progress: { total: 4, sent: 4, started: 3, completed: 2 },
  questions: [
    {
      id: 'q1', text: 'Насколько комфортно в команде?', kind: 'SCALE',
      answered: 2, average: 4.5,
      distribution: { '1': 0, '2': 0, '3': 0, '4': 1, '5': 1 },
    },
  ],
  offices: [{ name: 'Центральный', total: 4, completed: 2 }],
  departments: [{ name: 'Разработка', total: 4, completed: 2 }],
};

function network(
  own: (url: string, method: string) => Response | Promise<Response> | null = () => null,
  options: { templates?: unknown[]; campaigns?: unknown[] } = {},
) {
  return fakeNetwork((url, call) => {
    const mine = own(url, call.method);
    if (mine) return mine;
    const bare = clean(url);

    if (bare.includes('/auth/')) {
      return json(200, { ...USER, permissions: ['surveys.read', 'surveys.manage'] });
    }
    if (bare.endsWith('/surveys/templates/') && call.method === 'GET') {
      return json(200, {
        items: options.templates ?? [TEMPLATE],
        next_cursor: null,
        has_more: false,
      });
    }
    if (bare.endsWith('/surveys/campaigns/') && call.method === 'GET') {
      return json(200, {
        items: options.campaigns ?? [CAMPAIGN, UNSENT],
        next_cursor: null,
        has_more: false,
      });
    }
    if (bare.endsWith('/surveys/campaigns/c-1/')) return json(200, CAMPAIGN);
    if (bare.endsWith('/surveys/campaigns/c-1/answers/')) {
      return json(200, { items: [FILLED] });
    }
    if (bare.endsWith('/surveys/campaigns/c-1/summary/')) return json(200, SUMMARY);
    if (bare.endsWith('/surveys/campaigns/c-1/recipients/')) {
      return json(200, { items: [FILLED] });
    }
    if (bare.endsWith('/offices/')) {
      return json(200, {
        items: [{ id: 'off-1', name: 'Центральный', status: 'ACTIVE' }],
        next_cursor: null,
        has_more: false,
      });
    }
    return crm(url) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

describe('список рассылок', () => {
  test('неотправленная рассылка не показывает «0 из 0»', async () => {
    // Ноль здесь означал бы, что никто не ответил, — а её не отправляли.
    network();
    renderApp('/surveys');

    const row = (await screen.findByText('Опрос сентября'))
      .closest('tr') as HTMLElement;
    expect(within(row).getByText('—')).toBeTruthy();
    expect(within(row).queryByText('0 из 0')).toBeNull();
  });

  test('отправленная показывает, сколько дошли до конца', async () => {
    network();
    renderApp('/surveys');

    const row = (await screen.findAllByText('Пульс-опрос'))[0]!
      .closest('tr') as HTMLElement;
    expect(within(row).getByText('2 из 4')).toBeTruthy();
  });

  test('без шаблонов рассылку создать не предлагают', async () => {
    network(undefined, { templates: [], campaigns: [] });
    renderApp('/surveys');

    expect(await screen.findByText(/Сначала нужен шаблон/)).toBeTruthy();
    const create = screen.getByRole('button', { name: /Создать рассылку/ });
    expect((create as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('шаблон', () => {
  async function openForm() {
    network(undefined, { templates: [], campaigns: [] });
    renderApp('/surveys?tab=templates');
    fireEvent.click(await screen.findByRole('button', { name: /Новый шаблон/ }));
    await screen.findByLabelText('Новый шаблон');
  }

  test('вопрос с одним вариантом на сервер не уходит', async () => {
    const calls = network(undefined, { templates: [], campaigns: [] });
    renderApp('/surveys?tab=templates');
    fireEvent.click(await screen.findByRole('button', { name: /Новый шаблон/ }));

    fireEvent.change(await screen.findByLabelText('Название опроса'), {
      target: { value: 'Проверка' },
    });
    fireEvent.change(screen.getByLabelText('Текст вопроса 1'), {
      target: { value: 'Где удобнее?' },
    });
    fireEvent.change(screen.getByLabelText('Вариант 1 вопроса 1'), {
      target: { value: 'Офис' },
    });
    // Второй вариант остался пустым.
    const panel = screen.getByLabelText('Новый шаблон');
    fireEvent.click(within(panel).getByRole('button', { name: /Создать шаблон/ }));

    expect(
      await screen.findByText(/хотя бы два/),
    ).toBeTruthy();
    expect(calls.some((one) => one.method === 'POST')).toBe(false);
  });

  test('смена типа на шкалу убирает варианты', async () => {
    await openForm();

    fireEvent.click(screen.getByLabelText(/Тип вопроса 1/));
    fireEvent.click(await screen.findByRole('option', { name: 'Шкала 1–5' }));

    await waitFor(() => {
      expect(screen.queryByLabelText('Вариант 1 вопроса 1')).toBeNull();
    });
  });

  test('шаблон уходит одним запросом со всеми вопросами', async () => {
    const calls = network(
      (url, method) =>
        clean(url).endsWith('/surveys/templates/') && method === 'POST'
          ? json(201, TEMPLATE)
          : null,
      { templates: [], campaigns: [] },
    );
    renderApp('/surveys?tab=templates');
    fireEvent.click(await screen.findByRole('button', { name: /Новый шаблон/ }));

    fireEvent.change(await screen.findByLabelText('Название опроса'), {
      target: { value: 'Пульс' },
    });
    fireEvent.click(screen.getByLabelText(/Тип вопроса 1/));
    fireEvent.click(await screen.findByRole('option', { name: 'Шкала 1–5' }));
    fireEvent.change(screen.getByLabelText('Текст вопроса 1'), {
      target: { value: 'Как настроение?' },
    });
    fireEvent.click(
      within(screen.getByLabelText('Новый шаблон'))
        .getByRole('button', { name: /Создать шаблон/ }),
    );

    await waitFor(() => {
      expect(calls.some((one) =>
        one.url.includes('/surveys/templates/') && one.method === 'POST')).toBe(true);
    });
    const sent = calls.find((one) =>
      one.url.includes('/surveys/templates/') && one.method === 'POST');
    expect(sent?.body).toEqual({
      title: 'Пульс',
      questions: [
        { text: 'Как настроение?', kind: 'SCALE', is_required: true },
      ],
    });
  });
});

describe('рассылка', () => {
  async function openForm() {
    const calls = network();
    renderApp('/surveys');
    // Кнопка выключена, пока не пришёл список шаблонов: нажатие раньше
    // этого просто пропадает.
    await screen.findAllByText('Пульс-опрос');
    await waitFor(() => {
      const button = screen.getByRole('button', { name: /Создать рассылку/ });
      expect((button as HTMLButtonElement).disabled).toBe(false);
    });
    fireEvent.click(screen.getByRole('button', { name: /Создать рассылку/ }));
    await screen.findByLabelText('Новая рассылка');
    return calls;
  }

  test('пустой круг получателей на сервер не уходит', async () => {
    const calls = await openForm();

    fireEvent.click(screen.getByLabelText(/Кому отправить/));
    fireEvent.click(await screen.findByRole('option', { name: /Сотрудники офиса/ }));
    fireEvent.click(screen.getByRole('button', { name: /Отправить опрос/ }));

    expect(await screen.findByText(/хотя бы одного получателя/)).toBeTruthy();
    expect(
      calls.some((one) => one.url.includes('/surveys/campaigns/')
        && one.method === 'POST'),
    ).toBe(false);
  });

  test('дата в прошлом отклоняется до запроса', async () => {
    const calls = await openForm();

    fireEvent.click(screen.getByLabelText(/Когда отправить/));
    fireEvent.click(await screen.findByRole('option', { name: /Запланировать/ }));
    fireEvent.change(await screen.findByLabelText('Дата и время'), {
      target: { value: '2020-01-01T09:00' },
    });
    // Та же подпись есть у кнопки списка «Когда отправить» — берём
    // кнопку отправки формы, а не выбор значения.
    fireEvent.click(
      screen.getByRole('button', { name: 'Запланировать' }),
    );

    expect(await screen.findByText(/задним числом/)).toBeTruthy();
    expect(
      calls.some((one) => one.url.includes('/surveys/campaigns/')
        && one.method === 'POST'),
    ).toBe(false);
  });

  test('«всем» уходит без списка получателей', async () => {
    const calls = network(
      (url, method) =>
        clean(url).endsWith('/surveys/campaigns/') && method === 'POST'
          ? json(201, CAMPAIGN)
          : null,
    );
    renderApp('/surveys');
    await screen.findAllByText('Пульс-опрос');
    await waitFor(() => {
      const button = screen.getByRole('button', { name: /Создать рассылку/ });
      expect((button as HTMLButtonElement).disabled).toBe(false);
    });
    fireEvent.click(screen.getByRole('button', { name: /Создать рассылку/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Отправить опрос/ }));

    await waitFor(() => {
      expect(calls.some((one) =>
        one.url.includes('/surveys/campaigns/') && one.method === 'POST')).toBe(true);
    });
    const sent = calls.find((one) =>
      one.url.includes('/surveys/campaigns/') && one.method === 'POST');
    expect(sent?.body).toEqual({
      template_id: 't-1',
      audience_kind: 'ALL',
      send_now: true,
    });
  });
});

describe('карточка рассылки', () => {
  test('ответы показаны с именем, офисом и отделом', async () => {
    // Опрос именной — и страница этого не прячет.
    network();
    renderApp('/surveys/c-1');

    expect(await screen.findByText('Рахимов Далер')).toBeTruthy();
    expect(screen.getByText(/Центральный · Разработка/)).toBeTruthy();
    expect(screen.getByText('5 из 5')).toBeTruthy();
    expect(screen.getByText('Коллеги')).toBeTruthy();
  });

  test('сводка не заменяет ответы и говорит об этом', async () => {
    network();
    renderApp('/surveys/c-1?tab=summary');

    expect(await screen.findByText(/не делает опрос анонимным/)).toBeTruthy();
    expect(screen.getByText(/4\.5/)).toBeTruthy();
  });

  test('повторная отправка спрашивает и не задваивается', async () => {
    let posts = 0;
    let release: (() => void) | null = null;
    const held = new Promise<void>((resolve) => { release = resolve; });
    network((url, method) => {
      if (clean(url).endsWith('/surveys/campaigns/c-1/send/') && method === 'POST') {
        posts += 1;
        return held.then(() => json(200, CAMPAIGN));
      }
      return null;
    });
    renderApp('/surveys/c-1');

    fireEvent.click(await screen.findByRole('button', { name: /Отправить ещё раз/ }));
    const ask = await screen.findByRole('dialog', { name: /Отправить ещё раз/ });
    const confirm = within(ask).getByRole('button', { name: /^Отправить$/ });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await waitFor(() => expect(posts).toBe(1));
    release!();
  });

  test('отмена объясняет, что ответы останутся', async () => {
    network();
    renderApp('/surveys/c-1');

    fireEvent.click(await screen.findByRole('button', { name: /^Отменить$/ }));
    const ask = await screen.findByRole('dialog', { name: /Отменить рассылку/ });
    expect(within(ask).getByText(/ответы останутся/)).toBeTruthy();
  });
});

describe('вспомогательное', () => {
  test('оценка читается как оценка, а не как число', () => {
    expect(show({
      question_id: 'q', question_text: '', kind: 'SCALE',
      text: null, number: 4, options: null,
    })).toBe('4 из 5');
    expect(show({
      question_id: 'q', question_text: '', kind: 'MULTI',
      text: null, number: null, options: ['А', 'Б'],
    })).toBe('А, Б');
    expect(show({
      question_id: 'q', question_text: '', kind: 'TEXT',
      text: 'своими словами', number: null, options: null,
    })).toBe('своими словами');
  });

  test('счёт по-русски не сводится к «одному или больше»', () => {
    const forms: [string, string, string] = ['вопрос', 'вопроса', 'вопросов'];
    expect(plural(1, forms)).toBe('вопрос');
    expect(plural(2, forms)).toBe('вопроса');
    expect(plural(5, forms)).toBe('вопросов');
    expect(plural(11, forms)).toBe('вопросов');
  });
});
