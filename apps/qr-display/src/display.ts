/**
 * Логика экрана: сопряжение, получение кодов, восстановление после обрыва.
 *
 * Модуль не знает ни про React, ни про DOM — ему нужны только `fetch`
 * и часы. Поэтому его можно прогнать по всем состояниям без браузера,
 * а экран остаётся тонким.
 *
 * Главное правило: экран не выбирает НИЧЕГО. Ни офиса, ни точки, ни
 * направления. Он предъявляет свой credential и получает то, что положено
 * именно ему; офис и точка приходят в ответе, а не уходят в запросе.
 * Подделать можно только то, что где-то принимается.
 */

/** Что показывает экран прямо сейчас. */
export type DisplayState =
  /** Устройство ещё не сопряжено: нужен одноразовый код от отдела кадров. */
  | { kind: 'pairing'; error?: string }
  /** Первый код ещё не получен. */
  | { kind: 'starting' }
  /** Рабочее состояние: есть действующий код. */
  | { kind: 'showing'; code: QrCode; online: boolean }
  /** Доступ снят или credential не принят — нужно сопрягать заново. */
  | { kind: 'revoked' };

export interface QrCode {
  token: string;
  issuedAt: number;
  expiresAt: number;
  officeName: string;
  pointName: string;
  directionMode: string;
}

export interface Config {
  apiUrl: string;
  fetchImpl?: typeof fetch;
  now?: () => number;
}

/**
 * Где живёт credential экрана — и почему здесь `localStorage`.
 *
 * В Mini App правило обратное: там `localStorage` запрещён, потому что
 * хранит доступ ЧЕЛОВЕКА на устройстве, которое может взять кто угодно
 * ещё. Здесь хранится доступ САМОГО устройства — планшета, привинченного
 * к стене, — и он обязан пережить перезагрузку и утренний запуск без
 * человека рядом. Угрозы разные, поэтому и ответы разные; повторить
 * правило Mini App здесь значило бы требовать, чтобы кто-то приходил
 * сопрягать экран после каждого отключения питания.
 */
const CREDENTIAL_KEY = 'humotech.qr-display.credential';

export function saveCredential(value: string): void {
  try {
    localStorage.setItem(CREDENTIAL_KEY, value);
  } catch {
    /* хранилище недоступно — экран проработает до перезагрузки */
  }
}

export function loadCredential(): string | null {
  try {
    return localStorage.getItem(CREDENTIAL_KEY);
  } catch {
    return null;
  }
}

export function forgetCredential(): void {
  try {
    localStorage.removeItem(CREDENTIAL_KEY);
  } catch {
    /* уже нечего убирать */
  }
}

/** Обмен одноразового кода сопряжения на постоянный credential. */
export async function pair(
  pairingCode: string,
  config: Config,
): Promise<{ ok: true; credential: string } | { ok: false; message: string }> {
  const doFetch = config.fetchImpl ?? fetch;
  let response: Response;
  try {
    response = await doFetch(`${config.apiUrl}/qr-display/pair`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pairing_code: pairingCode.trim() }),
    });
  } catch {
    return { ok: false, message: 'Нет связи с сервером' };
  }

  if (!response.ok) {
    // Несуществующий и уже использованный код неразличимы намеренно —
    // так решено на сервере, и повторять здесь догадки незачем.
    return { ok: false, message: 'Код не подошёл. Проверьте и введите заново' };
  }

  const body = await response.json();
  return { ok: true, credential: body.credential };
}

/** Результат запроса очередного кода. */
export type FetchResult =
  | { kind: 'code'; code: QrCode }
  /** Сеть или сервер: прежний код ещё может быть действующим. */
  | { kind: 'offline' }
  /** Credential не принят: экран отозван или срок вышел. */
  | { kind: 'revoked' };

export async function fetchCode(
  credential: string,
  config: Config,
): Promise<FetchResult> {
  const doFetch = config.fetchImpl ?? fetch;
  let response: Response;
  try {
    response = await doFetch(`${config.apiUrl}/qr-display/code`, {
      headers: { Authorization: `Bearer ${credential}` },
    });
  } catch {
    return { kind: 'offline' };
  }

  if (response.status === 403 || response.status === 401) {
    // Отзыв действует немедленно. Держать старый код на экране после
    // этого нельзя: он проработает ещё полминуты и пропустит человека
    // туда, куда доступ уже закрыт.
    return { kind: 'revoked' };
  }
  if (!response.ok) {
    return { kind: 'offline' };
  }

  const body = await response.json();
  return {
    kind: 'code',
    code: {
      token: body.token,
      issuedAt: Date.parse(body.issued_at),
      expiresAt: Date.parse(body.expires_at),
      officeName: body.office_name,
      pointName: body.point_name,
      directionMode: body.direction_mode,
    },
  };
}

/**
 * Через сколько миллисекунд просить следующий код.
 *
 * Заранее, а не по истечении: если ждать до конца срока, между истечением
 * старого кода и появлением нового будет окно, в котором на экране висит
 * заведомо нерабочий код. Человек в это окно сканирует и получает отказ.
 *
 * Запас — треть срока, но не меньше пяти секунд: при коротком сроке
 * фиксированный запас съел бы его целиком.
 */
export function refreshDelay(code: QrCode, now: number): number {
  const lifetime = code.expiresAt - code.issuedAt;
  const margin = Math.max(Math.floor(lifetime / 3), 5000);
  return Math.max(code.expiresAt - margin - now, 1000);
}

/**
 * Пауза перед повтором после обрыва связи.
 *
 * Растёт до минуты: экран в офисе с упавшей сетью иначе бьётся в сервер
 * каждую секунду сутки напролёт. Верхний предел небольшой намеренно —
 * связь восстановится, и экран должен ожить сам, без человека.
 */
export function retryDelay(attempt: number): number {
  return Math.min(2000 * 2 ** Math.max(attempt - 1, 0), 60000);
}

/** Действует ли код прямо сейчас. */
export function isFresh(code: QrCode, now: number): boolean {
  return now < code.expiresAt;
}
