/**
 * Страница одной заявки: бумаги сотрудника и проверка кадровика.
 *
 * Здесь проверяется главное различие всего сценария: справку вернули
 * на замену — это не отказ по заявке. Заявка жива, процесс идёт, и
 * место для новой бумаги остаётся у сотрудника, а не у кадровика.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const EMPLOYEE = { id: 'p-1', full_name: 'Muradov Azizbek', employee_number: 'HT-002' };

const BASE = {
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
  application_received_at: null,
  documents: [],
  history: [
    { at: '2026-09-21T11:24:00Z', action: 'SUBMITTED', comment: null },
  ],
};

const CERTIFICATE = {
  id: 'd-1',
  document_type: 'SICK_NOTE',
  verification_status: 'PENDING',
  verified_at: null,
  verification_comment: null,
  file: {
    id: 'f-1', name: 'Справка_21-09-2026.pdf', mime_type: 'application/pdf',
    size_bytes: 1_887_436, uploaded_at: '2026-09-21T11:25:00Z', scan_status: 'CLEAN',
  },
};

/** Справку вернули на замену. Заявка при этом остаётся активной. */
const REFUSED = {
  ...BASE,
  stage: 'NEEDS_FIX',
  documents: [
    {
      ...CERTIFICATE,
      verification_status: 'REJECTED',
      verified_at: '2026-09-21T13:40:00Z',
      verification_comment: 'В справке не видны даты периода.',
    },
  ],
  history: [
    ...BASE.history,
    { at: '2026-09-21T11:25:00Z', action: 'DOCUMENT_ATTACHED', comment: null },
    {
      at: '2026-09-21T13:40:00Z',
      action: 'DOCUMENT_REJECTED',
      comment: 'В справке не видны даты периода.',
    },
  ],
};

/** Финальный отказ по всей заявке — другое состояние и другие права. */
const REJECTED = {
  ...BASE,
  status: 'REJECTED',
  stage: 'REJECTED',
  review_comment: 'Период не подтверждён документами.',
  history: [
    ...BASE.history,
    { at: '2026-09-22T09:00:00Z', action: 'REJECTED', comment: 'Период не подтверждён.' },
  ],
};

function network(
  absence: unknown,
  handler: (path: string, method: string) => Response | null = () => null,
) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, USER);
    if (/\/absence-requests\/[^/?]+$/.test(path)) return json(200, absence);
    return crm(path) ?? json(200, { items: [] });
  });
}

describe('страница заявки', () => {
  test('показывает сотрудника, бумаги и историю', async () => {
    network(BASE);
    renderApp('/requests/r-1');

    expect(await screen.findByText('Muradov Azizbek')).toBeTruthy();
    expect(screen.getByText('Материалы сотрудника')).toBeTruthy();
    // Бланк для печати — строкой файла, как он и лежит в деле.
    expect(screen.getByText('Заявление на больничный')).toBeTruthy();
    expect(screen.getByText(/Заявление_.*\.pdf/)).toBeTruthy();
    expect(screen.getByText('Справка пока не приложена')).toBeTruthy();
    expect(screen.getByText('Сотрудник может загрузить её в Mini App.')).toBeTruthy();
    expect(screen.getByText('Заявка создана')).toBeTruthy();
  });

  test('кадровик не загружает файл за сотрудника', async () => {
    // Разделение ролей: подгрузив справку вместо человека, кадровик
    // снимает с него ответственность за то, что тот принёс.
    network(REFUSED);
    renderApp('/requests/r-1');

    await screen.findByText('Справка не принята');
    expect(screen.queryByText(/Загрузить другую справку/)).toBeNull();
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });

  test('возврат справки — это не отклонение заявки', async () => {
    network(REFUSED);
    renderApp('/requests/r-1');

    // Состояние заявки — «нужны исправления», а не «отклонена».
    expect(await screen.findByText('Нужны исправления')).toBeTruthy();
    expect(screen.queryByText('Отклонён')).toBeNull();
    // Комментарий кадровика виден дословно — по нему человек поймёт,
    // что принести взамен.
    expect(screen.getByText('HR: В справке не видны даты периода.')).toBeTruthy();
    // Чего ждут — сказано в проверке справа, один раз.
    expect(
      screen.getByText(/Сотрудник должен заменить справку/),
    ).toBeTruthy();
    // И заявка остаётся в работе: финальное отклонение ещё доступно.
    expect(screen.getByRole('button', { name: /Отклонить заявку/ })).toBeTruthy();
  });

  test('пока справка не принята, фактические даты недоступны', async () => {
    network(REFUSED);
    renderApp('/requests/r-1');

    expect(await screen.findByText('Недоступны до принятия справки')).toBeTruthy();
    expect(
      screen.getByText(/Сотрудник должен заменить справку/),
    ).toBeTruthy();
    const approve = screen.getByRole('button', { name: /Одобрить/ });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
  });

  test('отказ по справке не уходит без комментария', async () => {
    const calls = network({ ...BASE, stage: 'HR_REVIEW', documents: [CERTIFICATE] });
    renderApp('/requests/r-1');

    fireEvent.click(await screen.findByRole('button', { name: /Справка/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Отправить на исправление' }));

    // Причину пишут в окне. Пустое окно на сервер не уходит: человеку
    // нужно сказать, что именно переснять.
    const ask = screen.getByRole('dialog');
    fireEvent.click(within(ask).getByRole('button', { name: 'Отправить на исправление' }));

    expect(await within(ask).findByRole('alert')).toBeTruthy();
    expect(calls.filter((c) => c.url.includes('/reject'))).toHaveLength(0);
  });

  test('с комментарием отказ по справке уходит на сервер', async () => {
    const calls = network(
      { ...BASE, stage: 'HR_REVIEW', documents: [CERTIFICATE] },
      (path, method) => (method === 'POST' && path.includes('/reject') ? json(200, {}) : null),
    );
    renderApp('/requests/r-1');

    fireEvent.click(await screen.findByRole('button', { name: /Справка/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Отправить на исправление' }));

    const ask = screen.getByRole('dialog');
    fireEvent.change(within(ask).getByLabelText('Причина отказа по справке'), {
      target: { value: 'В справке не видны даты периода.' },
    });
    fireEvent.click(within(ask).getByRole('button', { name: 'Отправить на исправление' }));

    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/documents/d-1/reject'))).toHaveLength(1),
    );
    const sent = calls.find((c) => c.url.includes('/documents/d-1/reject'));
    expect(sent?.body).toMatchObject({ comment: 'В справке не видны даты периода.' });
  });

  test('у отклонённой заявки нет ни бланка, ни повторной загрузки', async () => {
    network(REJECTED);
    renderApp('/requests/r-1');

    expect(await screen.findByText('Отклонён')).toBeTruthy();
    expect(screen.queryByText('Заявление на больничный')).toBeNull();
    expect(screen.queryByText(/Сотрудник может загрузить её в Mini App/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Отклонить заявку/ })).toBeNull();
    // История и причина остаются: по ним потом разбираются.
    expect(screen.getByText('Заявка отклонена')).toBeTruthy();
  });

  test('после замены видно новую справку, а не старый отказ', async () => {
    // Отклонённая бумага остаётся в заявке навсегда, и порядку в
    // списке верить нельзя: страница показывала старый отказ поверх
    // свежей справки и просила «новую версию», которая уже лежала.
    network({
      ...BASE,
      stage: 'HR_REVIEW',
      documents: [
        {
          ...CERTIFICATE,
          id: 'd-1',
          verification_status: 'REJECTED',
          verified_at: '2026-09-21T13:40:00Z',
          verification_comment: 'В справке не видны даты периода.',
        },
        {
          ...CERTIFICATE,
          id: 'd-2',
          file: { ...CERTIFICATE.file, id: 'f-2', name: 'Справка_22-09-2026.pdf',
                  uploaded_at: '2026-09-22T09:10:00Z' },
        },
      ],
    });
    renderApp('/requests/r-1');

    expect(await screen.findByText('Справка_22-09-2026.pdf')).toBeTruthy();
    expect(screen.queryByText('Справка не принята')).toBeNull();
    expect(screen.getByText('Требует решения')).toBeTruthy();
  });

  test('история хранит и отказ по справке, и его причину', async () => {
    // Прошлую справку и комментарий нельзя «стереть» новой загрузкой.
    network(REFUSED);
    renderApp('/requests/r-1');

    expect(await screen.findByText('Справка отклонена')).toBeTruthy();
    expect(screen.getAllByText('В справке не видны даты периода.').length)
      .toBeGreaterThan(0);
    expect(screen.getByText('Справка загружена')).toBeTruthy();
  });
});
