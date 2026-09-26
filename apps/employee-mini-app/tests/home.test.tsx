// @vitest-environment jsdom
/**
 * Главный экран: десять состояний, ради которых он и переделывался.
 *
 * Проверяется то, что легко сделать почти правильно и что стоит дорого:
 * «в офисе» без открытой сессии, ноль часов вместо «данных нет»,
 * выдуманный выход у незакрытой сессии, зелёный цвет у отсутствия.
 * Каждое из этого читается как утверждение о человеке, и ошибку в нём
 * замечают не сразу.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Home } from '../src/screens/Home';
import {
  firstEntry,
  punches,
  requestLabel,
  requestTone,
  shiftProgress,
  week,
} from '../src/screens/home-model';
import {
  day,
  failed,
  note,
  pending,
  ready,
  request,
  session,
  status,
} from './fixtures';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

const noop = () => undefined;

/** Экран целиком с подставленными секциями. Умолчание — «всё готово». */
function paint(over: Partial<Parameters<typeof Home>[0]> = {}) {
  const props: Parameters<typeof Home>[0] = {
    fullName: 'Рахимов Азиз Далерович',
    office: 'Головной офис',
    today: ready({ status: status(), sessions: [] }),
    week: pending(),
    requests: pending(),
    notes: pending(),
    onScan: noop,
    onHistory: noop,
    onRequests: noop,
    onNewRequest: noop,
    onCorrection: noop,
    onQuestion: noop,
    onNote: noop,
    onWeek: noop,
    ...over,
  };
  return render(<Home {...props} />);
}

// --- 1. сотрудник сейчас в офисе --------------------------------------------

describe('сотрудник в офисе', () => {
  it('крупная надпись, зелёная точка и последнее событие по QR', () => {
    const open = session({ is_open: true, ended_at: null, seconds: 13_320 });
    const { container } = paint({
      today: ready({
        status: status({
          state: 'IN_OFFICE',
          open_session: open,
          seconds_today: 24_120,
          last_entry_at: '2026-09-04T08:24:00Z',
        }),
        sessions: [open],
      }),
    });

    expect(screen.getByText('В офисе')).toBeTruthy();
    expect(container.querySelector('.dot-success')).toBeTruthy();
    expect(screen.getByText(/Последний QR: вход в 13:24/)).toBeTruthy();
    expect(screen.getByText('6 ч 42 мин')).toBeTruthy();
  });

  it('«в офисе» берётся из открытой сессии, а не из времени суток', () => {
    // Тот же час, та же смена — но сессии нет, и надпись обязана
    // измениться. Иначе статус был бы догадкой, а не фактом.
    paint({
      today: ready({
        status: status({ state: 'OUTSIDE', open_session: null }),
        sessions: [],
      }),
    });

    expect(screen.getByText('Не в офисе')).toBeTruthy();
  });
});

// --- 2. сотрудник не в офисе -------------------------------------------------

describe('сотрудник не в офисе', () => {
  it('подпись показывает последний настоящий выход', () => {
    paint({
      today: ready({
        status: status({
          state: 'OUTSIDE',
          open_session: null,
          last_entry_at: '2026-09-04T04:14:00Z',
          last_exit_at: '2026-09-04T07:36:00Z',
          seconds_today: 12_120,
        }),
        sessions: [
          session({ started_at: '2026-09-04T04:14:00Z', ended_at: '2026-09-04T07:36:00Z' }),
        ],
      }),
    });

    expect(screen.getByText('Не в офисе')).toBeTruthy();
    expect(screen.getByText(/Последний QR: выход в 12:36/)).toBeTruthy();
  });
});

// --- 3. сегодня нет отметок ---------------------------------------------------

describe('день без отметок', () => {
  it('список говорит словами, а не показывает пустоту', () => {
    paint({
      today: ready({
        status: status({
          state: 'WORKDAY_MISSED',
          open_session: null,
          seconds_today: 0,
          last_entry_at: null,
          last_exit_at: null,
        }),
        sessions: [],
      }),
    });

    expect(screen.getByText('Сегодня отметок ещё нет')).toBeTruthy();
    expect(screen.getByText('Отметок по QR ещё не было')).toBeTruthy();
    // Первый вход — прочерк, а не «00:00»: нуля здесь не было.
    expect(screen.getByText('—')).toBeTruthy();
  });
});

// --- 4. несколько входов и выходов -------------------------------------------

describe('несколько входов и выходов', () => {
  const three = [
    session({
      id: 's1',
      started_at: '2026-09-04T04:14:00Z',
      ended_at: '2026-09-04T07:36:00Z',
      entry_point_name: 'Центральный вход',
      exit_point_name: 'Центральный выход',
    }),
    session({
      id: 's2',
      started_at: '2026-09-04T08:24:00Z',
      ended_at: null,
      is_open: true,
      entry_point_name: 'Центральный вход',
      exit_point_name: null,
    }),
  ];

  it('события идут от свежего к старому и подписаны направлением', () => {
    const { container } = paint({
      today: ready({ status: status(), sessions: three }),
    });

    const rows = [...container.querySelectorAll('.log-title')].map(
      (node) => node.textContent,
    );
    expect(rows).toEqual(['Вход · 13:24', 'Выход · 12:36', 'Вход · 09:14']);
  });

  it('незакрытой сессии не дорисовывается выход', () => {
    const list = punches(three);
    expect(list.filter((p) => p.kind === 'exit')).toHaveLength(1);
  });

  it('показываются только три последних, остальное — по ссылке', () => {
    const many = [
      ...three,
      session({ id: 's0', started_at: '2026-09-04T02:00:00Z', ended_at: '2026-09-04T03:00:00Z' }),
    ];
    const { container } = paint({
      today: ready({ status: status(), sessions: many }),
    });

    expect(container.querySelectorAll('.log-row')).toHaveLength(3);
    expect(screen.getByText('Показать все события')).toBeTruthy();
  });

  it('первый вход дня — самый ранний, а не первый в списке', () => {
    expect(firstEntry(three)).toBe('2026-09-04T04:14:00Z');
  });
});

// --- 5. открытая сессия -------------------------------------------------------

describe('открытая сессия', () => {
  it('«в офисе сегодня» растёт вместе с ней и приходит с сервера', () => {
    const open = session({ is_open: true, ended_at: null, seconds: 13_320 });
    paint({
      today: ready({
        status: status({
          state: 'IN_OFFICE',
          open_session: open,
          seconds_today: 24_120,
        }),
        sessions: [open],
      }),
    });

    // Ровно то число, что отдал сервер: клиент секунды не досчитывает.
    expect(screen.getByText('6 ч 42 мин')).toBeTruthy();
  });

  it('вчерашняя открытая сессия названа вчерашней', () => {
    const open = session({
      is_open: true,
      ended_at: null,
      day: '2026-09-03',
      started_at: '2026-09-03T17:00:00Z',
    });
    paint({
      today: ready({
        status: status({
          state: 'IN_OFFICE',
          day: '2026-09-04',
          open_session: open,
          seconds_today: 0,
        }),
        sessions: [],
      }),
    });

    expect(screen.getByText(/Открытая сессия началась вчера/)).toBeTruthy();
  });
});

// --- 6. график не назначен ----------------------------------------------------

describe('график не назначен', () => {
  it('вместо полосы — честная подпись', () => {
    const { container } = paint({
      today: ready({
        status: status({ scheduled_start: null, scheduled_end: null }),
        sessions: [],
      }),
    });

    expect(screen.getByText('График не назначен')).toBeTruthy();
    expect(container.querySelector('.shift-track')).toBeNull();
  });

  it('выходной не называется «график не назначен»', () => {
    // График назначен, и в понедельник он снова заработает. Написать
    // обратное значило бы соврать о состоянии дел.
    paint({
      today: ready({
        status: status({
          state: 'DAY_OFF',
          scheduled_start: null,
          scheduled_end: null,
        }),
        sessions: [],
      }),
    });

    expect(screen.getByText('Сегодня выходной по графику')).toBeTruthy();
  });

  it('в отпуске подписан отпуск, а не отсутствие графика', () => {
    paint({
      today: ready({
        status: status({
          state: 'VACATION',
          absence_name: 'Ежегодный отпуск',
          scheduled_start: null,
          scheduled_end: null,
        }),
        sessions: [],
      }),
    });

    expect(screen.getByText('Ежегодный отпуск')).toBeTruthy();
  });

  it('без графика доли смены нет вовсе', () => {
    expect(
      shiftProgress(status({ scheduled_start: null, scheduled_end: null })),
    ).toBeNull();
  });

  it('полоса говорит о времени суток, а не о выполнении нормы', () => {
    const at = new Date('2026-09-04T08:30:00Z'); // 13:30 в Душанбе
    expect(shiftProgress(status(), at)).toEqual({
      elapsed: 270,
      total: 540,
      left: 270,
    });
  });

  it('прогресс не переваливает за сто процентов', () => {
    const at = new Date('2026-09-04T14:00:00Z'); // 19:00 в Душанбе, смена до 18:00
    const shift = shiftProgress(status(), at);
    expect(shift?.elapsed).toBe(shift?.total);
    expect(shift?.left).toBe(0);
  });

  it('до начала смены полоса пуста, а не отрицательна', () => {
    const at = new Date('2026-09-04T01:00:00Z'); // 06:00 в Душанбе
    expect(shiftProgress(status(), at)?.elapsed).toBe(0);
  });
});

// --- 7. выходной день ---------------------------------------------------------

describe('неделя', () => {
  const TODAY = '2026-09-04';

  it('выходной серый, а не зелёный', () => {
    // Прошедшая суббота, а не будущая: у будущего дня разговор другой —
    // он «данных нет», и проверять на нём выходной значило бы проверять
    // не то.
    const [saturday] = week([day({ day: '2026-08-29', seconds: 0,
      is_working_day: false, attended: false, norm_seconds: 0 })], TODAY);
    expect(saturday?.kind).toBe('off');
  });

  it('«ноль часов», «нет данных», «выходной» и «будущий день» — разное', () => {
    const rows = week(
      [
        // рабочий день без отметок: недоработка
        day({ day: '2026-09-01', seconds: 0, attended: false, norm_seconds: 28_800 }),
        // выходной, уже прошедший
        day({ day: '2026-08-29', seconds: 0, is_working_day: false, norm_seconds: 0 }),
        // будущий день
        day({ day: '2026-09-06', seconds: 0, norm_seconds: 28_800 }),
        // графика нет вовсе
        day({ day: '2026-09-02', seconds: 0, is_working_day: null, norm_seconds: null }),
      ],
      TODAY,
    );

    expect(rows.map((r) => r.kind)).toEqual(['short', 'off', 'blank', 'blank']);
  });

  it('отсутствие не красится зелёным', () => {
    const [vacation] = week(
      [day({ day: '2026-09-02', seconds: 0, attended: false,
        absence_code: 'ANNUAL_LEAVE', absence_name: 'Отпуск', norm_seconds: 28_800 })],
      TODAY,
    );
    expect(vacation?.kind).toBe('blank');
  });

  it('норма выполнена — зелёный, недобрана — янтарный', () => {
    const rows = week(
      [
        day({ day: '2026-09-01', seconds: 28_800, norm_seconds: 28_800 }),
        day({ day: '2026-09-02', seconds: 24_120, norm_seconds: 28_800 }),
      ],
      TODAY,
    );
    expect(rows.map((r) => r.kind)).toEqual(['done', 'short']);
  });

  it('переработка не даёт кольцу больше полного круга', () => {
    const [long] = week(
      [day({ day: '2026-09-01', seconds: 43_200, norm_seconds: 28_800 })],
      TODAY,
    );
    expect(long?.ratio).toBe(1);
  });

  it('вид сохраняется между открытиями приложения', () => {
    const days = [day({ day: '2026-09-04', seconds: 24_120 })];
    const { unmount } = paint({
      week: ready({ days, sessions: {} }),
      today: ready({ status: status({ day: '2026-09-04' }), sessions: [] }),
    });

    fireEvent.click(screen.getByRole('button', { name: 'Текстовый список' }));
    expect(localStorage.getItem('humotech.week-mode')).toBe('list');
    unmount();

    paint({
      week: ready({ days, sessions: {} }),
      today: ready({ status: status({ day: '2026-09-04' }), sessions: [] }),
    });
    expect(
      screen.getByRole('button', { name: 'Текстовый список' }).getAttribute('aria-pressed'),
    ).toBe('true');
  });

  it('карточка не исчезает при переключении вида', () => {
    const days = [day({ day: '2026-09-04', seconds: 24_120 })];
    const { container } = paint({
      week: ready({ days, sessions: {} }),
      today: ready({ status: status({ day: '2026-09-04' }), sessions: [] }),
    });

    expect(container.querySelector('.rings')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Интервалы рабочего времени' }));

    // Контейнер тот же, содержимое другое: страница не подпрыгивает.
    expect(container.querySelector('.week-body')).toBeTruthy();
    expect(container.querySelector('.rings')).toBeNull();
    expect(container.querySelector('.bars')).toBeTruthy();
  });
});

// --- 8. загрузка --------------------------------------------------------------

describe('загрузка', () => {
  it('первая загрузка — скелет вместо карточки, а не пустой экран', () => {
    const { container } = paint({ today: pending() });

    expect(container.querySelector('.card-skeleton')).toBeTruthy();
    // Приветствие уже на месте: имя известно до всяких запросов.
    expect(screen.getByText(/Рахимов|Азиз/)).toBeTruthy();
  });

  it('обновление не стирает показанное: данные остаются, значок рядом', () => {
    const { container } = paint({
      today: ready({ status: status({ state: 'IN_OFFICE' }), sessions: [] }, true),
    });

    expect(screen.getByText('В офисе')).toBeTruthy();
    expect(container.querySelector('.spinner')).toBeTruthy();
    expect(container.querySelector('.card-skeleton')).toBeNull();
  });
});

// --- 9. частичная ошибка API ---------------------------------------------------

describe('частичный сбой', () => {
  it('упавшая неделя не гасит статус', () => {
    paint({
      today: ready({ status: status({ state: 'IN_OFFICE' }), sessions: [] }),
      week: failed('Сервер временно недоступен'),
    });

    expect(screen.getByText('В офисе')).toBeTruthy();
    expect(screen.getByText('Сервер временно недоступен')).toBeTruthy();
    expect(screen.getByText('Моя неделя')).toBeTruthy();
  });

  it('у упавшей секции есть кнопка повтора, а не тупик', () => {
    const retry = vi.fn();
    paint({ week: failed('Сеть пропала', retry) });

    fireEvent.click(screen.getByRole('button', { name: /Ещё раз/ }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it('пустые заявки и объявления не занимают места', () => {
    const { container } = paint({
      requests: ready([]),
      notes: ready({ unread: 0, items: [] }),
    });

    expect(container.querySelectorAll('.mini')).toHaveLength(0);
  });
});

// --- 10. длинное имя и маленький экран ------------------------------------------

describe('длинное имя', () => {
  it('в приветствии остаётся имя, а не вся строка из трёх слов', () => {
    paint({ fullName: 'Абдурахмонова Гулнозахон Шарифджоновна' });
    expect(screen.getByText(/Гулнозахон/)).toBeTruthy();
    expect(screen.queryByText(/Шарифджоновна/)).toBeNull();
  });

  it('длинные подписи переносятся, а не растягивают страницу', () => {
    const { container } = paint({
      office: 'Представительство в Горно-Бадахшанской автономной области',
    });
    const office = container.querySelector('.hello-office');
    expect(office?.textContent).toContain('Горно-Бадахшанской');
  });
});

// --- заявки и объявления ---------------------------------------------------------

describe('нижние карточки', () => {
  it('активная заявка показана типом, датами и статусом', () => {
    paint({ requests: ready([request({ status: 'SUBMITTED' })]) });

    expect(screen.getByText('Ежегодный отпуск')).toBeTruthy();
    expect(screen.getByText('5–16 октября')).toBeTruthy();
    expect(screen.getByText('На рассмотрении')).toBeTruthy();
  });

  it.each([
    ['SUBMITTED', 'warning'],
    ['IN_REVIEW', 'warning'],
    ['APPROVED', 'success'],
    ['REJECTED', 'danger'],
    ['CANCELLED', 'idle'],
  ])('статус %s окрашен как %s', (state, tone) => {
    expect(requestTone(state, false)).toBe(tone);
  });

  it('ждущее решения продление тоже янтарное', () => {
    expect(requestTone('APPROVED', true)).toBe('warning');
    expect(requestLabel('APPROVED', true)).toBe('Продление на рассмотрении');
  });

  it('объявление показывает заголовок, текст и признак непрочитанного', () => {
    const { container } = paint({
      notes: ready({ unread: 1, items: [note({ title: 'Объявление' })] }),
    });

    expect(screen.getByText('Объявление')).toBeTruthy();
    expect(screen.getByText('Обновлён график работы офиса')).toBeTruthy();
    expect(container.querySelector('.mini-dot')).toBeTruthy();
  });

  it('прочитанное объявление не помечается', () => {
    const { container } = paint({
      notes: ready({
        unread: 0,
        items: [note({ is_read: true, read_at: '2026-09-04T06:00:00Z' })],
      }),
    });

    expect(container.querySelector('.mini-dot')).toBeNull();
  });

  it('нажатие на объявление отдаёт его целиком, а не один идентификатор', () => {
    const opened = vi.fn();
    const row = note();
    paint({ notes: ready({ unread: 1, items: [row] }), onNote: opened });

    fireEvent.click(screen.getByRole('button', { name: /Обновлён график/ }));
    expect(opened).toHaveBeenCalledWith(row);
  });
});

// --- быстрые действия ------------------------------------------------------------

describe('быстрые действия', () => {
  it('четыре плитки, каждая кнопка целиком', () => {
    const { container } = paint();
    const tiles = [...container.querySelectorAll('.tile')].map(
      (node) => node.textContent,
    );
    // У выключенных плиток вместо стрелки стоит причина: стрелка
    // обещала бы переход, которого нет.
    expect(tiles).toEqual([
      'Отпуск',
      'Больничный',
      'Исправить отметкуСкоро',
      'Задать вопросСкоро',
    ]);
  });

  it('исправление и вопрос выключены: своего endpoint у них нет', () => {
    paint();
    const fix = screen.getByRole('button', { name: 'Исправить отметку. Скоро' });
    expect(fix.hasAttribute('disabled')).toBe(true);
  });

  it('отпуск и больничный открывают свою форму', () => {
    const made = vi.fn();
    paint({ onNewRequest: made });

    fireEvent.click(screen.getByRole('button', { name: 'Отпуск' }));
    expect(made).toHaveBeenCalledWith('ANNUAL_LEAVE');

    fireEvent.click(screen.getByRole('button', { name: 'Больничный' }));
    expect(made).toHaveBeenCalledWith('SICK_LEAVE');
  });

  it('ручного входа и выхода на экране нет — только сканирование', () => {
    const { container } = paint();
    const labels = [...container.querySelectorAll('button')].map(
      (node) => node.textContent ?? '',
    );

    expect(labels.some((text) => text.includes('Отметиться'))).toBe(true);
    expect(labels.some((text) => /Отметить вход|Отметить выход/.test(text))).toBe(
      false,
    );
  });
});
