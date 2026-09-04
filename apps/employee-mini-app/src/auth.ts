/**
 * Авторизация Mini App: строка Telegram -> внутренний токен HUMOTECH.
 *
 * Модуль намеренно не знает ни про React, ни про DOM: всё, что ему нужно, —
 * `initData` и `fetch`. Поэтому его можно прогнать по всем состояниям без
 * браузера, а экраны остаются тонкими.
 *
 * Главное правило: решение о доступе принимает СЕРВЕР.
 * `Telegram.WebApp.initDataUnsafe` здесь не читается вовсе — это те же поля,
 * но без подписи, и любой, кто открыл страницу в обычном браузере, может
 * подставить туда что угодно. Наружу уходит только исходная подписанная
 * строка, а кто это и есть ли у него доступ, отвечает backend.
 */

/** Что получилось. Пять состояний, у каждого свой экран. */
export type AuthResult =
  /** Вошли. Токен держим в памяти, наружу не отдаём. */
  | { state: 'authenticated'; token: string; employee: Employee }
  /** Сотрудник перешёл по ссылке, но HR ещё не подтвердил привязку. */
  | { state: 'pending' }
  /** Этот Telegram не привязан ни к кому — нужна ссылка от HR. */
  | { state: 'unlinked' }
  /** Открыли вне Telegram: подписанной строки просто нет. */
  | { state: 'outside-telegram' }
  /** Всё остальное: сеть, сервер, отвергнутая подпись. */
  | { state: 'error'; message: string };

export interface Employee {
  id: string;
  full_name: string;
  employee_number: string;
  employment_status: string;
  preferred_language: string | null;
}

// Тип Telegram WebApp и объявление `window.Telegram` живут в `telegram.ts`:
// одно объявление глобального типа на приложение, иначе два разных
// описания одного и того же объекта неизбежно разъедутся.
import './telegram';


const MESSAGES = {
  network: 'Не удалось связаться с сервером. Проверьте связь и попробуйте ещё раз.',
  server: 'Сервер временно недоступен. Попробуйте через пару минут.',
  rejected:
    'Telegram не подтвердил запуск. Закройте приложение и откройте его заново из чата с ботом.',
} as const;

/**
 * Подписанная строка запуска, либо null.
 *
 * Пустая строка — это НЕ ошибка сервера, а признак того, что страницу
 * открыли вне Telegram: в обычном браузере `window.Telegram` отсутствует.
 * Состояние отдельное, потому что и делать в нём надо другое — не повторять
 * запрос, а объяснить, что открывать нужно из бота.
 */
export function readInitData(source: Window = window): string | null {
  const value = source.Telegram?.WebApp?.initData;
  return value ? value : null;
}

/**
 * Обменивает `initData` на внутренний токен.
 *
 * Ни `telegram_user_id`, ни `employee_id` не передаются: их нельзя подделать,
 * если их негде передать. Сотрудника определяет подпись.
 */
export async function authenticate(
  initData: string | null,
  options: { apiUrl: string; fetchImpl?: typeof fetch },
): Promise<AuthResult> {
  if (!initData) {
    return { state: 'outside-telegram' };
  }

  const doFetch = options.fetchImpl ?? fetch;
  let response: Response;
  try {
    response = await doFetch(`${options.apiUrl}/telegram/mini-app/auth`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: initData }),
    });
  } catch {
    // Сеть в Telegram-вебвью рвётся регулярно: это не отказ в доступе.
    return { state: 'error', message: MESSAGES.network };
  }

  if (response.ok) {
    const body = await response.json();
    return {
      state: 'authenticated',
      token: body.access_token,
      employee: body.employee,
    };
  }

  if (response.status === 403) {
    // Backend отвечает одним кодом на каждую причину отказа. Различать их
    // нужно: «ждите HR» и «попросите ссылку» — разные действия человека.
    const reason = await readReason(response);
    if (reason === 'pending_confirmation') return { state: 'pending' };
    if (reason === 'not_linked') return { state: 'unlinked' };
    return { state: 'error', message: MESSAGES.rejected };
  }

  return { state: 'error', message: MESSAGES.server };
}

async function readReason(response: Response): Promise<string | null> {
  try {
    const body = await response.json();
    return body?.error?.details?.reason ?? null;
  } catch {
    // Тело может оказаться не JSON — например, от промежуточного прокси.
    return null;
  }
}

/**
 * Хранилище токена на время работы приложения.
 *
 * Токен живёт в памяти, а копия кладётся в `sessionStorage`. Почему так:
 *
 *   * `localStorage` не годится. Он переживает закрытие Mini App и остаётся
 *     в вебвью неограниченно долго — на общем устройстве следующий человек
 *     открыл бы чужую сессию;
 *   * одной памяти мало. Перезагрузка страницы внутри вебвью не обновляет
 *     `initData`: строка сохраняет прежний `auth_date`, а он живёт пять
 *     минут. Без `sessionStorage` любая перезагрузка через пять минут
 *     после запуска означала бы «откройте приложение заново»;
 *   * `sessionStorage` очищается вместе с вкладкой вебвью, то есть при
 *     закрытии Mini App — ровно та граница, которая здесь нужна.
 *
 * Обращения обёрнуты в try: в приватном режиме и при запрете хранилища
 * доступ к нему бросает исключение, и приложение не должно из-за этого
 * падать — оно просто теряет способность пережить перезагрузку.
 */
const TOKEN_KEY = 'humotech.mini-app.token';

let inMemoryToken: string | null = null;

export function rememberToken(token: string): void {
  inMemoryToken = token;
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* хранилище недоступно — работаем только из памяти */
  }
}

export function recallToken(): string | null {
  if (inMemoryToken) return inMemoryToken;
  try {
    inMemoryToken = sessionStorage.getItem(TOKEN_KEY);
  } catch {
    inMemoryToken = null;
  }
  return inMemoryToken;
}

export function forgetToken(): void {
  inMemoryToken = null;
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* уже нечего убирать */
  }
}
