// @vitest-environment jsdom
/**
 * Быстрая отметка: /scan под синей кнопкой бота.
 *
 * Проверяется не вёрстка, а то, из-за чего у двери офиса ничего не
 * выходит: сканирование до проверки подписи, две отметки с одного
 * скана, посланное клиентом направление, повисший обработчик закрытия
 * окна.
 *
 * Отдельно — граница доверия. Клиент отправляет строку кода и ключ
 * попытки, и больше ничего. Ни сотрудника, ни офиса, ни «я выхожу»:
 * таких параметров у запроса нет, и подделать их поэтому негде.
 */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App, { isQuickScanRoute, QUICK_SCAN_PATH } from '../src/App';
import { forgetToken } from '../src/auth';
import { CLOSE_AFTER_MS } from '../src/screens/QuickScan';
import { TZ, profile as profileStub, session, status as statusStub } from './fixtures';

afterEach(() => {
  cleanup();
  forgetToken();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  delete (window as unknown as Record<string, unknown>).Telegram;
  // Геолокацию ставит тест, которому она нужна. Оставить её включённой
  // после себя значит подсунуть следующему тесту место, которого он не
  // просил, — и он пройдёт по чужой причине.
  nowhere();
  window.history.replaceState({}, '', '/');
});

beforeEach(() => {
  forgetToken();
});

// --- маршрут ----------------------------------------------------------------

describe('маршрут быстрой отметки', () => {
  it('узнаётся по адресу, с хвостовой косой чертой и без', () => {
    expect(isQuickScanRoute('/scan')).toBe(true);
    expect(isQuickScanRoute('/scan/')).toBe(true);
    expect(isQuickScanRoute('/')).toBe(false);
    expect(isQuickScanRoute('/scanner')).toBe(false);
    expect(QUICK_SCAN_PATH).toBe('/scan');
  });

  it('на /scan открывается сканер, а не кабинет', async () => {
    const scanner = openTelegram();
    stubApi();
    atScanRoute();

    render(<App />);

    await screen.findByText('Открываем сканер…');
    expect(scanner.show).toHaveBeenCalled();
    // Ни нижней навигации, ни разделов: экран на пятнадцать секунд.
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(screen.queryByText('Отметка')).toBeNull();
  });

  it('в корне по-прежнему открывается полный кабинет', async () => {
    const scanner = openTelegram();
    stubApi();

    render(<App />);

    await screen.findByRole('navigation');
    // И сканер сам собой не открывается: в кабинете это делает человек.
    expect(scanner.show).not.toHaveBeenCalled();
  });
});

// --- подпись ----------------------------------------------------------------

describe('без подписи Telegram ничего не происходит', () => {
  it('в обычном браузере объясняет, чем открывать', async () => {
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl);
    atScanRoute();

    render(<App />);

    await screen.findByText('Откройте кнопкой в чате');
    expect(screen.getByText(/кнопкой «📷 Отметиться»/)).toBeTruthy();
    // Не тупик: кнопка нижней клавиатуры по документации Telegram
    // подписи не приносит, и на этот случай нужен путь вперёд.
    expect(screen.getByText('/scan')).toBeTruthy();
    // Запрос не уходит вовсе: отправлять нечего.
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('отвергнутая подпись не запускает сканер', async () => {
    const scanner = openTelegram();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              error: { code: 'forbidden', message: 'нет', details: {} },
            }),
            { status: 403, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    );
    atScanRoute();

    render(<App />);

    await screen.findByText('Не получилось');
    expect(scanner.show).not.toHaveBeenCalled();
    // Отказов у входа два, и подсказка нужна на обоих: по документации
    // Telegram кнопка нижней клавиатуры подписи не приносит вовсе.
    expect(screen.getByText('/scan')).toBeTruthy();
  });

  it('непривязанный Telegram не сканирует', async () => {
    const scanner = openTelegram();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              error: {
                code: 'forbidden',
                message: 'нет',
                details: { reason: 'not_linked' },
              },
            }),
            { status: 403, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    );
    atScanRoute();

    render(<App />);

    await screen.findByText('Telegram не привязан');
    expect(scanner.show).not.toHaveBeenCalled();
  });
});

// --- сканирование -----------------------------------------------------------

describe('сканирование', () => {
  it('после чтения кода уходит запрос отметки', async () => {
    const scanner = openTelegram();
    const { calls } = stubApi();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');

    await screen.findByText('Вход отмечен');
    expect(posted(calls, '/me/attendance/scan')).toHaveLength(1);
  });

  it('клиент не присылает ни сотрудника, ни офис, ни направление', async () => {
    // Единственная настоящая защита: таких параметров у запроса нет.
    // Появись они — «я выхожу» отправлял бы кто угодно и когда угодно.
    const scanner = openTelegram();
    const { calls } = stubApi();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');
    await screen.findByText('Вход отмечен');

    const body = JSON.parse(
      String(posted(calls, '/me/attendance/scan')[0].init?.body),
    );
    expect(Object.keys(body).sort()).toEqual(['client_event_id', 'token']);
    for (const forbidden of [
      'employee_id',
      'organization_id',
      'office_id',
      'region',
      'direction',
      'event_type',
      'occurred_at',
    ]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it('печатный код уходит вместе с координатами', async () => {
    // Наклейка у двери висит круглосуточно и сама по себе не значит
    // ничего: её можно сфотографировать и показать из дома. Сервер
    // принимает такой код ТОЛЬКО с местоположением — значит, спросить
    // его должно приложение, иначе отметка отказывается всегда.
    const scanner = openTelegram();
    const { calls } = stubApi();
    somewhere({ latitude: 41.3111234567, longitude: 69.2405678912, accuracy: 18.4 });
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit(STICKER);

    await screen.findByText('Вход отмечен');
    const body = JSON.parse(
      String(posted(calls, '/me/attendance/scan')[0].init?.body),
    );
    // Шесть знаков после запятой, не тринадцать: поле на сервере
    // объявлено с такой точностью и лишние знаки не отбрасывает, а
    // отвергает запрос целиком.
    expect(body.latitude).toBe(41.311123);
    expect(body.longitude).toBe(69.240568);
    expect(body.accuracy_m).toBe(18.4);
  });

  it('без координат печатный код не отправляется вовсе', async () => {
    // Запрос, заведомо уходящий в отказ, — это лишние секунды у двери
    // и невнятное «нужно разрешить геопозицию» вместо объяснения.
    const scanner = openTelegram();
    const { calls } = stubApi();
    nowhere();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit(STICKER);

    await screen.findByText(/Телефон не сообщил, где вы/);
    expect(posted(calls, '/me/attendance/scan')).toHaveLength(0);
  });

  it('коду с экрана координаты не нужны', async () => {
    // У меняющегося кода собственный срок жизни, и сервер принимает
    // его без места. Требовать координаты и здесь значило бы сломать
    // отметку там, где она работает.
    const scanner = openTelegram();
    const { calls } = stubApi();
    nowhere();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');

    await screen.findByText('Вход отмечен');
    expect(posted(calls, '/me/attendance/scan')).toHaveLength(1);
  });

  it('«слишком далеко» называет расстояние, а не спорит', async () => {
    const scanner = openTelegram();
    stubApi({
      scan: {
        ...refusal('OUTSIDE_GEOFENCE'),
        distance_m: 342,
        radius_m: 100,
      },
    });
    somewhere();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit(STICKER);

    await screen.findByText('Вы слишком далеко от офиса');
    expect(screen.getByText(/342 м.*100 м/)).toBeTruthy();
  });

  it('чужой код в кадре не закрывает сканер и не уходит на сервер', async () => {
    // Окно Telegram отдаёт подряд всё, что попало в кадр. Без фильтра
    // отметка ломалась бы о штрихкод на кофейном стакане.
    const scanner = openTelegram();
    const { calls } = stubApi();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());

    expect(scanner.emit('https://посторонний.qr')).toBe(false);
    expect(posted(calls, '/me/attendance/scan')).toHaveLength(0);
  });

  it('один скан — одна отметка, сколько бы кадров ни пришло', async () => {
    const scanner = openTelegram();
    const { calls } = stubApi();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());

    scanner.emit('HT1.код-с-экрана');
    scanner.emit('HT1.код-с-экрана');
    scanner.emit('HT1.код-с-экрана');

    await screen.findByText('Вход отмечен');
    expect(posted(calls, '/me/attendance/scan')).toHaveLength(1);
  });

  it('закрытие окна человеком — не ошибка и не пустой экран', async () => {
    const scanner = openTelegram();
    stubApi();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.close();

    await screen.findByText('Сканирование отменено.');
    expect(
      screen.getByRole('button', { name: 'Сканировать повторно' }),
    ).toBeTruthy();
  });

  it('обработчик закрытия снимается при уходе с экрана', async () => {
    // Иначе он копится с каждым сканированием и однажды разрешает
    // ожидание, которое давно чужое.
    const scanner = openTelegram();
    stubApi();
    atScanRoute();

    const { unmount } = render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    unmount();

    expect(scanner.off).toHaveBeenCalledWith(
      'scanQrPopupClosed',
      expect.any(Function),
    );
    // И само окно тоже закрывается: камеру держит оно, не мы.
    expect(scanner.closePopup).toHaveBeenCalled();
  });
});

// --- результат --------------------------------------------------------------

describe('результат', () => {
  it('приход: слово, время в поясе офиса и офис', async () => {
    const scanner = openTelegram();
    stubApi();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');

    await screen.findByText('Вход отмечен');
    expect(screen.getByText('09:03')).toBeTruthy();
    expect(screen.getByText(/Ташкентский офис/)).toBeTruthy();
  });

  it('уход: время и сколько человек пробыл в офисе', async () => {
    const scanner = openTelegram();
    stubApi({ scan: exitResult() });
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');

    await screen.findByText('Выход отмечен');
    expect(screen.getByText('18:07')).toBeTruthy();
    expect(screen.getByText(/8 ч 24 мин/)).toBeTruthy();
  });

  it('удачная отметка закрывает приложение сама', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const scanner = openTelegram();
    stubApi();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');
    await screen.findByText('Вход отмечен');

    expect(scanner.close_).not.toHaveBeenCalled();
    vi.advanceTimersByTime(CLOSE_AFTER_MS);
    expect(scanner.close_).toHaveBeenCalled();
  });

  it('отказ окно не закрывает — его надо прочитать', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const scanner = openTelegram();
    stubApi({ scan: refusal('QR_EXPIRED') });
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');
    await screen.findByText('Код устарел');

    vi.advanceTimersByTime(CLOSE_AFTER_MS * 3);
    expect(scanner.close_).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: 'Сканировать повторно' }),
    ).toBeTruthy();
  });

  it('после отказа можно отсканировать заново', async () => {
    const scanner = openTelegram();
    stubApi({ scan: refusal('QR_EXPIRED') });
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');
    await screen.findByText('Код устарел');

    fireEvent.click(screen.getByRole('button', { name: 'Сканировать повторно' }));

    await waitFor(() => expect(scanner.show).toHaveBeenCalledTimes(2));
  });

  it('сорванная сеть объясняется словами, а не кодом ошибки', async () => {
    const scanner = openTelegram();
    stubApi({ failScan: true });
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/связ|сет/i);
    // Ни стека, ни внутренних идентификаторов.
    expect(alert.textContent).not.toMatch(/Error|http|\bat\s/);
  });

  it('из результата можно уйти в полный кабинет', async () => {
    const scanner = openTelegram();
    stubApi();
    atScanRoute();

    render(<App />);
    await waitFor(() => expect(scanner.show).toHaveBeenCalled());
    scanner.emit('HT1.код-с-экрана');
    await screen.findByText('Вход отмечен');

    fireEvent.click(screen.getByRole('button', { name: 'Открыть кабинет' }));

    await screen.findByRole('navigation');
  });
});

// --- старый клиент ----------------------------------------------------------

describe('клиент без родного сканера', () => {
  it('объясняет и отправляет в кабинет, а не молчит', async () => {
    // Ни ручного ввода, ни выбора картинки из галереи здесь нет
    // намеренно: и то и другое означало бы отметку по коду, который
    // человеку прислали, а не который он видит перед собой.
    openTelegram({ version: '6.0' });
    stubApi();
    atScanRoute();

    render(<App />);

    await screen.findByText('Этот Telegram не умеет сканировать');
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByRole('button', { name: 'Открыть кабинет' })).toBeTruthy();
  });

  it('клиент без метода сканирования тоже не остаётся ни с чем', async () => {
    openTelegram({ withScanner: false });
    stubApi();
    atScanRoute();

    render(<App />);

    await screen.findByText('Этот Telegram не умеет сканировать');
  });
});

// --- вспомогательное --------------------------------------------------------

function atScanRoute(): void {
  window.history.replaceState({}, '', QUICK_SCAN_PATH);
}

/**
 * Telegram с родным сканером.
 *
 * `emit` — кадр из окна сканера; возвращает то же, что вернул callback
 * приложения: `true` означает «закрой окно». `close` — человек закрыл
 * окно сам, это приходит событием, а не вызовом callback.
 */
function openTelegram({
  version = '7.0',
  withScanner = true,
}: { version?: string; withScanner?: boolean } = {}) {
  const handlers = new Map<string, Set<() => void>>();
  const show = vi.fn();
  const closePopup = vi.fn();
  const close_ = vi.fn();
  const off = vi.fn((event: string, handler: () => void) => {
    handlers.get(event)?.delete(handler);
  });
  let received: ((text: string) => boolean | void) | null = null;

  show.mockImplementation(
    (_params: unknown, callback: (text: string) => boolean | void) => {
      received = callback;
    },
  );

  const app: Record<string, unknown> = {
    initData: 'подписанная-строка',
    ready: vi.fn(),
    expand: vi.fn(),
    version,
    isVersionAtLeast: (want: string) =>
      Number.parseFloat(version) >= Number.parseFloat(want),
    close: close_,
    onEvent: vi.fn((event: string, handler: () => void) => {
      if (!handlers.has(event)) handlers.set(event, new Set());
      handlers.get(event)!.add(handler);
    }),
    offEvent: off,
  };
  if (withScanner) {
    app.showScanQrPopup = show;
    app.closeScanQrPopup = closePopup;
  }
  (window as unknown as Record<string, unknown>).Telegram = { WebApp: app };

  return {
    show,
    off,
    closePopup,
    close_,
    emit: (text: string) => received?.(text),
    close: () => handlers.get('scanQrPopupClosed')?.forEach((fn) => fn()),
  };
}

function entryResult() {
  return {
    status: 'ENTERED',
    accepted: true,
    office_name: 'Ташкентский офис',
    point_name: 'Главный вход',
    occurred_at: '2026-09-05T04:03:00Z',
    session: {
      id: 's1',
      started_at: '2026-09-05T04:03:00Z',
      ended_at: null,
      duration_seconds: null,
      status: 'OPEN',
    },
  };
}

function exitResult() {
  return {
    status: 'EXITED',
    accepted: true,
    office_name: 'Ташкентский офис',
    point_name: 'Главный вход',
    occurred_at: '2026-09-05T13:07:00Z',
    session: {
      id: 's1',
      started_at: '2026-09-05T04:43:00Z',
      ended_at: '2026-09-05T13:07:00Z',
      duration_seconds: 8 * 3600 + 24 * 60,
      status: 'CLOSED',
    },
  };
}

/** Печатный код: ссылка на бота с нагрузкой `qr_…` под наклейкой. */
const STICKER = `https://t.me/humotech_bot?start=qr_${'a'.repeat(43)}`;

/** Телефон знает, где человек. По умолчанию — у ташкентского офиса. */
function somewhere(
  {
    latitude = 41.304151,
    longitude = 69.332442,
    accuracy = 12,
  }: { latitude?: number; longitude?: number; accuracy?: number } = {},
) {
  Object.defineProperty(window.navigator, 'geolocation', {
    configurable: true,
    value: {
      getCurrentPosition: (ok: (position: unknown) => void) =>
        ok({ coords: { latitude, longitude, accuracy } }),
    },
  });
}

/**
 * Телефон не отдаёт место: доступ закрыт или устройство не умеет.
 *
 * Свойство именно УДАЛЯЕТСЯ, а не подменяется на `undefined`: jsdom
 * отдаёт один `navigator` на всё окружение, и оставленная подмена
 * достаётся следующему тесту, который её не просил.
 */
function nowhere() {
  Reflect.deleteProperty(window.navigator as unknown as object, 'geolocation');
}

function refusal(status: string) {
  return {
    status,
    accepted: false,
    office_name: 'Ташкентский офис',
    point_name: 'Главный вход',
    occurred_at: null,
    session: null,
  };
}

/** Сервер: подпись, статус, отметка. Считает и показывает, что ушло. */
function stubApi({
  scan = entryResult(),
  failScan = false,
}: { scan?: unknown; failScan?: boolean } = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];

  const impl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const address = String(url);
    calls.push({ url: address, init });

    if (address.includes('/telegram/mini-app/auth')) {
      return json({ access_token: 'токен', employee: profileStub.employee });
    }
    if (address.includes('/me/attendance/scan')) {
      if (failScan) throw new TypeError('сеть недоступна');
      return json(scan);
    }
    if (address.includes('/me/status')) {
      return json(statusStub({ state: 'OUTSIDE', open_session: session() }));
    }
    if (address.includes('/me/profile')) return json(profileStub);
    if (address.includes('/me/history')) {
      return json({
        period: { first: '2026-09-04', last: '2026-09-04', timezone: TZ },
        days: [],
        total: 0,
        offset: 0,
        limit: 30,
        has_more: false,
      });
    }
    if (address.includes('/me/statistics')) return json({ summary: null, days: [] });
    if (address.includes('/me/notifications')) {
      return json({ unread: 0, items: [] });
    }
    if (address.includes('/me/absences')) return json({ requests: [], total: 0 });
    return json({});
  });

  vi.stubGlobal('fetch', impl as unknown as typeof fetch);
  return { calls };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function posted(
  calls: Array<{ url: string; init?: RequestInit }>,
  path: string,
): Array<{ url: string; init?: RequestInit }> {
  return calls.filter(
    (call) => call.url.includes(path) && call.init?.method === 'POST',
  );
}
