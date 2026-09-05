/**
 * Вход, выход и «кто я» — ровно те три маршрута, что есть у backend.
 *
 * Организация в запросе входа обязательна: почта уникальна внутри
 * организации, а не глобально. В форме её поля нет намеренно — установка
 * CRM принадлежит одной организации, и берётся она из переменной сборки.
 */

import { request } from './client';

export type CurrentUser = {
  id: string;
  email: string;
  organization_id: string;
  organization_code: string;
  employee_id: string | null;
  status: string;
  roles: string[];
  permissions: string[];
};

export const organizationCode = (): string =>
  (import.meta.env['VITE_ORGANIZATION_CODE'] as string | undefined) ?? '';

export function login(email: string, password: string): Promise<CurrentUser> {
  return request<CurrentUser>('/auth/login', {
    method: 'POST',
    body: {
      organization_code: organizationCode(),
      email,
      password,
    },
  });
}

export function logout(): Promise<void> {
  return request<void>('/auth/logout', { method: 'POST' });
}

export function currentUser(signal?: AbortSignal): Promise<CurrentUser> {
  return request<CurrentUser>('/auth/me', signal ? { signal } : {});
}
