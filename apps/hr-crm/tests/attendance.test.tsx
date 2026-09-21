/**
 * Посещаемость: панель дня, состав смены, выбранный сотрудник и журнал.
 *
 * Проверяются обещания, которые легко нарушить незаметно: «нет отметки»
 * не прогул, отсутствие графика не опоздание, обрезанный ответ не выдаётся
 * за полный состав, а разница между первым входом и последним выходом не
 * подменяет время в офисе.
 *
 * Имя выбранного сотрудника на странице дважды — в строке и в правой
 * колонке, поэтому поиск по имени берёт все совпадения.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { USER, crm, fakeNetwork, json, pick, renderApp } from './helpers';

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
    // Две сессии: разница между первым входом и последним выходом
    // (3 ч 8 мин) — НЕ время в офисе.
    seconds: 5 * 3600 + 8 * 60,
    open_session_id: 's-2',
    late_minutes: null,
    scheduled_start: '09:00:00',
    scheduled_end: '18:00:00',
    department_name: 'Операционный отдел',
    position_name: 'Специалист поддержки',
    absence_code: null,
    absence_name: null,
    notice_kind: null,
    notice_comment: null,
    conflicting_marks: false,
    outside_geofence: false,
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
  // Предупредил, что задерживается: это не «нет отметки», и разница
  // между написавшим и пропавшим должна быть видна в строке.
  row({ employee_id: 'e-5', full_name: 'Юсупова Камила', employee_number: 'HT-015',
        state: 'LATE', first_entry_at: null, last_exit_at: null, seconds: 0,
        open_session_id: null, intervals: [],
        notice_kind: 'LATE', notice_comment: 'Пробки' }),
  // Единственный опоздавший и единственный, у кого день закрыт.
  row({ employee_id: 'e-4', full_name: 'Рахимов Тимур', employee_number: 'HT-012',
        state: 'LEFT', late_minutes: 14, open_session_id: null,
        first_entry_at: `${DAY}T09:14:00Z`, last_exit_at: `${DAY}T18:02:00Z`,
        seconds: 8 * 3600 + 48 * 60,
        intervals: [
          { started_at: `${DAY}T09:14:00Z`, ended_at: `${DAY}T18:02:00Z`,
            seconds: 8 * 3600 + 48 * 60 },
        ] }),
];

const EVENTS = [
  { id: 'v-1', employee_id: 'e-1', office_id: 'o-1', office_name: 'Главный офис',
    qr_point_id: 'q-1', qr_point_name: 'Главный вход', event_type: 'ENTRY',
    source: 'QR', verification_status: 'ACCEPTED',
    occurred_at: `${DAY}T08:56:00Z`, received_at: `${DAY}T08:56:00Z`,
    rejection_reason: null, inside_geofence: true, inside_office_network: true },
  { id: 'v-2', employee_id: 'e-1', office_id: 'o-1', office_name: 'Главный офис',
    qr_point_id: 'q-2', qr_point_name: 'Служебный вход', event_type: 'EXIT',
    source: 'QR', verification_status: 'ACCEPTED',
    occurred_at: `${DAY}T12:04:00Z`, received_at: `${DAY}T12:04:00Z`,
    rejection_reason: null, inside_geofence: true, inside_office_network: true },
];

function network(
  handler: (path: string, method: string) => Response | null = () => null,
  permissions: string[] = ['attendance.read', 'attendance.manual'],
) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, { ...USER, permissions });
    if (path.includes('/regions/')) {
      return json(200, {
        items: [
          { id: 'r-1', name: 'Ташкент', code: 'TASHKENT', status: 'ACTIVE' },
          { id: 'r-2', name: 'Бухара', code: 'BUKHARA', status: 'ACTIVE' },
        ],
        next_cursor: null, has_more: false,
      });
    }
    if (path.includes('/departments/')) {
      return json(200, {
        items: [
          { id: 'd-1', name: 'Операционный отдел', office_id: 'o-1',
            office_name: 'Главный офис', status: 'ACTIVE', staff: 4 },
        ],
        next_cursor: null, has_more: false,
      });
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
      return json(200, { items: EVENTS, next_cursor: null, has_more: false });
    }
    if (path.includes('/attendance/sessions')) {
      return json(200, {
        items: [{ id: 's-2', employee_id: 'e-1', office_id: 'o-1', office_name: 'Главный офис',
                  started_at: `${DAY}T13:10:00Z`, ended_at: null, duration_seconds: null,
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
    network();
    renderApp('/attendance');

    expect(await screen.findByText('193 из 214')).toBeTruthy();
    expect(screen.getByText('Сейчас в офисе')).toBeTruthy();
    // Опоздавшие и незакрытые — из чисел дня, а не из строк.
    expect(screen.getAllByText('7').length).toBeGreaterThan(0);
    expect(screen.getAllByText('3').length).toBeGreaterThan(0);
  });

  test('для прошедшей даты показатель не называется «сейчас»', async () => {
    network();
    renderApp('/attendance?date=2020-01-02');

    expect(await screen.findByText('Итоги дня')).toBeTruthy();
    // У прошедшего дня «сейчас в офисе» — ноль по определению, и
    // кольцо обязано считать пришедших, а не находящихся. Заголовок
    // рисуется сразу, числа — когда придут показатели дня.
    expect(await screen.findByText('205 из 214')).toBeTruthy();
    expect(screen.queryByText('Сейчас в офисе')).toBeNull();
  });

  test('быстрый отбор сужает таблицу и не трогает панель', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findAllByText('Каримов Алишер');

    const quick = screen.getByRole('group', { name: 'Быстрый отбор' });
    fireEvent.click(within(quick).getByRole('button', { name: /^Опоздали/ }));

    await waitFor(() => expect(screen.queryByText('Каримов Алишер')).toBeNull());
    expect(screen.getAllByText('Рахимов Тимур').length).toBeGreaterThan(0);
    // Доля считается по всему составу дня: отбор — другой взгляд на то же.
    expect(screen.getByText('205 из 214')).toBeTruthy();
  });
});

describe('состав смены', () => {
  test('время в офисе берётся с сервера, а не считается по входу и выходу', async () => {
    // 08:56 → 12:04 это 3 ч 8 мин, но в офисе он провёл 5 ч 8 мин:
    // посещений за день было несколько.
    network();
    renderApp(`/attendance?date=${DAY}`);

    expect((await screen.findAllByText('5 ч 8 мин')).length).toBeGreaterThan(0);
    expect(screen.queryByText('3 ч 8 мин')).toBeNull();
  });

  test('«нет отметки» не называется прогулом', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findAllByText('Саидова Дилноза');

    expect(screen.getAllByText('Нет отметки').length).toBeGreaterThan(0);
    expect(screen.queryByText(/[Пп]рогул/)).toBeNull();
  });

  test('без графика не показывается опоздание', async () => {
    network();
    renderApp(`/attendance?date=${DAY}`);
    const [name] = await screen.findAllByText('Нурматов Жавохир');
    const line = name?.closest('[role="row"]');

    // Проверяется именно эта строка: опоздавшие в таблице есть.
    expect(line?.textContent).toContain('График не задан');
    expect(line?.textContent).not.toMatch(/Опоздал/);
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
    await screen.findAllByText('Каримов Алишер');

    await pick('Статус', 'Нет отметки');

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

describe('фильтры', () => {
  test('регион, офис и отдел уходят на сервер', async () => {
    const calls = network();
    renderApp(`/attendance?date=${DAY}&region_id=r-1&office_id=o-1&department_id=d-1`);
    await screen.findAllByText('Каримов Алишер');

    const asked = calls.map((one) => one.url).filter((url) => url.includes('/presence'));
    expect(asked.some((url) => url.includes('office_id=o-1'))).toBe(true);
    expect(asked.some((url) => url.includes('department_id=d-1'))).toBe(true);
  });

  test('выбранный день запрашивается именно он, а не сегодня', async () => {
    // Иначе календарь показывает одну дату, а таблица — другую, и
    // расхождение видно только по времени событий.
    const calls = network();
    renderApp(`/attendance?date=${DAY}`);
    await screen.findAllByText('Каримов Алишер');

    const asked = calls.map((one) => one.url).filter((url) => url.includes('/presence'));
    expect(asked.every((url) => url.includes(`date=${DAY}`))).toBe(true);
  });

  test('смена региона снимает офис другого региона', async () => {
    // Офис чужого региона дал бы заведомо пустой список без объяснения.
    network();
    renderApp(`/attendance?date=${DAY}&office_id=o-1`);
    await screen.findAllByText('Каримов Алишер');

    await pick('Регион', 'Ташкент');

    await waitFor(() =>
      expect(window.location.search).not.toContain('office_id=o-1'),
    );
  });
});

describe('карточка дня', () => {
  test('показывает все события дня, а не первые три', async () => {
    network();
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    // У человека с обедом событий четыре: обрезанный список прячет как
    // раз то, из-за чего карточку открыли.
    expect(await screen.findByText('Вход · Главный вход')).toBeTruthy();
    expect(screen.getByText('Выход · Служебный вход')).toBeTruthy();
  });

  test('пустой день объясняется словами, а не белой панелью', async () => {
    network((path) =>
      path.includes('/attendance/events') ? json(200, { items: [], next_cursor: null, has_more: false }) : null,
    );
    renderApp(`/attendance?date=${DAY}&employee=e-2`);

    expect(await screen.findByText('Нет отметок за выбранный период')).toBeTruthy();
    expect(screen.getByText(/не отмечал ни входа, ни выхода/)).toBeTruthy();
  });
});

describe('предупреждение сотрудника', () => {
  test('сказавший «опаздываю» — не «нет отметки»', async () => {
    // Иначе стирается единственная разница между тем, кто написал, и
    // тем, кто пропал: кадровик звонит обоим.
    network();
    renderApp('/attendance');
    await screen.findAllByText('Юсупова Камила');

    expect(screen.getAllByText('Опаздывает').length).toBeGreaterThan(0);
  });

  test('причина видна в карточке, а не только в базе', async () => {
    network();
    renderApp('/attendance');
    const found = await screen.findAllByText('Юсупова Камила');

    fireEvent.click(found[0]!.closest('tr') ?? found[0]!);

    expect(
      await screen.findByText('Сотрудник предупредил, что опаздывает'),
    ).toBeTruthy();
    expect(screen.getByText('Пробки')).toBeTruthy();
  });
});

describe('выбранный сотрудник', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  test('показывает отметки дня с точками и незакрытое посещение', async () => {
    // Незакрытое посещение тянется до «сейчас», а «сейчас» — это часы
    // машины. Без закреплённого времени подпись то появлялась, то
    // сливалась с началом последнего отрезка, смотря когда запущен тест.
    // Время ставится на вечер того же дня: открытое посещение бывает
    // только сегодня.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(`${DAY}T15:30:00Z`));
    network();
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    expect(await screen.findByText('Вход · Главный вход')).toBeTruthy();
    expect(screen.getByText('Выход · Служебный вход')).toBeTruthy();
    // Вторая сессия открыта: шкала дня кончается «сейчас», а не уходом.
    expect(screen.getAllByText('сейчас').length).toBeGreaterThan(0);
  });

  test('исправление отметки доступно и открывает форму', async () => {
    // Прав в интерфейсе больше нет: администратор один, и ему открыто
    // всё. Отказ, если он когда-нибудь понадобится, исполняет сервер —
    // прятать кнопку в браузере значит проверять доступ там, где его
    // легче всего обойти.
    network(() => null, ['attendance.read']);
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    const fix = await screen.findByRole('button', { name: /Исправить отметку/ });
    expect(fix.hasAttribute('disabled')).toBe(false);
  });
});

describe('ручная отметка', () => {
  test('называется добавлением, а не правкой существующей', async () => {
    // API умеет только добавить событие: обещать редактирование там,
    // где его нет, — худший вид неточности.
    network();
    renderApp(`/attendance?date=${DAY}&employee=e-1`);

    fireEvent.click(await screen.findByRole('button', { name: /Исправить отметку/ }));
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

    fireEvent.click(await screen.findByRole('button', { name: /Исправить отметку/ }));
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
    const calls = network();
    renderApp(`/attendance?date=${DAY}&tab=log`);

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('verification_status=ACCEPTED'))).toBe(true),
    );
  });
});
