/**
 * Модуль «Опросы» в CRM: шаблоны, рассылки, автоматизации.
 *
 * Проверяется то, что легко сделать почти правильно:
 *
 * — порядок вкладок и то, с какой открывается раздел;
 * — опубликованный шаблон, который дали переписать на месте;
 * — рассылка по черновику: люди получили бы неготовые вопросы;
 * — «0 из 0» вместо «ещё не отправляли»;
 * — круг получателей, отправленный пустым;
 * — дата отправки в прошлом;
 * — «Пропущен» без причины: кадровику остаётся гадать;
 * — опрос, показанный анонимным: сводка вместо имён;
 * — правило, у которого «сдвиг 1, час 10» так и осталось непрочитанным.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';
import { plural } from '../src/pages/SurveysPage';
import { show } from '../src/pages/SurveyCampaignPage';
import { whenLine } from '../src/features/surveys/model';

const clean = (url: string) => url.split('?')[0] ?? '';

const PUBLISHED = {
  id: 't-1',
  title: 'Пульс-опрос',
  description: 'Короткий опрос о работе',
  status: 'PUBLISHED',
  version: 2,
  published_at: '2026-09-02T05:00:00Z',
  created_at: '2026-09-01T05:00:00Z',
  updated_at: '2026-09-02T05:00:00Z',
  questions: [
    {
      id: 'q1', position: 1, kind: 'SCALE', is_required: true,
      text: 'Насколько комфортно в команде?', options: null,
    },
    {
      id: 'q2', position: 2, kind: 'TEXT', is_required: false,
      text: 'Что бы вы изменили?', options: null,
    },
  ],
};

const DRAFT = {
  ...PUBLISHED,
  id: 't-2',
  title: 'Оценка руководителя',
  description: 'Для сотрудников отдела',
  status: 'DRAFT',
  version: 1,
  published_at: null,
};

const CAMPAIGN = {
  id: 'c-1',
  template_id: 't-1',
  template_title: 'Пульс-опрос',
  title: 'Пульс-опрос сентября',
  status: 'ACTIVE',
  audience_kind: 'OFFICE',
  audience_ids: ['off-1'],
  scheduled_at: null,
  repeat_months: null,
  remind_at: null,
  due_at: null,
  next_send_at: null,
  sent_at: '2026-09-17T06:00:00Z',
  template_version: 2,
  automation_id: null,
  total: 4,
  done: 2,
  created_at: '2026-09-17T05:00:00Z',
};

const UNSENT = {
  ...CAMPAIGN,
  id: 'c-2',
  title: 'Опрос октября',
  status: 'DRAFT',
  sent_at: null,
  total: 0,
  done: 0,
};

const RULE = {
  id: 'a-1',
  title: 'Итоги стажировки',
  template_id: 't-1',
  template_title: 'Пульс-опрос',
  trigger_kind: 'PROBATION_END',
  offset_days: 1,
  send_hour: 10,
  send_minute: 0,
  repeat_months: null,
  scope: null,
  is_active: true,
  last_run_at: null,
  next_run_at: null,
  created_at: '2026-09-01T05:00:00Z',
};

const FILLED = {
  id: 'r-1',
  employee_id: 'e-1',
  full_name: 'Рахимов Далер',
  status: 'COMPLETED',
  sent_at: '2026-09-17T06:00:00Z',
  started_at: '2026-09-17T07:00:00Z',
  completed_at: '2026-09-17T07:04:00Z',
  skip_reason: null,
  office_name: 'Центральный',
  department_name: 'Разработка',
  answers: [
    {
      question_id: 'q1', question_text: 'Насколько комфортно в команде?',
      kind: 'SCALE', text: null, number: 5, options: null,
    },
    {
      question_id: 'q2', question_text: 'Что бы вы изменили?',
      kind: 'TEXT', text: 'Больше примеров на обучении.',
      number: null, options: null,
    },
  ],
};

/** Человеку без Telegram опрос отправить некуда — это исход, не ошибка. */
const SKIPPED = {
  id: 'r-2',
  employee_id: 'e-2',
  full_name: 'Каримова Нигора',
  status: 'SKIPPED',
  sent_at: null,
  started_at: null,
  completed_at: null,
  skip_reason: 'Нет привязки Telegram',
};

const SUMMARY = {
  progress: {
    total: 4, reachable: 3, sent: 3, started: 3,
    completed: 2, skipped: 1, expired: 0,
  },
  questions: [
    {
      id: 'q1', text: 'Насколько комфортно в команде?', kind: 'SCALE',
      answered: 2, average: 4.5,
      distribution: { '1': 0, '2': 0, '3': 0, '4': 1, '5': 1 },
    },
    {
      id: 'q2', text: 'Что бы вы изменили?', kind: 'TEXT',
      answered: 1, texts: ['Больше примеров на обучении.'],
    },
  ],
  offices: [{ name: 'Центральный', total: 4, completed: 2 }],
  departments: [{ name: 'Разработка', total: 4, completed: 2 }],
};

function network(
  own: (url: string, method: string) => Response | Promise<Response> | null = () => null,
  options: {
    templates?: unknown[];
    campaigns?: unknown[];
    rules?: unknown[];
    recipients?: unknown[];
  } = {},
) {
  return fakeNetwork((url, call) => {
    const mine = own(url, call.method);
    if (mine) return mine;
    const bare = clean(url);

    if (bare.includes('/auth/')) {
      return json(200, { ...USER, permissions: ['surveys.read', 'surveys.manage'] });
    }
    if (bare.endsWith('/surveys/templates/') && call.method === 'GET') {
      const rows = options.templates ?? [PUBLISHED, DRAFT];
      // Отбор по состоянию делает сервер: мастер просит только
      // опубликованные, и черновик до него доходить не должен.
      const only = url.includes('status=PUBLISHED')
        ? rows.filter((one) => (one as { status: string }).status === 'PUBLISHED')
        : rows;
      return json(200, { items: only, next_cursor: null, has_more: false });
    }
    if (bare.endsWith('/surveys/templates/t-1/')) return json(200, PUBLISHED);
    if (bare.endsWith('/surveys/templates/t-2/')) return json(200, DRAFT);
    if (bare.endsWith('/surveys/campaigns/') && call.method === 'GET') {
      return json(200, {
        items: options.campaigns ?? [CAMPAIGN, UNSENT],
        next_cursor: null,
        has_more: false,
      });
    }
    if (bare.endsWith('/surveys/automations/') && call.method === 'GET') {
      return json(200, { items: options.rules ?? [RULE] });
    }
    if (bare.endsWith('/surveys/campaigns/c-1/')) return json(200, CAMPAIGN);
    if (bare.endsWith('/surveys/campaigns/c-1/answers/')) {
      return json(200, { items: [FILLED] });
    }
    if (bare.endsWith('/surveys/campaigns/c-1/summary/')) return json(200, SUMMARY);
    if (bare.endsWith('/surveys/campaigns/c-1/recipients/')) {
      return json(200, { items: options.recipients ?? [FILLED, SKIPPED] });
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

describe('раздел', () => {
  test('вкладки стоят в обязательном порядке', async () => {
    // Порядок — утверждение о том, с чего начинают: сначала готовят
    // вопросы, потом рассылают, потом поручают правилу.
    network();
    renderApp('/surveys');

    await screen.findAllByText('Пульс-опрос');
    const tabs = screen.getAllByRole('tab').map((one) => one.textContent ?? '');
    expect(tabs[0]).toMatch(/^Шаблоны/);
    expect(tabs[1]).toMatch(/^Рассылки/);
    expect(tabs[2]).toMatch(/^Автоматизации/);
  });

  test('раздел открывается на шаблонах, а не на рассылках', async () => {
    // Без шаблонов остальные две вкладки пусты по построению.
    network();
    renderApp('/surveys');

    const chosen = await screen.findByRole('tab', { selected: true });
    expect(chosen.textContent).toMatch(/^Шаблоны/);
  });

  test('у каждой вкладки свой адрес', async () => {
    network();
    renderApp('/surveys/automations');

    const chosen = await screen.findByRole('tab', { selected: true });
    expect(chosen.textContent).toMatch(/^Автоматизации/);
  });
});

describe('вкладки одного экрана', () => {
  test('смена вкладки не пересоздаёт лист и линию, меняется только содержимое', async () => {
    // Раньше каждая вкладка была своей страницей: лист исчезал, менял
    // высоту и появлялся заново — интерфейс дёргался.
    network();
    renderApp('/surveys');
    await screen.findAllByText('Пульс-опрос');

    const sheet = document.querySelector('.sv-sheet');
    const place = document.querySelector('.sv-pane-place');
    const ink = document.querySelector('.sv-tabs__ink');
    expect(document.querySelectorAll('.sv-tabs__ink')).toHaveLength(1);

    fireEvent.click(screen.getByRole('tab', { name: /^Рассылки/ }));
    expect(await screen.findByText('Пульс-опрос сентября')).toBeTruthy();

    expect(document.querySelector('.sv-sheet')).toBe(sheet);
    expect(document.querySelector('.sv-pane-place')).toBe(place);
    expect(document.querySelector('.sv-tabs__ink')).toBe(ink);
    expect(screen.getByRole('tabpanel', { name: 'Рассылки' })).toBeTruthy();
  });

  test('пока список грузится — заготовка строк, а не пустой экран', async () => {
    network((url) => (clean(url).endsWith('/surveys/automations/')
      ? new Promise<Response>(() => {}) as unknown as Response
      : null));
    renderApp('/surveys/automations');

    expect(await screen.findByLabelText('Загружаем список')).toBeTruthy();
    expect(screen.getByRole('tablist', { name: 'Разделы опросов' })).toBeTruthy();
  });
});

describe('шаблоны', () => {
  test('состояние, редакция и дата правки видны в строке', async () => {
    network();
    renderApp('/surveys');

    const row = (await screen.findByText('Пульс-опрос')).closest('li') as HTMLElement;
    // Состояние и редакция одной подписью: «Опубликован · v2».
    expect(within(row).getByText('Опубликован · v2')).toBeTruthy();
    expect(within(row).getByText('2 вопроса')).toBeTruthy();
    // Дата и время — одной фразой, через «в».
    expect(within(row).getByText(/^Обновлён \d+ \S+ в \d{2}:\d{2}$/)).toBeTruthy();

    const draft = screen.getByText('Оценка руководителя').closest('li') as HTMLElement;
    expect(within(draft).getByText(/^Черновик · v\d+$/)).toBeTruthy();
    // Список — строки, а не таблица с шапкой колонок.
    expect(screen.queryByRole('table')).toBeNull();
  });

  test('второстепенные действия живут в меню, а не в строке', async () => {
    // Постоянная кнопка «Копия» спорила с основным действием
    // строки и читалась как равная ему.
    network();
    renderApp('/surveys');

    await screen.findByText('Пульс-опрос');
    expect(screen.queryByRole('button', { name: 'Копия' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Действия: Пульс-опрос' }));
    const menu = await screen.findByRole('dialog');
    expect(within(menu).getByRole('button', { name: 'Открыть' })).toBeTruthy();
    expect(within(menu).getByRole('button', { name: 'Дублировать' })).toBeTruthy();
    // Опубликованный правят новой редакцией, черновик — как есть.
    expect(within(menu).getByRole('button', { name: 'Создать новую версию' })).toBeTruthy();
    expect(within(menu).queryByRole('button', { name: 'Переименовать' })).toBeNull();
  });

  test('у черновика в меню — переименование', async () => {
    network();
    renderApp('/surveys');

    await screen.findByText('Оценка руководителя');
    fireEvent.click(
      screen.getByRole('button', { name: 'Действия: Оценка руководителя' }),
    );
    const menu = await screen.findByRole('dialog');
    expect(within(menu).getByRole('button', { name: 'Переименовать' })).toBeTruthy();
    expect(
      within(menu).queryByRole('button', { name: 'Создать новую версию' }),
    ).toBeNull();
  });

  test('переименование уходит одним полем', async () => {
    const calls = network(
      (url, method) =>
        clean(url).endsWith('/surveys/templates/t-2/') && method === 'PATCH'
          ? json(200, DRAFT)
          : null,
    );
    renderApp('/surveys');

    await screen.findByText('Оценка руководителя');
    fireEvent.click(
      screen.getByRole('button', { name: 'Действия: Оценка руководителя' }),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Переименовать' }));
    fireEvent.change(await screen.findByLabelText('Название'), {
      target: { value: 'Оценка руководителя, осень' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => {
      expect(calls.some((one) => one.method === 'PATCH')).toBe(true);
    });
    const sent = calls.find((one) => one.method === 'PATCH');
    expect(sent?.body).toEqual({ title: 'Оценка руководителя, осень' });
  });

  test('поиск отбирает строки, а число на вкладке идёт за ним', async () => {
    network();
    renderApp('/surveys');

    await screen.findByText('Пульс-опрос');
    fireEvent.change(screen.getByLabelText('Найти шаблон'), {
      target: { value: 'Пульс' },
    });

    await waitFor(() => expect(screen.queryByText('Оценка руководителя')).toBeNull());
    expect(screen.getByText('1 шаблон')).toBeTruthy();
    // Число на вкладке и число над списком — одно и то же число.
    const chosen = screen.getByRole('tab', { selected: true });
    expect(chosen.textContent).toMatch(/1$/);
  });

  test('если отбор ничего не нашёл, поиск остаётся, а сброс возвращает список', async () => {
    network();
    renderApp('/surveys');

    await screen.findByText('Пульс-опрос');
    fireEvent.change(screen.getByLabelText('Найти шаблон'), {
      target: { value: 'такого нет' },
    });

    expect(await screen.findByText('По этим условиям шаблонов нет')).toBeTruthy();
    // Поле поиска на месте: человек правит запрос, а не начинает заново.
    expect(screen.getByLabelText('Найти шаблон')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));
    expect(await screen.findByText('Оценка руководителя')).toBeTruthy();
  });

  test('без шаблонов показывают одно действие, а не пустую таблицу', async () => {
    network(undefined, { templates: [], campaigns: [], rules: [] });
    renderApp('/surveys');

    expect(await screen.findByText('Шаблонов пока нет')).toBeTruthy();
    expect(screen.getByText(/Создайте первый шаблон/)).toBeTruthy();
    // Искать не в чем — поиска над пустым листом нет.
    expect(screen.queryByLabelText('Найти шаблон')).toBeNull();
    // Таблицы нет вовсе: пустая сетка с шапкой колонок говорит
    // «здесь должны быть данные, но их нет» — как об ошибке.
    expect(screen.queryByRole('table')).toBeNull();
  });

  test('опубликованный правят новой версией, а не на месте', async () => {
    // По нему уже спрашивали людей: переписанный вопрос сделал бы
    // прежние ответы ответами на другой вопрос.
    network();
    renderApp('/surveys/templates/t-1');

    expect(
      await screen.findByRole('button', { name: /Создать новую версию/ }),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Опубликовать$/ })).toBeNull();
    expect(screen.getByText(/Изменения создаются только в новой версии/)).toBeTruthy();
  });

  test('черновик правится и публикуется', async () => {
    network();
    renderApp('/surveys/templates/t-2');

    expect(await screen.findByRole('button', { name: /^Опубликовать$/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Создать новую версию/ })).toBeNull();
  });

  test('вопрос с одним вариантом на сервер не уходит', async () => {
    const calls = network(undefined, { templates: [], campaigns: [], rules: [] });
    renderApp('/surveys/templates/new');

    // Название — на вкладке «Настройки»: в конструкторе правят вопросы.
    fireEvent.click(await screen.findByRole('tab', { name: 'Настройки' }));
    fireEvent.change(await screen.findByLabelText('Название шаблона'), {
      target: { value: 'Проверка' },
    });
    fireEvent.click(screen.getByRole('tab', { name: 'Конструктор' }));
    fireEvent.change(await screen.findByLabelText('Текст вопроса 1'), {
      target: { value: 'Где удобнее?' },
    });
    fireEvent.change(screen.getByLabelText('Вариант 1 вопроса 1'), {
      target: { value: 'Офис' },
    });
    // Второй вариант остался пустым.
    fireEvent.click(screen.getByRole('button', { name: /Сохранить/ }));

    expect((await screen.findAllByText(/хотя бы два/)).length).toBeGreaterThan(0);
    expect(calls.some((one) => one.method === 'POST')).toBe(false);
  });

  test('смена типа на шкалу убирает варианты', async () => {
    network(undefined, { templates: [], campaigns: [], rules: [] });
    renderApp('/surveys/templates/new');

    fireEvent.click(await screen.findByLabelText(/Тип вопроса 1/));
    fireEvent.click(await screen.findByRole('option', { name: 'Шкала оценки' }));

    await waitFor(() => {
      expect(screen.queryByLabelText('Вариант 1 вопроса 1')).toBeNull();
    });
  });

  test('шаблон уходит одним запросом со всеми вопросами', async () => {
    const calls = network(
      (url, method) =>
        clean(url).endsWith('/surveys/templates/') && method === 'POST'
          ? json(201, DRAFT)
          : null,
      { templates: [], campaigns: [], rules: [] },
    );
    renderApp('/surveys/templates/new');

    fireEvent.click(await screen.findByRole('tab', { name: 'Настройки' }));
    fireEvent.change(await screen.findByLabelText('Название шаблона'), {
      target: { value: 'Пульс' },
    });
    fireEvent.click(screen.getByRole('tab', { name: 'Конструктор' }));
    fireEvent.click(await screen.findByLabelText(/Тип вопроса 1/));
    fireEvent.click(await screen.findByRole('option', { name: 'Шкала оценки' }));
    fireEvent.change(screen.getByLabelText('Текст вопроса 1'), {
      target: { value: 'Как настроение?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Сохранить/ }));

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

  test('предпросмотр показывает вопрос так, как его увидят', async () => {
    // Вопрос пишут в широком поле, а читают в узком пузыре с телефона.
    network(undefined, { templates: [], campaigns: [], rules: [] });
    renderApp('/surveys/templates/new');

    fireEvent.change(await screen.findByLabelText('Текст вопроса 1'), {
      target: { value: 'Насколько понятны задачи?' },
    });

    const preview = screen.getByLabelText('Предпросмотр в Telegram');
    expect(within(preview).getByText('Насколько понятны задачи?')).toBeTruthy();
  });
});

describe('рассылки', () => {
  test('неотправленная рассылка не показывает «0 из 0»', async () => {
    // Ноль здесь означал бы, что никто не ответил, — а её не отправляли.
    network();
    renderApp('/surveys/campaigns');

    const row = (await screen.findByText('Опрос октября'))
      .closest('li') as HTMLElement;
    expect(within(row).getByText('Ещё не отправлена')).toBeTruthy();
    expect(within(row).queryByText('0 из 0')).toBeNull();
  });

  test('отправленная показывает, сколько дошли до конца', async () => {
    network();
    renderApp('/surveys/campaigns');

    const row = (await screen.findByText('Пульс-опрос сентября'))
      .closest('li') as HTMLElement;
    expect(within(row).getByText('2 из 4')).toBeTruthy();
  });

  test('без опубликованного шаблона рассылку создать не предлагают', async () => {
    // Черновик правят прямо сейчас: рассылка по нему спросила бы людей
    // о том, о чём спрашивать ещё не собирались.
    network(undefined, { templates: [DRAFT], campaigns: [], rules: [] });
    renderApp('/surveys/campaigns');

    expect(await screen.findByText(/Сначала опубликуйте шаблон/)).toBeTruthy();
    const create = screen.getByRole('button', { name: /Создать рассылку/ });
    expect((create as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('мастер рассылки', () => {
  async function toStep(step: number) {
    const calls = network();
    renderApp('/surveys/campaigns/new');
    await screen.findByText('Пульс-опрос');
    fireEvent.click(screen.getByRole('radio', { name: /Пульс-опрос/ }));
    for (let at = 0; at < step; at += 1) {
      fireEvent.click(screen.getByRole('button', { name: /Далее/ }));
    }
    return calls;
  }

  test('черновик в выборе шаблона не предлагается', async () => {
    network();
    renderApp('/surveys/campaigns/new');

    await screen.findByText('Пульс-опрос');
    expect(screen.queryByText('Оценка руководителя')).toBeNull();
  });

  test('без выбранного шаблона дальше не пускают', async () => {
    network();
    renderApp('/surveys/campaigns/new');

    await screen.findByText('Пульс-опрос');
    fireEvent.click(screen.getByRole('button', { name: /Далее/ }));

    expect((await screen.findByRole('alert')).textContent).toBe('Выберите шаблон');
  });

  test('пустой круг получателей дальше не пропускают', async () => {
    await toStep(1);

    // Выбираем «по офису» и никого не отмечаем.
    fireEvent.click(screen.getByRole('radio', { name: /По офису/ }));
    fireEvent.click(screen.getByRole('button', { name: /Далее/ }));

    expect(await screen.findByText(/хотя бы одного получателя/)).toBeTruthy();
  });

  test('дата в прошлом отклоняется до запроса', async () => {
    const calls = await toStep(2);

    fireEvent.click(screen.getByRole('radio', { name: /Запланировать отправку/ }));
    const day = await screen.findByLabelText('Дата отправки');
    fireEvent.change(day, { target: { value: '01.01.2020' } });
    fireEvent.blur(day);
    fireEvent.click(screen.getByRole('button', { name: /Далее/ }));

    // Календарь не берёт прошедший день, а без дня дальше не пускают.
    expect(await screen.findByText(/не раньше/)).toBeTruthy();
    expect(await screen.findByText('Укажите дату и время')).toBeTruthy();
    expect(
      calls.some((one) => clean(one.url).endsWith('/surveys/campaigns/')
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
    renderApp('/surveys/campaigns/new');

    await screen.findByText('Пульс-опрос');
    fireEvent.click(screen.getByRole('radio', { name: /Пульс-опрос/ }));
    fireEvent.click(screen.getByRole('button', { name: /Далее/ }));
    fireEvent.click(screen.getByRole('button', { name: /Далее/ }));
    fireEvent.click(screen.getByRole('button', { name: /Далее/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Отправить рассылку/ }));
    const ask = await screen.findByRole('dialog', { name: /Отправить рассылку/ });
    fireEvent.click(within(ask).getByRole('button', { name: /^Отправить$/ }));

    // Подсчёт круга тоже POST, но в /preview/ — он не создаёт рассылку.
    const made = (one: { url: string; method: string }) =>
      clean(one.url).endsWith('/surveys/campaigns/') && one.method === 'POST';
    await waitFor(() => {
      expect(calls.some(made)).toBe(true);
    });
    const sent = calls.find(made);
    expect(sent?.body).toEqual({
      template_id: 't-1',
      audience_kind: 'ALL',
      send_now: true,
    });
  });

  test('последний шаг показывает, что именно уйдёт', async () => {
    // Отправка необратима: сообщение уже в Telegram, и отозвать его
    // нельзя. Ошибку надо увидеть до нажатия.
    await toStep(3);

    expect(await screen.findByText('Проверьте рассылку')).toBeTruthy();
    const review = screen.getByText('Шаблон', { selector: 'dt' }).closest('div') as HTMLElement;
    expect(within(review).getByText('Пульс-опрос')).toBeTruthy();
    const whom = screen.getByText('Кому', { selector: 'dt' }).closest('div') as HTMLElement;
    expect(within(whom).getByText(/Все действующие/)).toBeTruthy();
  });
});

describe('результаты рассылки', () => {
  test('ответы показаны с именем, офисом и отделом', async () => {
    // Опрос именной — и страница этого не прячет.
    network();
    renderApp('/surveys/campaigns/c-1');

    fireEvent.click(await screen.findByRole('tab', { name: /Вопросы/ }));
    const said = (await screen.findByText('Больше примеров на обучении.'))
      .closest('li') as HTMLElement;
    expect(within(said).getByText('Рахимов Далер')).toBeTruthy();
    const scale = screen.getByLabelText('Насколько комфортно в команде?');
    expect(within(scale).getByText('4,5')).toBeTruthy();
  });

  test('ответы одного человека раскрываются рядом с фамилией', async () => {
    // Средняя оценка отвечает «как в целом», а разговаривать
    // идут с конкретным человеком.
    network();
    renderApp('/surveys/campaigns/c-1');

    fireEvent.click(await screen.findByRole('tab', { name: /Получатели/ }));
    const list = await screen.findByLabelText('Получатели');
    const row = (await within(list).findByText('Рахимов Далер')).closest('li') as HTMLElement;
    expect(within(row).getByText(/Центральный · Разработка/)).toBeTruthy();
    fireEvent.click(within(row).getByRole('button', { name: 'Ответы' }));

    expect(await within(row).findByText('5 из 5')).toBeTruthy();
    expect(within(row).getByText('Насколько комфортно в команде?')).toBeTruthy();
  });

  test('у пропущенного раскрывать нечего', async () => {
    // У него нет ответов и не будет: опрос до него не дошёл.
    network();
    renderApp('/surveys/campaigns/c-1');

    fireEvent.click(await screen.findByRole('tab', { name: /Получатели/ }));
    const list = await screen.findByLabelText('Получатели');
    const row = (await within(list).findByText('Каримова Нигора')).closest('li') as HTMLElement;
    expect(within(row).queryByRole('button')).toBeNull();
  });

  test('пропущенный виден отдельно и с причиной', async () => {
    // Без причины строка «Пропущен» оставляет кадровика гадать, а без
    // самого исхода она висела бы в «ожидает» вечно.
    network();
    renderApp('/surveys/campaigns/c-1');

    const row = (await screen.findByText('Нет привязки Telegram'))
      .closest('li') as HTMLElement;
    expect(within(row).getByText('Не доставлено')).toBeTruthy();
    expect(within(row).getByText('Каримова Нигора')).toBeTruthy();
  });

  test('доля считается от достижимых, а не от всех', async () => {
    // «2 из 4» при одном недостижимом занижает результат и ставит
    // кадровику не тот вопрос.
    network();
    renderApp('/surveys/campaigns/c-1');

    expect(await screen.findByText('2 из 3')).toBeTruthy();
  });

  test('сводка не заменяет имена и говорит об этом', async () => {
    network();
    renderApp('/surveys/campaigns/c-1');

    fireEvent.click(await screen.findByRole('tab', { name: /Вопросы/ }));
    expect(await screen.findByText(/не делает опрос анонимным/)).toBeTruthy();
  });

  test('повторная отправка спрашивает и не задваивается', async () => {
    let posts = 0;
    let release: (() => void) | null = null;
    const held = new Promise<void>((resolve) => { release = resolve; });
    // Досрочная отправка запланированной рассылки: второй клик по
    // подтверждению не должен отправить приглашение второй раз.
    const planned = {
      ...CAMPAIGN, status: 'SCHEDULED', sent_at: null, total: 0, done: 0,
      scheduled_at: '2099-01-10T05:00:00Z',
    };
    network((url, method) => {
      if (clean(url).endsWith('/surveys/campaigns/c-1/send/') && method === 'POST') {
        posts += 1;
        return held.then(() => json(200, CAMPAIGN));
      }
      if (clean(url).endsWith('/surveys/campaigns/c-1/') && method === 'GET') {
        return json(200, planned);
      }
      return null;
    });
    renderApp('/surveys/campaigns/c-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Ещё действия' }));
    fireEvent.click(await screen.findByRole('button', { name: /Отправить сейчас/ }));
    const ask = await screen.findByRole('dialog', { name: /Отправить сейчас/ });
    const confirm = within(ask).getByRole('button', { name: /^Отправить$/ });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await waitFor(() => expect(posts).toBe(1));
    release!();
  });

  test('отправка ещё раз у идущей рассылки спрашивает и не задваивается', async () => {
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
    renderApp('/surveys/campaigns/c-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Ещё действия' }));
    fireEvent.click(await screen.findByRole('button', { name: /Отправить ещё раз/ }));
    const ask = await screen.findByRole('dialog', { name: /Отправить ещё раз/ });
    expect(within(ask).getByText(/второго сообщения не будет/)).toBeTruthy();
    const confirm = within(ask).getByRole('button', { name: /^Отправить$/ });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await waitFor(() => expect(posts).toBe(1));
    release!();
  });

  test('отмена объясняет, что ответы останутся', async () => {
    network();
    renderApp('/surveys/campaigns/c-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Ещё действия' }));
    fireEvent.click(await screen.findByRole('button', { name: /Отменить рассылку/ }));
    const ask = await screen.findByRole('dialog', { name: /Отменить рассылку/ });
    expect(within(ask).getByText(/ответы останутся/)).toBeTruthy();
  });
});

describe('автоматизации', () => {
  test('список говорит, что и когда отправится', async () => {
    network();
    renderApp('/surveys/automations');

    expect(await screen.findByText('Итоги стажировки')).toBeTruthy();
    expect(screen.getByText('Окончание стажировки')).toBeTruthy();
    expect(screen.getByText('На следующий день, 10:00')).toBeTruthy();
  });

  test('правило выключают, а не удаляют', async () => {
    const calls = network(
      (url, method) =>
        clean(url).endsWith('/surveys/automations/a-1/disable/') && method === 'POST'
          ? json(200, { ...RULE, is_active: false })
          : null,
    );
    renderApp('/surveys/automations');

    const toggle = await screen.findByLabelText(/включено/);
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(calls.some((one) => one.url.includes('/disable/'))).toBe(true);
    });
  });

  test('удаление предупреждает про историю отправок', async () => {
    network();
    renderApp('/surveys/automations');

    fireEvent.click(
      await screen.findByRole('button', { name: 'Действия: Итоги стажировки' }),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Удалить' }));
    const ask = await screen.findByRole('dialog', { name: /Удалить автоматизацию/ });
    expect(within(ask).getByText(/нельзя потерять вместе с правилом/)).toBeTruthy();
  });

  test('панель пересказывает настройку обычными словами', async () => {
    // «Сдвиг 1, час 10» прочитать правильно нельзя: правило сработает
    // через месяц, и ошибку видно только сейчас.
    network();
    renderApp('/surveys/automations/new');

    const how = await screen.findByLabelText('Как это будет работать');
    expect(within(how).getByText(/На следующий день, 10:00/)).toBeTruthy();
  });

  test('у регулярной отправки период обязателен', async () => {
    const calls = network();
    renderApp('/surveys/automations/new');

    fireEvent.click(await screen.findByLabelText(/^Событие:/));
    fireEvent.click(await screen.findByRole('option', { name: /Регулярно/ }));
    // Период сбрасывается вручную: правило без него не должно уйти.
    fireEvent.click(screen.getByLabelText(/^Период:/));
    fireEvent.click(await screen.findByRole('option', { name: 'Выберите период' }));
    fireEvent.click(screen.getByRole('button', { name: /Сохранить/ }));

    expect(await screen.findByText(/нужен период/)).toBeTruthy();
    expect(calls.some((one) => one.method === 'POST')).toBe(false);
  });

  const HISTORY = {
    items: [
      {
        id: 'h-1', campaign_id: 'c-9', employee_id: 'e-1', full_name: 'Рахимов Далер',
        event_kind: 'PROBATION_END', event_day: '2026-09-20', fired_at: '2026-09-21T05:00:00Z',
        status: 'COMPLETED', skip_reason: null, completed_at: '2026-09-21T07:00:00Z',
      },
      {
        id: 'h-2', campaign_id: 'c-9', employee_id: 'e-2', full_name: 'Каримова Нигора',
        event_kind: 'PROBATION_END', event_day: '2026-09-20', fired_at: '2026-09-21T05:00:00Z',
        status: 'SKIPPED', skip_reason: 'Нет привязки Telegram', completed_at: null,
      },
    ],
    stats: { fired: 2, sent: 1, completed: 1, skipped: 1 },
  };

  function ruleNetwork() {
    return network((url, method) => {
      const bare = clean(url);
      if (bare.endsWith('/surveys/automations/a-1/history/')) return json(200, HISTORY);
      if (bare.endsWith('/surveys/automations/a-1/') && method === 'GET') return json(200, RULE);
      return null;
    });
  }

  test('карточка правила считает срабатывания по истории, а не выдумывает', async () => {
    ruleNetwork();
    renderApp('/surveys/automations/a-1');

    const stats = await screen.findByLabelText('За последние 30 дней');
    expect(await within(stats).findByText('Сработало')).toBeTruthy();
    await waitFor(() => {
      expect(within(stats).getAllByText('1').length).toBe(3);
    });
    expect(within(stats).getByText('2')).toBeTruthy();
  });

  test('в истории пропуск стоит с причиной', async () => {
    ruleNetwork();
    renderApp('/surveys/automations/a-1?tab=history');

    const table = await screen.findByLabelText('История срабатываний');
    const row = (await within(table).findByText('Каримова Нигора')).closest('li') as HTMLElement;
    expect(within(row).getByText('Нет привязки Telegram')).toBeTruthy();
    expect(within(row).getByText('Пропущено')).toBeTruthy();
  });

  test('настройки открываются формой с текущими значениями', async () => {
    ruleNetwork();
    renderApp('/surveys/automations/a-1?tab=settings');

    expect(await screen.findByLabelText(/^Событие: Окончание стажировки/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'На следующий день', pressed: true })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Сохранить автоматизацию' })).toBeTruthy();
  });

  test('без опубликованного шаблона правило создать не предлагают', async () => {
    network(undefined, { templates: [DRAFT], campaigns: [], rules: [] });
    renderApp('/surveys/automations');

    expect(await screen.findByText(/черновик рассылать не вправе/)).toBeTruthy();
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

  test('время отправки читается словами, а не числами', () => {
    const base = { send_hour: 10, send_minute: 0, repeat_months: null };
    expect(whenLine({ ...base, trigger_kind: 'PROBATION_END', offset_days: 0 }))
      .toBe('В день события, 10:00');
    expect(whenLine({ ...base, trigger_kind: 'PROBATION_END', offset_days: 1 }))
      .toBe('На следующий день, 10:00');
    expect(whenLine({ ...base, trigger_kind: 'DAYS_AFTER_HIRE', offset_days: 30 }))
      .toBe('Через 30 дней, 10:00');
    expect(whenLine({
      trigger_kind: 'SCHEDULE', offset_days: 0,
      send_hour: 9, send_minute: 30, repeat_months: 3,
    })).toBe('Раз в 3 месяца, 09:30');
  });

  test('счёт по-русски не сводится к «одному или больше»', () => {
    const forms: [string, string, string] = ['вопрос', 'вопроса', 'вопросов'];
    expect(plural(1, forms)).toBe('вопрос');
    expect(plural(2, forms)).toBe('вопроса');
    expect(plural(5, forms)).toBe('вопросов');
    expect(plural(11, forms)).toBe('вопросов');
  });
});
