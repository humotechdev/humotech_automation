// @vitest-environment jsdom
/**
 * Стык с родной оболочкой Telegram.
 *
 * Главное здесь — не картинка, а две вещи, которые ломаются молча:
 * камера, оставшаяся работать после ухода с экрана отметки, и родная
 * панель Telegram, поверх которой оказывается наш контент.
 *
 * Своей верхней панели у кабинета нет и быть не должно: сверху уже стоит
 * телеграмная, с названием бота и крестиком. Две одинаковые шапки — это
 * не «фирменно», это отнятые полсотни пикселей на экране телефона.
 */

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../src/App';
import { forgetToken } from '../src/auth';
import {
  TZ,
  day,
  options,
  profile as profileStub,
  session,
  status as statusFixture,
  summary,
} from './fixtures';

const statusStub = statusFixture({ state: 'IN_OFFICE', open_session: session() });

afterEach(() => {
  cleanup();
  forgetToken();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as unknown as Record<string, unknown>).Telegram;
  document.documentElement.removeAttribute('style');
});

beforeEach(forgetToken);

/**
 * Оболочка Telegram с записанными вызовами и управляемыми событиями.
 *
 * `emit` вызывает подписчиков вручную: настоящий клиент присылает эти
 * события сам, а здесь их присылаем мы, когда проверяем реакцию.
 */
function fakeWebApp(over: Record<string, unknown> = {}) {
  const listeners = new Map<string, Set<() => void>>();

  const app = {
    initData: 'подписанная-строка',
    ready: vi.fn(),
    expand: vi.fn(),
    isVersionAtLeast: vi.fn((version: string) => Number.parseFloat(version) <= 8),
    setHeaderColor: vi.fn(),
    setBackgroundColor: vi.fn(),
    setBottomBarColor: vi.fn(),
    isFullscreen: false,
    requestFullscreen: vi.fn(),
    exitFullscreen: vi.fn(),
    showScanQrPopup: vi.fn(),
    closeScanQrPopup: vi.fn(),
    BackButton: {
      show: vi.fn(),
      hide: vi.fn(),
      onClick: vi.fn(),
      offClick: vi.fn(),
    },
    HapticFeedback: { notificationOccurred: vi.fn() },
    safeAreaInset: { top: 0, bottom: 0, left: 0, right: 0 },
    contentSafeAreaInset: { top: 0, bottom: 0, left: 0, right: 0 },
    viewportStableHeight: 800,
    onEvent: vi.fn((event: string, handler: () => void) => {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)!.add(handler);
    }),
    offEvent: vi.fn((event: string, handler: () => void) => {
      listeners.get(event)?.delete(handler);
    }),
    ...over,
  };

  (window as unknown as Record<string, unknown>).Telegram = { WebApp: app };

  return {
    app,
    count: (event: string) => listeners.get(event)?.size ?? 0,
    emit: async (event: string) => {
      await act(async () => {
        listeners.get(event)?.forEach((handler) => handler());
      });
    },
  };
}

/** Кабинет, который открывается: профиль, статус и статистика отвечают. */
function serveCabinet(scan?: unknown) {
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });

  const impl = async (url: string | URL | Request) => {
    const address = String(url);
    if (address.includes('/telegram/mini-app/auth')) {
      return json({ access_token: 'токен', employee: profileStub.employee });
    }
    if (address.includes('/me/profile')) return json(profileStub);
    if (address.includes('/me/status')) return json(statusStub);
    if (address.includes('/me/attendance/scan')) return json(scan ?? {});
    if (address.includes('/me/statistics')) {
      return json({ summary: summary(), days: [day()] });
    }
    if (address.includes('/me/history')) {
      return json({
        period: { first: '2026-09-01', last: '2026-09-04', timezone: TZ },
        days: [day()],
        total: 1,
        offset: 0,
        limit: 30,
      });
    }
    if (address.includes('/me/absences/options')) return json(options);
    return json({ requests: [], total: 0 });
  };
  vi.stubGlobal('fetch', impl as unknown as typeof fetch);
}

/** Открыть кабинет и дождаться главной. */
async function openCabinet() {
  render(<App />);
  await screen.findByText('Сейчас в офисе');
}

const tab = (name: string) =>
  screen.getByRole('button', { name: new RegExp(name) });

// --- запуск -----------------------------------------------------------------

describe('запуск оболочки', () => {
  it('нижняя панель красится только на клиенте, который это умеет', async () => {
    // setBottomBarColor появился в Bot API 7.10. На клиенте постарше
    // вызов не просто бесполезен — часть сборок отвечает исключением.
    const old = fakeWebApp({ isVersionAtLeast: vi.fn(() => false) });
    serveCabinet();

    render(<App />);
    await waitFor(() => expect(old.app.ready).toHaveBeenCalled());

    expect(old.app.setBackgroundColor).toHaveBeenCalledWith('#F7F9FC');
    expect(old.app.setBottomBarColor).not.toHaveBeenCalled();

    cleanup();
    forgetToken();

    const fresh = fakeWebApp();
    serveCabinet();
    render(<App />);
    await waitFor(() =>
      expect(fresh.app.setBottomBarColor).toHaveBeenCalledWith('#FFFFFF'),
    );
  });

  it('без window.Telegram приложение не падает', async () => {
    // Обычный браузер: телеграмных методов нет вовсе, ни один не зовём,
    // полноэкранный режим не изображаем.
    serveCabinet();
    expect(() => render(<App />)).not.toThrow();
    await screen.findByText('Откройте кнопкой в чате');
  });
});

// --- полноэкранный режим только на отметке ----------------------------------

describe('полноэкранный режим', () => {
  it('на главной и на статистике не запрашивается', async () => {
    const tg = fakeWebApp();
    serveCabinet();
    await openCabinet();

    expect(tg.app.requestFullscreen).not.toHaveBeenCalled();

    fireEvent.click(tab('Статистика'));
    await waitFor(() => expect(screen.queryByText('Сейчас в офисе')).toBeNull());
    expect(tg.app.requestFullscreen).not.toHaveBeenCalled();
  });

  it('на отметке запрашивается один раз при Bot API 8.0', async () => {
    const tg = fakeWebApp();
    serveCabinet();
    await openCabinet();

    fireEvent.click(tab('Отметка'));
    await screen.findByRole('heading', { name: 'Отметка' });

    expect(tg.app.isVersionAtLeast).toHaveBeenCalledWith('8.0');
    expect(tg.app.requestFullscreen).toHaveBeenCalledTimes(1);

    // Событие о входе в режим не должно вызывать повторный запрос.
    tg.app.isFullscreen = true;
    await tg.emit('fullscreenChanged');
    expect(tg.app.requestFullscreen).toHaveBeenCalledTimes(1);
  });

  it('на старом клиенте отметка работает без него', async () => {
    const tg = fakeWebApp({ isVersionAtLeast: vi.fn(() => false) });
    serveCabinet();
    await openCabinet();

    fireEvent.click(tab('Отметка'));
    await screen.findByRole('heading', { name: 'Отметка' });

    expect(tg.app.requestFullscreen).not.toHaveBeenCalled();
    // Экран полноценный: и камера, и ручной ввод на месте.
    expect(screen.getByRole('button', { name: /Открыть камеру/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ввести код вручную' })).toBeTruthy();
  });

  it('отказ не ломает экран и не показывает человеку ошибку', async () => {
    const tg = fakeWebApp();
    serveCabinet();
    await openCabinet();

    fireEvent.click(tab('Отметка'));
    await screen.findByRole('heading', { name: 'Отметка' });

    await tg.emit('fullscreenFailed');

    // Ни слова про fullscreen: человеку это ничего не объясняет.
    expect(screen.queryByText(/fullscreen|полноэкран/i)).toBeNull();
    expect(screen.getByRole('button', { name: /Открыть камеру/ })).toBeTruthy();
    // Навигация на месте — значит, уйти с экрана можно.
    expect(
      screen.getByRole('navigation', { name: 'Разделы приложения' }),
    ).toBeTruthy();
    // И повторного запроса после отказа нет.
    expect(tg.app.requestFullscreen).toHaveBeenCalledTimes(1);
  });

  it('в полноэкранном режиме нижней навигации нет поверх сканера', async () => {
    const tg = fakeWebApp();
    serveCabinet();
    await openCabinet();

    fireEvent.click(tab('Отметка'));
    await screen.findByRole('heading', { name: 'Отметка' });

    tg.app.isFullscreen = true;
    await tg.emit('fullscreenChanged');

    expect(
      screen.queryByRole('navigation', { name: 'Разделы приложения' }),
    ).toBeNull();
    // Взамен — своя кнопка возврата. Это возврат внутрь приложения,
    // а не подделка телеграмного крестика, который закрывает всё.
    expect(screen.getByRole('button', { name: 'На главную' })).toBeTruthy();
  });
});

// --- выход и уборка ---------------------------------------------------------

describe('уход с отметки', () => {
  it('нижняя навигация уводит с экрана и гасит режим и камеру', async () => {
    const tg = fakeWebApp();
    serveCabinet();
    await openCabinet();

    fireEvent.click(tab('Отметка'));
    await screen.findByRole('heading', { name: 'Отметка' });
    tg.app.isFullscreen = true;

    fireEvent.click(tab('История'));
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Отметка' })).toBeNull(),
    );

    expect(tg.app.exitFullscreen).toHaveBeenCalled();
    // Окно сканера держит камеру. Оставить его открытым — оставить
    // камеру работать после ухода с экрана.
    expect(tg.app.closeScanQrPopup).toHaveBeenCalled();
  });

  it('после прошедшей отметки камера и режим закрываются до результата', async () => {
    // Результат рисуется ВНУТРИ того же экрана, размонтирования нет,
    // и очистка эффекта не сработает. Если не закрыть здесь, итог
    // отметки человек читал бы поверх работающей камеры.
    const tg = fakeWebApp({
      showScanQrPopup: vi.fn(
        (_params: unknown, callback: (text: string) => boolean) => {
          callback('HT1-код-с-экрана');
        },
      ),
    });
    serveCabinet({
      status: 'ENTERED',
      accepted: true,
      occurred_at: '2026-09-04T03:54:00Z',
      office_name: 'Головной офис',
      point_name: 'Главный вход',
      session: null,
    });
    await openCabinet();

    fireEvent.click(tab('Отметка'));
    await screen.findByRole('heading', { name: 'Отметка' });
    tg.app.isFullscreen = true;

    fireEvent.click(screen.getByRole('button', { name: /Открыть камеру/ }));

    await screen.findByText('Вход отмечен');
    expect(tg.app.closeScanQrPopup).toHaveBeenCalled();
    expect(tg.app.exitFullscreen).toHaveBeenCalled();
  });

  it('подписки снимаются при уходе с экрана', async () => {
    const tg = fakeWebApp();
    serveCabinet();
    await openCabinet();

    const before = tg.count('viewportChanged');

    fireEvent.click(tab('Отметка'));
    await screen.findByRole('heading', { name: 'Отметка' });
    fireEvent.click(tab('Главная'));
    await screen.findByText('Сейчас в офисе');

    // Ни одна подписка не накопилась: сколько было до захода на экран,
    // столько и осталось.
    expect(tg.count('viewportChanged')).toBe(before);
  });
});

// --- безопасные зоны --------------------------------------------------------

describe('безопасные зоны', () => {
  it('пересчитываются по событиям Telegram', async () => {
    const tg = fakeWebApp();
    serveCabinet();
    await openCabinet();

    const root = document.documentElement;
    expect(root.style.getPropertyValue('--app-safe-top')).toBe('0px');

    // Телефон повернули: вырез сверху и полоса жеста снизу.
    tg.app.safeAreaInset = { top: 44, bottom: 34, left: 0, right: 0 };
    // Плюс собственная шапка Telegram в полноэкранном режиме.
    tg.app.contentSafeAreaInset = { top: 12, bottom: 0, left: 0, right: 0 };
    tg.app.viewportStableHeight = 742;
    await tg.emit('safeAreaChanged');

    // Складываются, а не выбираются: шапка стоит поверх выреза.
    expect(root.style.getPropertyValue('--app-safe-top')).toBe('56px');
    expect(root.style.getPropertyValue('--app-safe-bottom')).toBe('34px');
    expect(root.style.getPropertyValue('--app-viewport-height')).toBe('742px');
  });
});

// --- кнопка «назад» ---------------------------------------------------------

describe('кнопка «назад» Telegram', () => {
  it('на главной скрыта, на других экранах показана и ведёт внутрь', async () => {
    const tg = fakeWebApp();
    serveCabinet();
    await openCabinet();

    expect(tg.app.BackButton.hide).toHaveBeenCalled();
    expect(tg.app.BackButton.show).not.toHaveBeenCalled();

    fireEvent.click(tab('Статистика'));
    await waitFor(() => expect(tg.app.BackButton.show).toHaveBeenCalled());

    // Нажатие возвращает на главную, а не закрывает кабинет.
    const handler = tg.app.BackButton.onClick.mock.calls.at(-1)?.[0] as () => void;
    await act(async () => handler());
    await screen.findByText('Сейчас в офисе');
  });

  it('профиль за аватаром тоже закрывается ею', async () => {
    const tg = fakeWebApp();
    serveCabinet();
    await openCabinet();

    fireEvent.click(screen.getByRole('button', { name: 'Профиль и помощь' }));
    await screen.findByRole('heading', { name: 'Место работы' });

    const handler = tg.app.BackButton.onClick.mock.calls.at(-1)?.[0] as () => void;
    await act(async () => handler());
    await screen.findByText('Сейчас в офисе');
  });
});

// --- второй шапки нет -------------------------------------------------------

describe('никакой второй шапки', () => {
  it('кабинет не рисует ни названия бота, ни крестика закрытия', async () => {
    fakeWebApp();
    serveCabinet();
    const { container } = render(<App />);
    await screen.findByText('Сейчас в офисе');

    // Сверху уже стоит родная панель Telegram с названием и крестиком.
    expect(container.textContent).not.toContain('HUMOTECH');
    expect(
      screen.queryByRole('button', { name: /Закрыть приложение|Закрыть кабинет/ }),
    ).toBeNull();

    // Первое, что идёт после родной панели, — приветствие и имя.
    const header = container.querySelector('.app-header');
    expect(header?.textContent).toContain(profileStub.employee.full_name);
  });

  it('родная панель не прячется отступами и наложениями', () => {
    // Обходы вида `margin-top: -56px` ломаются на каждом обновлении
    // клиента и на каждом устройстве с другой высотой панели.
    const css = readStyles();

    for (const selector of ['.app-shell', '.app-scroll', '.page', '.app-header']) {
      const at = css.indexOf(`${selector} {`);
      expect(at).toBeGreaterThan(-1);
      const body = css.slice(at, css.indexOf('}', at));
      expect(body).not.toMatch(/margin-top:\s*-/);
      expect(body).not.toMatch(/top:\s*-/);
      expect(body).not.toContain('position: fixed');
    }

    // И высота считается от устойчивой высоты Telegram, а не от 100vh:
    // на 100vh нижняя панель уезжает за край при открытой клавиатуре.
    expect(css).toMatch(
      /\.app-shell\s*\{[^}]*min-height:\s*var\(--app-viewport-height/,
    );
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
