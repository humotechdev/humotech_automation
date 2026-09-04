/**
 * Договор с backend не изменился.
 *
 * Самая дешёвая и самая полезная проверка во всём наборе: редизайн
 * перекладывает экраны, и незаметно поменять при этом адрес, метод или
 * тело запроса — вопрос одной невнимательной правки. Сервер об этом
 * скажет отказом уже в бою.
 *
 * Отдельно проверяется, что наружу не уходит ни `employee_id`, ни
 * `organization_id`, ни офис, ни направление отметки. Подделать можно
 * только то, что где-то принимается.
 */

import { describe, expect, it } from 'vitest';

import { api } from '../src/api';
import { rememberToken } from '../src/auth';
import { fakeFetch } from './fixtures';

/** Подменяем глобальный fetch: `api.*` не принимает свой. */
function withFetch(routes: Record<string, unknown> = {}) {
  const fake = fakeFetch(routes);
  const original = globalThis.fetch;
  globalThis.fetch = fake.impl;
  return {
    calls: fake.calls,
    restore: () => {
      globalThis.fetch = original;
    },
  };
}

const CONTRACT: Array<{
  name: string;
  run: () => Promise<unknown>;
  path: string;
  method: string;
}> = [
  { name: 'профиль', run: () => api.profile(), path: '/me/profile', method: 'GET' },
  { name: 'статус', run: () => api.status(), path: '/me/status', method: 'GET' },
  {
    name: 'статистика',
    run: () => api.statistics({ period: 'week' }),
    path: '/me/statistics?period=week',
    method: 'GET',
  },
  {
    name: 'история',
    run: () => api.history({ period: 'month', limit: 15, offset: 0 }),
    path: '/me/history?period=month&limit=15&offset=0',
    method: 'GET',
  },
  {
    name: 'отметка',
    run: () => api.scan('HT1код', 'a-1'),
    path: '/me/attendance/scan',
    method: 'POST',
  },
  { name: 'заявки', run: () => api.absences(), path: '/me/absences', method: 'GET' },
  {
    name: 'правила отсутствий',
    run: () => api.absenceOptions(),
    path: '/me/absences/options',
    method: 'GET',
  },
  {
    name: 'остаток отпуска',
    run: () => api.leaveBalance(),
    path: '/me/leave-balance',
    method: 'GET',
  },
  {
    name: 'отмена заявки',
    run: () => api.cancelAbsence('r1'),
    path: '/me/absences/r1',
    method: 'DELETE',
  },
];

describe('договор с backend', () => {
  it.each(CONTRACT)('$name: адрес и метод прежние', async ({ run, path, method }) => {
    rememberToken('токен');
    const fetch = withFetch();
    try {
      await run();
    } finally {
      fetch.restore();
    }

    expect(fetch.calls).toHaveLength(1);
    expect(fetch.calls[0].url).toContain(path);
    expect(fetch.calls[0].init?.method ?? 'GET').toBe(method);
  });

  it('отметка несёт только код и ключ попытки', async () => {
    rememberToken('токен');
    const fetch = withFetch();
    try {
      await api.scan('HT1код', 'a-1');
    } finally {
      fetch.restore();
    }

    const body = JSON.parse(String(fetch.calls[0].init?.body));
    expect(Object.keys(body).sort()).toEqual(['client_event_id', 'token']);
  });

  it('ни один запрос не несёт employee_id или organization_id', async () => {
    rememberToken('токен');
    const fetch = withFetch();
    try {
      for (const entry of CONTRACT) await entry.run();
      await api.createAbsence(new FormData());
    } finally {
      fetch.restore();
    }

    const everything = fetch.calls
      .map((call) => `${call.url} ${String(call.init?.body ?? '')}`)
      .join('\n');
    expect(everything).not.toContain('employee_id');
    expect(everything).not.toContain('organization_id');
    expect(everything).not.toContain('office_id');
  });

  it('токен уходит заголовком, а не в адресе', async () => {
    rememberToken('секрет-сессии');
    const fetch = withFetch();
    try {
      await api.status();
    } finally {
      fetch.restore();
    }

    const call = fetch.calls[0];
    expect(call.url).not.toContain('секрет-сессии');
    expect(
      (call.init?.headers as Record<string, string>).Authorization,
    ).toBe('Bearer секрет-сессии');
  });

  it('заявка уходит как multipart, без ручного Content-Type', async () => {
    // Boundary проставляет браузер; заданный вручную заголовок сломал бы
    // разбор на сервере.
    rememberToken('токен');
    const fetch = withFetch();
    const form = new FormData();
    form.append('absence_type_code', 'SICK_LEAVE');
    try {
      await api.createAbsence(form);
    } finally {
      fetch.restore();
    }

    const headers = fetch.calls[0].init?.headers as Record<string, string>;
    expect(headers['Content-Type']).toBeUndefined();
    expect(fetch.calls[0].init?.body).toBeInstanceOf(FormData);
  });
});
