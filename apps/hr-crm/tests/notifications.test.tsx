/**
 * Уведомления.
 *
 * Проверяется то, что легче всего изобразить и труднее всего заметить:
 *
 * — «Отправлено» сразу после нажатия. Статус обязан приходить с сервера;
 * — второй заказ повтора от двойного нажатия — это второе сообщение
 *   человеку;
 * — придуманная история попыток у строки, для которой её не вели;
 * — сырой код ошибки вместо причины;
 * — счётчик вкладки по загруженной странице вместо всего набора.
 *
 * Данные здесь выдуманные. Настоящие сообщения сотрудникам в тестовые
 * данные не попадают.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import {
  CHANNEL, STATUS, TABS, attemptTitle, cancelBlockedBecause, deliveryNote,
  eventTitle, moving, reasonTitle, relatedLink, retryBlockedBecause, tabCount,
} from '../src/features/notifications/model';
import {
  historyNotKept, moment, shownLine,
} from '../src/pages/NotificationsPage';
import { USER, crm, fakeNetwork, json, pick, renderApp } from './helpers';

// --- чистые правила ---------------------------------------------------------

describe('вкладки и счётчики', () => {
  const counts = {
    total: 17, PENDING: 1, RUNNING: 1, SENT: 11, READ: 1, FAILED: 2,
    CANCELLED: 1, sent: 12, queued: 2, failed: 2, cancelled: 1,
    timezone: 'Asia/Dushanbe',
  };

  test('число на вкладке равно тому, что по ней отфильтруется', () => {
    // Инвариант: слагаемые счётчика и состояния фильтра — одно и то же.
    for (const tab of TABS) {
      if (tab.key === 'all') continue;
      const expected = tab.statuses
        .split(',')
        .reduce(
          (sum, code) =>
            sum + ((counts as unknown as Record<string, number>)[code] ?? 0),
          0,
        );
      expect(tabCount(tab.key, counts)).toBe(expected);
    }
  });

  test('вкладки покрывают весь набор: снятые не пропадают', () => {
    const sum = TABS.filter((tab) => tab.key !== 'all')
      .reduce((total, tab) => total + (tabCount(tab.key, counts) ?? 0), 0);
    expect(sum).toBe(counts.total);
    // «Снято» — своя вкладка, а не часть ошибок.
    expect(TABS.some((tab) => tab.key === 'cancelled')).toBe(true);
  });

  test('прочитанное считается отправленным, отправляемое — очередью', () => {
    expect(tabCount('sent', counts)).toBe(12);
    expect(tabCount('queued', counts)).toBe(2);
  });

  test('без сводки числа нет — ноль не выдаётся за пустоту', () => {
    expect(tabCount('sent', null)).toBeNull();
  });
});

describe('опрос', () => {
  test('ждать нечего — таймер не нужен', () => {
    expect(moving([{ status: 'SENT' }, { status: 'FAILED' }])).toBe(false);
    expect(moving([{ status: 'SENT' }, { status: 'PENDING' }])).toBe(true);
    expect(moving([{ status: 'RUNNING' }])).toBe(true);
  });
});

describe('причины отказа', () => {
  test('код переводится в понятную фразу', () => {
    expect(reasonTitle('not_linked')).toBe('Сотрудник не привязал Telegram');
    expect(reasonTitle('blocked_by_user')).toBe('Получатель заблокировал бота');
    expect(reasonTitle('TelegramNetworkError'))
      .toBe('Не удалось соединиться с Telegram');
    expect(reasonTitle('TelegramRetryAfter')).toBe('Отправка временно ограничена');
  });

  test('незнакомый код не выдаётся за причину и не показывается сырым', () => {
    expect(reasonTitle('SomeInternalTransportError')).toBe('Причина не распознана');
    expect(reasonTitle('SomeInternalTransportError'))
      .not.toContain('SomeInternalTransport');
  });

  test('причины нет — и подписи нет', () => {
    expect(reasonTitle(null)).toBeNull();
  });

  test('у успешной попытки причины не бывает', () => {
    expect(attemptTitle({ number: 1, attempted_at: '', outcome: 'SENT', reason: null }))
      .toBe('Отправлено');
    expect(attemptTitle({
      number: 2, attempted_at: '', outcome: 'FAILED', reason: 'blocked_by_user',
    })).toBe('Получатель заблокировал бота');
  });
});

describe('отправка и прочтение — разные вещи', () => {
  const base = { status: 'SENT' } as never;

  test('успешная отправка не объявляется прочтением', () => {
    const note = deliveryNote(base);
    expect(note).toContain('передано в Telegram');
    expect(note).toMatch(/Прочтение этим не подтверждается/);
    expect(note).not.toMatch(/^Прочитано/);
  });

  test('прочитано пишется только для состояния READ', () => {
    expect(deliveryNote({ status: 'READ' } as never)).toContain('открыл сообщение');
    expect(STATUS['SENT']).toBe('Отправлено');
    expect(STATUS['READ']).toBe('Прочитано');
  });
});

describe('разрешённые действия', () => {
  const row = (status: string, can_retry: boolean, can_cancel: boolean) =>
    ({ status, can_retry, can_cancel }) as never;

  test('отправленное не повторяют и не снимают, и причина названа', () => {
    expect(retryBlockedBecause(row('SENT', false, false), true))
      .toContain('уже в чате');
    expect(cancelBlockedBecause(row('SENT', false, false), true))
      .toContain('снять его из Telegram нельзя');
  });

  test('строку, которую держит отправщик, не трогают', () => {
    expect(retryBlockedBecause(row('RUNNING', false, false), true))
      .toContain('держит отправщик');
    expect(cancelBlockedBecause(row('RUNNING', false, false), true))
      .toContain('держит отправщик');
  });

  test('разрешение считает сервер, а интерфейс только объясняет', () => {
    expect(retryBlockedBecause(row('FAILED', true, true), true)).toBeNull();
    // Права нет — причина другая и проверяется раньше состояния.
    expect(retryBlockedBecause(row('FAILED', true, true), false))
      .toContain('Управление уведомлениями');
  });
});

describe('связанный объект', () => {
  const row = (type: string | null, id: string | null) =>
    ({ related_entity_type: type, related_entity_id: id,
       employee_id: 'e-1' }) as never;

  test('ссылка ведёт на существующую страницу CRM', () => {
    expect(relatedLink(row('absence_requests', 'r-1'))?.to)
      .toBe('/requests?request=r-1');
    expect(relatedLink(row('employee_questions', 'q-1'))?.to)
      .toBe('/questions?id=q-1');
    expect(relatedLink(row('telegram_accounts', 't-1'))?.to)
      .toBe('/employees/e-1');
  });

  test('для неизвестного вида ссылки нет — «открыть» в никуда хуже', () => {
    expect(relatedLink(row('some_future_table', 'x-1'))).toBeNull();
    expect(relatedLink(row(null, null))).toBeNull();
  });
});

describe('подписи', () => {
  test('для периода дата обязательна, для одного дня хватает времени', () => {
    const at = '2026-09-07T05:32:00Z'; // 10:32 в Asia/Dushanbe
    expect(moment(at, 'Asia/Dushanbe', true)).toBe('10:32');
    expect(moment(at, 'Asia/Dushanbe', false)).toMatch(/07 сент/);
    // Пояс берётся с сервера, а не из браузера.
    expect(moment(at, 'UTC', true)).toBe('05:32');
  });

  test('«показаны все» — только когда набор действительно кончился', () => {
    expect(shownLine(10, false, 10)).toBe('Показаны все 10 уведомлений');
    expect(shownLine(20, true, 57)).toBe('Показано 20 уведомлений из 57');
    expect(shownLine(1, false, 1)).toBe('Показаны все 1 уведомление');
    expect(shownLine(0, false, 0)).toBe('');
  });

  test('без счётчика длина страницы за итог не выдаётся', () => {
    expect(shownLine(20, false, null)).toBe('Показано 20 уведомлений');
  });

  test('число попыток в предупреждении не называется: оно про другой цикл', () => {
    expect(historyNotKept(false)).not.toMatch(/\d/);
    expect(historyNotKept(true)).not.toMatch(/\d/);
    expect(historyNotKept(false)).toContain('старше самой истории');
    // Записанные попытки есть — но сказано, что записаны НЕ ВСЕ.
    expect(historyNotKept(true)).toContain('не все попытки');
  });

  test('название события берётся из заголовка, а не из кода', () => {
    expect(eventTitle({ title: 'Отпуск согласован', notification_type: 'absence.x' }))
      .toBe('Отпуск согласован');
    expect(eventTitle({ title: null, notification_type: 'telegram.link.confirmed' }))
      .toBe('Привязка подтверждена');
    // Совсем незнакомый тип показывается как есть — выдумывать хуже.
    expect(eventTitle({ title: null, notification_type: 'custom.thing' }))
      .toBe('custom.thing');
    expect(CHANNEL['TELEGRAM']).toBe('Telegram');
  });
});

// --- страница целиком -------------------------------------------------------

const COUNTS = {
  total: 4, PENDING: 1, RUNNING: 0, SENT: 1, READ: 0, FAILED: 1, CANCELLED: 1,
  sent: 1, queued: 1, failed: 1, cancelled: 1, timezone: 'Asia/Dushanbe',
};

const ROW = {
  id: 'n-1',
  employee_id: 'e-1',
  employee: { id: 'e-1', full_name: 'Тестов Тест', employee_number: 'HT-001' },
  office_id: 'off-1',
  office_name: 'Тестовый офис',
  region_name: 'Тестовый регион',
  channel: 'TELEGRAM',
  notification_type: 'absence.request.approved',
  title: 'Тестовое событие',
  body: 'Тестовый текст уведомления.',
  status: 'FAILED',
  attempts: 3,
  error_message: 'blocked_by_user',
  scheduled_at: null,
  next_attempt_at: null,
  sent_at: null,
  read_at: null,
  related_entity_type: 'absence_requests',
  related_entity_id: 'r-1',
  created_at: '2026-09-07T05:32:00Z',
  updated_at: '2026-09-07T05:40:00Z',
  can_retry: true,
  can_cancel: true,
};

const SENT_ROW = {
  ...ROW, id: 'n-2', status: 'SENT', error_message: null,
  sent_at: '2026-09-07T05:35:00Z', can_retry: false, can_cancel: false,
  title: 'Тестовое отправленное',
};

const ATTEMPTS = {
  items: [
    { number: 1, attempted_at: '2026-09-07T05:33:00Z', outcome: 'FAILED',
      reason: 'TelegramNetworkError' },
    { number: 2, attempted_at: '2026-09-07T05:34:00Z', outcome: 'FAILED',
      reason: 'blocked_by_user' },
  ],
  kept: true,
};

const clean = (url: string) => url.split('?')[0] ?? '';

function network(
  own: (url: string, method: string) => Response | Promise<Response> | null = () => null,
  options: { rows?: unknown[]; counts?: unknown; permissions?: string[] } = {},
) {
  return fakeNetwork((url, call) => {
    const mine = own(url, call.method);
    if (mine) return mine;
    const bare = clean(url);

    if (bare.includes('/auth/')) {
      return json(200, {
        ...USER,
        permissions: options.permissions ?? [
          'notifications.read', 'notifications.manage', 'employees.read',
        ],
      });
    }
    if (bare.includes('/notifications/counts/')) {
      return json(200, options.counts ?? COUNTS);
    }
    const attempts = /\/notifications\/([^/]+)\/attempts\/$/.exec(bare);
    if (attempts) return json(200, ATTEMPTS);
    const one = /\/notifications\/([^/]+)\/$/.exec(bare);
    if (one && call.method === 'GET') {
      const id = one[1] as string;
      const row = [ROW, SENT_ROW].find((item) => item.id === id);
      return row ? json(200, row) : json(404, {});
    }
    if (bare.endsWith('/notifications/')) {
      return json(200, {
        items: options.rows ?? [ROW, SENT_ROW],
        next_cursor: null,
        has_more: false,
      });
    }
    if (bare.includes('/regions/')) {
      return json(200, {
        items: [{ id: 'reg-1', name: 'Тестовый регион', status: 'ACTIVE' }],
      });
    }
    if (bare.includes('/offices/')) {
      return json(200, {
        items: [
          { id: 'off-1', name: 'Тестовый офис', region_id: 'reg-1', status: 'ACTIVE' },
          { id: 'off-2', name: 'Второй офис', region_id: null, status: 'ACTIVE' },
        ],
      });
    }
    return crm(url) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

/**
 * Очередь отправки — вторая вкладка страницы.
 *
 * Первой открывается лента событий кадровика (её проверяет
 * `notification-feed.test.tsx`), поэтому сюда приходят по адресу
 * вкладки. Тот же адрес стоит в уведомлениях об ошибке доставки.
 */
const QUEUE = '/notifications?view=delivery';

const opened = () => screen.findByText('Тестовое событие');

describe('список', () => {
  test('сводка и счётчики приходят с сервера, а не считаются по странице', async () => {
    const calls = network(undefined, {
      counts: { ...COUNTS, total: 57, sent: 40, queued: 10, failed: 5, cancelled: 2 },
    });
    renderApp(QUEUE);
    await opened();

    const summary = screen.getByLabelText('Сводка по состояниям');
    expect(within(summary).getByText('57')).toBeTruthy();
    // На странице две строки — сводка обязана говорить про весь набор.
    expect(screen.getAllByRole('row').length).toBeLessThan(10);
    const counts = calls.filter((c) => clean(c.url).includes('/notifications/counts/'));
    expect(counts.length).toBeGreaterThan(0);
    // Фильтр вкладки в сводку не уходит.
    expect(counts.every((c) => !c.url.includes('status='))).toBe(true);
  });

  test('вкладка запрашивает ровно те состояния, что считает', async () => {
    const calls = network();
    renderApp(QUEUE);
    await opened();

    fireEvent.click(screen.getByRole('tab', { name: /Отправлено/ }));
    await waitFor(() => {
      const asked = calls.map((c) => decodeURIComponent(c.url));
      expect(asked.some((url) => url.includes('status=SENT,READ'))).toBe(true);
    });

    fireEvent.click(screen.getByRole('tab', { name: /В очереди/ }));
    await waitFor(() => {
      const asked = calls.map((c) => decodeURIComponent(c.url));
      expect(asked.some((url) => url.includes('status=PENDING,RUNNING'))).toBe(true);
    });
  });

  test('поиск, область и период уходят на сервер', async () => {
    const calls = network();
    renderApp(QUEUE);
    await opened();

    fireEvent.change(screen.getByLabelText('Поиск сообщения или сотрудника'), {
      target: { value: 'Тестов' },
    });
    await waitFor(() =>
      expect(calls.some((c) => decodeURIComponent(c.url).includes('search=Тестов')))
        .toBe(true),
    );

    await pick('Регион', 'Тестовый регион');
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('region_id=reg-1'))).toBe(true),
    );

    // Поле создаёт `AppDateRangePicker`: метка собирается из его
    // подписи, поэтому «Период уведомлений: начало». Значение вводится
    // так, как его вводит человек — днём, месяцем и годом, — и
    // применяется по уходу из поля.
    const since = screen.getByLabelText('Период уведомлений: начало');
    fireEvent.change(since, { target: { value: '01.09.2026' } });
    fireEvent.blur(since);
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('date_from=2026-09-01'))).toBe(true),
    );
  });

  test('смена региона сбрасывает несовместимый офис', async () => {
    const calls = network();
    renderApp(QUEUE);
    await opened();

    // Офис выбирается своим меню, а не `<select>`: у него портал и
    // поиск, и обращаться к `options` тут не к чему.
    await pick('Офис', 'Второй офис');
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('office_id=off-2'))).toBe(true),
    );

    await pick('Регион', 'Тестовый регион');

    // Офис снят вместе со сменой региона: показанное совпадает с
    // отправляемым. Проверяется по запросу, а не по значению поля:
    // меню рисует выбранное само и `value` у него нет.
    await waitFor(() => {
      const last = calls[calls.length - 1]?.url ?? '';
      expect(last.includes('office_id=off-2')).toBe(false);
    });
  });

  test('офис в строке — тот, что вернул сервер', async () => {
    network();
    renderApp(QUEUE);
    await opened();

    // Исторический офис считает сервер; интерфейс его не подменяет.
    expect(screen.getAllByText('Тестовый офис').length).toBeGreaterThan(0);
  });

  test('ошибка списка не выдаётся за пустую историю', async () => {
    network((url, method) =>
      method === 'GET' && clean(url).endsWith('/notifications/')
        ? json(500, { error: { code: 'server_error', message: 'x' } })
        : null,
    );
    renderApp(QUEUE);

    expect(await screen.findByText(/это ошибка запроса, а не пустая история/i))
      .toBeTruthy();
  });

  test('очередь открыта администратору без лишних вопросов', async () => {
    // Прав в интерфейсе больше нет: администратор один, и ему открыто
    // всё. Отказ исполняет сервер — прятать страницу в браузере значит
    // проверять доступ там, где его легче всего обойти.
    const calls = network(undefined, { permissions: ['employees.read'] });
    renderApp(QUEUE);

    await opened();
    expect(screen.queryByText(/Нет права на просмотр очереди отправки/)).toBeNull();
    expect(calls.some((c) => clean(c.url).includes('/notifications/'))).toBe(true);
  });

  test('под списком честное количество', async () => {
    network();
    renderApp(QUEUE);
    await opened();

    // На странице две строки, а в наборе четыре — подпись говорит правду.
    expect(await screen.findByText('Показано 2 уведомления из 4')).toBeTruthy();
    expect(screen.getByText(/Статус отправки не подтверждает прочтение/)).toBeTruthy();
  });
});

describe('карточка', () => {
  test('показывает содержимое, историю попыток и связанный объект', async () => {
    network();
    renderApp(QUEUE);
    await opened();
    fireEvent.click(screen.getByText('Тестовое событие'));

    const card = await screen.findByLabelText('Выбранное уведомление');
    expect(within(card).getByText('Тестовый текст уведомления.')).toBeTruthy();
    expect(within(card).getByText('Тестов Тест')).toBeTruthy();
    expect(within(card).getByText('Telegram')).toBeTruthy();
    // История — из серверных записей, обе попытки на месте.
    expect(within(card).getByText('Не удалось соединиться с Telegram')).toBeTruthy();
    expect(within(card).getAllByText('Получатель заблокировал бота').length)
      .toBeGreaterThan(0);
    expect(within(card).getByRole('link', { name: /Открыть заявку/ }))
      .toHaveProperty('href', expect.stringContaining('/requests?request=r-1'));
  });

  test('ни chat_id, ни сырого кода ошибки в карточке нет', async () => {
    network();
    renderApp(QUEUE);
    await opened();
    fireEvent.click(screen.getByText('Тестовое событие'));

    const card = await screen.findByLabelText('Выбранное уведомление');
    expect(card.textContent).not.toContain('blocked_by_user');
    expect(card.textContent).not.toContain('chat_id');
    expect(card.textContent).not.toContain('TelegramNetworkError');
  });

  test('у отправленного оба действия выключены и объяснены', async () => {
    const calls = network();
    renderApp(QUEUE);
    await opened();
    fireEvent.click(screen.getByText('Тестовое отправленное'));

    const card = await screen.findByLabelText('Выбранное уведомление');
    const retry = within(card).getByRole('button', { name: /Повторить отправку/ });
    expect(retry).toHaveProperty('disabled', true);
    expect(retry.getAttribute('title')).toMatch(/уже в чате/);
    fireEvent.click(retry);
    expect(calls.some((c) => c.url.includes('/retry/'))).toBe(false);
  });

  test('после повтора показывается ТО состояние, что вернул сервер', async () => {
    // Сервер моделируется с памятью: после повтора он и на чтение
    // карточки отвечает новым состоянием, как настоящий.
    let requeued = false;
    const QUEUED = { ...ROW, status: 'PENDING', attempts: 0,
                     error_message: null, can_retry: false, can_cancel: true };
    network((url, method) => {
      if (method === 'POST' && url.includes('/n-1/retry/')) {
        requeued = true;
        return json(200, QUEUED);
      }
      if (method === 'GET' && clean(url).endsWith('/notifications/n-1/') && requeued) {
        return json(200, QUEUED);
      }
      return null;
    });
    renderApp(QUEUE);
    await opened();
    fireEvent.click(screen.getByText('Тестовое событие'));
    const card = await screen.findByLabelText('Выбранное уведомление');

    fireEvent.click(within(card).getByRole('button', { name: /Повторить отправку/ }));

    // «Отправлено» сразу после нажатия было бы неправдой: сообщение
    // только вернулось в очередь.
    expect(await within(card).findByText('В очереди')).toBeTruthy();
    expect(within(card).queryByText('Отправлено')).toBeNull();
  });

  test('двойное нажатие не заказывает второй повтор', async () => {
    const calls = network((url, method) =>
      method === 'POST' && url.includes('/retry/')
        ? new Promise<Response>(() => {})
        : null,
    );
    renderApp(QUEUE);
    await opened();
    fireEvent.click(screen.getByText('Тестовое событие'));
    const card = await screen.findByLabelText('Выбранное уведомление');
    const retry = within(card).getByRole('button', { name: /Повторить отправку/ });

    fireEvent.click(retry);
    fireEvent.click(retry);
    fireEvent.click(retry);

    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/retry/'))).toHaveLength(1),
    );
  });

  test('конфликт объясняется и карточка перечитывается', async () => {
    let sent = false;
    network((url, method) => {
      if (method === 'POST' && url.includes('/retry/')) {
        sent = true;
        return json(409, {
          error: { code: 'conflict', message: 'Нельзя', details: null },
        });
      }
      if (method === 'GET' && clean(url).endsWith('/notifications/n-1/') && sent) {
        // Пока карточка была открыта, воркер успел отправить.
        return json(200, { ...ROW, status: 'SENT', can_retry: false,
                           can_cancel: false, error_message: null });
      }
      return null;
    });
    renderApp(QUEUE);
    await opened();
    fireEvent.click(screen.getByText('Тестовое событие'));
    const card = await screen.findByLabelText('Выбранное уведомление');

    fireEvent.click(within(card).getByRole('button', { name: /Повторить отправку/ }));

    expect(await within(card).findByRole('alert')).toHaveProperty(
      'textContent', expect.stringContaining('Состояние изменилось'),
    );
    await waitFor(() =>
      expect(
        within(card).getByRole('button', { name: /Повторить отправку/ }),
      ).toHaveProperty('disabled', true),
    );
  });

  test('история, которой не вели, не изображается пустым списком', async () => {
    network((url) =>
      clean(url).endsWith('/attempts/')
        ? json(200, { items: [], kept: false })
        : null,
    );
    renderApp(QUEUE);
    await opened();
    fireEvent.click(screen.getByText('Тестовое событие'));

    const card = await screen.findByLabelText('Выбранное уведомление');
    expect(await within(card).findByText(/История попыток .* не велась/))
      .toBeTruthy();
  });

  test('быстрое переключение строк не подставляет устаревший ответ', async () => {
    const gate: { open?: (value: Response) => void } = {};
    network((url, method) => {
      if (method === 'GET' && clean(url).endsWith('/notifications/n-1/')) {
        return new Promise<Response>((resolve) => {
          gate.open = resolve;
        });
      }
      return null;
    });
    renderApp(QUEUE);
    await opened();

    fireEvent.click(screen.getByText('Тестовое событие'));
    await screen.findByText('Открываем уведомление…');
    fireEvent.click(screen.getByText('Тестовое отправленное'));
    const card = await screen.findByLabelText('Выбранное уведомление');
    await within(card).findByText('Тестовое отправленное');

    gate.open?.(json(200, ROW));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(within(card).getByText('Тестовое отправленное')).toBeTruthy();
  });
});
