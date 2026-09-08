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
import { USER, crm, empty, fakeNetwork, html, json, renderApp } from './helpers';

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

describe('backend не отвечает — это не выход', () => {
  /**
   * Отдельная группа, потому что раньше эти случаи были склеены.
   * Любой отказ `/auth/me` превращался в «войдите», и перезапуск
   * dev-сервера или backend выглядел как потеря доступа: серверная
   * сессия при этом цела, а человек видел форму входа и вводил пароль
   * заново без всякой причины.
   */

  test('оборванный запрос не выбрасывает из системы', async () => {
    fakeNetwork(() => {
      throw new TypeError('Failed to fetch');
    });
    renderApp('/admin');

    expect(await screen.findByRole('alert')).toHaveProperty(
      'textContent',
      expect.stringContaining('Сервер не отвечает'),
    );
    expect(screen.queryByRole('heading', { name: 'Добро пожаловать' })).toBeNull();
  });

  test('пятисотка тоже не считается отсутствием доступа', async () => {
    // Так отвечает dev-прокси, пока backend поднимается.
    fakeNetwork(() => html(502));
    renderApp('/admin');

    await screen.findByRole('alert');
    expect(screen.queryByRole('heading', { name: 'Добро пожаловать' })).toBeNull();
  });

  test('страница не подменяется формой входа: человек остаётся где был',
    async () => {
      // Форма входа — это переход на `/login`, и вернуться после него
      // на прежний экран уже нельзя. Экран «сервер не отвечает»
      // остаётся на месте защищённой страницы.
      fakeNetwork(() => html(502));
      renderApp('/admin');

      await screen.findByRole('alert');
      expect(screen.queryByLabelText('Логин')).toBeNull();
      expect(screen.queryByRole('button', { name: 'Войти' })).toBeNull();
    });

  test('«Повторить» спрашивает сервер заново и открывает страницу',
    async () => {
      let down = true;
      const calls = fakeNetwork((path) => {
        if (path.endsWith('/auth/me') && down) return html(502);
        return crm(path) ?? json(200, USER);
      });
      renderApp('/');
      await screen.findByRole('alert');

      down = false;
      await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));

      await screen.findByText('Обзор на сегодня');
      expect(calls.filter((c) => c.url.endsWith('/auth/me')).length)
        .toBeGreaterThan(1);
    });

  test('403 по-прежнему означает вход: отключённая запись и снятая сессия',
    async () => {
      // Проверка обратной стороны. Различать причины — не значит
      // перестать пускать на форму входа тех, кому туда и надо.
      fakeNetwork(() => REFUSED.clone());
      renderApp('/admin');

      expect(await screen.findByRole('heading', { name: 'Добро пожаловать' }))
        .toBeTruthy();
      expect(screen.queryByText('Сервер не отвечает')).toBeNull();
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
