/**
 * Отчёты: конструктор, предпросмотр, заказ, история и шаблоны.
 *
 * Проверяется то, что легко подделать глазами: поля не из каталога,
 * предпросмотр, не следующий за галочками, второй заказ от двойного
 * нажатия, процент без знаменателя и «Скачать» у файла, которого нет.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { days, retentionTitle, shownTitle } from '../src/pages/ReportsPage';
import {
  jobParams, jobTitle, kindTitle, periodLong, periodMode, periodRange, periodShort,
  progressPercent, tabCount,
} from '../src/features/reports/kinds';
import { momentTitle, sizeTitle, spanTitle } from '../src/features/reports/format';
import { pending } from '../src/features/reports/queue';
import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

// --- чистые правила ---------------------------------------------------------

describe('периоды', () => {
  test('«этот месяц» — с первого числа по сегодня, «прошлый» — целиком', () => {
    expect(periodRange('this_month', '2026-09-15')).toEqual(['2026-09-01', '2026-09-15']);
    expect(periodRange('last_month', '2026-09-15')).toEqual(['2026-08-01', '2026-08-31']);
    expect(periodRange('last_month', '2026-01-10')).toEqual(['2025-12-01', '2025-12-31']);
  });

  test('кнопка горит, только когда даты совпадают с её правилом', () => {
    expect(periodMode('2026-09-01', '2026-09-15', '2026-09-15')).toBe('this_month');
    expect(periodMode('2026-08-01', '2026-08-31', '2026-09-15')).toBe('last_month');
    expect(periodMode('2026-08-15', '2026-09-13', '2026-09-15')).toBe('custom');
  });

  test('подписи периода как в образце', () => {
    expect(periodShort('2026-08-01', '2026-08-31')).toBe('Август 2026');
    expect(periodShort('2026-08-15', '2026-09-13')).toBe('15 авг — 13 сен');
    expect(periodLong('2026-08-15', '2026-09-13')).toBe('15 августа — 13 сентября 2026');
    expect(periodLong('2026-08-15', '2026-09-13', true)).toBe('15 августа 2026 — 13 сентября 2026');
  });

  test('период считается включительно, перепутанные даты дают не больше нуля', () => {
    expect(days('2026-08-01', '2026-08-31')).toBe(31);
    expect(days('2026-08-31', '2026-08-01')).toBeLessThanOrEqual(0);
  });
});

describe('форматы величин', () => {
  test('даты отчёта и время заказа форматируются по-разному', () => {
    expect(spanTitle('2026-08-01', '2026-08-31')).toBe('01–31 авг 2026');
    expect(momentTitle('2026-09-07T09:30:00Z', new Date('2026-09-07T10:00:00Z'))).toMatch(/^Сегодня, /);
  });

  test('размер показывается только когда он известен', () => {
    expect(sizeTitle(251_904)).toBe('246 КБ');
    expect(sizeTitle(null)).toBeNull();
  });

  test('срок хранения — днями, если делится на сутки', () => {
    expect(retentionTitle(72)).toBe('3 дня');
    expect(retentionTitle(168)).toBe('7 дней');
    expect(retentionTitle(12)).toBe('12 часов');
  });

  test('старые виды очереди называются, а не прячутся', () => {
    expect(kindTitle('summary')).toBe('Сводка по офисам');
    expect(kindTitle('что-то новое')).toBe('что-то новое');
  });
});

describe('прогресс и счётчики', () => {
  test('процент — только со знаменателем от сервера', () => {
    expect(progressPercent({ status: 'RUNNING', progress_done: 72, progress_total: 100 } as never)).toBe(72);
    expect(progressPercent({ status: 'RUNNING', progress_done: 5, progress_total: null } as never)).toBeNull();
    // Сто процентов у незавершённого — неправда: файл ещё пишется.
    expect(progressPercent({ status: 'RUNNING', progress_done: 10, progress_total: 10 } as never)).toBe(99);
  });

  test('«в работе» — очередь и сборка вместе, истёкшие не в «готовых»', () => {
    const counts = { total: 7, QUEUED: 1, RUNNING: 1, SUCCEEDED: 3, FAILED: 1, CANCELLED: 0, EXPIRED: 1 };
    expect(tabCount('work', counts)).toBe(2);
    expect(tabCount('ready', counts)).toBe(3);
    expect(tabCount('all', counts)).toBe(7);
  });

  test('«показаны все» — только когда набор действительно получен', () => {
    expect(shownTitle({ items: [1, 2, 3], hasMore: false, counts: { total: 3 } })).toBe('Показаны все 3 выгрузки');
    expect(shownTitle({ items: [1, 2, 3], hasMore: true, counts: { total: 30 } })).toBe('Показано 3 выгрузки');
  });

  test('опрос идёт, только пока есть незавершённые задания', () => {
    expect(pending([{ status: 'SUCCEEDED' } as never])).toBe(false);
    expect(pending([{ status: 'QUEUED' } as never])).toBe(true);
  });

  test('строка истории называет отчёт и главные параметры', () => {
    const job = {
      kind: 'attendance', status: 'SUCCEEDED', title: null, total_rows: 4872,
      filters: { builder: 2, date_from: '2026-08-15', date_to: '2026-09-13', office_ids: [] },
    } as never;
    expect(jobTitle(job)).toBe('Посещаемость • 15 авг — 13 сен');
    expect(jobParams(job, [], [])).toMatch(/^Все офисы • 4\s872 строки$/);
    const several = { ...(job as object), filters: { builder: 2, office_ids: ['a', 'b'] } } as never;
    expect(jobParams(several, [], [])).toMatch(/^2 офиса/);
  });
});

// --- страница ---------------------------------------------------------------

const ALL = ['reports.export', 'attendance.read', 'employees.read', 'absences.read', 'offices.read'];

const CATALOG = {
  kinds: [
    { key: 'attendance', title: 'Посещаемость', permission: 'attendance.read', fields: [
      { key: 'employee', title: 'Сотрудник', default: true, columns: ['Сотрудник'] },
      { key: 'office_department', title: 'Офис и отдел', default: true, columns: ['Офис', 'Отдел'] },
      { key: 'date', title: 'Дата', default: true, columns: ['Дата'] },
      { key: 'marks', title: 'Все входы и выходы', default: false, columns: ['Все входы и выходы'] },
      { key: 'day_status', title: 'Статус дня', default: true, columns: ['Статус'] },
    ] },
    { key: 'worktime', title: 'Рабочее время', permission: 'attendance.read', fields: [
      { key: 'employee', title: 'Сотрудник', default: true, columns: ['Сотрудник'] },
      { key: 'planned', title: 'Плановое время', default: true, columns: ['План'] },
    ] },
    { key: 'lateness', title: 'Опоздания', permission: 'attendance.read', fields: [
      { key: 'employee', title: 'Сотрудник', default: true, columns: ['Сотрудник'] },
    ] },
    { key: 'absences', title: 'Отсутствия', permission: 'absences.read', fields: [
      { key: 'employee', title: 'Сотрудник', default: true, columns: ['Сотрудник'] },
      { key: 'absence_type', title: 'Тип', default: true, columns: ['Тип отсутствия'] },
      { key: 'decision', title: 'Решение HR', default: false, columns: ['Решение HR'] },
    ] },
    { key: 'employees', title: 'Сотрудники', permission: 'employees.read', fields: [
      { key: 'employee', title: 'ФИО', default: true, columns: ['ФИО'] },
    ] },
  ],
  max_period_days: 366,
  xlsx_max_rows: 50000,
  retention_hours: 72,
  preview_min_rows: 5,
  preview_max_rows: 20,
};

function previewFor(body: { kind: string; fmt: string }) {
  return {
    kind: body.kind, title: 'Посещаемость', date_from: '2026-09-01', date_to: '2026-09-15',
    fmt: body.fmt, file_name: 'Посещаемость_01.09–15.09.2026.xlsx',
    offices: 12, employees: 252, employee_name: null, days: 15,
    rows_estimate: 4872, estimate_exact: true, sampled_days: null,
    columns: [
      { key: 'employee', title: 'Сотрудник', type: 'text' },
      { key: 'day_status', title: 'Статус', type: 'status' },
    ],
    rows: [
      [{ text: 'Иванов А. С.', tone: null }, { text: 'Рабочий день', tone: 'good' }],
      [{ text: 'Волков Н. П.', tone: null }, { text: 'Опоздание', tone: 'warn' }],
    ],
    sheets: ['Сводка', 'По сотрудникам', 'По дням'],
    timezones: ['Asia/Tashkent'],
    warnings: [],
  };
}

const JOB = {
  id: 'j-1', kind: 'attendance', fmt: 'xlsx', status: 'SUCCEEDED', display_status: 'SUCCEEDED',
  filters: { builder: 2, date_from: '2026-08-15', date_to: '2026-09-13', office_ids: [], fields: ['employee'] },
  title: null, requested_by_user_id: USER.id, requested_by: USER.email, attempts: 1,
  progress_rows: 4872, total_rows: 4872, progress_done: 30, progress_total: 30,
  file_name: 'Посещаемость_15.08–13.09.2026.xlsx', size_bytes: 251_904,
  expires_at: '2099-09-18T21:32:00Z', error_message: null,
  started_at: '2026-09-15T16:31:00Z', finished_at: '2026-09-15T16:32:00Z',
  created_at: '2026-09-15T16:30:00Z', updated_at: '2026-09-15T16:32:00Z',
};

const RUNNING = {
  ...JOB, id: 'j-2', kind: 'absences', status: 'RUNNING', display_status: 'RUNNING',
  progress_done: 72, progress_total: 100, total_rows: null, size_bytes: null, expires_at: null,
  filters: { builder: 2, date_from: '2026-09-01', date_to: '2026-09-30', office_ids: ['o-1'],
             department_ids: [], fields: ['employee', 'decision'], include_inactive: true },
};

const FAILED = {
  ...JOB, id: 'j-3', kind: 'lateness', status: 'FAILED', display_status: 'FAILED',
  total_rows: null, size_bytes: null, expires_at: null,
  error_message: 'В Excel помещается не более 50000 строк. Выберите CSV или период короче.',
};

const EXPIRED = { ...JOB, id: 'j-4', kind: 'employees', display_status: 'EXPIRED' };

const COUNTS = { total: 4, QUEUED: 0, RUNNING: 1, SUCCEEDED: 1, FAILED: 1, CANCELLED: 0, EXPIRED: 1 };

type Handler = (path: string, method: string, body: unknown) => Response | null;

function network(
  { permissions = ALL, items = [JOB, RUNNING, FAILED, EXPIRED], handler = () => null }:
  { permissions?: string[]; items?: unknown[]; handler?: Handler } = {},
) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method, call.body);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, { ...USER, permissions });
    if (path.includes('/reports/catalog')) return json(200, CATALOG);
    if (path.includes('/reports/preview')) return json(200, previewFor(call.body as never));
    if (path.includes('/report-templates/')) return json(200, { items: [] });
    if (path.includes('/export-jobs/counts')) return json(200, COUNTS);
    if (path.includes('/export-jobs/') && call.method === 'POST') {
      return json(201, { ...JOB, id: 'new', status: 'QUEUED', display_status: 'QUEUED' });
    }
    if (path.includes('/export-jobs')) return json(200, { items, next_cursor: null, has_more: false });
    if (path.includes('/regions/')) return json(200, { items: [{ id: 'r-1', code: 'T', name: 'Ташкент', status: 'ACTIVE' }] });
    if (path.includes('/offices/')) {
      return json(200, { items: [
        { id: 'o-1', code: 'A', name: 'Офис А', region_id: 'r-1', region_name: 'Ташкент', status: 'ACTIVE' },
        { id: 'o-2', code: 'B', name: 'Офис Б', region_id: 'r-1', region_name: 'Ташкент', status: 'ACTIVE' },
      ] });
    }
    if (path.includes('/departments/')) return json(200, { items: [{ id: 'd-1', name: 'Продажи' }] });
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

const previews = (calls: { url: string; method: string; body?: unknown }[]) =>
  calls.filter((call) => call.url.includes('/reports/preview'));

async function choose(title: RegExp) {
  const card = await screen.findByRole('radio', { name: title });
  await waitFor(() => expect((card as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(card);
}

describe('конструктор', () => {
  test('шаги идут за состоянием: без вида — первый, с видом и полями — третий', async () => {
    network();
    renderApp('/reports');
    const steps = await screen.findByRole('list', { name: 'Шаги' });
    expect(within(steps).getByText('Тип отчёта').closest('li')?.getAttribute('aria-current')).toBe('step');

    await choose(/Посещаемость/);
    expect(within(steps).getByText('Формат и создание').closest('li')?.getAttribute('aria-current')).toBe('step');
  });

  test('вид включает поля по умолчанию, а предпросмотр показывает настоящие строки', async () => {
    const calls = network();
    renderApp('/reports');
    await choose(/Посещаемость/);

    await screen.findByText('Иванов А. С.');
    const body = previews(calls).at(-1)?.body as { fields: string[]; kind: string; fmt: string };
    expect(body.kind).toBe('attendance');
    expect(body.fields).toEqual(['employee', 'office_department', 'date', 'day_status']);
    expect(screen.getByText('Готов к формированию')).toBeTruthy();
    expect(screen.getByText(/12 офисов · 252 сотрудника/)).toBeTruthy();
    expect(screen.getByText('Опоздание').className).toContain('rp-pill--warn');
    expect(screen.getByText('4 872')).toBeTruthy();
  });

  test('галочка поля меняет запрос предпросмотра', async () => {
    const calls = network();
    renderApp('/reports');
    await choose(/Посещаемость/);
    await screen.findByText('Иванов А. С.');

    fireEvent.click(screen.getByRole('checkbox', { name: 'Статус дня' }));
    await waitFor(() => {
      const body = previews(calls).at(-1)?.body as { fields: string[] };
      expect(body.fields).not.toContain('day_status');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Выбрать все поля' }));
    await waitFor(() => {
      const body = previews(calls).at(-1)?.body as { fields: string[] };
      expect(body.fields).toHaveLength(5);
    });
  });

  test('без полей заказать нельзя', async () => {
    network();
    renderApp('/reports');
    await choose(/Посещаемость/);
    await screen.findByText('Иванов А. С.');
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать все поля' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Снять все поля' }));

    expect(await screen.findByText('Выберите хотя бы одно поле')).toBeTruthy();
    expect((screen.getByRole('button', { name: /Сформировать отчёт/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  test('вид без права на данные недоступен', async () => {
    network({ permissions: ['reports.export', 'employees.read'] });
    renderApp('/reports');
    const card = await screen.findByRole('radio', { name: /Сотрудники/ });
    await waitFor(() => expect((card as HTMLButtonElement).disabled).toBe(false));
    expect((screen.getByRole('radio', { name: /Посещаемость/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  test('без права на выгрузку — понятный отказ', async () => {
    network({ permissions: ['attendance.read'], items: [] });
    renderApp('/reports');
    expect(await screen.findByText(/Нет права на выгрузку отчётов/)).toBeTruthy();
  });

  test('ошибка предпросмотра показывает причину и повтор', async () => {
    let fail = true;
    network({
      handler: (path) => {
        if (path.includes('/reports/preview') && fail) {
          return json(403, { error: { code: 'permission_denied', message: 'Офис вне вашей области видимости', details: {} } });
        }
        return null;
      },
    });
    renderApp('/reports');
    await choose(/Посещаемость/);
    const reason = await screen.findByText(/Нет доступа к этим данным или офисам|Офис вне/);
    fail = false;
    fireEvent.click(within(reason.closest('p') as HTMLElement).getByRole('button', { name: 'Повторить' }));
    expect(await screen.findByText('Иванов А. С.')).toBeTruthy();
  });
});

describe('заказ', () => {
  test('двойное нажатие ставит один отчёт, с ключом повтора и параметрами', async () => {
    const calls = network();
    renderApp('/reports');
    await choose(/Посещаемость/);
    await screen.findByText('Иванов А. С.');

    const button = screen.getByRole('button', { name: /Сформировать отчёт/ });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(await screen.findByText(/поставлен в очередь/)).toBeTruthy();
    const orders = calls.filter((call) => call.url.includes('/export-jobs/') && call.method === 'POST');
    expect(orders).toHaveLength(1);
    const body = orders[0]!.body as Record<string, unknown>;
    expect(body['builder']).toBe(true);
    expect(typeof body['client_request_id']).toBe('string');
    expect(body['fields']).toEqual(['employee', 'office_department', 'date', 'day_status']);
    expect(body['fmt']).toBe('xlsx');
  });

  test('после ошибки повтор уходит с тем же ключом', async () => {
    let first = true;
    const calls = network({
      handler: (path, method) => {
        if (path.includes('/export-jobs/') && method === 'POST' && first) {
          first = false;
          return json(409, { error: { code: 'conflict', message: 'Много выгрузок', details: {} } });
        }
        return null;
      },
    });
    renderApp('/reports');
    await choose(/Посещаемость/);
    await screen.findByText('Иванов А. С.');

    fireEvent.click(screen.getByRole('button', { name: /Сформировать отчёт/ }));
    const reason = await screen.findByText(/Слишком много незавершённых выгрузок/);
    fireEvent.click(within(reason.closest('p') as HTMLElement).getByRole('button', { name: 'Повторить' }));
    expect(await screen.findByText(/поставлен в очередь/)).toBeTruthy();

    const orders = calls.filter((call) => call.url.includes('/export-jobs/') && call.method === 'POST');
    expect(orders).toHaveLength(2);
    expect((orders[0]!.body as Record<string, unknown>)['client_request_id'])
      .toBe((orders[1]!.body as Record<string, unknown>)['client_request_id']);
  });

  test('формат и имя файла уходят в заказ', async () => {
    const calls = network();
    renderApp('/reports');
    await choose(/Посещаемость/);
    await screen.findByText('Иванов А. С.');

    fireEvent.click(screen.getByRole('radio', { name: /CSV/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Имя файла' }), { target: { value: 'Март офис' } });
    await waitFor(() => {
      const body = previews(calls).at(-1)?.body as { fmt: string; name: string };
      expect(body.fmt).toBe('csv');
      expect(body.name).toBe('Март офис');
    });
  });
});

describe('история', () => {
  test('процент готовности, причина ошибки и истёкший файл', async () => {
    network();
    renderApp('/reports');

    const bar = await screen.findByRole('progressbar', { name: 'Готовность отчёта' });
    expect(bar.getAttribute('aria-valuenow')).toBe('72');
    expect(screen.getByText('72%')).toBeTruthy();
    expect(screen.getByText(/В Excel помещается не более 50000 строк/)).toBeTruthy();
    expect(screen.getByText('Истёк')).toBeTruthy();
    // Скачать можно только готовый файл, у истёкшего ссылки нет.
    const links = screen.getAllByRole('link', { name: /Скачать/ });
    expect(links).toHaveLength(1);
    expect(links[0]!.getAttribute('href')).toContain('/export-jobs/j-1/download/');
    expect(screen.getByText(/автоматически удаляются через 3 дня/)).toBeTruthy();
  });

  test('ошибку повторяет сервер, а не новая строка', async () => {
    const calls = network();
    renderApp('/reports');
    const row = (await screen.findByText(/В Excel помещается/)).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: /Повторить/ }));
    await waitFor(() => expect(calls.some((call) => call.url.includes('/export-jobs/j-3/retry/'))).toBe(true));
  });

  test('удаление из истории и повтор истёкшего с теми же параметрами', async () => {
    const calls = network();
    renderApp('/reports');
    await screen.findByText('Истёк');

    fireEvent.click(screen.getByRole('button', { name: 'Действия: Посещаемость • 15 авг — 13 сен' }));
    fireEvent.click(screen.getByRole('button', { name: 'Удалить из моей истории' }));
    await waitFor(() => expect(calls.some((call) => call.url.includes('/export-jobs/j-1/hide/'))).toBe(true));

    const expired = screen.getByText('Истёк').closest('tr') as HTMLElement;
    fireEvent.click(within(expired).getByRole('button', { name: /Повторить/ }));
    await waitFor(() => {
      const order = calls.find((call) => call.method === 'POST' && call.url.endsWith('/export-jobs/'));
      expect((order?.body as Record<string, unknown>)?.['builder']).toBe(true);
      expect((order?.body as Record<string, unknown>)?.['kind']).toBe('employees');
    });
  });

  test('«Открыть параметры» переносит заказ в форму', async () => {
    const calls = network();
    renderApp('/reports');
    await screen.findByRole('progressbar');

    fireEvent.click(screen.getByRole('button', { name: /^Действия: Отсутствия/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Открыть параметры' }));

    await waitFor(() =>
      expect(screen.getByRole('radio', { name: /Отсутствия/ }).getAttribute('aria-checked')).toBe('true'));
    expect((screen.getByRole('checkbox', { name: 'Решение HR' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('checkbox', { name: 'Тип' }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByRole('checkbox', { name: /Включить неактивных/ }) as HTMLInputElement).checked).toBe(true);
    await waitFor(() => {
      const body = previews(calls).at(-1)?.body as { office_ids: string[]; date_to: string };
      expect(body.office_ids).toEqual(['o-1']);
      expect(body.date_to).toBe('2026-09-30');
    });
  });
});

describe('шаблоны', () => {
  test('сохранение отправляет вид, фильтры и название', async () => {
    const calls = network({
      handler: (path, method, body) => {
        if (path.includes('/report-templates/') && method === 'POST') {
          const sent = body as { template_name: string };
          return json(201, { id: 't-1', name: sent.template_name, kind: 'attendance', fmt: 'xlsx',
                             filters: {}, created_at: '', updated_at: '' });
        }
        return null;
      },
    });
    renderApp('/reports');
    await choose(/Посещаемость/);
    await screen.findByText('Иванов А. С.');

    fireEvent.click(screen.getByRole('button', { name: /Сохранить как шаблон/ }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Название шаблона' }), { target: { value: 'Офис за месяц' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByText('Шаблон «Офис за месяц» сохранён')).toBeTruthy();
    const saved = calls.find((call) => call.url.includes('/report-templates/') && call.method === 'POST');
    const body = saved?.body as Record<string, unknown>;
    expect(body['template_name']).toBe('Офис за месяц');
    expect(body['kind']).toBe('attendance');
    expect(body['period']).toBe('this_month');
  });

  test('шаблон из списка применяется к форме', async () => {
    network({
      handler: (path, method) => {
        if (path.includes('/report-templates/') && method === 'GET') {
          return json(200, { items: [{
            id: 't-1', name: 'Опоздания офиса', kind: 'lateness', fmt: 'csv',
            filters: { builder: 2, period: 'last_month', fields: ['employee'], office_ids: [] },
            created_at: '', updated_at: '',
          }] });
        }
        return null;
      },
    });
    renderApp('/reports');
    await screen.findByRole('radio', { name: /Опоздания/ });
    fireEvent.click(screen.getByRole('button', { name: /Шаблоны отчётов/ }));
    fireEvent.click(await screen.findByRole('button', { name: /^Опоздания офиса/ }));

    await waitFor(() =>
      expect(screen.getByRole('radio', { name: /Опоздания/ }).getAttribute('aria-checked')).toBe('true'));
    expect(screen.getByRole('radio', { name: /CSV/ }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('button', { name: 'Прошлый месяц' }).getAttribute('aria-pressed')).toBe('true');
  });
});
