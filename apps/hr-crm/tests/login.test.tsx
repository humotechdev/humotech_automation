/**
 * Страница входа.
 *
 * Проверяется не вёрстка, а обещания: что показано, что уходит на
 * сервер и чего на этой странице быть не должно.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';

import { USER, empty, fakeNetwork, html, json, renderApp } from './helpers';

/** Сессии нет: приложение спрашивает `/auth/me` и получает отказ. */
function anonymous(after: (path: string, method: string) => Response | Promise<Response>) {
  return fakeNetwork((path, call) => {
    if (path.endsWith('/auth/me')) {
      return json(403, { error: { code: 'not_authenticated', message: '…', details: null } });
    }
    return after(path, call.method);
  });
}

async function openForm() {
  await screen.findByRole('heading', { name: 'Добро пожаловать' });
}

describe('форма входа', () => {
  test('показывает оба поля, кнопку и объяснение доступа', async () => {
    anonymous(() => empty(204));
    renderApp('/login');
    await openForm();

    expect(screen.getByLabelText('Логин')).toBeTruthy();
    expect(screen.getByLabelText('Пароль')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Войти' })).toBeTruthy();
    expect(screen.getByText('Войдите в HR-систему HUMOTECH')).toBeTruthy();
    expect(screen.getByText('Доступ только для авторизованных сотрудников')).toBeTruthy();
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

    expect(await screen.findByText('Интерфейс CRM будет добавлен следующим этапом')).toBeTruthy();

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
    await screen.findByText('Интерфейс CRM будет добавлен следующим этапом');
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
      'Нет связи с сервером. Проверьте подключение',
    );
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
    expect(alert.textContent).toBe('Сервер временно недоступен. Попробуйте позже');
    expect(document.body.textContent).not.toContain('Server Error');
    expect(document.body.textContent).not.toContain('doctype');
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
    await screen.findByText('Интерфейс CRM будет добавлен следующим этапом');

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
    await screen.findByText('Интерфейс CRM будет добавлен следующим этапом');

    expect(JSON.stringify(localStorage)).not.toContain('hr@humotech.local');
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
