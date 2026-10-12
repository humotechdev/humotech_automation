/**
 * «Обращения»: очередь уезжает в сторону нажатой вкладки или фильтра.
 *
 * jsdom не раскладывает страницу и не проигрывает анимации: здесь
 * проверяется ЛОГИКА — направление по порядку в ряду, что переход
 * начинается, когда на экране новые данные (а не по нажатию), что
 * повторное нажатие и «туда и обратно» ничего не двигают, что быстрые
 * нажатия дают не больше двух слоёв, что без движения смена мгновенная,
 * и что отбор и запросы остались прежними. Плавность и отсутствие
 * мигания видны только в настоящем браузере.
 */

import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp, type Call } from './helpers';

const HR = { ...USER, permissions: ['questions.read', 'questions.answer'] };

const now = Date.now();

function row(id: string, name: string, status: string) {
  return {
    id,
    number: Number(id.replace(/\D/g, '')) || 1,
    employee: { id: `e-${id}`, full_name: name, employee_number: null, has_photo: false },
    office: { id: 'o-1', name: 'Головной офис' },
    topic: `Тема ${name}`,
    snippet: 'Текст',
    last_message_kind: 'EMPLOYEE',
    category: 'OTHER',
    priority: 'NORMAL',
    status,
    unread: false,
    awaiting_reply: true,
    due_at: new Date(now + 3_600_000).toISOString(),
    overdue: false,
    last_message_at: new Date(now - 60_000).toISOString(),
    created_at: new Date(now - 60_000).toISOString(),
    assignee: null,
  };
}

/** Строки каждой очереди: по одному узнаваемому имени. */
const ROWS: Record<string, ReturnType<typeof row>[]> = {
  'NEW|all': [row('q-1', 'Новый Алишер', 'NEW')],
  'NEW|unanswered': [row('q-2', 'Безответный Бобур', 'NEW')],
  'NEW|urgent': [row('q-3', 'Срочный Сардор', 'NEW')],
  'IN_PROGRESS|all': [row('q-4', 'Рабочий Ислом', 'IN_PROGRESS')],
  'IN_PROGRESS|urgent': [row('q-8', 'Рабочий срочный Умид', 'IN_PROGRESS')],
  'WAITING_EMPLOYEE|all': [row('q-5', 'Ждущий Жасур', 'WAITING_EMPLOYEE')],
  'CLOSED|all': [row('q-6', 'Закрытый Камол', 'CLOSED')],
};

const COUNTS = {
  statuses: { NEW: 3, IN_PROGRESS: 1, WAITING_EMPLOYEE: 1, CLOSED: 1 },
  total: 3, quick: { all: 3, unanswered: 1, mine: 0, urgent: 1, unread: 0 },
};

const LIST = /\/knowledge\/escalations\/(\?|$)/;

/**
 * Сервер очереди. Пока `hold` включён, ответы списка ждут `release()` —
 * так видно, что до ответа ничего не едет.
 */
function server() {
  const control = { hold: false, waiting: [] as (() => void)[] };
  const calls = fakeNetwork((path, call: Call) => {
    if (path.includes('/auth/')) return json(200, HR);
    if (path.includes('/knowledge/escalations/counts')) return json(200, COUNTS);
    if (path.includes('/knowledge/escalations/assignees')) return json(200, { items: [] });
    if (path.includes('/context/')) return json(404, {});
    if (/\/knowledge\/escalations\/q-\d+\/?$/.test(path)) return json(404, {});
    if (LIST.test(path)) {
      const query = new URL(path, 'http://x').searchParams;
      const key = `${query.get('status') ?? 'NEW'}|${query.get('quick') ?? 'all'}`;
      const body = { items: ROWS[key] ?? [], next_cursor: null, has_more: false };
      if (!control.hold) return json(200, body);
      return new Promise<Response>((resolve) => {
        const done = () => resolve(json(200, body));
        control.waiting.push(done);
        call.signal?.addEventListener('abort', done);
      });
    }
    if (path.includes('/offices')) return json(200, { items: [] });
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
  const release = async () => {
    await act(async () => {
      for (const done of control.waiting.splice(0)) done();
      await Promise.resolve();
    });
  };
  return { control, calls, release };
}

// --- среда с анимацией ------------------------------------------------------

type Fake = { element: Element; keyframes: Keyframe[]; finish: () => void; cancelled: boolean };

/** Подменяет `matchMedia` и `Element.animate`; анимации завершает тест. */
function motionEnvironment({ reduce = false } = {}) {
  const fakes: Fake[] = [];
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: reduce && query.includes('reduce'),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
  }));
  const animate = vi.fn(function animate(this: Element, keyframes: Keyframe[]) {
    let done: () => void = () => undefined;
    let fail: (error: unknown) => void = () => undefined;
    const finished = new Promise<void>((resolve, reject) => {
      done = resolve;
      fail = reject;
    });
    finished.catch(() => undefined);
    const fake: Fake = { element: this, keyframes, finish: () => done(), cancelled: false };
    fakes.push(fake);
    return {
      finished,
      cancel: () => {
        fake.cancelled = true;
        fail(new DOMException('cancelled', 'AbortError'));
      },
    } as unknown as Animation;
  });
  Element.prototype.animate = animate as unknown as Element['animate'];
  Element.prototype.getAnimations = function getAnimations(this: Element) {
    return fakes
      .filter((one) => one.element === this && !one.cancelled)
      .map((one) => ({ cancel: () => { one.cancelled = true; } })) as unknown as Animation[];
  };
  /** Только анимации очереди — переписка может двигаться по-своему. */
  const slides = () => fakes.filter((one) => one.element.classList.contains('tk-list'));
  return { fakes, slides };
}

afterEach(() => {
  delete (Element.prototype as Partial<Element>).animate;
  delete (Element.prototype as Partial<Element>).getAnimations;
});

const queue = () => screen.getByRole('region', { name: 'Очередь обращений' });
const liveList = () => queue().querySelector('.tk-list:not(.tk-list--leaving)') as HTMLElement;
const leavingLayers = () => queue().querySelectorAll('.tk-list--leaving');
const tab = (name: RegExp) => screen.getByRole('tab', { name });
const chip = (name: RegExp) => within(queue()).getByRole('button', { name });

/** Вход живого списка и уход снимка последнего перехода. */
function lastPair(slides: Fake[]) {
  const enter = [...slides].reverse().find((one) => !one.element.classList.contains('tk-list--leaving'));
  const leave = [...slides].reverse().find((one) => one.element.classList.contains('tk-list--leaving'));
  return { enter, leave };
}

async function open(path: string, name: string) {
  renderApp(path);
  await within(await screen.findByRole('region', { name: 'Очередь обращений' })).findByText(name);
}

// --- направление ------------------------------------------------------------

describe('направление по порядку в ряду', () => {
  test('вкладка правее: новый список приходит справа, старый уходит влево', async () => {
    const { slides } = motionEnvironment();
    const { control, release } = server();
    await open('/questions', 'Новый Алишер');

    control.hold = true;
    fireEvent.click(tab(/В работе/));
    // До ответа сервера ничего не едет и прежний список не выдаётся за новый.
    expect(slides()).toHaveLength(0);
    expect(leavingLayers()).toHaveLength(0);

    await release();
    await within(liveList()).findByText('Рабочий Ислом');
    const { enter, leave } = lastPair(slides());
    expect(enter?.keyframes[0]?.transform).toBe('translateX(14%)');
    expect(enter?.keyframes[1]?.transform).toBe('translateX(0)');
    expect(leave?.keyframes[1]?.transform).toBe('translateX(-14%)');
    expect(slides()).toHaveLength(2);

    // Уходит именно то, что было на экране, и он скрыт от фокуса.
    const ghost = leavingLayers()[0] as HTMLElement;
    expect(within(ghost).getByText('Новый Алишер')).toBeTruthy();
    expect(ghost.getAttribute('aria-hidden')).toBe('true');
    expect(ghost.hasAttribute('inert')).toBe(true);
    expect(within(liveList()).queryByText('Новый Алишер')).toBeNull();

    await act(async () => {
      for (const one of slides()) one.finish();
      await Promise.resolve();
    });
    await waitFor(() => expect(leavingLayers()).toHaveLength(0));
  });

  test('вкладка левее: новый список приходит слева', async () => {
    const { slides } = motionEnvironment();
    server();
    await open('/questions?status=WAITING_EMPLOYEE', 'Ждущий Жасур');
    fireEvent.click(tab(/Новые/));
    await within(liveList()).findByText('Новый Алишер');
    const { enter, leave } = lastPair(slides());
    expect(enter?.keyframes[0]?.transform).toBe('translateX(-14%)');
    expect(leave?.keyframes[1]?.transform).toBe('translateX(14%)');
  });

  test('прыжок через несколько вкладок — то же направление, один переход', async () => {
    const { slides } = motionEnvironment();
    server();
    await open('/questions', 'Новый Алишер');
    fireEvent.click(tab(/Закрытые/));
    await within(liveList()).findByText('Закрытый Камол');
    expect(slides()).toHaveLength(2);
    expect(lastPair(slides()).enter?.keyframes[0]?.transform).toBe('translateX(14%)');
  });

  test('быстрые фильтры — свой ряд: «Все → Срочные» справа, «Срочные → Без ответа» слева', async () => {
    const { slides } = motionEnvironment();
    server();
    await open('/questions', 'Новый Алишер');

    fireEvent.click(chip(/^Срочные/));
    await within(liveList()).findByText('Срочный Сардор');
    expect(lastPair(slides()).enter?.keyframes[0]?.transform).toBe('translateX(14%)');

    fireEvent.click(chip(/^Без ответа/));
    await within(liveList()).findByText('Безответный Бобур');
    expect(lastPair(slides()).enter?.keyframes[0]?.transform).toBe('translateX(-14%)');
    // Не больше двух слоёв даже при незавершённых переходах.
    expect(leavingLayers()).toHaveLength(1);
  });
});

// --- без движения там, где его не должно быть ----------------------------------

describe('повторные нажатия', () => {
  test('нажатие на активный фильтр и активную вкладку ничего не двигает', async () => {
    const { slides } = motionEnvironment();
    const { calls } = server();
    await open('/questions', 'Новый Алишер');
    const before = calls.filter((one) => LIST.test(one.url)).length;

    fireEvent.click(chip(/^Все/));
    fireEvent.click(tab(/Новые/));
    await act(async () => { await Promise.resolve(); });
    expect(slides()).toHaveLength(0);
    expect(leavingLayers()).toHaveLength(0);
    expect(calls.filter((one) => LIST.test(one.url)).length).toBe(before);
  });

  test('активная вкладка при фильтре «Срочные» сбрасывает фильтр без перехода', async () => {
    const { slides } = motionEnvironment();
    server();
    await open('/questions?status=IN_PROGRESS&quick=urgent', 'Рабочий срочный Умид');
    fireEvent.click(tab(/В работе/));
    await within(liveList()).findByText('Рабочий Ислом');
    expect(slides()).toHaveLength(0);
    expect(leavingLayers()).toHaveLength(0);
  });

  test('«туда и обратно» до ответа сервера — ни одного перехода', async () => {
    const { slides } = motionEnvironment();
    const { control, release } = server();
    await open('/questions', 'Новый Алишер');
    control.hold = true;
    fireEvent.click(tab(/В работе/));
    fireEvent.click(tab(/Новые/));
    await release();
    await release();
    await within(liveList()).findByText('Новый Алишер');
    expect(slides()).toHaveLength(0);
    expect(leavingLayers()).toHaveLength(0);
  });
});

// --- быстрые нажатия -------------------------------------------------------------

describe('быстрые нажатия', () => {
  test('несколько вкладок до ответа: один переход к итоговой, уходит показанная', async () => {
    const { slides } = motionEnvironment();
    const { control, release } = server();
    await open('/questions', 'Новый Алишер');
    control.hold = true;
    fireEvent.click(tab(/В работе/));
    fireEvent.click(tab(/Ждут сотрудника/));
    await release();
    await release();
    await within(liveList()).findByText('Ждущий Жасур');
    expect(slides()).toHaveLength(2);
    expect(lastPair(slides()).enter?.keyframes[0]?.transform).toBe('translateX(14%)');
    const ghost = leavingLayers()[0] as HTMLElement;
    expect(within(ghost).getByText('Новый Алишер')).toBeTruthy();
    expect(within(ghost).queryByText('Рабочий Ислом')).toBeNull();
  });

  test('новое нажатие во время перехода: прежний отменён, слоёв не больше двух', async () => {
    const { slides } = motionEnvironment();
    server();
    await open('/questions', 'Новый Алишер');

    fireEvent.click(tab(/В работе/));
    await within(liveList()).findByText('Рабочий Ислом');
    const first = lastPair(slides());

    fireEvent.click(tab(/Закрытые/));
    await within(liveList()).findByText('Закрытый Камол');
    expect(first.enter?.cancelled).toBe(true);
    expect(leavingLayers()).toHaveLength(1);
    const ghost = leavingLayers()[0] as HTMLElement;
    expect(within(ghost).getByText('Рабочий Ислом')).toBeTruthy();

    // Завершение старого перехода не снимает новый слой.
    await act(async () => {
      first.leave?.finish();
      await Promise.resolve();
    });
    expect(leavingLayers()).toHaveLength(1);

    await act(async () => {
      for (const one of slides()) one.finish();
      await Promise.resolve();
    });
    await waitFor(() => expect(leavingLayers()).toHaveLength(0));
    expect(within(liveList()).getByText('Закрытый Камол')).toBeTruthy();
  });

  test('возврат на вкладку из памяти: переход один, свежий ответ не двигает снова', async () => {
    const { slides } = motionEnvironment();
    server();
    await open('/questions', 'Новый Алишер');
    fireEvent.click(tab(/В работе/));
    await within(liveList()).findByText('Рабочий Ислом');
    await act(async () => {
      for (const one of slides()) one.finish();
      await Promise.resolve();
    });
    const before = slides().length;

    fireEvent.click(tab(/Новые/));
    await within(liveList()).findByText('Новый Алишер');
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    expect(slides().length - before).toBe(2);
  });
});

// --- без движения ------------------------------------------------------------------

describe('просьба уменьшить движение', () => {
  test('смена мгновенная, анимаций и второго слоя нет', async () => {
    const { slides } = motionEnvironment({ reduce: true });
    server();
    await open('/questions', 'Новый Алишер');
    fireEvent.click(tab(/В работе/));
    await within(liveList()).findByText('Рабочий Ислом');
    fireEvent.click(chip(/^Срочные/));
    await within(liveList()).findByText('Рабочий срочный Умид');
    expect(slides()).toHaveLength(0);
    expect(leavingLayers()).toHaveLength(0);
  });
});

// --- отбор не изменился ----------------------------------------------------------------

describe('отбор и запросы', () => {
  test('запрос несёт вкладку и фильтр, показаны строки ответа', async () => {
    motionEnvironment();
    const { calls } = server();
    await open('/questions', 'Новый Алишер');
    fireEvent.click(tab(/В работе/));
    await within(liveList()).findByText('Рабочий Ислом');
    fireEvent.click(chip(/^Срочные/));
    await within(liveList()).findByText('Рабочий срочный Умид');

    const last = calls.filter((one) => LIST.test(one.url)).at(-1) as Call;
    const query = new URL(last.url, 'http://x').searchParams;
    expect(query.get('status')).toBe('IN_PROGRESS');
    expect(query.get('quick')).toBe('urgent');
    expect(within(liveList()).queryByText('Рабочий Ислом')).toBeNull();
    expect(tab(/В работе/).getAttribute('aria-selected')).toBe('true');
    expect(chip(/^Срочные/).getAttribute('aria-pressed')).toBe('true');
  });
});
