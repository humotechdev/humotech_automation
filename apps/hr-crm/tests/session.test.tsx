/**
 * Сессия: проверка при запуске, защита маршрута и выход.
 *
 * Единственный источник правды о доступе — cookie сессии, которую видит
 * только браузер. Поэтому приложение при каждом запуске спрашивает
 * сервер, а не свою память.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';

import { csrfToken } from '../src/api/client';
import { USER, crm, empty, fakeNetwork, json, renderApp } from './helpers';

const REFUSED = json(403, {
  error: { code: 'not_authenticated', message: '…', details: null },
});

describe('проверка сессии при запуске', () => {
  test('живая сессия открывает страницу без повторного входа', async () => {
    const calls = fakeNetwork((path) => crm(path) ?? json(200, USER));
    renderApp('/');

    expect(await screen.findByText('Обзор на сегодня')).toBeTruthy();
    // Область доступа берётся из сессии, а не подставляется всем одна.
    expect(screen.getByText('DEMO')).toBeTruthy();
    expect(calls[0]?.url.endsWith('/auth/me')).toBe(true);
  });

  test('вошедшему не показывают форму входа заново', async () => {
    // После обновления вкладки человек с живой сессией не должен снова
    // вводить пароль там, где он уже не нужен.
    fakeNetwork((path) => crm(path) ?? json(200, USER));
    renderApp('/login');

    await screen.findByText('Обзор на сегодня');
    expect(screen.queryByRole('heading', { name: 'Добро пожаловать' })).toBeNull();
  });

  test('без сессии защищённая страница не показывается', async () => {
    fakeNetwork(() => REFUSED.clone());
    renderApp('/');

    expect(await screen.findByRole('heading', { name: 'Добро пожаловать' })).toBeTruthy();
    expect(screen.queryByText('Обзор на сегодня')).toBeNull();
  });

  test('неизвестный адрес не показывает чужого содержимого', async () => {
    fakeNetwork(() => REFUSED.clone());
    renderApp('/employees');

    expect(await screen.findByRole('heading', { name: 'Добро пожаловать' })).toBeTruthy();
  });

  test('пока идёт проверка, не показывают ни форму, ни кабинет', async () => {
    let answer: (response: Response) => void = () => undefined;
    fakeNetwork(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    renderApp('/');

    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Проверяем сессию…');
    expect(screen.queryByRole('heading', { name: 'Добро пожаловать' })).toBeNull();

    answer(json(200, USER));
    await screen.findByText('Обзор на сегодня');
  });

  test('запрос сессии уходит с cookie', async () => {
    // Без `credentials: include` cookie не уйдёт, и вход будет
    // «успешным» ровно до следующего запроса.
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
      crm(String(input)) ?? json(200, USER),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderApp('/');
    await screen.findByText('Обзор на сегодня');

    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ credentials: 'include' });
  });
});

describe('выход', () => {
  test('после выхода снова показывается форма входа', async () => {
    const calls = fakeNetwork((path) =>
      path.endsWith('/auth/logout') ? empty(204) : crm(path) ?? json(200, USER),
    );
    renderApp('/');
    await screen.findByText('Обзор на сегодня');

    await userEvent.click(screen.getByRole('button', { name: 'Выйти' }));

    expect(await screen.findByRole('heading', { name: 'Добро пожаловать' })).toBeTruthy();
    const out = calls.find((call) => call.url.endsWith('/auth/logout'));
    expect(out?.method).toBe('POST');
  });

  test('человек выходит даже если сервер не ответил', async () => {
    // Он нажал «выйти». Оставить его в интерфейсе вошедшим нельзя.
    fakeNetwork((path) => {
      if (path.endsWith('/auth/logout')) throw new TypeError('Failed to fetch');
      return crm(path) ?? json(200, USER);
    });
    renderApp('/');
    await screen.findByText('Обзор на сегодня');

    await userEvent.click(screen.getByRole('button', { name: 'Выйти' }));

    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Добро пожаловать' })).not.toBeNull(),
    );
  });
});

describe('CSRF', () => {
  test('изменяющий запрос несёт токен из cookie', async () => {
    document.cookie = 'csrftoken=токен-из-cookie';
    const calls = fakeNetwork((path) =>
      path.endsWith('/auth/logout') ? empty(204) : crm(path) ?? json(200, USER),
    );
    renderApp('/');
    await screen.findByText('Обзор на сегодня');

    await userEvent.click(screen.getByRole('button', { name: 'Выйти' }));

    const out = await waitFor(() => {
      const call = calls.find((item) => item.url.endsWith('/auth/logout'));
      expect(call).toBeTruthy();
      return call;
    });
    expect(out?.headers['X-CSRFToken']).toBe('токен-из-cookie');
  });

  test('чтение не требует токена', async () => {
    const calls = fakeNetwork((path) => crm(path) ?? json(200, USER));
    renderApp('/');
    await screen.findByText('Обзор на сегодня');

    expect(calls[0]?.headers['X-CSRFToken']).toBeUndefined();
  });

  test('токен находится среди других cookie', () => {
    expect(csrfToken('sessionid=abc; csrftoken=нужный; other=1')).toBe('нужный');
    expect(csrfToken('sessionid=abc')).toBeNull();
  });
});
