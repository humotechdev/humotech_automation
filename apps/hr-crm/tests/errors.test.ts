/**
 * Разбор ответов сервера.
 *
 * Проверяется на уровне клиента, а не формы, намеренно: часть состояний
 * до формы входа не доходит. Вход открыт всем (`AllowAny`) и в DRF
 * освобождён от проверки CSRF, поэтому «ошибка CSRF» на самом входе
 * недостижима — она встречает уже вошедшего человека на изменяющих
 * запросах, начиная с выхода. Формулировка для неё всё равно нужна, но
 * тест, изображающий её на форме, описывал бы несуществующее поведение.
 */

import { describe, expect, test, vi } from 'vitest';

import { request } from '../src/api/client';
import { ApiFailure, messageFor } from '../src/api/errors';

const answer = (status: number, body: unknown) =>
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );

async function kindOf(run: () => Promise<unknown>): Promise<string> {
  try {
    await run();
  } catch (error) {
    return error instanceof ApiFailure ? error.kind : 'не ApiFailure';
  }
  return 'без ошибки';
}

describe('коды ответов превращаются в понятные состояния', () => {
  test('401 — неверные данные', async () => {
    answer(401, { error: { code: 'invalid_credentials', message: '…', details: null } });
    expect(await kindOf(() => request('/auth/login', { method: 'POST', body: {} }))).toBe(
      'credentials',
    );
  });

  test('400 — неполный запрос, с разбором по полям', async () => {
    answer(400, {
      error: {
        code: 'invalid',
        message: 'Запрос не выполнен',
        details: { password: ['Обязательное поле.'] },
      },
    });
    try {
      await request('/auth/login', { method: 'POST', body: {} });
      expect.unreachable('должен был отказать');
    } catch (error) {
      expect((error as ApiFailure).kind).toBe('validation');
      expect((error as ApiFailure).fields['password']).toEqual(['Обязательное поле.']);
    }
  });

  test('403 без сессии — сессия', async () => {
    answer(403, { error: { code: 'not_authenticated', message: '…', details: null } });
    expect(await kindOf(() => request('/auth/me'))).toBe('session');
  });

  test('403 с провалом CSRF отличается от истёкшей сессии', async () => {
    // Лечится по-разному: одно — входом, другое — обновлением страницы.
    answer(403, {
      error: { code: 'permission_denied', message: 'CSRF Failed: CSRF token missing.', details: null },
    });
    expect(await kindOf(() => request('/auth/logout', { method: 'POST' }))).toBe('csrf');
  });

  test('500 — временная ошибка сервера', async () => {
    answer(500, { error: { code: 'error', message: '…', details: null } });
    expect(await kindOf(() => request('/auth/me'))).toBe('server');
  });

  test('оборванная сеть отличается от ответа сервера', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );
    expect(await kindOf(() => request('/auth/me'))).toBe('offline');
  });

  test('HTML вместо JSON не ломает разбор', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response('<!doctype html><h1>Forbidden (403)</h1>', {
          status: 403,
          headers: { 'Content-Type': 'text/html' },
        }),
      ),
    );
    expect(await kindOf(() => request('/auth/logout', { method: 'POST' }))).toBe('session');
  });
});

describe('тексты для человека', () => {
  test('на каждое состояние — русская фраза без внутренностей', () => {
    for (const kind of ['credentials', 'validation', 'session', 'csrf', 'offline', 'server'] as const) {
      const text = messageFor(new ApiFailure(kind));
      expect(text.length).toBeGreaterThan(10);
      expect(text).not.toMatch(/CSRF Failed|Traceback|doctype|Error \(/i);
    }
  });

  test('неизвестная ошибка не роняет интерфейс', () => {
    expect(messageFor(new Error('что-то своё'))).toBe(
      'Сервер временно недоступен. Попробуйте позже',
    );
  });
});
