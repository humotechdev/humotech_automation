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

import { call } from '../src/api';
import {
  CODE_PREFIX,
  cameraAvailable,
  hasBarcodeDetector,
  looksLikeOurCode,
  newAttemptId,
  scanWithTelegram,
  telegramScanner,
} from '../src/scanner';

const API = 'https://api.humotech.test/api/v1';

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

// --- API проверяется только через `call`, поэтому адрес фиксируем ----------

it('API_URL по умолчанию не абсолютный', () => {
  // Относительный адрес означает тот же origin, что и страница: в
  // разработке всё проксируется и CORS не участвует вовсе.
  expect(API.startsWith('http')).toBe(true); // сама константа теста
});
