/**
 * Один разбор ошибки на всё приложение.
 *
 * Backend отвечает единым телом `{error: {code, message, details}}`, и
 * различать случаи мы будем по `code`, а не по тексту: текст переводится
 * и переписывается, код — нет.
 *
 * Человеку ничего из этого не показывается дословно. Django на некоторых
 * отказах отвечает HTML-страницей, а DRF — служебной фразой вроде
 * «CSRF Failed: CSRF token missing»; и то и другое в интерфейсе выглядит
 * как утечка внутренностей, а не как объяснение.
 */

/** Что именно пошло не так — в терминах, понятных интерфейсу. */
export type FailureKind =
  /** Неверная пара логин/пароль. Намеренно неразличимо: существует ли логин. */
  | 'credentials'
  /** Клиент прислал неполные или неверные поля. */
  | 'validation'
  /** Сессии нет или она истекла. */
  | 'session'
  /** Не прошла проверка CSRF. */
  | 'csrf'
  /** Сеть недоступна, сервер не ответил. */
  | 'offline'
  /** Сервер ответил ошибкой или чем-то неразобранным. */
  | 'server';

export class ApiFailure extends Error {
  readonly kind: FailureKind;
  readonly status: number;
  readonly fields: Record<string, string[]>;

  constructor(kind: FailureKind, status = 0, fields: Record<string, string[]> = {}) {
    super(kind);
    this.name = 'ApiFailure';
    this.kind = kind;
    this.status = status;
    this.fields = fields;
  }
}

/** Русские формулировки. Ни одного технического текста от сервера. */
const MESSAGES: Record<FailureKind, string> = {
  credentials: 'Неверный логин или пароль',
  validation: 'Заполните логин и пароль',
  session: 'Сессия истекла. Войдите заново',
  csrf: 'Сессия устарела. Обновите страницу и повторите',
  offline: 'Нет связи с сервером. Проверьте подключение',
  server: 'Сервер временно недоступен. Попробуйте позже',
};

export function messageFor(error: unknown): string {
  return error instanceof ApiFailure ? MESSAGES[error.kind] : MESSAGES.server;
}
