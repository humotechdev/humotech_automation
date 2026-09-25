// @vitest-environment jsdom
/**
 * Аудит безопасности клиента Mini App.
 *
 * Проверяется то, что клиент может испортить сам: куда кладётся токен,
 * что из адресной строки уходит в запрос к API и нет ли в исходниках
 * способов вставить чужой HTML.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import { surveyOf } from '../src/App';
import { api } from '../src/api';
import { forgetToken, recallToken, rememberToken } from '../src/auth';

const UUID = '3f2b85d6-9a3f-4c6a-8327-a34c3334a5c6';

afterEach(() => {
  forgetToken();
  vi.unstubAllGlobals();
  try {
    localStorage.clear();
  } catch {
    /* нет хранилища — нечего чистить */
  }
});

describe('идентификатор опроса из адреса', () => {
  it('принимает UUID', () => {
    expect(surveyOf(`/survey/${UUID}`)).toBe(UUID);
    expect(surveyOf(`/survey/${UUID}/`)).toBe(UUID);
  });

  it.each([
    '/survey/..%2F..%2Fprofile',
    '/survey/%2e%2e',
    '/survey/abc',
    `/survey/${UUID}%3Fx=1`,
    `/survey/${UUID}x`,
    '/survey/javascript:alert(1)',
    `/survey/${UUID}/extra`,
  ])('отвергает %s', (path) => {
    expect(surveyOf(path)).toBeNull();
  });

  it('кодирует идентификатор в пути запроса', async () => {
    const seen: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        seen.push(url);
        return new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    rememberToken('test-token');
    await api.survey('a/../../profile?x=1');
    expect(seen[0]).toContain('/me/surveys/a%2F..%2F..%2Fprofile%3Fx%3D1');
  });
});

describe('хранение токена', () => {
  it('токен не попадает в localStorage', () => {
    rememberToken('secret-for-test');
    expect(recallToken()).toBe('secret-for-test');
    expect(JSON.stringify({ ...localStorage })).not.toContain('secret-for-test');
  });

  it('токен уходит заголовком, а не в адресе', async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        return new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    rememberToken('secret-for-test');
    await api.profile();
    expect(calls[0].url).not.toContain('secret-for-test');
    expect(
      (calls[0].init.headers as Record<string, string>).Authorization,
    ).toBe('Bearer secret-for-test');
  });

  it('забытый токен не восстанавливается', () => {
    rememberToken('secret-for-test');
    forgetToken();
    expect(recallToken()).toBeNull();
  });
});

describe('исходники', () => {
  function files(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) return files(path);
      return /\.(tsx?|jsx?)$/.test(name) ? [path] : [];
    });
  }

  const sources = files(join(__dirname, '..', 'src')).map((path) => ({
    path,
    text: readFileSync(path, 'utf-8'),
  }));

  it.each([
    ['dangerouslySetInnerHTML', /dangerouslySetInnerHTML/],
    ['innerHTML', /\.innerHTML\s*=/],
    ['outerHTML', /\.outerHTML\s*=/],
    ['insertAdjacentHTML', /insertAdjacentHTML/],
    ['document.write', /document\.write/],
    ['eval', /\beval\s*\(/],
    ['new Function', /new\s+Function\s*\(/],
    ['javascript: в разметке', /["'`]javascript:/i],
    ['localStorage для токена', /localStorage\.setItem\([^)]*[Tt]oken/],
  ])('нет %s', (_name, pattern) => {
    const hits = sources.filter(({ text }) => pattern.test(text)).map((s) => s.path);
    expect(hits).toEqual([]);
  });
});
