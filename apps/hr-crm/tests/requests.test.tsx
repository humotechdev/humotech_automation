/**
 * Очередь заявок: вкладки, фильтры, курсор, подробности и решения.
 *
 * Главные обещания: состояние документа не смешивается со статусом
 * рассмотрения, рассмотренная заявка не выглядит доступной для решения,
 * отказ без причины не уходит, а повторное нажатие не отправляет второй
 * запрос.
 */

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const EMPLOYEE = { id: 'p-1', full_name: 'Рахимова Мадина', employee_number: 'HT-002' };
const PLACE = { office_name: 'Самарканд', department_name: 'Отдел продаж' };

const SICK = {
  kind: 'absence',
  id: 'r-1',
  created_at: '2026-09-05T09:12:00Z',
  place: PLACE,
  absence: {
    id: 'r-1',
    kind: 'CREATE',
    employee: EMPLOYEE,
    absence_type: { code: 'SICK_LEAVE', name: 'Больничный' },
    status: 'SUBMITTED',
    first_day: '2026-09-04',
    last_day: '2026-09-08',
    submitted_at: '2026-09-05T09:12:00Z',
    comment: 'Прикрепила справку за период отсутствия.',
    review_comment: null,
    requires_document: true,
    history: [
      { at: '2026-09-05T09:12:00Z', action: 'CREATED', comment: null },
      { at: '2026-09-05T09:18:00Z', action: 'DOCUMENT_ATTACHED', comment: null },
    ],
    documents: [
      {
        id: 'd-1',
        document_type: 'SICK_NOTE',
        verification_status: 'PENDING',
        verified_at: null,
        file: {
          id: 'f-1', name: 'spravka_04-09.pdf', mime_type: 'application/pdf',
          size_bytes: 253952, uploaded_at: '2026-09-05T09:18:00Z', scan_status: 'CLEAN',
        },
      },
    ],
  },
};

const WAITING = {
  ...SICK,
  id: 'r-2',
  absence: { ...SICK.absence, id: 'r-2', employee: { ...EMPLOYEE, id: 'p-2' }, documents: [] },
};

const DECIDED = {
  ...SICK,
  id: 'r-3',
  absence: { ...SICK.absence, id: 'r-3', status: 'APPROVED', documents: [] },
};

const COUNTS = { open: 16, all: 40, leave: 5, sick: 7, fixes: 4, cancel: 2 };

function network(handler: (path: string, method: string) => Response | null = () => null) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/requests/counts')) return json(200, COUNTS);
    if (path.includes('/requests')) {
      return json(200, { items: [SICK, WAITING, DECIDED], next_cursor: 'c2', has_more: true });
    }
    return crm(path) ?? json(200, { items: [] });
  });
}

const listCalls = (calls: { url: string }[]) =>
  calls.filter((c) => c.url.includes('/requests') && !c.url.includes('/requests/counts'));

describe('очередь', () => {
  test('по умолчанию открыты заявки, которые ждут решения', async () => {
    const calls = network();
    renderApp('/requests');

    await waitFor(() =>
      expect(listCalls(calls).some((c) => c.url.includes('status=SUBMITTED%2CIN_REVIEW')
        || c.url.includes('status=SUBMITTED,IN_REVIEW'))).toBe(true),
    );
    expect(screen.getByRole('tab', { name: /Требуют решения/ }).getAttribute('aria-selected')).toBe('true');
  });

  test('счётчики вкладок приходят с сервера, а не с показанной страницы', async () => {
    // На странице три строки — вкладка обязана назвать 16 из счётчиков.
    network();
    renderApp('/requests');

    const tab = await screen.findByRole('tab', { name: /Требуют решения/ });
    await waitFor(() => expect(tab.textContent).toContain('16'));
    expect(screen.getByRole('tab', { name: /Больничные/ }).textContent).toContain('7');
  });

  test('строка различает справку на проверке и её отсутствие', async () => {
    // «Загружена» и «проверена» — разные вещи: документ не считается
    // проверенным только потому, что он есть.
    network();
    renderApp('/requests');

    expect((await screen.findAllByText('Справка на проверке')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Нет справки').length).toBeGreaterThan(0);
  });

  test('вкладка уходит фильтром на сервер, а не режет загруженную страницу', async () => {
    const calls = network();
    renderApp('/requests');
    await screen.findAllByText('Справка на проверке');

    fireEvent.click(screen.getByRole('tab', { name: /Больничные/ }));

    await waitFor(() =>
      expect(listCalls(calls).some((c) => c.url.includes('type=SICK_LEAVE'))).toBe(true),
    );
  });

  test('страница листается курсором', async () => {
    const calls = network();
    renderApp('/requests');
    await screen.findAllByText('Справка на проверке');

    fireEvent.click(screen.getByRole('button', { name: /Далее/ }));

    await waitFor(() => expect(calls.some((c) => c.url.includes('cursor=c2'))).toBe(true));
  });

  test('ошибка не превращается в пустую очередь', async () => {
    network((path) =>
      path.includes('/requests') && !path.includes('/requests/counts') ? json(500, { error: {} }) : null,
    );
    renderApp('/requests');

    expect(await screen.findByText(/Не удалось загрузить очередь/)).toBeTruthy();
  });
});

describe('подробности', () => {
  test('открываются рядом со списком, список остаётся виден', async () => {
    network();
    renderApp('/requests');
    fireEvent.click((await screen.findAllByText('Рахимова Мадина'))[0] as HTMLElement);

    expect(await screen.findByRole('complementary', { name: 'Подробности заявки' })).toBeTruthy();
    // Очередь никуда не делась: кадровик не теряет место, где остановился.
    expect(screen.getByRole('tab', { name: 'Все' })).toBeTruthy();
  });

  test('показывают имя и размер файла, офис и историю', async () => {
    network();
    renderApp('/requests?request=r-1');

    expect(await screen.findByText('spravka_04-09.pdf')).toBeTruthy();
    expect(screen.getByText(/248 КБ/)).toBeTruthy();
    expect(screen.getAllByText('Самарканд').length).toBeGreaterThan(0);
    expect(screen.getByText('Справка загружена')).toBeTruthy();
    expect(screen.getByText('Ожидает решения')).toBeTruthy();
  });

  test('рассмотренная заявка не предлагает решение заново', async () => {
    network();
    renderApp('/requests?request=r-3');

    expect(await screen.findByText(/Заявка уже рассмотрена/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Одобрить/ })).toBeNull();
  });

  test('запрос документа не выдаёт себя за работающее действие', async () => {
    network();
    renderApp('/requests?request=r-1');

    const ask = await screen.findByRole('button', { name: /Запросить документ/ });
    expect(ask.hasAttribute('disabled')).toBe(true);
  });
});

describe('решение по справке', () => {
  test('принятая справка уходит на свой адрес, а не на адрес заявки', async () => {
    // Это разные решения: одобренный больничный с отклонённой справкой —
    // законное состояние, и путать их адресами нельзя.
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/documents/d-1/accept')
        ? json(200, { id: 'd-1', verification_status: 'VERIFIED',
                      verification_comment: null })
        : null,
    );
    renderApp('/requests?request=r-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Принять справку' }));

    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/documents/d-1/accept')))
        .toHaveLength(1),
    );
    // Решение по самой заявке при этом не трогается.
    expect(calls.filter((c) => c.url.endsWith('/approve'))).toHaveLength(0);
  });

  test('возврат справки без причины на сервер не уходит', async () => {
    // Человек принесёт ту же бумагу второй раз и не поймёт, почему её
    // опять не взяли.
    const calls = network();
    renderApp('/requests?request=r-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Вернуть справку' }));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Вернуть на доработку' }),
    );

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(calls.filter((c) => c.url.includes('/documents/d-1/reject')))
      .toHaveLength(0);
  });

  test('причина уходит вместе с возвратом', async () => {
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/documents/d-1/reject')
        ? json(200, { id: 'd-1', verification_status: 'REJECTED',
                      verification_comment: 'Фото нечитаемое' })
        : null,
    );
    renderApp('/requests?request=r-1');

    fireEvent.click(await screen.findByRole('button', { name: 'Вернуть справку' }));
    fireEvent.change(
      await screen.findByPlaceholderText(/Что не так со справкой/),
      { target: { value: 'Фото нечитаемое' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Вернуть на доработку' }));

    await waitFor(() => {
      const sent = calls.find((c) => c.url.includes('/documents/d-1/reject'));
      expect(sent?.body).toEqual({ comment: 'Фото нечитаемое' });
    });
  });
});

describe('решение', () => {
  test('повторное нажатие не отправляет второй запрос', async () => {
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/approve') ? json(200, {}) : null,
    );
    renderApp('/requests?request=r-1');

    const button = await screen.findByRole('button', { name: /Одобрить/ });
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/approve'))).toHaveLength(1),
    );
  });

  test('отказ без причины не уходит на сервер', async () => {
    // Без причины сотрудник подаст ту же заявку заново, и так по кругу.
    const calls = network();
    renderApp('/requests?request=r-1');

    fireEvent.click(await screen.findByRole('button', { name: /Отклонить/ }));

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(calls.filter((c) => c.url.includes('/reject'))).toHaveLength(0);
  });

  test('при ошибке комментарий остаётся в поле', async () => {
    network((path, method) =>
      method === 'POST' && path.includes('/approve')
        ? json(409, { error: { code: 'conflict', message: 'уже рассмотрена' } })
        : null,
    );
    renderApp('/requests?request=r-1');

    const field = await screen.findByLabelText('Комментарий HR');
    fireEvent.change(field, { target: { value: 'Проверила справку' } });
    fireEvent.click(screen.getByRole('button', { name: /Одобрить/ }));

    await screen.findByRole('alert');
    expect((field as HTMLInputElement).value).toBe('Проверила справку');
  });
});
