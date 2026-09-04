/**
 * Авторизация Mini App: все исходы, включая те, что легко перепутать.
 *
 * DOM здесь не поднимается: `auth.ts` — чистая логика над `initData` и
 * `fetch`, и проверять её надо именно так. Экраны — тонкая обёртка над
 * этими же пятью состояниями.
 *
 * Ни одного настоящего запроса: `fetch` подменён, `window.Telegram` собран
 * вручную. В Telegram при прогоне тестов не уходит ничего.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  authenticate,
  forgetToken,
  readInitData,
  recallToken,
  rememberToken,
  type AuthResult,
} from '../src/auth';

const API = 'https://api.humotech.test/api/v1';
const INIT_DATA = 'auth_date=1&user=%7B%22id%22%3A1%7D&hash=deadbeef';

function respond(status: number, body: unknown): typeof fetch {
  return vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
}

function refusal(reason: string) {
  return { error: { code: 'forbidden', message: 'нельзя', details: { reason } } };
}

function run(
  initData: string | null,
  fetchImpl: typeof fetch,
): Promise<AuthResult> {
  return authenticate(initData, { apiUrl: API, fetchImpl });
}

afterEach(() => {
  forgetToken();
  vi.restoreAllMocks();
});

// --- чтение строки запуска -------------------------------------------------

describe('readInitData', () => {
  it('возвращает строку, когда приложение открыто в Telegram', () => {
    const fake = { Telegram: { WebApp: { initData: INIT_DATA } } } as Window;
    expect(readInitData(fake)).toBe(INIT_DATA);
  });

  it('возвращает null вне Telegram: объекта Telegram просто нет', () => {
    expect(readInitData({} as Window)).toBeNull();
  });

  it('пустая строка — это тоже «вне Telegram», а не пустой запрос', () => {
    const fake = { Telegram: { WebApp: { initData: '' } } } as Window;
    expect(readInitData(fake)).toBeNull();
  });
});

// --- исходы обмена ---------------------------------------------------------

describe('authenticate', () => {
  it('успех отдаёт токен и сотрудника', async () => {
    const employee = {
      id: 'e-1',
      full_name: 'Иванов Иван',
      employee_number: 'EMP-0001',
      employment_status: 'ACTIVE',
      preferred_language: 'ru',
    };
    const result = await run(
      INIT_DATA,
      respond(200, { access_token: 'внутренний-токен', expires_in: 43200, employee }),
    );

    expect(result).toEqual({
      state: 'authenticated',
      token: 'внутренний-токен',
      employee,
    });
  });

  it('привязка без подтверждения HR — отдельное состояние, не ошибка', async () => {
    const result = await run(INIT_DATA, respond(403, refusal('pending_confirmation')));
    expect(result.state).toBe('pending');
  });

  it('непривязанный Telegram — тоже отдельное состояние', async () => {
    const result = await run(INIT_DATA, respond(403, refusal('not_linked')));
    expect(result.state).toBe('unlinked');
  });

  it('отвергнутая подпись не выдаётся за отсутствие привязки', async () => {
    // Человеку надо сделать разное: в одном случае открыть приложение
    // заново, в другом — идти к HR.
    const result = await run(INIT_DATA, respond(403, refusal('init_data_rejected')));
    expect(result.state).toBe('error');
  });

  it('403 без разбираемого тела не роняет приложение', async () => {
    const notJson = vi.fn(
      async () => new Response('<html>прокси</html>', { status: 403 }),
    ) as unknown as typeof fetch;
    const result = await run(INIT_DATA, notJson);
    expect(result.state).toBe('error');
  });

  it('вне Telegram запрос не отправляется вовсе', async () => {
    const fetchImpl = respond(200, {});
    const result = await run(null, fetchImpl);

    expect(result.state).toBe('outside-telegram');
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('обрыв сети не выглядит как отказ в доступе', async () => {
    const failing = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;

    const result = await run(INIT_DATA, failing);
    expect(result.state).toBe('error');
    if (result.state === 'error') {
      expect(result.message).toContain('связь');
    }
  });

  it('ошибка сервера отличается от отказа в доступе', async () => {
    const result = await run(INIT_DATA, respond(500, {}));
    expect(result.state).toBe('error');
  });

  it('наружу уходит только подписанная строка', async () => {
    const fetchImpl = respond(200, {
      access_token: 't',
      employee: { id: 'e' },
    });
    await run(INIT_DATA, fetchImpl);

    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(url).toBe(`${API}/telegram/mini-app/auth`);
    expect(init.method).toBe('POST');

    const body = JSON.parse(init.body);
    // Ни идентификатора сотрудника, ни организации, ни Telegram ID:
    // подделать можно только то, что где-то принимается.
    expect(Object.keys(body)).toEqual(['init_data']);
    expect(body.init_data).toBe(INIT_DATA);
  });

  it('токен не попадает в заголовки запроса на вход', async () => {
    rememberToken('старый-токен');
    const fetchImpl = respond(200, { access_token: 'новый', employee: {} });
    await run(INIT_DATA, fetchImpl);

    const [, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
  });
});

// --- хранение токена -------------------------------------------------------

describe('хранение токена', () => {
  it('токен переживает перезагрузку страницы внутри вебвью', () => {
    // `initData` при перезагрузке не обновляется и через пять минут
    // протухает — без сохранения человек оказался бы заперт снаружи.
    rememberToken('токен');
    expect(recallToken()).toBe('токен');
  });

  it('выход стирает токен', () => {
    rememberToken('токен');
    forgetToken();
    expect(recallToken()).toBeNull();
  });

  it('запрещённое хранилище не роняет приложение', () => {
    // Приватный режим и запрет данных сайта бросают исключение на доступе.
    const broken = {
      getItem() {
        throw new Error('доступ запрещён');
      },
      setItem() {
        throw new Error('доступ запрещён');
      },
      removeItem() {
        throw new Error('доступ запрещён');
      },
    };
    vi.stubGlobal('sessionStorage', broken);

    expect(() => rememberToken('токен')).not.toThrow();
    // Из памяти токен всё равно доступен: теряется только устойчивость
    // к перезагрузке, а не работоспособность.
    expect(recallToken()).toBe('токен');
    expect(() => forgetToken()).not.toThrow();
  });

  it('токен не хранится в localStorage', () => {
    // localStorage переживает закрытие Mini App и остаётся в вебвью:
    // на общем устройстве следующий человек открыл бы чужую сессию.
    const written: string[] = [];
    vi.stubGlobal('localStorage', {
      setItem: (key: string) => written.push(key),
      getItem: () => null,
      removeItem: () => undefined,
    });

    rememberToken('токен');
    expect(written).toEqual([]);
  });
});
