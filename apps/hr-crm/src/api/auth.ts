/**
 * Вход, выход и «кто я» — ровно те три маршрута, что есть у backend.
 *
 * Организация в запросе входа обязательна: почта уникальна внутри
 * организации, а не глобально. В форме её поля нет намеренно — установка
 * CRM принадлежит одной организации, и берётся она из переменной сборки.
 */

import { csrfToken, request } from './client';
import { ApiFailure } from './errors';

export type CurrentUser = {
  id: string;
  email: string;
  organization_id: string;
  organization_code: string;
  employee_id: string | null;
  status: string;
  /**
   * Пояс организации. В нём показывается время там, где у строки нет
   * своего офиса: журнал действий, карточка учётной записи, сроки
   * назначений. Своей арифметики над поясами в интерфейсе нет.
   */
  timezone: string;
  roles: string[];
  permissions: string[];
};

export const organizationCode = (): string =>
  (import.meta.env['VITE_ORGANIZATION_CODE'] as string | undefined) ?? '';

/**
 * Вход проверяет CSRF, как и любой изменяющий запрос.
 *
 * Cookie `csrftoken` ставит `GET /auth/me`, который приложение делает при
 * запуске. Но cookie может не оказаться: её стёрли, она истекла, пока
 * форма была открыта, или первый `/auth/me` не дошёл. Поэтому перед
 * входом без cookie токен сначала запрашивается, а отказ именно по CSRF
 * повторяется один раз со свежим токеном — человеку незачем знать, что
 * такое CSRF, и незачем вводить пароль дважды.
 */
export async function login(email: string, password: string): Promise<CurrentUser> {
  const send = () => request<CurrentUser>('/auth/login', {
    method: 'POST',
    body: {
      organization_code: organizationCode(),
      email,
      password,
    },
  });
  if (!csrfToken()) await primeCsrf();
  try {
    return await send();
  } catch (failure) {
    if (!(failure instanceof ApiFailure) || failure.kind !== 'csrf') throw failure;
    await primeCsrf();
    return send();
  }
}

/** Получить cookie `csrftoken`. Ответ неважен: 403 без сессии — норма. */
async function primeCsrf(): Promise<void> {
  try {
    await currentUser();
  } catch {
    /* cookie ставится и на отказ; если сети нет — скажет сам вход */
  }
}

export function logout(): Promise<void> {
  return request<void>('/auth/logout', { method: 'POST' });
}

export function currentUser(signal?: AbortSignal): Promise<CurrentUser> {
  return request<CurrentUser>('/auth/me', signal ? { signal } : {});
}
