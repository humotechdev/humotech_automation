/**
 * Посещаемость: состав смены, карточка дня и журнал.
 *
 * Проверяются обещания, которые легко нарушить незаметно: «нет отметки»
 * не прогул, отсутствие графика не опоздание, обрезанный ответ не выдаётся
 * за полный состав, а разница между первым входом и последним выходом не
 * подменяет время в офисе.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const DAY = '2026-09-04';

const CARDS = [
  ['should_work_today', 'Должны работать сегодня', 214],
  ['in_office', 'Сейчас в офисе', 193],
  ['came', 'Пришли', 205],
  ['left', 'Уже ушли', 12],
  ['not_come', 'Не пришли', 9],
  ['late', 'Опоздали', 7],
  ['open_sessions', 'Незакрытые сессии', 3],
] as const;

function row(over: Partial<Record<string, unknown>> = {}) {
  return {
    employee_id: 'e-1',
    full_name: 'Каримов Алишер',
    employee_number: 'HT-001',
    office_id: 'o-1',
    office_name: 'Ташкент',
    state: 'IN_OFFICE',
    first_entry_at: `${DAY}T08:56:00Z`,
    last_exit_at: `${DAY}T12:04:00Z`,
    // Две сессии по два с половиной часа: разница между первым входом
    // и последним выходом (3 ч 08 м) — НЕ время в офисе.
    seconds: 5 * 3600 + 8 * 60,
    open_session_id: 's-2',
    late_minutes: null,
    scheduled_start: '09:00:00',
    scheduled_end: '18:00:00',
    department_name: 'Операционный отдел',
    position_name: 'Специалист поддержки',
    absence_code: null,
    absence_name: null,
    conflicting_marks: false,
    // Две сессии: до обеда и после. Вторая открыта — человек в офисе
    // сейчас, и правого края у неё нет.
    intervals: [
      { started_at: `${DAY}T08:56:00Z`, ended_at: `${DAY}T12:04:00Z`, seconds: 2 * 3600 + 30 * 60 },
      { started_at: `${DAY}T13:10:00Z`, ended_at: null, seconds: 2 * 3600 + 38 * 60 },
    ],
    ...over,
  };
}

const ROWS = [
  row(),
  row({ employee_id: 'e-2', full_name: 'Саидова Дилноза', employee_number: 'HT-004',
        state: 'NOT_COME', first_entry_at: null, last_exit_at: null, seconds: 0,
        open_session_id: null, intervals: [] }),
  row({ employee_id: 'e-3', full_name: 'Нурматов Жавохир', employee_number: 'HT-009',
        state: 'NO_SCHEDULE', scheduled_start: null, scheduled_end: null,
        late_minutes: null, last_exit_at: null, intervals: [] }),
  // Единственный опоздавший и единственный, у кого день закрыт: на нём
  // проверяется быстрый отбор «Опоздали».
  row({ employee_id: 'e-4', full_name: 'Рахимов Тимур', employee_number: 'HT-012',
        state: 'LEFT', late_minutes: 14, open_session_id: null,
        first_entry_at: `${DAY}T09:14:00Z`, last_exit_at: `${DAY}T18:02:00Z`,
        seconds: 8 * 3600 + 48 * 60,
        intervals: [
          { started_at: `${DAY}T09:14:00Z`, ended_at: `${DAY}T18:02:00Z`,
            seconds: 8 * 3600 + 48 * 60 },
        ] }),
];

function network(handler: (path: string, method: string) => Response | null = () => null) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    // Право на ручную отметку: без него кнопки в карточке нет вовсе.
    if (path.includes('/auth/')) {
      return json(200, { ...USER, permissions: ['attendance.read', 'attendance.manual'] });
    }
    if (path.includes('/dashboard')) {
      return json(200, {
        date: DAY,
        timezone: 'Asia/Dushanbe',
        cards: CARDS.map(([key, title, value]) => ({
          key, title, value, endpoint: null, params: {}, attention: false,
        })),
        warnings: [],
      });
    }
    if (path.includes('/attendance/presence')) {
      return json(200, {
        date: DAY, timezone: 'Asia/Dushanbe',
        counts: { IN_OFFICE: 1, NOT_COME: 1, NO_SCHEDULE: 1, LEFT: 1 },
        total: ROWS.length, truncated: false, items: ROWS,
      });
    }
    if (path.includes('/attendance/events')) {
      return json(200, {
        items: [
          { id: 'v-1', employee_id: 'e-1', office_id: 'o-1', office_name: 'Главный офис',
            qr_point_id: 'q-1', qr_point_name: 'Главный вход', event_type: 'ENTRY',
            source: 'QR', verification_status: 'ACCEPTED',
            occurred_at: `${DAY}T08:56:00Z`, received_at: `${DAY}T08:56:00Z`,
            rejection_reason: null },
          { id: 'v-2', employee_id: 'e-1', office_id: 'o-1', office_name: 'Главный офис',
            qr_point_id: 'q-1', qr_point_name: 'Главный вход', event_type: 'EXIT',
            source: 'QR', verification_status: 'ACCEPTED',
            occurred_at: `${DAY}T12:04:00Z`, received_at: `${DAY}T12:04:00Z`,
            rejection_reason: null },
        ],
        next_cursor: null, has_more: false,
      });
    }
    if (path.includes('/attendance/sessions')) {
      return json(200, {
        items: [{ id: 's-2', employee_id: 'e-1', office_id: 'o-1', office_name: 'Главный офис',
                  started_at: `${DAY}T12:42:00Z`, ended_at: null, duration_seconds: null,
                  status: 'OPEN', is_open: true }],
        next_cursor: null, has_more: false,
      });
    }
    return crm(path) ?? json(200, { items: [] });
  });
}

describe('панель «Сегодня»', () => {
  test('числа приходят с сервера, а не считаются по показанным строкам', async () => {
    // В ответе состава четыре строки. Панель обязана называть 193 и 214
    // из чисел дня: итог по тому, что поместилось в ответ, — не итог.
    // Без даты страница открывает сегодняшний день — тот, у которого
    // «сейчас в офисе» имеет смысл.
    network();
    renderApp('/attendance');

    expect(await screen.findByText('193 из 214')).toBeTruthy();
    expect(screen.getByText('сейчас в офисе')).toBeTruthy();
    // Опоздавшие и незакрытые сессии — из чисел дня, а не из строк.
    expect(screen.getAllByText('7').length).toBeGreaterThan(0);
    expect(screen.getAllByText('3').length).toBeGreaterThan(0);
  });

  test('для прошедшей даты показатель не называется «сейчас»', async () => {
    // 4 сентября 2026 в прошлом относительно «сегодня» тестовой среды
    // не гарантировано, поэтому проверяем прямо противоположное:
    // подпись зависит от даты, а не зашита в код.
    network();
    renderApp('/attendance?date=2020-01-02');

    expect(await screen.findByText('пришли на работу')).toBeTruthy();
    expect(screen.queryByText('сейчас в офисе')).toBeNull();
    // У прошедшего дня «сейчас в офисе» — ноль по определению, и
    // кольцо обязано считать пришедших, а не находящихся.
    expect(screen.getByText('205 из 214')).toBeTruthy();
  });

  test('быстрый отбор сужает таблицу и не трогает панель', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Каримов Алишер');

    const quick = screen.getByRole('group', { name: 'Быстрый отбор' });
    fireEvent.click(within(quick).getByRole('button', { name: /^Опоздали/ }));

    await waitFor(() => expect(screen.queryByText('Каримов Алишер')).toBeNull());
    expect(screen.getByText('Рахимов Тимур')).toBeTruthy();
    // Доля считается по всему составу дня: отбор в таблице — это не
    // новое положение дел, а другой взгляд на то же самое.
    expect(screen.getByText('205 из 214')).toBeTruthy();
  });
});

describe('состав смены', () => {
  test('время в офисе берётся с сервера, а не считается по входу и выходу', async () => {
    // 08:56 → 12:04 это 3 ч 08 м, но в офисе он провёл 5 ч 08 м:
    // посещений за день было несколько.
    network();
    renderApp(`/attendance?date=${DAY}`);

    expect((await screen.findAllByText('5 ч 08 м')).length).toBeGreaterThan(0);
    expect(screen.queryByText('3 ч 08 м')).toBeNull();
  });

  test('«нет отметки» не называется прогулом', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Саидова Дилноза');

    expect(screen.getAllByText('Нет отметки').length).toBeGreaterThan(0);
    expect(screen.queryByText(/[Пп]рогул/)).toBeNull();
  });

  test('без графика не показывается опоздание', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);
    const name = await screen.findByText('Нурматов Жавохир');
    const line = name.closest('tr');

    // Проверяется именно эта строка: опоздавшие в таблице есть, и
    // «нигде нет слова "позже"» было бы проверкой не того.
    expect(line?.textContent).toContain('График не задан');
    expect(line?.textContent).not.toMatch(/Позже на/);
  });

  test('обрезанный ответ не выдаётся за полный состав', async () => {
    network((path) =>
      path.includes('/attendance/presence')
        ? json(200, {
            date: DAY, timezone: 'Asia/Dushanbe', counts: {},
            total: 900, truncated: true, items: ROWS,
          })
        : null,
    );
    renderApp(`/attendance?date=${DAY}`);

    expect(await screen.findByText(/Показаны не все/)).toBeTruthy();
  });

  test('фильтр статуса уходит на сервер', async () => {
    const calls = network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findByText('Каримов Алишер');

    fireEvent.change(screen.getByLabelText('Статус'), { target: { value: 'NOT_COME' } });

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('state=NOT_COME'))).toBe(true),
    );
  });

  test('ошибка не превращается в пустой состав', async () => {
    network((path) =>
      path.includes('/attendance/presence') ? json(500, { error: {} }) : null,
    );
    renderApp(`/attendance?date=${DAY}`);

    expect(await screen.findByText(/Не удалось загрузить состав смены/)).toBeTruthy();
  });
});

describe('карточка дня', () => {
  test('показывает последовательность отметок и незакрытое посещение', async () => {
    network();
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    expect(await screen.findByText('Посещение продолжается')).toBeTruthy();
    expect(screen.getByText('Вход')).toBeTruthy();
    expect(screen.getByText('Выход')).toBeTruthy();
  });

  test('источник отметки показан рядом с точкой', async () => {
    network();
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    expect(await screen.findAllByText(/Главный вход · QR/)).toBeTruthy();
  });

  test('без права на ручную отметку кнопки нет', async () => {
    fakeNetwork((path) => {
      if (path.includes('/auth/')) return json(200, { ...USER, permissions: [] });
      if (path.includes('/dashboard')) {
        return json(200, { date: DAY, timezone: 'Asia/Dushanbe', cards: [], warnings: [] });
      }
      if (path.includes('/attendance/presence')) {
        return json(200, {
          date: DAY, timezone: 'Asia/Dushanbe', counts: {},
          total: 1, truncated: false, items: [row()],
        });
      }
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    expect(await screen.findByText(/Прав на ручную отметку нет/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Добавить отметку/ })).toBeNull();
  });
});

describe('ручная отметка', () => {
  test('называется добавлением, а не правкой существующей', async () => {
    // API умеет только добавить событие: обещать редактирование там,
    // где его нет, — худший вид неточности.
    network();
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    expect(await screen.findByRole('button', { name: /Добавить отметку/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Внести исправление/ })).toBeNull();
  });

  test('без причины сохранить нельзя, а при ошибке введённое остаётся', async () => {
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/attendance/manual')
        ? json(409, { error: { code: 'conflict', message: 'уже есть' } })
        : null,
    );
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    fireEvent.click(await screen.findByRole('button', { name: /Добавить отметку/ }));
    const save = screen.getByRole('button', { name: 'Сохранить' });
    expect(save.hasAttribute('disabled')).toBe(true);

    const reason = screen.getByLabelText('Причина');
    fireEvent.change(reason, { target: { value: 'Забыл отметиться на входе' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await screen.findByRole('alert');
    expect((reason as HTMLTextAreaElement).value).toBe('Забыл отметиться на входе');
    expect(calls.filter((c) => c.url.includes('/attendance/manual'))).toHaveLength(1);
  });
});

describe('журнал отметок', () => {
  test('по умолчанию показывает только успешные отметки', async () => {
    // Успешные отметки и отклонённые попытки — разные вещи.
    const calls = network();
    renderApp(`/attendance?date=${DAY}&tab=log`);

    await waitFor(() =>
      expect(
        calls.some((c) => c.url.includes('verification_status=ACCEPTED')),
      ).toBe(true),
    );
  });
});
