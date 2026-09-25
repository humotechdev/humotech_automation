/**
 * Очередь заявок: вкладки, фильтры, выбор строки и предпросмотр.
 *
 * Главные обещания этой страницы: выбранная строка и панель справа
 * говорят об одном и том же; панель берёт состояние у сервера, а не
 * додумывает его; одобрить отсюда нельзя — за этим идут на страницу
 * заявки; «нужны исправления» не выглядит отказом.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const EMPLOYEE = { id: 'p-1', full_name: 'Muradov Azizbek', employee_number: 'HT-002' };
const OTHER = { id: 'p-2', full_name: 'Рахимова Мадина', employee_number: 'HT-003' };
const PLACE = { office_name: 'город Ташкент', department_name: 'Отдел продаж' };

const SICK = {
  kind: 'absence',
  id: 'r-1',
  created_at: '2026-09-21T11:24:00Z',
  place: PLACE,
  absence: {
    id: 'r-1',
    kind: 'CREATE',
    employee: EMPLOYEE,
    absence_type: { code: 'SICK_LEAVE', name: 'Больничный' },
    status: 'SUBMITTED',
    stage: 'WAITING_DOCUMENTS',
    missing_for_approval: ['certificate', 'application', 'period'],
    first_day: null,
    last_day: null,
    submitted_at: '2026-09-21T11:24:00Z',
    comment: null,
    review_comment: null,
    requires_document: true,
    history: [],
    documents: [],
  },
};

/** Справку вернули на замену: заявка жива и ждёт новую бумагу. */
const NEEDS_FIX = {
  ...SICK,
  id: 'r-2',
  absence: {
    ...SICK.absence,
    id: 'r-2',
    employee: OTHER,
    stage: 'NEEDS_FIX',
    documents: [
      {
        id: 'd-1',
        document_type: 'SICK_NOTE',
        verification_status: 'REJECTED',
        verified_at: '2026-09-21T13:40:00Z',
        verification_comment: 'В справке не видны даты периода.',
        file: {
          id: 'f-1', name: 'spravka.pdf', mime_type: 'application/pdf',
          size_bytes: 253952, uploaded_at: '2026-09-21T11:25:00Z', scan_status: 'CLEAN',
        },
      },
    ],
  },
};

const LEAVE = {
  kind: 'absence',
  id: 'r-3',
  created_at: '2026-09-20T09:00:00Z',
  place: PLACE,
  absence: {
    id: 'r-3',
    kind: 'CREATE',
    employee: OTHER,
    absence_type: { code: 'ANNUAL_LEAVE', name: 'Ежегодный отпуск' },
    status: 'SUBMITTED',
    stage: 'PENDING',
    missing_for_approval: [],
    first_day: '2026-10-05',
    last_day: '2026-10-16',
    submitted_at: '2026-09-20T09:00:00Z',
    comment: null,
    review_comment: null,
    requires_document: false,
    history: [],
    documents: [],
  },
};

const COUNTS = { open: 3, all: 3, leave: 1, sick: 2, fixes: 0, cancel: 0 };

function network(items: unknown[] = [SICK, NEEDS_FIX, LEAVE]) {
  const byId: Record<string, unknown> = {
    'r-1': SICK.absence, 'r-2': NEEDS_FIX.absence, 'r-3': LEAVE.absence,
  };
  return fakeNetwork((path) => {
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/requests/counts')) return json(200, COUNTS);
    if (path.includes('/requests')) {
      return json(200, { items, next_cursor: null, has_more: false });
    }
    const one = /\/absence-requests\/([^/?]+)$/.exec(path);
    if (one) return json(200, byId[one[1] ?? ''] ?? {});
    return crm(path) ?? json(200, { items: [] });
  });
}

const panel = () => screen.getByRole('complementary', { name: 'Выбранная заявка' });

describe('очередь', () => {
  test('строки показывают человека, вид и дату подачи', async () => {
    network();
    renderApp('/requests');

    expect(await screen.findByText('Muradov Azizbek')).toBeTruthy();
    expect(screen.getAllByText('город Ташкент').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Больничный').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Подана').length).toBeGreaterThan(0);
  });

  test('вкладка уходит фильтром на сервер, а не режет загруженную страницу', async () => {
    const calls = network();
    renderApp('/requests');
    await screen.findByText('Muradov Azizbek');

    fireEvent.click(screen.getByRole('tab', { name: /Больничные/ }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('type=SICK_LEAVE'))).toBe(true),
    );
  });

  test('пустой отбор отвечает строкой, а не пустой таблицей с листалкой', async () => {
    network([]);
    renderApp('/requests?search=никого');

    expect(await screen.findByText('По выбранным условиям заявок нет')).toBeTruthy();
    expect(screen.getByText('Измените фильтры или сбросьте поиск.')).toBeTruthy();
    // Ни шапки колонок, ни кнопок листания: обещать страницы, которых
    // нет, — худшее, что может сделать пустой список.
    expect(screen.queryByText('Сотрудник')).toBeNull();
    expect(screen.queryByRole('button', { name: /Далее/ })).toBeNull();
  });
});

describe('предпросмотр', () => {
  test('выбор строки меняет панель справа', async () => {
    network();
    renderApp('/requests');

    await screen.findByText('Muradov Azizbek');
    fireEvent.click(screen.getByText('Muradov Azizbek'));
    await waitFor(() =>
      expect(within(panel()).getByText('Ожидаем документы')).toBeTruthy(),
    );

    // Вторая строка — отпуск. В панели меняется всё: вид, человек и то,
    // что по заявке надо проверить.
    fireEvent.click(screen.getAllByText('Отпуск')[0]!);
    await waitFor(() => expect(within(panel()).getByText('Период')).toBeTruthy());
  });

  test('чек-лист берётся из данных сервера, а не собирается на клиенте', async () => {
    network();
    renderApp('/requests?request=r-1');

    await screen.findAllByText('Ожидаем документы');
    const side = panel();
    expect(within(side).getByText('Справка')).toBeTruthy();
    expect(within(side).getByText('Не приложена')).toBeTruthy();
    expect(within(side).getByText('Подписанное заявление')).toBeTruthy();
    expect(within(side).getByText('Не подтверждено')).toBeTruthy();
    expect(within(side).getByText('Фактические даты')).toBeTruthy();
    expect(within(side).getByText('Не указаны')).toBeTruthy();
  });

  test('«Одобрить» выключена, пока сервер называет незакрытые пункты', async () => {
    network();
    renderApp('/requests?request=r-1');

    await screen.findAllByText('Ожидаем документы');
    const side = panel();
    const approve = within(side).getByRole('button', { name: /Одобрить/ });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(
      within(side).getByText(/Одобрение станет доступно после проверки/),
    ).toBeTruthy();
  });

  test('у отпуска нет ни справки, ни заявления', async () => {
    // Правила больничного к отпуску не применяются: бумаг у него нет,
    // и спрашивать их — значит просить то, чего никто не ждёт.
    network();
    renderApp('/requests?request=r-3');

    await screen.findAllByText('Период');
    const side = panel();
    expect(within(side).getByText('Отпуск')).toBeTruthy();
    expect(within(side).queryByText('Справка')).toBeNull();
    expect(within(side).queryByText('Подписанное заявление')).toBeNull();
  });

  test('«Нужны исправления» — не отклонение заявки', async () => {
    // Заявка жива и остаётся в очереди: сотрудник должен заменить
    // справку, а не подавать всё заново.
    network();
    renderApp('/requests?request=r-2');

    await screen.findAllByText('Нужна новая версия');
    const side = panel();
    expect(within(side).getByText('Нужны исправления')).toBeTruthy();
    expect(within(side).getByText('Нужна новая версия')).toBeTruthy();
    expect(within(side).queryByText('Отклонён')).toBeNull();
  });

  test('«Открыть заявку» ведёт на страницу этой заявки', async () => {
    network();
    renderApp('/requests?request=r-1');

    await screen.findAllByText('Ожидаем документы');
    const link = within(panel()).getByRole('link', { name: /Открыть заявку/ });
    expect(link.getAttribute('href')).toBe('/requests/r-1');

    fireEvent.click(link);

    // Отдельная страница, а не окно поверх очереди: у заявки свой
    // заголовок и своя карточка сотрудника.
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Больничный'),
    );
    expect(screen.getByText('Материалы сотрудника')).toBeTruthy();
  });
});
