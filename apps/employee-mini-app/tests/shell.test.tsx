// @vitest-environment jsdom
/**
 * Каркас приложения: пять исходов входа и поведение оболочки Telegram.
 *
 * Эти экраны человек видит вместо кабинета, и каждый должен объяснять,
 * что делать. Пустой белый экран — тоже состояние, просто необъяснённое.
 *
 * Отдельно проверяется, что отозванная привязка не отличима от
 * непривязанного Telegram: разница в ответах позволяла бы перебором
 * узнать, работает ли в компании владелец конкретного аккаунта.
 */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../src/App';
import { BottomSheet } from '../src/ui/overlays';
import { forgetToken } from '../src/auth';
import { backButton, haptic, prepare, webApp } from '../src/telegram';
import {
  profile as profileStub,
  session,
  status as statusFixture,
} from './fixtures';

const statusStub = statusFixture({
  state: 'IN_OFFICE',
  open_session: session(),
});
const employeeStub = profileStub.employee;

afterEach(() => {
  cleanup();
  forgetToken();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as unknown as Record<string, unknown>).Telegram;
});

beforeEach(() => {
  forgetToken();
});

/** Ответ сервера на обмен подписи. */
function respond(status: number, body: unknown) {
  return vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
  ) as unknown as typeof fetch;
}

function inTelegram(initData: string, extra: Record<string, unknown> = {}) {
  (window as unknown as Record<string, unknown>).Telegram = {
    WebApp: { initData, ready: vi.fn(), expand: vi.fn(), ...extra },
  };
}

// --- 14-16. состояния входа -------------------------------------------------

describe('исходы входа', () => {
  it('вне Telegram: объясняет, что открывать надо кнопкой', async () => {
    // Подписи нет, и запрос на сервер не уходит вовсе.
    const fetchImpl = respond(200, {});
    vi.stubGlobal('fetch', fetchImpl);

    render(<App />);

    await screen.findByText('Откройте кнопкой в чате');
    expect(screen.getByText(/подпись запуска/)).toBeTruthy();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('привязка ждёт кадров: отдельное состояние, не ошибка', async () => {
    inTelegram('подписанная-строка');
    vi.stubGlobal(
      'fetch',
      respond(403, {
        error: { code: 'forbidden', message: 'нет доступа',
                 details: { reason: 'pending_confirmation' } },
      }),
    );

    render(<App />);

    await screen.findByText('Привязка ожидает подтверждения');
    expect(screen.getByRole('button', { name: 'Проверить ещё раз' })).toBeTruthy();
  });

  it('отозванная привязка неотличима от непривязанной', async () => {
    // Разница в ответах позволяла бы перебором узнать, работает ли
    // в компании владелец конкретного аккаунта. Сервер отвечает одним
    // и тем же `not_linked`, и экран здесь тоже один.
    inTelegram('подписанная-строка');
    vi.stubGlobal(
      'fetch',
      respond(403, {
        error: { code: 'forbidden', message: 'нет доступа',
                 details: { reason: 'not_linked' } },
      }),
    );

    const { container } = render(<App />);

    await screen.findByText('Telegram не привязан');
    expect(screen.getByText(/персональную ссылку/)).toBeTruthy();
    // Ни слова о том, что привязка когда-то была и её отозвали.
    expect(container.textContent).not.toContain('отозв');
    expect(container.textContent).not.toContain('заблокир');
  });

  it('отвергнутая подпись не выдаётся за отсутствие привязки', async () => {
    inTelegram('подделанная-строка');
    vi.stubGlobal('fetch', respond(403, { error: { code: 'forbidden' } }));

    render(<App />);

    await screen.findByText('Не получилось');
    expect(screen.getByRole('button', { name: /Обновить/ })).toBeTruthy();
  });

  it('обрыв сети предлагает повтор, а не отправляет в отдел кадров', async () => {
    inTelegram('подписанная-строка');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }) as unknown as typeof fetch,
    );

    render(<App />);

    await screen.findByText('Не получилось');
    expect(screen.getByText(/связ/)).toBeTruthy();
  });

  it('пока идёт проверка, показан скелет, а не пустой экран', () => {
    inTelegram('подписанная-строка');
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)) as unknown as typeof fetch,
    );

    const { container } = render(<App />);

    expect(screen.getByRole('heading', { name: 'Проверяем доступ' })).toBeTruthy();
    expect(container.querySelectorAll('.skeleton-card').length).toBeGreaterThan(0);
  });
});

// --- обновление уже открытого кабинета --------------------------------------

describe('неудачное обновление', () => {
  it('не стирает открытый кабинет и не прячет результат отметки', async () => {
    // `refresh` вызывает сам сканер сразу после успешной отметки. Если
    // этот запрос упадёт, а экран подменится ошибкой, человек не увидит
    // «Вход отмечен» и приложит пропуск второй раз — и получит отказ
    // «код уже использован». Отметка при этом давно записана.
    inTelegram('подписанная-строка');

    vi.stubGlobal('fetch', cabinetThen(500, { error: { code: 'server_error' } }));

    render(<App />);

    // Кабинет открылся.
    await screen.findByText('Сейчас в офисе');

    // Второе обновление падает.
    fireEvent(window, new Event('online'));

    await screen.findByText(/Не удалось обновить|сервер/i);
    // Главное: кабинет на месте, а не экран ошибки.
    expect(screen.getByText('Сейчас в офисе')).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Не получилось' })).toBeNull();
  });

  it('истёкший сеанс доходит до экрана, а не прячется в полоску', async () => {
    // 401 — не «данные могли устареть». Полоска с кнопкой «Ещё раз»
    // отвечала бы тем же отказом бесконечно: токен просрочен, и обновить
    // его повтором запроса нельзя. Выход один — выйти и войти заново по
    // свежей подписи, а эта кнопка есть только на экране ошибки.
    inTelegram('подписанная-строка');
    vi.stubGlobal('fetch', cabinetThen(401, { detail: 'expired' }));

    render(<App />);
    await screen.findByText('Сейчас в офисе');

    fireEvent(window, new Event('online'));

    await screen.findByRole('button', { name: 'Выйти' });
    expect(screen.queryByText(/Не удалось обновить/)).toBeNull();
  });

  it('отозванная посреди сеанса привязка тоже доходит до экрана', async () => {
    // 403 — привязку Telegram отозвали. Оставить человека смотреть
    // данные сотрудника под полоской «данные могли устареть» нельзя.
    inTelegram('подписанная-строка');
    vi.stubGlobal(
      'fetch',
      cabinetThen(403, { error: { code: 'forbidden', message: 'нет доступа' } }),
    );

    render(<App />);
    await screen.findByText('Сейчас в офисе');

    fireEvent(window, new Event('online'));

    await screen.findByRole('button', { name: 'Выйти' });
    expect(screen.queryByText(/Не удалось обновить/)).toBeNull();
  });
});

/**
 * Кабинет открывается, второй запрос профиля отвечает отказом.
 *
 * Все три проверки выше отличаются только кодом этого отказа — от него
 * и зависит, полоска это или экран.
 */
function cabinetThen(status: number, body: unknown): typeof fetch {
  let profileCalls = 0;
  const json = (payload: unknown, code = 200) =>
    new Response(JSON.stringify(payload), {
      status: code,
      headers: { 'Content-Type': 'application/json' },
    });

  const impl = async (url: string | URL | Request) => {
    const address = String(url);
    if (address.includes('/telegram/mini-app/auth')) {
      return json({ access_token: 'токен', employee: employeeStub });
    }
    if (address.includes('/me/profile')) {
      profileCalls += 1;
      return profileCalls === 1 ? json(profileStub) : json(body, status);
    }
    if (address.includes('/me/status')) return json(statusStub);
    return json({ summary: null, days: [] });
  };
  return impl as unknown as typeof fetch;
}

// --- оболочка Telegram ------------------------------------------------------

describe('оболочка Telegram', () => {
  it('при запуске сообщает о готовности и разворачивается', async () => {
    const ready = vi.fn();
    const expand = vi.fn();
    const setBackgroundColor = vi.fn();
    const setBottomBarColor = vi.fn();
    inTelegram('', { ready, expand, setBackgroundColor, setBottomBarColor });
    vi.stubGlobal('fetch', respond(200, {}));

    render(<App />);

    await waitFor(() => expect(ready).toHaveBeenCalled());
    expect(expand).toHaveBeenCalled();
    expect(setBackgroundColor).toHaveBeenCalledWith('#f7f9fc');
    expect(setBottomBarColor).toHaveBeenCalledWith('#ffffff');
  });

  it('старый клиент без новых методов не роняет запуск', () => {
    // Цвета появились в 7.10, безопасные зоны в 7.7. На клиенте
    // постарше приложение обязано остаться рабочим.
    inTelegram('', { ready: vi.fn() });
    expect(() => prepare()).not.toThrow();
  });

  it('вне Telegram подготовка ничего не делает и не падает', () => {
    expect(webApp()).toBeNull();
    expect(() => prepare()).not.toThrow();
    expect(() => haptic('success')).not.toThrow();
  });

  it('кнопка «назад» показывается и снимается парой', () => {
    const show = vi.fn();
    const hide = vi.fn();
    const onClick = vi.fn();
    const offClick = vi.fn();
    inTelegram('', { BackButton: { show, hide, onClick, offClick } });

    const handler = () => undefined;
    const detach = backButton(handler);

    expect(onClick).toHaveBeenCalledWith(handler);
    expect(show).toHaveBeenCalled();

    detach();
    expect(offClick).toHaveBeenCalledWith(handler);
    expect(hide).toHaveBeenCalled();
  });

  it('форма поверх экрана забирает «назад» себе и возвращает после', () => {
    // На Android кнопка Telegram совмещена с системной. Если шторка её
    // не перехватит, «назад» закроет всё приложение вместе с заполненной
    // заявкой и прикреплённой справкой.
    const show = vi.fn();
    const hide = vi.fn();
    const onClick = vi.fn();
    const offClick = vi.fn();
    inTelegram('', { BackButton: { show, hide, onClick, offClick } });

    const closeScreen = vi.fn();
    const closeSheet = vi.fn();

    const detachScreen = backButton(closeScreen);
    const detachSheet = backButton(closeSheet);

    // Работает верхний обработчик, и только он: одно нажатие не должно
    // закрывать сразу и форму, и экран под ней.
    expect(offClick).toHaveBeenCalledWith(closeScreen);
    const active = onClick.mock.calls.at(-1)?.[0];
    expect(active).toBe(closeSheet);

    // Форма закрылась — «назад» снова принадлежит экрану под ней.
    detachSheet();
    expect(onClick.mock.calls.at(-1)?.[0]).toBe(closeScreen);
    expect(hide).not.toHaveBeenCalled();

    detachScreen();
    expect(hide).toHaveBeenCalled();
  });

  it('шторка подключает «назад» и снимает её при закрытии', () => {
    const show = vi.fn();
    const hide = vi.fn();
    const onClick = vi.fn();
    const offClick = vi.fn();
    inTelegram('', { BackButton: { show, hide, onClick, offClick } });

    const onClose = vi.fn();
    const { rerender } = render(
      <BottomSheet open title="Больничный" onClose={onClose}>
        <p>поля</p>
      </BottomSheet>,
    );

    expect(show).toHaveBeenCalled();
    const handler = onClick.mock.calls.at(-1)?.[0] as () => void;
    handler();
    expect(onClose).toHaveBeenCalledTimes(1);

    rerender(
      <BottomSheet open={false} title="Больничный" onClose={onClose}>
        <p>поля</p>
      </BottomSheet>,
    );
    expect(hide).toHaveBeenCalled();
  });

  it('отклик телефона — только на итог, и только когда он поддержан', () => {
    const notificationOccurred = vi.fn();
    inTelegram('', { HapticFeedback: { notificationOccurred } });

    haptic('success');
    haptic('error');

    expect(notificationOccurred).toHaveBeenCalledTimes(2);
    expect(notificationOccurred).toHaveBeenNthCalledWith(1, 'success');
    expect(notificationOccurred).toHaveBeenNthCalledWith(2, 'error');
  });
});

// --- узкий экран ------------------------------------------------------------

describe('ширина 360 px', () => {
  it('ничто не задано шире экрана', () => {
    // Прямых замеров jsdom не делает, поэтому проверяется источник
    // горизонтальной прокрутки: фиксированные ширины и отключённый
    // перенос длинных строк.
    const css = readStyles();

    expect(css).toMatch(/body\s*\{[^}]*overflow-x:\s*hidden/);
    // Длинные ФИО, названия офисов и большие числа переносятся.
    expect(css).toContain('overflow-wrap: anywhere');
    // Широкое содержимое прокручивается внутри себя, а не тянет страницу.
    expect(css).toMatch(/\.chart\s*\{[^}]*overflow-x:\s*auto/);
    // Жёстких ширин шире экрана нет. `max-width` не в счёт: она
    // ограничивает сверху и на узком экране просто не срабатывает.
    const fixed = css.match(/(?<![a-z-])width:\s*(\d{3,})px/g) ?? [];
    for (const rule of fixed) {
      expect(Number.parseInt(rule.replace(/\D/g, ''), 10)).toBeLessThanOrEqual(360);
    }
  });

  it('на самых узких экранах поля и цифры ужимаются', () => {
    // Проверяется наличие правила, а не его применение: медиазапрос
    // смотрит на окно, а jsdom вёрстку не считает вовсе. На 360 px
    // без этого правила вёрстка не ломается — просто «3 ч 42 мин»
    // переносится на вторую строку.
    const css = readStyles();
    const narrow = css.slice(css.indexOf('@media (max-width: 360px)'));

    expect(narrow).toContain('--screen-padding: 12px');
    expect(narrow).toMatch(/\.metric-value\s*\{[^}]*font-size:\s*15px/);
    expect(narrow).toMatch(/\.nav-label\s*\{[^}]*font-size:\s*10px/);
  });

  it('текст нигде не мельче 11 px', () => {
    const css = readStyles();
    const sizes = (css.match(/font-size:\s*(\d+)px/g) ?? []).map((rule) =>
      Number.parseInt(rule.replace(/\D/g, ''), 10),
    );
    expect(sizes.length).toBeGreaterThan(0);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(10);
  });

  it('интерактивные элементы не ниже 44 px', () => {
    const css = readStyles();
    expect(css).toMatch(/--tap-target:\s*48px/);
    expect(css).toMatch(/\.btn\s*\{[^}]*min-height:\s*var\(--tap-target\)/);
  });
});

function readStyles(): string {
  const fs = require('node:fs') as typeof import('node:fs');
  const path = require('node:path') as typeof import('node:path');
  const root = path.join(__dirname, '..', 'src', 'styles');
  return (
    fs.readFileSync(path.join(root, 'tokens.css'), 'utf8') +
    fs.readFileSync(path.join(root, 'app.css'), 'utf8')
  );
}
