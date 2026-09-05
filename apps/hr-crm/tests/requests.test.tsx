/**
 * Очередь заявок: фильтры, курсор, подробности и решения.
 *
 * Главные обещания: состояние документа не смешивается со статусом
 * рассмотрения, рассмотренная заявка не выглядит доступной для решения,
 * а повторное нажатие не отправляет второй запрос.
 */

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const EMPLOYEE = { id: 'p-1', full_name: 'Рахимова Мадина', employee_number: 'HT-002' };

const SICK = {
  kind: 'absence',
  id: 'r-1',
  created_at: '2026-09-05T09:12:00Z',
  absence: {
    id: 'r-1',
    employee: EMPLOYEE,
    absence_type: { code: 'SICK_LEAVE', name: 'Больничный' },
    status: 'SUBMITTED',
    first_day: '2026-09-04',
    last_day: '2026-09-08',
    submitted_at: '2026-09-05T09:12:00Z',
    comment: 'Прикрепила справку за период отсутствия.',
    review_comment: null,
    requires_document: true,
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

function network(handler: (path: string, method: string) => Response | null = () => null) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/requests')) {
      return json(200, { items: [SICK, WAITING, DECIDED], next_cursor: 'c2', has_more: true });
    }
    return crm(path) ?? json(200, { items: [] });
  });
}

describe('очередь', () => {
  test('строка различает загруженную справку и её отсутствие', async () => {
    // «Загружена» и «проверена» — разные вещи, и документ не считается
    // проверенным только потому, что он есть.
    network();
    renderApp('/requests');

    expect(await screen.findByText('Справка на проверке')).toBeTruthy();
    // Две заявки без справки в наборе — важно, что состояние отличается
    // от «загружена», а не сколько строк его показывают.
    expect(screen.getAllByText('Нет справки').length).toBeGreaterThan(0);
  });

  test('фильтр уходит на сервер, а не режет загруженную страницу', async () => {
    const calls = network();
    renderApp('/requests');
    await screen.findByText('Справка на проверке');

    fireEvent.click(screen.getByRole('tab', { name: /Больничные/ }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('type=SICK_LEAVE'))).toBe(true),
    );
  });

  test('страница листается курсором', async () => {
    const calls = network();
    renderApp('/requests');
    await screen.findByText('Справка на проверке');

    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));

    await waitFor(() => expect(calls.some((c) => c.url.includes('cursor=c2'))).toBe(true));
  });

  test('ошибка не превращается в пустую очередь', async () => {
    network((path) => (path.includes('/requests') ? json(500, { error: {} }) : null));
    renderApp('/requests');

    expect(await screen.findByText(/Не удалось загрузить очередь/)).toBeTruthy();
  });
});

describe('подробности', () => {
  test('открываются рядом со списком, список остаётся виден', async () => {
    network();
    renderApp('/requests');
    fireEvent.click((await screen.findAllByText('Рахимова Мадина'))[0] as HTMLElement);

    const panel = await screen.findByRole('complementary', { name: 'Подробности заявки' });
    expect(panel).toBeTruthy();
    // Очередь никуда не делась: кадровик не теряет место, где остановился.
    expect(screen.getByRole('tab', { name: /Все/ })).toBeTruthy();
  });

  test('показывают имя, формат и размер файла', async () => {
    network();
    renderApp('/requests');
    fireEvent.click((await screen.findAllByText('Рахимова Мадина'))[0] as HTMLElement);

    expect(await screen.findByText('spravka_04-09.pdf')).toBeTruthy();
    expect(screen.getByText(/248 КБ/)).toBeTruthy();
  });

  test('рассмотренная заявка не предлагает решение заново', async () => {
    network();
    renderApp('/requests?request=r-3');

    expect(await screen.findByText(/Заявка уже рассмотрена/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Подтвердить' })).toBeNull();
  });
});

describe('решение', () => {
  test('повторное нажатие не отправляет второй запрос', async () => {
    const calls = network((path, method) => {
      if (method === 'POST' && path.includes('/approve')) {
        // Ответ придёт не сразу: как раз в это окно и приходится
        // второе нажатие.
        return new Response(JSON.stringify({}), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return null;
    });
    renderApp('/requests?request=r-1');

    const button = await screen.findByRole('button', { name: 'Подтвердить' });
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/approve'))).toHaveLength(1),
    );
  });

  test('при ошибке комментарий остаётся в поле', async () => {
    network((path, method) =>
      method === 'POST' && path.includes('/approve')
        ? json(409, { error: { code: 'conflict', message: 'уже рассмотрена' } })
        : null,
    );
    renderApp('/requests?request=r-1');

    const area = await screen.findByLabelText('Комментарий HR');
    fireEvent.change(area, { target: { value: 'Проверила справку' } });
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));

    await screen.findByRole('alert');
    expect((area as HTMLTextAreaElement).value).toBe('Проверила справку');
  });
});
