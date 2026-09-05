/**
 * «Запомнить логин» — и ровно это.
 *
 * Хранится только строка логина. Ни пароля, ни токена, ни признака
 * «уже вошёл» здесь нет и быть не может: доступ живёт в cookie сессии,
 * которую ставит сервер, а всё, что лежит в `localStorage`, доступно
 * любому скрипту на странице.
 */

const KEY = 'humotech.crm.login';

export function rememberedLogin(): string {
  try {
    return localStorage.getItem(KEY) ?? '';
  } catch {
    // Приватное окно или запрещённое хранилище — не повод падать.
    return '';
  }
}

export function rememberLogin(login: string): void {
  try {
    localStorage.setItem(KEY, login);
  } catch {
    /* без памяти между визитами можно жить */
  }
}

export function forgetLogin(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* уже нечего убирать */
  }
}
