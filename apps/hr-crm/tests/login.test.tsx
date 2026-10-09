/**
 * Страница входа.
 *
 * Проверяется не вёрстка, а обещания: что показано, что уходит на
 * сервер и чего на этой странице быть не должно.
 */

import { act, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { App } from '../src/app/App';
import { SessionProvider } from '../src/features/auth/session';
import { USER, crm, empty, fakeNetwork, html, json, renderApp } from './helpers';

/** Сессии нет: приложение спрашивает `/auth/me` и получает отказ. */
function anonymous(after: (path: string, method: string) => Response | Promise<Response>) {
  return fakeNetwork((path, call) => {
    if (path.endsWith('/auth/me')) {
      return json(403, { error: { code: 'not_authenticated', message: '…', details: null } });
    }
    return crm(path) ?? after(path, call.method);
  });
}

async function openForm() {
  await screen.findByRole('heading', { name: 'Вход в систему' });
}

describe('форма входа', () => {
  test('показывает оба поля, кнопку и объяснение доступа', async () => {
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    expect(screen.getByLabelText('Логин')).toBeTruthy();
    expect(screen.getByLabelText('Пароль')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Войти' })).toBeTruthy();
    expect(screen.getByText('Введите данные вашей учётной записи')).toBeTruthy();
    expect(screen.getByText('Доступ предоставляется администратором')).toBeTruthy();
    expect(screen.getByText('Нет доступа? Обратитесь к администратору')).toBeTruthy();
  });

  test('регистрации и восстановления пароля здесь нет', async () => {
    // Учётные записи заводит администратор. Кнопка «создать аккаунт»
    // обещала бы то, чего система не делает.
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    for (const forbidden of [/регистрац/i, /создать аккаунт/i, /забыли пароль/i,
                             /восстановить/i, /google/i, /продолжить через/i]) {
      expect(screen.queryByText(forbidden)).toBeNull();
    }
  });

  test('пустая форма не уходит на сервер', async () => {
    const calls = anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));

    expect(screen.getByText('Введите логин')).toBeTruthy();
    expect(screen.getByText('Введите пароль')).toBeTruthy();
    expect(calls.filter((call) => call.url.endsWith('/auth/login'))).toHaveLength(0);
  });

  test('ошибка поля связана с самим полем', async () => {
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));

    const field = screen.getByLabelText('Логин');
    expect(field.getAttribute('aria-invalid')).toBe('true');
    const described = field.getAttribute('aria-describedby');
    expect(described).toBeTruthy();
    expect(document.getElementById(described ?? '')?.textContent).toBe('Введите логин');
  });

  test('пароль показывается и снова скрывается', async () => {
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    const password = screen.getByLabelText('Пароль');
    expect(password.getAttribute('type')).toBe('password');

    await userEvent.click(screen.getByRole('button', { name: 'Показать пароль' }));
    expect(password.getAttribute('type')).toBe('text');

    await userEvent.click(screen.getByRole('button', { name: 'Скрыть пароль' }));
    expect(password.getAttribute('type')).toBe('password');
  });

  test('показ пароля не теряет введённое', async () => {
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    const password = screen.getByLabelText('Пароль') as HTMLInputElement;
    await userEvent.type(password, 'секрет-123');
    await userEvent.click(screen.getByRole('button', { name: 'Показать пароль' }));
    expect(password.value).toBe('секрет-123');
    await userEvent.click(screen.getByRole('button', { name: 'Скрыть пароль' }));
    expect(password.value).toBe('секрет-123');
  });

  test('подсказки автозаполнения стоят на своих полях', async () => {
    // Без них менеджер паролей браузера не узнаёт форму входа и
    // предлагает сохранить логин как пароль.
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    expect(screen.getByLabelText('Логин').getAttribute('autocomplete')).toBe('username');
    expect(screen.getByLabelText('Пароль').getAttribute('autocomplete')).toBe('current-password');
  });

  test('переключателя языка нет: перевод один, выбирать не из чего', async () => {
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    expect(screen.queryByRole('button', { name: /язык/i })).toBeNull();
  });

  test('вход отправляется клавишей Enter', async () => {
    const calls = anonymous(() => json(200, USER));
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'пароль{Enter}');

    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/auth/login'))).toBe(true),
    );
  });
});

describe('отправка', () => {
  test('успешный вход уводит на защищённую страницу', async () => {
    const calls = anonymous(() => json(200, USER));
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'пароль');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByText('Обзор на сегодня')).toBeTruthy();

    const login = calls.find((call) => call.url.endsWith('/auth/login'));
    expect(login?.method).toBe('POST');
    // Организация уходит из настройки сборки: почта уникальна внутри
    // организации, а не глобально, и одной пары «почта + пароль» серверу
    // не хватает.
    expect(Object.keys(login?.body as object).sort()).toEqual([
      'email', 'organization_code', 'password',
    ]);
  });

  test('второе нажатие во время отправки не создаёт второй запрос', async () => {
    let release: () => void = () => undefined;
    const pause = new Promise<void>((resolve) => {
      release = resolve;
    });
    const calls = anonymous(async (path) => {
      if (path.endsWith('/auth/login')) {
        await pause;
        return json(200, USER);
      }
      return empty(204);
    });
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'пароль');

    const button = screen.getByRole('button', { name: 'Войти' });
    await userEvent.click(button);
    expect(screen.getByRole('button', { name: 'Проверяем…' }).hasAttribute('disabled')).toBe(true);

    await userEvent.click(screen.getByRole('button', { name: 'Проверяем…' }));
    await userEvent.click(screen.getByRole('button', { name: 'Проверяем…' }));

    release();
    await screen.findByText('Обзор на сегодня');
    expect(calls.filter((call) => call.url.endsWith('/auth/login'))).toHaveLength(1);
  });

  test('неверные данные объясняются одинаково для любой причины', async () => {
    // Разные тексты на «нет такого логина» и «не тот пароль» дают
    // перебором список существующих учётных записей.
    anonymous(() =>
      json(401, { error: { code: 'invalid_credentials', message: '…', details: null } }),
    );
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'не тот');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByRole('alert')).toHaveProperty(
      'textContent',
      'Неверный логин или пароль',
    );
  });

  test('после отказа пароль очищается, а логин остаётся', async () => {
    anonymous(() =>
      json(401, { error: { code: 'invalid_credentials', message: '…', details: null } }),
    );
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'не тот');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await screen.findByRole('alert');

    expect((screen.getByLabelText('Логин') as HTMLInputElement).value).toBe('hr@humotech.local');
    expect((screen.getByLabelText('Пароль') as HTMLInputElement).value).toBe('');
  });

  test('оборванная сеть объясняется человеку, а не молчит', async () => {
    fakeNetwork((path) => {
      if (path.endsWith('/auth/me')) {
        return json(403, { error: { code: 'not_authenticated', message: '…', details: null } });
      }
      throw new TypeError('Failed to fetch');
    });
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'пароль');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByRole('alert')).toHaveProperty(
      'textContent',
      'Нет связи с сервером. Проверьте подключение и попробуйте ещё раз.',
    );
  });

  test('после сбоя сети пароль остаётся — можно просто повторить', async () => {
    let attempts = 0;
    fakeNetwork((path, call) => {
      if (path.endsWith('/auth/me')) {
        return json(403, { error: { code: 'not_authenticated', message: '…', details: null } });
      }
      if (path.endsWith('/auth/login') && call.method === 'POST') {
        attempts += 1;
        if (attempts === 1) throw new TypeError('Failed to fetch');
        return json(200, USER);
      }
      return crm(path) ?? json(200, { items: [] });
    });
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'пароль');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await screen.findByRole('alert');
    // Не «неверный пароль» — и пароль не стёрт.
    expect(screen.getByRole('alert').textContent).not.toContain('Неверный');
    expect((screen.getByLabelText('Пароль') as HTMLInputElement).value).toBe('пароль');

    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByText('Обзор на сегодня')).toBeTruthy();
    expect(attempts).toBe(2);
  });

  test('слишком много попыток — просьба подождать, а не «неверный пароль»', async () => {
    anonymous(() => json(429, {
      error: { code: 'too_many_attempts', message: '…', details: { retry_after: 120 } },
    }));
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'пароль');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Слишком много попыток');
    expect(alert.textContent).not.toContain('Неверный');
  });

  test('HTML-страница ошибки Django не попадает на экран', async () => {
    // Django на части отказов отвечает своей страницей. Показать её
    // содержимое значило бы вывесить внутренности сервера.
    anonymous(() => html(500));
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'пароль');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Сервис временно недоступен. Попробуйте позже.');
    expect(document.body.textContent).not.toContain('Server Error');
    expect(document.body.textContent).not.toContain('doctype');
  });
});

describe('отказы, которых не видно по коду ответа', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  async function fillAndSend() {
    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'пароль');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
  }

  test('зависший сервер обрывается по таймауту, пароль остаётся', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fakeNetwork((path, call) => {
      if (path.endsWith('/auth/me')) {
        return json(403, { error: { code: 'not_authenticated', message: '…', details: null } });
      }
      // Ответа нет, пока форма сама не оборвёт запрос.
      return new Promise<Response>((_, reject) => {
        call.signal?.addEventListener('abort', () =>
          reject(new DOMException('The operation was aborted.', 'AbortError')));
      });
    });
    renderApp('/login');
    await openForm();

    await fillAndSend();
    expect(screen.getByRole('button', { name: 'Проверяем…' })).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(20_000));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Сервер не ответил вовремя. Попробуйте ещё раз.');
    expect((screen.getByLabelText('Пароль') as HTMLInputElement).value).toBe('пароль');
    expect(screen.getByRole('button', { name: 'Войти' }).hasAttribute('disabled')).toBe(false);
  });

  test('в StrictMode после отказа форма снова готова к попытке', async () => {
    // Приложение запущено в StrictMode (`main.tsx`): страница монтируется
    // дважды. Флаг «форма на экране» после второго монтирования должен
    // снова стать истинным — иначе кнопка навсегда остаётся «Проверяем…».
    anonymous(() =>
      json(401, { error: { code: 'invalid_credentials', message: '…', details: null } }),
    );
    render(
      <StrictMode>
        <MemoryRouter initialEntries={['/login']}>
          <SessionProvider>
            <App />
          </SessionProvider>
        </MemoryRouter>
      </StrictMode>,
    );
    await openForm();

    await fillAndSend();

    expect((await screen.findByRole('alert')).textContent).toBe('Неверный логин или пароль');
    const button = screen.getByRole('button', { name: 'Войти' });
    expect(button.hasAttribute('disabled')).toBe(false);
  });

  test('200 без учётной записи в теле — не вход', async () => {
    // Прокси или заглушка может ответить 200 чем угодно. Открыть кабинет
    // с таким «пользователем» значит упасть на первом же списке прав.
    anonymous((path) => (path.endsWith('/auth/login') ? json(200, { ok: true }) : empty(204)));
    renderApp('/login');
    await openForm();

    await fillAndSend();

    expect((await screen.findByRole('alert')).textContent)
      .toBe('Сервис временно недоступен. Попробуйте позже.');
    expect(screen.queryByText('Обзор на сегодня')).toBeNull();
  });

  test('отказ в доступе — не «неверный пароль» и без слов сервера', async () => {
    anonymous(() => json(403, {
      error: { code: 'permission_denied', message: 'Учётная запись отключена', details: null },
    }));
    renderApp('/login');
    await openForm();

    await fillAndSend();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('Доступ к системе закрыт. Обратитесь к администратору.');
    expect(document.body.textContent).not.toContain('Учётная запись отключена');
  });
});

describe('«Запомнить логин»', () => {
  test('сохраняется только логин и никогда пароль', async () => {
    anonymous(() => json(200, USER));
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'очень-секретный-пароль');
    await userEvent.click(screen.getByLabelText('Запомнить логин'));
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await screen.findByText('Обзор на сегодня');

    const stored = JSON.stringify(localStorage);
    expect(stored).toContain('hr@humotech.local');
    expect(stored).not.toContain('очень-секретный-пароль');
    // Ни токена, ни признака «уже вошёл»: доступ живёт только в cookie.
    expect(stored.toLowerCase()).not.toContain('token');
  });

  test('без галочки логин не запоминается', async () => {
    anonymous(() => json(200, USER));
    renderApp('/login');
    await openForm();

    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'пароль');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await screen.findByText('Обзор на сегодня');

    expect(JSON.stringify(localStorage)).not.toContain('hr@humotech.local');
  });

  test('снятая галочка сразу удаляет запомненный логин', async () => {
    localStorage.setItem('humotech.crm.login', 'hr@humotech.local');
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    await userEvent.click(screen.getByLabelText('Запомнить логин'));

    expect(localStorage.getItem('humotech.crm.login')).toBeNull();
  });

  test('запомненный логин подставляется при следующем открытии', async () => {
    localStorage.setItem('humotech.crm.login', 'hr@humotech.local');
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    expect((screen.getByLabelText('Логин') as HTMLInputElement).value).toBe('hr@humotech.local');
    expect((screen.getByLabelText('Запомнить логин') as HTMLInputElement).checked).toBe(true);
  });
});
