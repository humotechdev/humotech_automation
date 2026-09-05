/**
 * Единственное место, где приложение ходит в сеть.
 *
 * Аутентификация у backend сессионная: Django ставит cookie `sessionid`,
 * а изменяющие запросы требуют заголовка `X-CSRFToken`. Поэтому здесь
 * `credentials: 'include'` и чтение csrf-токена из cookie — и нигде
 * не хранится ни токен доступа, ни пароль. Их и нет: JWT в этом проекте
 * не используется, а `localStorage` для доступа человека — чужое место.
 */

import { ApiFailure, type FailureKind } from './errors';

const BASE = (import.meta.env['VITE_API_URL'] as string | undefined) ?? '/api/v1';

/** Django кладёт токен сюда же, в обычную cookie, доступную скрипту. */
export function csrfToken(source: string = document.cookie): string | null {
  for (const part of source.split(';')) {
    const [name, ...rest] = part.trim().split('=');
    if (name === 'csrftoken') return decodeURIComponent(rest.join('='));
  }
  return null;
}

type Options = {
  method?: 'GET' | 'POST';
  body?: unknown;
  signal?: AbortSignal;
};

/**
 * Запрос к API. Возвращает разобранное тело или бросает `ApiFailure`.
 *
 * Тело ошибки разбирается защищённо: на части отказов Django отвечает
 * HTML-страницей, и попытка прочитать её как JSON — вторая ошибка
 * поверх первой.
 */
export async function request<T>(path: string, options: Options = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') {
    const token = csrfToken();
    if (token) headers['X-CSRFToken'] = token;
  }

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      // Без этого cookie сессии не уйдёт и не вернётся — вход будет
      // «успешным» ровно до следующего запроса.
      credentials: 'include',
      ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
      ...(options.signal ? { signal: options.signal } : {}),
    });
  } catch {
    throw new ApiFailure('offline');
  }

  if (response.ok) {
    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiFailure('server', response.status);
    }
  }

  const body = await safeBody(response);
  throw new ApiFailure(kindOf(response.status, body), response.status, fieldsOf(body));
}

type ErrorBody = { code?: string; message?: string; details?: unknown };

async function safeBody(response: Response): Promise<ErrorBody> {
  const type = response.headers.get('Content-Type') ?? '';
  if (!type.includes('application/json')) return {};
  try {
    const parsed = (await response.json()) as { error?: ErrorBody };
    return parsed.error ?? {};
  } catch {
    return {};
  }
}

function kindOf(status: number, body: ErrorBody): FailureKind {
  if (status === 401) return 'credentials';
  if (status === 400) return 'validation';
  if (status === 403) {
    // Отличаем «нет сессии» от «не прошла проверка CSRF»: первое лечится
    // входом, второе — обновлением страницы, и путать их значит давать
    // человеку бесполезный совет.
    if (body.code === 'permission_denied' && (body.message ?? '').startsWith('CSRF')) {
      return 'csrf';
    }
    return 'session';
  }
  if (status >= 500) return 'server';
  return 'server';
}

function fieldsOf(body: ErrorBody): Record<string, string[]> {
  const details = body.details;
  if (!details || typeof details !== 'object' || Array.isArray(details)) return {};
  const fields: Record<string, string[]> = {};
  for (const [key, value] of Object.entries(details as Record<string, unknown>)) {
    if (Array.isArray(value) && value.every((item) => typeof item === 'string')) {
      fields[key] = value as string[];
    }
  }
  return fields;
}
