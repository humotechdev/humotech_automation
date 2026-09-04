/**
 * Логика экрана: обновление кода, обрыв связи, отзыв доступа.
 *
 * DOM здесь не поднимается — `display.ts` это чистая логика над `fetch`
 * и часами. Проверяется то, что ломается в офисе: код, протухший между
 * запросами; сеть, пропавшая на минуту; доступ, снятый отделом кадров.
 *
 * Ни одного настоящего запроса: `fetch` подменён.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  fetchCode,
  forgetCredential,
  isFresh,
  loadCredential,
  pair,
  refreshDelay,
  retryDelay,
  saveCredential,
  type QrCode,
} from '../src/display';

const API = 'https://api.humotech.test/api/v1';
const NOW = Date.parse('2026-09-04T10:00:00Z');

function respond(status: number, body: unknown): typeof fetch {
  return vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
  ) as unknown as typeof fetch;
}

function code(overrides: Partial<QrCode> = {}): QrCode {
  return {
    token: 'HT1abcdef',
    issuedAt: NOW,
    expiresAt: NOW + 30_000,
    officeName: 'Офис MAIN',
    pointName: 'Главный вход',
    directionMode: 'BOTH',
    ...overrides,
  };
}

afterEach(() => {
  forgetCredential();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// --- сопряжение ------------------------------------------------------------

describe('сопряжение', () => {
  it('обменивает одноразовый код на постоянный credential', async () => {
    const fetchImpl = respond(201, {
      credential: 'долгий-секрет-экрана',
      expires_at: '2026-10-04T10:00:00Z',
      office_name: 'Офис MAIN',
      point_name: 'Главный вход',
      direction_mode: 'BOTH',
    });

    const result = await pair('одноразовый', { apiUrl: API, fetchImpl });

    expect(result).toEqual({ ok: true, credential: 'долгий-секрет-экрана' });
  });

  it('наружу уходит только код сопряжения', async () => {
    // Ни офиса, ни точки: экран их не выбирает и выбрать не может.
    const fetchImpl = respond(201, { credential: 'x' });
    await pair('одноразовый', { apiUrl: API, fetchImpl });

    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe(`${API}/qr-display/pair`);
    expect(Object.keys(JSON.parse(init.body))).toEqual(['pairing_code']);
  });

  it('отказ объясняется человеку, а не кодом ответа', async () => {
    const result = await pair('чужой', {
      apiUrl: API,
      fetchImpl: respond(403, {}),
    });

    expect(result).toEqual({
      ok: false,
      message: 'Код не подошёл. Проверьте и введите заново',
    });
  });

  it('обрыв связи не выдаётся за неверный код', async () => {
    const failing = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;

    const result = await pair('код', { apiUrl: API, fetchImpl: failing });

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain('связи');
  });
});

// --- получение кода --------------------------------------------------------

describe('получение кода', () => {
  it('credential уходит в заголовке, а не в адресе', async () => {
    // Адрес попадает в журналы сервера и в историю браузера целиком.
    const fetchImpl = respond(200, {
      token: 'HT1abc',
      issued_at: '2026-09-04T10:00:00Z',
      expires_at: '2026-09-04T10:00:30Z',
      office_name: 'Офис MAIN',
      point_name: 'Главный вход',
      direction_mode: 'BOTH',
    });

    await fetchCode('секрет-экрана', { apiUrl: API, fetchImpl });

    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe(`${API}/qr-display/code`);
    expect(url).not.toContain('секрет-экрана');
    expect(init.headers.Authorization).toBe('Bearer секрет-экрана');
  });

  it('офис и точка приходят с сервера, а не задаются экраном', async () => {
    const result = await fetchCode('секрет', {
      apiUrl: API,
      fetchImpl: respond(200, {
        token: 'HT1abc',
        issued_at: '2026-09-04T10:00:00Z',
        expires_at: '2026-09-04T10:00:30Z',
        office_name: 'Офис MAIN',
        point_name: 'Главный вход',
        direction_mode: 'ENTRY',
      }),
    });

    expect(result.kind).toBe('code');
    if (result.kind === 'code') {
      expect(result.code.officeName).toBe('Офис MAIN');
      expect(result.code.pointName).toBe('Главный вход');
    }
  });

  it('снятый доступ отличается от обрыва связи', async () => {
    // Разные действия: в одном случае ждать, в другом — сопрягать заново.
    const revoked = await fetchCode('старый', {
      apiUrl: API,
      fetchImpl: respond(403, {}),
    });
    expect(revoked.kind).toBe('revoked');

    const failing = vi.fn(async () => {
      throw new TypeError('offline');
    }) as unknown as typeof fetch;
    const offline = await fetchCode('норм', { apiUrl: API, fetchImpl: failing });
    expect(offline.kind).toBe('offline');
  });

  it('ошибка сервера — это обрыв, а не отзыв', async () => {
    // Пятисотка проходит: гасить рабочий экран из-за неё нельзя.
    const result = await fetchCode('норм', {
      apiUrl: API,
      fetchImpl: respond(500, {}),
    });
    expect(result.kind).toBe('offline');
  });
});

// --- когда обновляться -----------------------------------------------------

describe('расписание обновления', () => {
  it('следующий код запрашивается ДО истечения текущего', () => {
    // Иначе между истечением старого и появлением нового будет окно,
    // в котором на экране висит заведомо нерабочий код.
    const item = code();
    const delay = refreshDelay(item, NOW);

    expect(delay).toBeLessThan(item.expiresAt - NOW);
    expect(NOW + delay).toBeLessThan(item.expiresAt);
  });

  it('запас не съедает короткий срок целиком', () => {
    const short = code({ expiresAt: NOW + 6_000 });
    expect(refreshDelay(short, NOW)).toBeGreaterThan(0);
  });

  it('просроченный код обновляется немедленно, а не в прошлом', () => {
    const stale = code({ expiresAt: NOW - 10_000 });
    expect(refreshDelay(stale, NOW)).toBeGreaterThan(0);
  });

  it('пауза после обрыва растёт, но не бесконечно', () => {
    // Экран с упавшей сетью иначе бьётся в сервер сутки напролёт.
    expect(retryDelay(1)).toBeLessThan(retryDelay(3));
    expect(retryDelay(50)).toBeLessThanOrEqual(60_000);
  });

  it('действующий код отличается от протухшего', () => {
    expect(isFresh(code(), NOW + 10_000)).toBe(true);
    expect(isFresh(code(), NOW + 40_000)).toBe(false);
  });
});

// --- хранение credential ---------------------------------------------------

describe('хранение credential', () => {
  it('переживает перезагрузку экрана', () => {
    // Планшет на стене должен ожить сам после отключения питания:
    // приходить сопрягать его каждое утро некому.
    const store: Record<string, string> = {};
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => {
        store[key] = value;
      },
      removeItem: (key: string) => {
        delete store[key];
      },
    });

    saveCredential('секрет-экрана');
    expect(loadCredential()).toBe('секрет-экрана');
  });

  it('запрещённое хранилище не роняет экран', () => {
    vi.stubGlobal('localStorage', {
      getItem() {
        throw new Error('доступ запрещён');
      },
      setItem() {
        throw new Error('доступ запрещён');
      },
      removeItem() {
        throw new Error('доступ запрещён');
      },
    });

    expect(() => saveCredential('x')).not.toThrow();
    expect(loadCredential()).toBeNull();
    expect(() => forgetCredential()).not.toThrow();
  });

  it('отзыв стирает credential', () => {
    const store: Record<string, string> = {};
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => {
        store[key] = value;
      },
      removeItem: (key: string) => {
        delete store[key];
      },
    });

    saveCredential('секрет');
    forgetCredential();
    expect(loadCredential()).toBeNull();
  });
});
