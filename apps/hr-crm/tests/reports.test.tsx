/**
 * Отчёты: заказ выгрузки, история и действия над строками.
 *
 * Проверяется то, что легко подделать глазами: подтверждение раньше
 * ответа сервера, счётчик по загруженной странице вместо набора, живая
 * кнопка «Скачать» у файла, которого уже нет, и второй заказ от двойного
 * нажатия.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { days, fileGone, shownTitle, zoneNote } from '../src/pages/ReportsPage';
import { KINDS, TABS, kindTitle, tabCount } from '../src/features/reports/kinds';
import { momentTitle, sizeTitle, spanTitle } from '../src/features/reports/format';
import { pending } from '../src/features/reports/queue';
import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

// --- чистые правила ---------------------------------------------------------

describe('форматы величин', () => {
  test('даты отчёта и время заказа форматируются по-разному', () => {
    // «01–31 авг 2026» в колонке «Создан» означало бы заказ длиной в месяц.
    expect(spanTitle('2026-08-01', '2026-08-31')).toBe('01–31 авг 2026');
    expect(momentTitle('2026-09-06T18:12:00Z', new Date('2026-09-07T10:00:00Z')))
      .toMatch(/^06 сен, /);
    expect(momentTitle('2026-09-07T09:30:00Z', new Date('2026-09-07T10:00:00Z')))
      .toMatch(/^Сегодня, /);
  });

  test('размер показывается только когда он известен', () => {
    expect(sizeTitle(251_904)).toBe('246 КБ');
    // Не «0 КБ»: файла нет, а не пустой файл.
    expect(sizeTitle(null)).toBeNull();
  });

  test('период считается включительно, перепутанные даты дают не больше нуля', () => {
    expect(days('2026-08-01', '2026-08-31')).toBe(31);
    expect(days('2026-08-31', '2026-08-01')).toBeLessThanOrEqual(0);
  });
});

describe('счётчики и итоги', () => {
  const counts = { total: 6, QUEUED: 1, RUNNING: 1, SUCCEEDED: 3, FAILED: 1, CANCELLED: 0 };

  test('«в работе» — это очередь и сборка вместе', () => {
    expect(tabCount('work', counts)).toBe(2);
    expect(tabCount('all', counts)).toBe(6);
  });

  test('«все» включает и отменённые', () => {
    const all = TABS.find((tab) => tab.key === 'all');
    expect(all?.statuses).toBe('');
  });

  test('«показаны все» пишется только когда набор действительно получен', () => {
    const items = [1, 2, 3];
    expect(shownTitle({ items, hasMore: false, counts: { total: 3 } }))
      .toBe('Показаны все 3 выгрузки');
    // Есть следующая страница — итог неизвестен.
    expect(shownTitle({ items, hasMore: true, counts: { total: 30 } }))
      .toBe('Показано 3 выгрузки');
    // Счётчик не пришёл — длина массива за итог не выдаётся.
    expect(shownTitle({ items, hasMore: false, counts: null }))
      .toBe('Показано 3 выгрузки');
  });
});

describe('состояние файла', () => {
  const ready = {
    status: 'SUCCEEDED', size_bytes: 1024, expires_at: '2026-09-10T00:00:00Z',
  } as never;

  test('у просроченного и убранного файла скачивать нечего', () => {
    const now = new Date('2026-09-07T10:00:00Z');
    expect(fileGone(ready, now)).toBe(false);
    // Уборка обнуляет размер вместе с ключом хранения.
    expect(fileGone({ ...(ready as object), size_bytes: null } as never, now)).toBe(true);
    // Срок мог истечь и до прохода уборки.
    expect(fileGone(
      { ...(ready as object), expires_at: '2026-09-01T00:00:00Z' } as never, now,
    )).toBe(true);
  });

  test('незавершённое задание не считается потерянным файлом', () => {
    expect(fileGone({ status: 'RUNNING', size_bytes: null, expires_at: null } as never))
      .toBe(false);
  });
});

describe('опрос', () => {
  test('идёт, только пока есть незавершённые задания', () => {
    expect(pending([{ status: 'SUCCEEDED' } as never])).toBe(false);
    expect(pending([{ status: 'SUCCEEDED' } as never, { status: 'QUEUED' } as never]))
      .toBe(true);
  });
});

describe('часовые пояса', () => {
  test('пояс офиса обещается только когда офис один', () => {
    const attendance = KINDS[0]!;
    expect(zoneNote(attendance, true)).toBe('Даты — по часовому поясу офиса');
    expect(zoneNote(attendance, false)).toContain('первого офиса');
  });

  test('у отсутствий свои правила сравнения дат', () => {
    const absences = KINDS.find((kind) => kind.key === 'absences')!;
    expect(zoneNote(absences, true)).not.toContain('офиса');
  });
});

describe('незнакомый вид отчёта', () => {
  test('показывается, а не прячется и не ломает страницу', () => {
    // `summary` собирается тем же механизмом, но карточки у него нет.
    expect(kindTitle('summary')).toBe('Сводка по офисам');
    expect(kindTitle('что-то новое')).toBe('что-то новое');
  });
});

// --- страница ---------------------------------------------------------------

const PERMISSIONS = ['reports.export', 'attendance.read', 'employees.read',
                     'absences.read', 'offices.read'];

const READY = {
  id: 'j-1',
  kind: 'attendance',
  fmt: 'xlsx',
  status: 'SUCCEEDED',
  filters: { date_from: '2026-08-01', date_to: '2026-08-31' },
  requested_by_user_id: USER.id,
  requested_by: USER.email,
  attempts: 1,
  progress_rows: 120,
  total_rows: null,
  file_name: 'humotech-attendance-2026-09-07.xlsx',
  size_bytes: 251_904,
  expires_at: '2026-12-01T00:00:00Z',
  error_message: null,
  started_at: '2026-09-07T05:41:00Z',
  finished_at: '2026-09-07T05:42:00Z',
  created_at: '2026-09-07T05:40:00Z',
  updated_at: '2026-09-07T05:42:00Z',
};

const QUEUED = {
  ...READY, id: 'j-2', kind: 'sessions', status: 'QUEUED',
  file_name: null, size_bytes: null, expires_at: null,
  started_at: null, finished_at: null,
};

const BROKEN = {
  ...READY, id: 'j-3', kind: 'lateness', fmt: 'csv', status: 'FAILED',
  file_name: null, size_bytes: null, expires_at: null,
  error_message: 'Не удалось собрать выгрузку. Попробуйте позже',
};

const COUNTS = { total: 3, QUEUED: 1, RUNNING: 0, SUCCEEDED: 1, FAILED: 1, CANCELLED: 0 };

function network(
  handler: (path: string, method: string) => Response | null = () => null,
  items: unknown[] = [READY, QUEUED, BROKEN],
) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) {
      return json(200, { ...USER, permissions: PERMISSIONS });
    }
    if (path.includes('/export-jobs/counts')) return json(200, COUNTS);
    if (path.includes('/export-jobs')) {
      return json(200, { items, next_cursor: null, has_more: false });
    }
    if (path.includes('/regions/')) {
      return json(200, {
        items: [{ id: 'r-1', code: 'TAS', name: 'Ташкент', status: 'ACTIVE' },
                { id: 'r-2', code: 'SAM', name: 'Самарканд', status: 'ACTIVE' }],
      });
    }
    if (path.includes('/offices/')) {
      return json(200, {
        items: [
          { id: 'o-1', code: 'A', name: 'Офис А', region_id: 'r-1',
            region_name: 'Ташкент', status: 'ACTIVE' },
          { id: 'o-2', code: 'B', name: 'Офис Б', region_id: 'r-2',
            region_name: 'Самарканд', status: 'ACTIVE' },
        ],
      });
    }
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

describe('выбор параметров', () => {
  test('регион ограничивает список офисов и сбрасывает чужой', async () => {
    network();
    renderApp('/reports');

    const office = await screen.findByLabelText('Офис');
    await waitFor(() => expect(within(office).getByText('Офис Б')).toBeTruthy());

    fireEvent.change(office, { target: { value: 'o-2' } });
    expect((office as HTMLSelectElement).value).toBe('o-2');

    fireEvent.change(screen.getByLabelText('Регион'), { target: { value: 'r-1' } });

    await waitFor(() => {
      // Офис Самарканда в Ташкенте не предлагается и не остаётся выбранным.
      expect(within(office).queryByText('Офис Б')).toBeNull();
      expect((office as HTMLSelectElement).value).toBe('');
    });
  });

  test('перепутанные даты названы у поля, а кнопка выключена', async () => {
    network();
    renderApp('/reports');

    const from = await screen.findByLabelText('Начало периода');
    fireEvent.change(from, { target: { value: '2026-09-30' } });
    fireEvent.change(screen.getByLabelText('Конец периода'), {
      target: { value: '2026-09-01' },
    });

    expect(await screen.findByText('Дата окончания раньше даты начала')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Сформировать отчёт/ })).toHaveProperty(
      'disabled', true,
    );
  });

  test('у списка сотрудников не предлагается срез на дату', async () => {
    network();
    renderApp('/reports');

    fireEvent.click(await screen.findByRole('radio', { name: /Сотрудники/ }));

    await waitFor(() => expect(screen.queryByLabelText('Начало периода')).toBeNull());
    expect(screen.getByText(/Среза на прошлую дату/)).toBeTruthy();
  });

  test('карточка без права на данные не предлагается', async () => {
    network((path) =>
      path.includes('/auth/')
        ? json(200, { ...USER, permissions: ['reports.export', 'attendance.read'] })
        : null,
    );
    renderApp('/reports');

    const card = await screen.findByRole('radio', { name: /Отсутствия/ });
    expect(card).toHaveProperty('disabled', true);
  });
});

describe('заказ', () => {
  test('подтверждение появляется только после ответа сервера', async () => {
    const gate: { open?: (value: Response) => void } = {};
    network((path, method) =>
      method === 'POST' && path.includes('/export-jobs')
        ? (new Promise<Response>((done) => { gate.open = done; }) as unknown as Response)
        : null,
    );
    renderApp('/reports');

    fireEvent.click(await screen.findByRole('button', { name: /Сформировать отчёт/ }));

    await waitFor(() => expect(screen.getByText('Отправляем…')).toBeTruthy());
    expect(screen.queryByText(/добавлен в очередь/)).toBeNull();

    gate.open?.(json(201, QUEUED));
    expect(await screen.findByText(/добавлен в очередь/)).toBeTruthy();
  });

  test('двойное нажатие не создаёт второе задание', async () => {
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/export-jobs') ? json(201, QUEUED) : null,
    );
    renderApp('/reports');

    const button = await screen.findByRole('button', { name: /Сформировать отчёт/ });
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() =>
      expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1),
    );
  });

  test('при отказе параметры остаются в форме', async () => {
    network((path, method) =>
      method === 'POST' && path.includes('/export-jobs')
        ? json(409, { error: { code: 'conflict', message: 'много заданий' } })
        : null,
    );
    renderApp('/reports');

    const from = await screen.findByLabelText('Начало периода');
    fireEvent.change(from, { target: { value: '2026-08-01' } });
    fireEvent.click(screen.getByRole('button', { name: /Сформировать отчёт/ }));

    expect(await screen.findByText(/незавершённых выгрузок/)).toBeTruthy();
    expect((from as HTMLInputElement).value).toBe('2026-08-01');
  });

  test('заказ уходит с выбранным видом, форматом и периодом', async () => {
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/export-jobs') ? json(201, QUEUED) : null,
    );
    renderApp('/reports');

    fireEvent.click(await screen.findByRole('radio', { name: /Опоздания/ }));
    fireEvent.click(screen.getByRole('button', { name: 'CSV' }));
    fireEvent.change(screen.getByLabelText('Начало периода'), {
      target: { value: '2026-08-01' },
    });
    fireEvent.change(screen.getByLabelText('Конец периода'), {
      target: { value: '2026-08-31' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Сформировать отчёт/ }));

    await waitFor(() => {
      const post = calls.find((c) => c.method === 'POST');
      expect(post?.body).toMatchObject({
        kind: 'lateness', fmt: 'csv',
        date_from: '2026-08-01', date_to: '2026-08-31',
      });
    });
  });
});

describe('история', () => {
  test('счётчики вкладок не зависят от выбранной вкладки', async () => {
    const calls = network();
    renderApp('/reports');
    await screen.findByText('Готов');

    fireEvent.click(screen.getByRole('tab', { name: /С ошибкой/ }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('status=FAILED'))).toBe(true),
    );
    const counts = calls.filter((c) => c.url.includes('/export-jobs/counts'));
    expect(counts.length).toBeGreaterThan(0);
    expect(counts.every((c) => !c.url.includes('status='))).toBe(true);
  });

  test('«в работе» спрашивает оба состояния одним запросом', async () => {
    const calls = network();
    renderApp('/reports');
    await screen.findByText('Готов');

    fireEvent.click(screen.getByRole('tab', { name: /В работе/ }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('status=QUEUED%2CRUNNING'))).toBe(true),
    );
  });

  test('ошибка не превращается в «выгрузок нет»', async () => {
    network((path) =>
      path.includes('/export-jobs') && !path.includes('counts')
        ? json(500, { error: {} })
        : null,
    );
    renderApp('/reports');

    expect(await screen.findByText(/Не удалось загрузить историю/)).toBeTruthy();
    expect(screen.queryByText(/Выгрузок пока нет/)).toBeNull();
  });

  test('пустая история предлагает сформировать первый отчёт', async () => {
    network(() => null, []);
    renderApp('/reports');

    expect(await screen.findByText(/сформируйте первый/)).toBeTruthy();
  });

  test('фильтр автора появляется только с правом на журнал', async () => {
    network();
    renderApp('/reports');
    await screen.findByText('Готов');
    expect(screen.queryByLabelText('Чьи выгрузки показывать')).toBeNull();
  });

  test('с правом на журнал фильтр уходит на сервер', async () => {
    const calls = network((path) =>
      path.includes('/auth/')
        ? json(200, { ...USER, permissions: [...PERMISSIONS, 'audit.read'] })
        : null,
    );
    renderApp('/reports');

    const pick = await screen.findByLabelText('Чьи выгрузки показывать');
    fireEvent.change(pick, { target: { value: 'all' } });

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('mine_only=false'))).toBe(true),
    );
  });
});

describe('действия над строкой', () => {
  test('готовый файл скачивается ссылкой на защищённый адрес', async () => {
    network();
    renderApp('/reports');

    const link = await screen.findByRole('link', { name: /Скачать/ });
    expect(link.getAttribute('href')).toContain('/export-jobs/j-1/download/');
    // Имя файла придумывает сервер: подменять его атрибутом download нельзя.
    expect(link.hasAttribute('download')).toBe(false);
  });

  test('у собираемой выгрузки кнопки отмены нет', async () => {
    network(() => null, [{ ...READY, id: 'j-9', status: 'RUNNING',
                           size_bytes: null, expires_at: null }]);
    renderApp('/reports');

    expect(await screen.findByText('Формируется')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Отменить' })).toBeNull();
  });

  test('очередь отменяется, и строка обновляется ответом сервера', async () => {
    // Сервер — источник правды: после отмены он и в списке отдаёт
    // отменённое задание. Клиент показывает ответ сразу, а затем
    // перечитывает набор ради счётчиков вкладок.
    let cancelled = false;
    network((path, method) => {
      if (method === 'POST' && path.includes('/cancel/')) {
        cancelled = true;
        return json(200, { ...QUEUED, status: 'CANCELLED' });
      }
      if (cancelled && path.includes('/export-jobs/counts')) {
        return json(200, { ...COUNTS, QUEUED: 0, CANCELLED: 1 });
      }
      if (cancelled && path.includes('/export-jobs')) {
        return json(200, {
          items: [READY, { ...QUEUED, status: 'CANCELLED' }, BROKEN],
          next_cursor: null, has_more: false,
        });
      }
      return null;
    });
    renderApp('/reports');

    fireEvent.click(await screen.findByRole('button', { name: 'Отменить' }));

    expect(await screen.findByText('Отменено')).toBeTruthy();
  });

  test('после действия счётчики вкладок пересчитываются', async () => {
    // Отмена меняет не только строку: «В работе» обязано уменьшиться,
    // иначе число рядом с вкладкой держится за прошлое состояние.
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/cancel/')
        ? json(200, { ...QUEUED, status: 'CANCELLED' })
        : null,
    );
    renderApp('/reports');

    const before = calls.filter((c) => c.url.includes('/export-jobs/counts')).length;
    fireEvent.click(await screen.findByRole('button', { name: 'Отменить' }));

    await waitFor(() =>
      expect(
        calls.filter((c) => c.url.includes('/export-jobs/counts')).length,
      ).toBeGreaterThan(before),
    );
  });

  test('параллельное изменение показывается, а не подменяется', async () => {
    network((path, method) => {
      if (method === 'POST' && path.includes('/cancel/')) {
        return json(409, { error: { code: 'conflict', message: 'уже началась' } });
      }
      if (method === 'GET' && path.match(/export-jobs\/j-2\/$/)) {
        return json(200, { ...QUEUED, status: 'RUNNING' });
      }
      return null;
    });
    renderApp('/reports');

    fireEvent.click(await screen.findByRole('button', { name: 'Отменить' }));

    expect(await screen.findByText(/Состояние выгрузки изменилось/)).toBeTruthy();
  });

  test('сломанная выгрузка повторяется и не даёт нажать дважды', async () => {
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/retry/')
        ? json(200, { ...BROKEN, status: 'QUEUED', error_message: null })
        : null,
    );
    renderApp('/reports');

    const retry = await screen.findByRole('button', { name: /Повторить/ });
    fireEvent.click(retry);
    fireEvent.click(retry);

    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/retry/'))).toHaveLength(1),
    );
  });

  test('у файла с истёкшим сроком кнопки «Скачать» нет', async () => {
    network(() => null, [{ ...READY, size_bytes: null }]);
    renderApp('/reports');

    expect(await screen.findByText(/Файл удалён по сроку хранения/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: /Скачать/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Заказать заново' })).toBeTruthy();
  });

  test('«заказать заново» переносит настройки, но не создаёт задание само', async () => {
    const calls = network(() => null, [{ ...READY, size_bytes: null }]);
    renderApp('/reports');

    fireEvent.click(await screen.findByRole('button', { name: 'Заказать заново' }));

    await waitFor(() =>
      expect((screen.getByLabelText('Начало периода') as HTMLInputElement).value)
        .toBe('2026-08-01'),
    );
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  });
});
