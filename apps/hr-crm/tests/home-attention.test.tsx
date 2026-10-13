/**
 * «Фокус HR» на главной: пять пунктов и то, что открывается по каждому.
 *
 * Баг, ради которого написан файл: ссылка открывала нужную страницу и
 * выбирала вкладку, а список был пуст — в адрес уходил `status=open`,
 * которого сервер не знает. Ручной выбор той же вкладки статус
 * сбрасывал, и данные «появлялись».
 *
 * Сервер здесь подменён: очередь и присутствие отвечают из набора строк,
 * отбирая их по тем же параметрам, что и настоящий (`kind`, `type`,
 * `request_kind`, `status`, `stage`, `certificate`, `office_id`,
 * `state`, `started`). Совпадение этих правил с сервером проверяют
 * backend-тесты `django_tests/test_home_attention.py`. Живой стенд с
 * входом в этих проверках не участвовал.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { describe, expect, test } from 'vitest';

import { App } from '../src/app/App';
import { today } from '../src/features/dashboard/data';
import { SessionProvider } from '../src/features/auth/session';
import { USER, fakeNetwork, json, pick, type Call } from './helpers';
import { serveQueue } from './queue-fake';

const DAY = today();

// --- присутствие -----------------------------------------------------------------

const presenceRow = (id: string, name: string, state: string, started: boolean) => ({
  employee_id: id, full_name: name, employee_number: null, office_id: 'o-1', office_name: 'Ташкент (HQ)',
  department_name: null, position_name: null, state, first_entry_at: null, last_exit_at: null,
  seconds: 0, open_session_id: null, late_minutes: null,
  scheduled_start: started ? '09:00:00' : '18:00:00', scheduled_end: '22:00:00',
  absence_code: null, absence_name: null, notice_kind: null, notice_comment: null,
  conflicting_marks: false, outside_geofence: false, intervals: [], field_work: null,
  shift_started: started,
});

const PEOPLE = [
  presenceRow('p-missing', 'Нет Отметки', 'NOT_COME', true),
  // Смена с шести вечера: утром он ещё ни к чему не опоздал.
  presenceRow('p-later', 'Смена Вечером', 'NOT_COME', false),
  { ...presenceRow('p-came', 'Пришёл Вовремя', 'LEFT', true), first_entry_at: `${DAY}T04:00:00Z`, last_exit_at: `${DAY}T05:00:00Z`, seconds: 3600 },
];

const card = (key: string, value: number, params: Record<string, string> = {}) => ({
  key, title: key, value, attention: false, endpoint: '/api/v1/attendance/presence',
  params: { date: DAY, ...params },
});

const DASHBOARD = {
  date: DAY, timezone: 'Asia/Tashkent', warnings: [],
  cards: [
    card('active_employees', 3), card('should_work_today', 3), card('came', 1),
    card('in_office', 0, { state: 'IN_OFFICE' }),
    // Общее «не пришли» — двое, из них у одного смена ещё не началась.
    card('not_come', 2, { state: 'NOT_COME' }),
    card('not_come_started', 1, { state: 'NOT_COME', started: 'true' }),
  ],
};

type Handler = (url: URL, call: Call) => Response | Promise<Response> | null;

function network(own: Handler = () => null) {
  return fakeNetwork((path, call) => {
    const url = new URL(path, 'http://x');
    const mine = own(url, call);
    if (mine) return mine;
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/dashboard')) return json(200, DASHBOARD);
    if (path.includes('/regions')) return json(200, { items: [{ id: 'r-1', name: 'Ташкент' }, { id: 'r-2', name: 'Бухара' }] });
    if (path.includes('/offices')) {
      return json(200, { items: [
        { id: 'o-1', name: 'Ташкент (HQ)', status: 'ACTIVE', region_id: 'r-1' },
        { id: 'o-2', name: 'Бухара', status: 'ACTIVE', region_id: 'r-2' },
      ] });
    }
    if (path.includes('/requests/counts')) return json(200, { open: 6, all: 10, leave: 3, sick: 5, fixes: 3, rejected: 2 });
    if (url.pathname.endsWith('/requests')) return json(200, serveQueue(url));
    if (path.includes('/attendance/presence')) {
      const counts = { NOT_COME: 2, LEFT: 1 };
      return json(200, { date: DAY, timezone: 'Asia/Tashkent', counts, total: PEOPLE.length, truncated: false, items: PEOPLE });
    }
    if (path.includes('/analytics')) {
      return json(200, { period: { first: DAY, last: DAY, timezone: 'Asia/Tashkent' }, headcount: 3, series: [],
        summary: { difference_points: 0 } });
    }
    return json(200, { items: [], next_cursor: null, has_more: false });
  });
}

/** Кнопки, которыми тест ходит по истории — как «Назад» и новая ссылка браузера. */
function Steer() {
  const navigate = useNavigate();
  return (
    <div>
      <button type="button" onClick={() => navigate(-1)}>test-back</button>
      <button type="button" onClick={() => navigate(1)}>test-forward</button>
      <button type="button" onClick={() => navigate(String(window.__goto))}>test-go</button>
    </div>
  );
}

declare global {
  interface Window { __goto?: string }
}

function open(path: string | string[]) {
  const entries = Array.isArray(path) ? path : [path];
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
      <SessionProvider>
        <App />
        <Steer />
      </SessionProvider>
    </MemoryRouter>,
  );
}

function go(path: string) {
  window.__goto = path;
  fireEvent.click(screen.getByRole('button', { name: 'test-go' }));
}

const queueCalls = (calls: Call[]) =>
  calls.filter((c) => new URL(c.url, 'http://x').pathname.endsWith('/requests'));
const lastQueue = (calls: Call[]) => {
  const all = queueCalls(calls);
  return Object.fromEntries(new URL(all[all.length - 1]!.url, 'http://x').searchParams);
};

const queue = () => screen.getByRole('region', { name: 'Очередь заявок' });
const selectedTab = () =>
  within(screen.getByRole('tablist', { name: 'Вид заявок' })).getAllByRole('tab')
    .filter((tab) => tab.getAttribute('aria-selected') === 'true')
    .map((tab) => tab.textContent?.replace(/\d+/g, ''));
const names = () => [...queue().querySelectorAll('.rq-row__who b')].map((one) => one.textContent);

async function focusLink(title: RegExp) {
  const focus = await screen.findByRole('region', { name: 'Фокус HR' });
  return (await within(focus).findByText(title)).closest('a') as HTMLAnchorElement;
}

// Сколько показывает главная и что открывается по ссылке.
const REVIEW = 'HR_REVIEW,PENDING';
const CASES = [
  {
    // «Ждут справку» — это пункт «Ожидают документа»: возвращённая
    // справка — уже «Исправление».
    title: /больничн\S* жд\S* справку/, count: '1', tab: 'Больничные', status: 'Ожидают документа',
    params: { kind: 'absence', type: 'SICK_LEAVE', request_kind: 'CREATE,EXTEND', stage: 'WAITING_DOCUMENTS' },
    shown: ['Ждёт Справку'], chip: null,
  },
  {
    title: /справк\S* жд\S* проверки/, count: '1', tab: 'Больничные', status: 'На рассмотрении',
    params: { kind: 'absence', type: 'SICK_LEAVE', request_kind: 'CREATE,EXTEND', stage: REVIEW, certificate: 'PENDING' },
    shown: ['Справка Напроверке'], chip: 'Справка ждёт проверки',
  },
  {
    title: /отпуск\S* жд\S* решения/, count: '2', tab: 'Отпуска', status: 'На рассмотрении',
    params: { kind: 'absence', type: 'ANNUAL_LEAVE,UNPAID_LEAVE', request_kind: 'CREATE,EXTEND', stage: REVIEW },
    shown: ['Отпуск Ждёт', 'Отпуск Бухара'], chip: null,
  },
  {
    title: /исправлени\S* отмет/, count: '1', tab: 'Исправления', status: 'На рассмотрении',
    params: { kind: 'fixes', stage: REVIEW },
    shown: ['Исправление Ждёт'], chip: null,
  },
] as const;

describe('переходы из «Фокуса HR» в заявки (сервер подменён)', () => {
  test.each(CASES)('$title → вкладка, отбор, запрос и строки сразу, без ручного клика', async (one) => {
    const calls = network();
    open('/');

    const link = await focusLink(one.title);
    expect(link.querySelector('.hm-focus__n')?.textContent).toBe(one.count);
    fireEvent.click(link);

    await waitFor(() => expect(names()).toEqual(one.shown));
    expect(selectedTab()).toEqual([one.tab]);
    const sent = lastQueue(calls);
    for (const [key, value] of Object.entries(one.params)) expect(sent[key]).toBe(value);
    // Чужих условий запрос не несёт: ни курсора, ни статусов базы.
    expect(sent['cursor']).toBeUndefined();
    expect(sent['status']).toBeUndefined();
    if (!('certificate' in one.params)) expect(sent['certificate']).toBeUndefined();
    // Пункт в поле «Статус» — понятный человеку, а не «open».
    expect(screen.getByLabelText(/^Статус/).textContent).toContain(one.status);
    if (one.chip) expect(screen.getByRole('button', { name: `Снять фильтр: ${one.chip}` })).toBeTruthy();
  });

  test('чего в выборках быть не должно: принятая справка, решённые заявки', async () => {
    network();
    open('/');
    fireEvent.click(await focusLink(/справк\S* жд\S* проверки/));
    await waitFor(() => expect(names()).toEqual(['Справка Напроверке']));
    for (const name of ['Справка Принята', 'Больничный Подтверждён', 'Ждёт Справку', 'Вернули Справку']) {
      expect(within(queue()).queryByText(name)).toBeNull();
    }

    go('/');
    fireEvent.click(await focusLink(/отпуск\S* жд\S* решения/));
    await waitFor(() => expect(names()).toContain('Отпуск Ждёт'));
    expect(within(queue()).queryByText('Отпуск Решён')).toBeNull();

    go('/');
    fireEvent.click(await focusLink(/исправлени\S* отмет/));
    await waitFor(() => expect(names()).toEqual(['Исправление Ждёт']));
    expect(within(queue()).queryByText('Исправление Рассмотрено')).toBeNull();
  });

  test('ссылка несёт офис главной — и список сужен им же, как и число', async () => {
    const calls = network();
    open('/');
    await screen.findByRole('region', { name: 'Фокус HR' });
    await pick(/^Офис: Все офисы/, 'Бухара');

    const link = await focusLink(/отпуск\S* жд\S* решения/);
    expect(link.querySelector('.hm-focus__n')?.textContent).toBe('1');
    fireEvent.click(link);

    await waitFor(() => expect(names()).toEqual(['Отпуск Бухара']));
    expect(lastQueue(calls)['office_id']).toBe('o-2');
  });

  test('ручной выбор вкладки и статуса = переход с главной', async () => {
    const calls = network();
    open('/requests');
    await waitFor(() => expect(queueCalls(calls).length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('tab', { name: /Отпуска/ }));
    await pick('Статус', 'На рассмотрении');
    await waitFor(() => expect(names()).toEqual(['Отпуск Ждёт', 'Отпуск Бухара']));
    const manual = lastQueue(calls);

    go('/');
    fireEvent.click(await focusLink(/отпуск\S* жд\S* решения/));
    await waitFor(() => expect(names()).toEqual(['Отпуск Ждёт', 'Отпуск Бухара']));
    expect(lastQueue(calls)).toEqual(manual);
  });

  test('прямая ссылка и перезагрузка открывают тот же отбор', async () => {
    network();
    open('/');
    const href = (await focusLink(/больничн\S* жд\S* справку/)).getAttribute('href')!;
    expect(href).toContain('tab=sick');
    expect(href).toContain('status=waiting');

    // «Перезагрузка» — новое приложение с тем же адресом.
    const again = network();
    open(href);
    await waitFor(() => expect(names()).toEqual(['Ждёт Справку']));
    expect(lastQueue(again)['stage']).toBe('WAITING_DOCUMENTS');
  });

  test('«Назад» и «Вперёд» восстанавливают отбор своего адреса', async () => {
    const calls = network();
    open('/');
    fireEvent.click(await focusLink(/справк\S* жд\S* проверки/));
    await waitFor(() => expect(names()).toEqual(['Справка Напроверке']));
    go('/requests?tab=leave&status=open');
    await waitFor(() => expect(names()).toEqual(['Отпуск Ждёт', 'Отпуск Бухара']));

    fireEvent.click(screen.getByRole('button', { name: 'test-back' }));
    await waitFor(() => expect(names()).toEqual(['Справка Напроверке']));
    expect(selectedTab()).toEqual(['Больничные']);
    expect(lastQueue(calls)['certificate']).toBe('PENDING');

    fireEvent.click(screen.getByRole('button', { name: 'test-forward' }));
    await waitFor(() => expect(names()).toEqual(['Отпуск Ждёт', 'Отпуск Бухара']));
    expect(lastQueue(calls)['certificate']).toBeUndefined();
  });

  test('переход поверх чужих фильтров и второй страницы: старое не мешает', async () => {
    const calls = network();
    open('/requests?tab=all&status=APPROVED&search=Справка&cursor=page-2&request=verified');
    await waitFor(() => expect(lastQueue(calls)['cursor']).toBe('page-2'));

    go('/requests?tab=sick&status=open&certificate=PENDING');
    await waitFor(() => expect(names()).toEqual(['Справка Напроверке']));
    const sent = lastQueue(calls);
    expect(sent['cursor']).toBeUndefined();
    expect(sent['search']).toBeUndefined();
    expect(sent['status']).toBeUndefined();
    expect(sent['stage']).toBe(REVIEW);
    expect(selectedTab()).toEqual(['Больничные']);
  });

  test('ручная вкладка снимает отбор по справке — он есть только у больничных', async () => {
    const calls = network();
    open('/requests?tab=sick&status=open&certificate=PENDING');
    await waitFor(() => expect(names()).toEqual(['Справка Напроверке']));

    fireEvent.click(screen.getByRole('tab', { name: /Исправления/ }));
    await waitFor(() => expect(names()).toEqual(['Вернули Справку', 'Исправление Ждёт', 'Исправление Рассмотрено']));
    expect(lastQueue(calls)['stage']).toBeUndefined();
    expect(lastQueue(calls)['certificate']).toBeUndefined();
  });

  test('быстрые переходы: поздний ответ прошлого отбора не подменяет последний', async () => {
    let release: (() => void) | null = null;
    network((url) => {
      if (url.pathname.endsWith('/requests') && url.searchParams.get('stage') === 'WAITING_DOCUMENTS') {
        // Первый ответ задерживается и приходит после второго.
        return new Promise<Response>((resolve) => {
          release = () => resolve(json(200, serveQueue(url)));
        });
      }
      return null;
    });
    open('/requests?tab=leave&status=open');
    await waitFor(() => expect(names()).toEqual(['Отпуск Ждёт', 'Отпуск Бухара']));

    go('/requests?tab=sick&status=waiting');
    go('/requests?tab=sick&status=open&certificate=PENDING');
    await waitFor(() => expect(names()).toEqual(['Справка Напроверке']));

    await act(async () => { release?.(); await Promise.resolve(); });
    expect(names()).toEqual(['Справка Напроверке']);
  });

  test('пока грузится — загрузка, пустая выборка — словами, ошибка — ошибкой', async () => {
    let answer: ((response: Response) => void) | null = null;
    network((url) => {
      if (!url.pathname.endsWith('/requests')) return null;
      return new Promise<Response>((resolve) => { answer = resolve; });
    });
    open('/requests?tab=sick&status=open&certificate=PENDING');

    expect(await screen.findByText('Загружаем очередь…')).toBeTruthy();
    expect(screen.queryByText(/заявок нет/)).toBeNull();
    await act(async () => { answer?.(json(200, { items: [], next_cursor: null, has_more: false })); });
    expect(await screen.findByText('По выбранным условиям заявок нет')).toBeTruthy();

    go('/requests?tab=leave&status=open');
    await waitFor(() => expect(answer).not.toBeNull());
    await act(async () => { answer?.(json(500, { error: { code: 'server_error', message: 'boom' } })); });
    expect(await screen.findByText(/Не удалось загрузить очередь/)).toBeTruthy();
    expect(screen.queryByText('По выбранным условиям заявок нет')).toBeNull();
    expect(screen.queryByText('Загружаем очередь…')).toBeNull();
  });

  test('сбой на новой вкладке поверх показанной — ошибка, а не вечный скелетон', async () => {
    network((url) =>
      url.pathname.endsWith('/requests') && url.searchParams.get('kind') === 'fixes'
        ? json(500, { error: { code: 'server_error', message: 'boom' } })
        : null);
    open('/requests?tab=leave&status=open');
    await waitFor(() => expect(names()).toEqual(['Отпуск Ждёт', 'Отпуск Бухара']));

    fireEvent.click(screen.getByRole('tab', { name: /Исправления/ }));
    expect(await screen.findByText(/Не удалось загрузить очередь/)).toBeTruthy();
    expect(within(queue()).queryByText('Отпуск Ждёт')).toBeNull();
  });
});

describe('«Сотрудники без отметки» → посещаемость (сервер подменён)', () => {
  test('число — только начавшиеся смены, ссылка открывает ровно их', async () => {
    const calls = network();
    open('/');

    const link = await focusLink(/без отметки/);
    expect(link.querySelector('.hm-focus__n')?.textContent).toBe('1');
    const href = link.getAttribute('href')!;
    expect(href).toContain('/attendance?');
    expect(href).toContain(`date=${DAY}`);
    expect(href).toContain('state=NOT_COME');
    expect(href).toContain('started=true');
    fireEvent.click(link);

    const table = await screen.findByRole('table', { name: 'Сотрудники за день' });
    await waitFor(() => expect(within(table).getByText('Нет Отметки')).toBeTruthy());
    // Смена ещё не началась, отметка есть — оба не в этом списке.
    expect(within(table).queryByText('Смена Вечером')).toBeNull();
    expect(within(table).queryByText('Пришёл Вовремя')).toBeNull();
    const presence = calls.filter((c) => c.url.includes('/attendance/presence')).pop()!;
    expect(new URL(presence.url, 'http://x').searchParams.get('date')).toBe(DAY);
  });

  test('снятие отбора возвращает всех, кто требует внимания', async () => {
    network();
    open(`/attendance?date=${DAY}&state=NOT_COME&started=true`);
    const table = await screen.findByRole('table', { name: 'Сотрудники за день' });
    await waitFor(() => expect(within(table).getByText('Нет Отметки')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Снять отбор по статусу' }));
    await waitFor(() => expect(within(table).getByText('Смена Вечером')).toBeTruthy());
  });
});
