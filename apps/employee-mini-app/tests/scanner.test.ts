/**
 * Сканирование и отправка отметки.
 *
 * Проверяется то, из-за чего отметка ломается на живом телефоне: чужой QR
 * в кадре, отсутствующий сканер, двойная отправка и попытка отправить
 * что-нибудь помимо кода.
 *
 * Ни одного настоящего запроса и ни одного обращения к камере.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { call, placeOf } from '../src/api';
import {
  CODE_PREFIX,
  SCAN_RESULTS,
  SCAN_UNKNOWN,
  cameraAvailable,
  hasBarcodeDetector,
  isSticker,
  looksLikeOurCode,
  newAttemptId,
  scanView,
  scanWithTelegram,
  telegramScanner,
} from '../src/scanner';
import { requestPosition } from '../src/telegram';

function windowWith(webApp: object | null): Window {
  return (webApp ? { Telegram: { WebApp: webApp } } : {}) as unknown as Window;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// --- распознавание своего кода ---------------------------------------------

describe('свой код', () => {
  it('узнаётся по метке формата', () => {
    expect(looksLikeOurCode(`${CODE_PREFIX}abcdef`)).toBe(true);
    expect(looksLikeOurCode(`  ${CODE_PREFIX}abcdef `)).toBe(true);
  });

  it('печатный код офиса узнаётся по ссылке на бота', () => {
    const secret = 'A'.repeat(43);
    expect(looksLikeOurCode(`https://t.me/humotech_bot?start=qr_${secret}`)).toBe(true);
    expect(looksLikeOurCode(`qr_${secret}`)).toBe(true);
    // Похожая ссылка на чужой сайт и огрызок нагрузки — не наш код.
    expect(looksLikeOurCode(`https://evil.example/?start=qr_${secret}`)).toBe(false);
    expect(looksLikeOurCode('qr_short')).toBe(false);
  });

  it('чужой QR отсеивается без запроса к серверу', () => {
    // Ссылка, код Wi-Fi, штрихкод на кофейном стакане — всё это попадает
    // в кадр, и ходить за отказом на сервер каждый раз незачем.
    for (const foreign of [
      'https://example.com',
      'WIFI:S:office;',
      '4600051000057',
      '',
    ]) {
      expect(looksLikeOurCode(foreign)).toBe(false);
    }
  });

  it('печатный код отличается от кода с экрана', () => {
    // Разница решает, ждать ли геопозицию: без неё сервер печатный код
    // не принимает, а код с экрана принимает. Спутать одно с другим
    // значит либо отказывать у двери, либо ждать место впустую.
    const secret = 'A'.repeat(43);
    expect(isSticker(`https://t.me/humotech_bot?start=qr_${secret}`)).toBe(true);
    expect(isSticker(`  qr_${secret}  `)).toBe(true);
    expect(isSticker(`${CODE_PREFIX}abcdef`)).toBe(false);
    expect(isSticker('')).toBe(false);
  });
});

// --- итог отметки словами --------------------------------------------------

describe('итог отметки', () => {
  it('у каждого ответа сервера есть свой текст', () => {
    // «Не получилось» на любой отказ — это тупик: «слишком далеко» и
    // «точка выключена» требуют разных действий, а общий текст
    // предлагает одно и то же, что не поможет ни в одном из случаев.
    for (const status of [
      'ENTERED', 'EXITED', 'ALREADY_INSIDE', 'NOT_INSIDE', 'QR_EXPIRED',
      'QR_INVALID', 'QR_ALREADY_USED', 'QR_POINT_INACTIVE',
      'OFFICE_NOT_ALLOWED', 'OUTSIDE_GEOFENCE', 'LOCATION_TOO_VAGUE',
      'NETWORK_REQUIRED', 'GEOLOCATION_REQUIRED', 'QR_REVOKED',
      'GEOFENCE_NOT_CONFIGURED', 'TOO_SOON',
    ]) {
      expect(SCAN_RESULTS[status], status).toBeTruthy();
    }
  });

  it('«слишком далеко» называет расстояние и радиус', () => {
    const view = scanView({
      status: 'OUTSIDE_GEOFENCE', distance_m: 341.7, radius_m: 100,
    });
    expect(view.title).toBe('Вы слишком далеко от офиса');
    expect(view.hint).toContain('342 м');
    expect(view.hint).toContain('100 м');
  });

  it('без расстояния остаётся общий текст, а не «до офиса null м»', () => {
    const view = scanView({ status: 'OUTSIDE_GEOFENCE', distance_m: null,
                            radius_m: null });
    expect(view.hint).toBe(SCAN_RESULTS.OUTSIDE_GEOFENCE!.hint);
  });

  it('незнакомый ответ не роняет экран', () => {
    expect(scanView({ status: 'ЧТО-ТО_НОВОЕ' }).title).toBe(SCAN_UNKNOWN.title);
  });
});

// --- сканер Telegram -------------------------------------------------------

describe('сканер Telegram', () => {
  it('используется, когда Telegram его предоставляет', () => {
    expect(telegramScanner(windowWith({ showScanQrPopup: () => {} }))).not.toBeNull();
    expect(telegramScanner(windowWith({}))).toBeNull();
    expect(telegramScanner(windowWith(null))).toBeNull();
  });

  it('окно не закрывается на чужом коде', async () => {
    // Иначе одна случайная наклейка в кадре прерывала бы отметку.
    const seen: Array<boolean | void> = [];
    const source = windowWith({
      showScanQrPopup: (
        _params: unknown,
        callback: (text: string) => boolean | void,
      ) => {
        seen.push(callback('https://example.com'));
        seen.push(callback('WIFI:S:office;'));
        seen.push(callback(`${CODE_PREFIX}настоящий`));
      },
    });

    const outcome = await scanWithTelegram(source);

    expect(seen.slice(0, 2)).toEqual([false, false]);
    expect(seen[2]).toBe(true);
    expect(outcome).toEqual({ kind: 'code', value: `${CODE_PREFIX}настоящий` });
  });

  it('без сканера сообщает о недоступности, а не падает', async () => {
    const outcome = await scanWithTelegram(windowWith({}));
    expect(outcome).toEqual({ kind: 'unavailable' });
  });

  it('камера считается доступной, если есть хоть один путь', () => {
    expect(cameraAvailable(windowWith({ showScanQrPopup: () => {} }))).toBe(true);
    expect(cameraAvailable(windowWith({}))).toBe(false);

    const withDetector = windowWith({});
    (withDetector as unknown as Record<string, unknown>).BarcodeDetector = class {};
    expect(hasBarcodeDetector(withDetector)).toBe(true);
    expect(cameraAvailable(withDetector)).toBe(true);
  });
});

// --- ключ повтора ----------------------------------------------------------

describe('ключ попытки', () => {
  it('у двух попыток он разный', () => {
    expect(newAttemptId()).not.toBe(newAttemptId());
  });

  it('работает и без crypto.randomUUID', () => {
    // В старых вебвью его нет, и отметка не должна из-за этого падать.
    vi.stubGlobal('crypto', {});
    expect(newAttemptId()).toBeTruthy();
  });
});

// --- откуда берётся место --------------------------------------------------

describe('определение места', () => {
  /** Телефон, отдающий точки одну за другой. */
  function phone(
    fixes: Array<{ latitude: number; longitude: number; accuracy: number }>,
    webApp: object | null = null,
  ) {
    const clearWatch = vi.fn();
    const source = {
      ...(webApp ? { Telegram: { WebApp: webApp } } : {}),
      navigator: {
        geolocation: {
          getCurrentPosition: vi.fn(),
          clearWatch,
          watchPosition: vi.fn((ok: (position: unknown) => void) => {
            for (const fix of fixes) ok({ coords: fix });
            return 7;
          }),
        },
      },
    } as unknown as Window;
    return { source, clearWatch };
  }

  it('берётся самая точная точка, а не первая', async () => {
    // Первой почти всегда приходит точка по вышкам — она мгновенная и
    // врёт на сотню метров. Спутниковая приходит следом. Взять первую
    // значит отказать человеку, стоящему у самой двери.
    const { source } = phone([
      { latitude: 41.305215, longitude: 69.335188, accuracy: 100 },
      { latitude: 41.304151, longitude: 69.332442, accuracy: 12 },
    ]);

    const place = await requestPosition({ source });

    expect(place?.accuracy).toBe(12);
    expect(place?.latitude).toBe(41.304151);
  });

  it('хорошая точка прекращает ожидание сразу', async () => {
    const { source, clearWatch } = phone([
      { latitude: 41.304151, longitude: 69.332442, accuracy: 8 },
    ]);

    await requestPosition({ source });

    // Наблюдение снимается: держать включённым GPS после ответа незачем.
    expect(clearWatch).toHaveBeenCalledWith(7);
  });

  it('грубая точка Telegram уточняется браузером', async () => {
    // `LocationManager` отдаёт ровно одну точку и второй раз даст ту же.
    // Если она грубая, единственный способ узнать место точнее —
    // дождаться спутников браузерным наблюдением.
    const { source } = phone(
      [{ latitude: 41.304151, longitude: 69.332442, accuracy: 9 }],
      {
        version: '8.0',
        isVersionAtLeast: () => true,
        LocationManager: {
          isInited: true,
          getLocation: (done: (location: unknown) => void) =>
            done({ latitude: 41.305215, longitude: 69.335188,
                   horizontal_accuracy: 100 }),
        },
      },
    );

    const place = await requestPosition({ source });

    expect(place?.accuracy).toBe(9);
    expect(place?.latitude).toBe(41.304151);
  });

  it('точную точку Telegram браузером не переспрашивают', async () => {
    const { source } = phone(
      [{ latitude: 41.3, longitude: 69.3, accuracy: 5 }],
      {
        version: '8.0',
        isVersionAtLeast: () => true,
        LocationManager: {
          isInited: true,
          getLocation: (done: (location: unknown) => void) =>
            done({ latitude: 41.304151, longitude: 69.332442,
                   horizontal_accuracy: 11 }),
        },
      },
    );

    const place = await requestPosition({ source });

    expect(place?.accuracy).toBe(11);
    // Лишний запрос разрешения там, где всё и так хорошо, — плата ни за что.
    const watching = (source.navigator.geolocation as unknown as
      { watchPosition: ReturnType<typeof vi.fn> }).watchPosition;
    expect(watching).not.toHaveBeenCalled();
  });

  it('без геолокации возвращается пусто, а не выдуманная точка', async () => {
    const source = { navigator: {} } as unknown as Window;
    expect(await requestPosition({ source })).toBeNull();
  });
});

// --- координаты ------------------------------------------------------------

describe('координаты для сервера', () => {
  it('округляются до шести знаков', () => {
    // Поле на сервере объявлено с шестью знаками после запятой и лишние
    // не отбрасывает, а отвергает запрос целиком. Телефон же выдаёт
    // тринадцать знаков всегда — без округления отметка ломалась бы на
    // каждом устройстве и выглядела бы так же, как отказ по месту.
    expect(placeOf({ latitude: 41.3111234567, longitude: 69.2405678912,
                     accuracy: 18.446 })).toEqual({
      latitude: 41.311123,
      longitude: 69.240568,
      accuracy_m: 18.45,
    });
  });

  it('нулевая погрешность не выдаётся за точность', () => {
    // Ноль на сервере — признак подделки, а не идеального GPS.
    expect(placeOf({ latitude: 41.3, longitude: 69.3, accuracy: 0 }).accuracy_m)
      .toBe(30);
  });

  it('без места полей просто нет', () => {
    expect(placeOf(null)).toEqual({});
    expect(placeOf(undefined)).toEqual({});
    expect(placeOf({ latitude: Number.NaN, longitude: 69.3, accuracy: 5 }))
      .toEqual({});
  });
});

// --- что уходит на сервер --------------------------------------------------

describe('отправка отметки', () => {
  function respond(status: number, body: unknown) {
    return vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
    ) as unknown as typeof fetch;
  }

  it('наружу уходит только код и ключ попытки', async () => {
    // Ни офиса, ни направления, ни времени: всё это решает сервер,
    // и подделать можно только то, что где-то принимается.
    const fetchImpl = respond(200, { status: 'ENTERED', accepted: true });

    await call('/me/attendance/scan', {
      method: 'POST',
      body: { token: `${CODE_PREFIX}код`, client_event_id: 'a-1' },
      fetchImpl,
      token: 'внутренний-токен',
    });

    const [, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(Object.keys(JSON.parse(init.body)).sort()).toEqual([
      'client_event_id',
      'token',
    ]);
  });

  it('токен уходит заголовком, а не в адресе', async () => {
    // Адрес попадает в журналы сервера, в историю и в Referer целиком.
    const fetchImpl = respond(200, {});
    await call('/me/status', { fetchImpl, token: 'секрет-сессии' });

    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).not.toContain('секрет-сессии');
    expect(init.headers.Authorization).toBe('Bearer секрет-сессии');
  });

  it('без токена запрос не отправляется вовсе', async () => {
    const fetchImpl = respond(200, {});
    const result = await call('/me/status', { fetchImpl, token: null });

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(result).toMatchObject({ ok: false, kind: 'auth' });
  });

  it('истёкшая сессия отличается от отказа по существу', async () => {
    // Разные действия: в одном случае открыть приложение заново,
    // в другом — прочитать, почему отметка не прошла.
    const expired = await call('/me/status', {
      fetchImpl: respond(401, {}),
      token: 'x',
    });
    expect(expired).toMatchObject({ ok: false, kind: 'auth' });

    const refused = await call('/me/attendance/scan', {
      method: 'POST',
      fetchImpl: respond(403, {
        error: { code: 'forbidden', message: 'нельзя',
                 details: { reason: 'office_not_allowed' } },
      }),
      token: 'x',
    });
    expect(refused).toMatchObject({
      ok: false,
      kind: 'refused',
      reason: 'office_not_allowed',
    });
  });

  it('обрыв сети не выглядит как отказ в доступе', async () => {
    const failing = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;

    const result = await call('/me/status', { fetchImpl: failing, token: 'x' });

    expect(result).toMatchObject({ ok: false, kind: 'network' });
    if (!result.ok) expect(result.message).toContain('связ');
  });

  it('ответ не-JSON не роняет приложение', async () => {
    const html = vi.fn(
      async () => new Response('<html>прокси</html>', { status: 502 }),
    ) as unknown as typeof fetch;

    const result = await call('/me/status', { fetchImpl: html, token: 'x' });
    expect(result).toMatchObject({ ok: false, kind: 'server' });
  });
});

// --- код никуда не сохраняется ---------------------------------------------

describe('хранение', () => {
  it('QR не попадает в localStorage', async () => {
    // Код действует полминуты; лишняя его копия на устройстве, которое
    // могут взять другие, не нужна ни для чего.
    const written: string[] = [];
    vi.stubGlobal('localStorage', {
      setItem: (key: string, value: string) => written.push(`${key}=${value}`),
      getItem: () => null,
      removeItem: () => undefined,
    });

    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify({ status: 'ENTERED', accepted: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    ) as unknown as typeof fetch;

    await call('/me/attendance/scan', {
      method: 'POST',
      body: { token: `${CODE_PREFIX}секретный-код`, client_event_id: 'a-1' },
      fetchImpl,
      token: 'сессия',
    });

    expect(written).toEqual([]);
  });
});

// --- API_URL ---------------------------------------------------------------

describe('адрес API', () => {
  it('путь строится от VITE_API_URL', async () => {
    const fetchImpl = vi.fn(
      async () => new Response('{}', { status: 200,
        headers: { 'Content-Type': 'application/json' } }),
    ) as unknown as typeof fetch;

    await call('/me/profile', { fetchImpl, token: 'x' });

    const [url] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url).endsWith('/me/profile')).toBe(true);
  });

  it('пустые параметры не попадают в строку запроса', async () => {
    const fetchImpl = vi.fn(
      async () => new Response('{}', { status: 200,
        headers: { 'Content-Type': 'application/json' } }),
    ) as unknown as typeof fetch;

    await call('/me/statistics', {
      query: { period: 'week', date_from: undefined },
      fetchImpl,
      token: 'x',
    });

    const [url] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain('period=week');
    expect(String(url)).not.toContain('date_from');
  });
});

it('по умолчанию адрес относительный, а не чужой хост', async () => {
  // Тот же origin, что и страница: в разработке всё проксируется, CORS не
  // участвует, а запрос с внутренним токеном не может уйти на чужой хост
  // из-за незаданной переменной сборки.
  const fetchImpl = vi.fn(
    async () => new Response('{}', { status: 200,
      headers: { 'Content-Type': 'application/json' } }),
  ) as unknown as typeof fetch;

  await call('/me/profile', { fetchImpl, token: 'x' });

  const [url] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(String(url).startsWith('/api/v1/')).toBe(true);
});
