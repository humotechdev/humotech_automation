/**
 * Сканирование QR: три пути, от лучшего к запасному.
 *
 * 1. **`Telegram.WebApp.showScanQrPopup`** — собственный сканер Telegram.
 *    Разрешение на камеру у приложения Telegram уже есть, спрашивать
 *    ничего не нужно, работает в мобильном вебвью на обоих платформах.
 *    Библиотека для этого не нужна вовсе;
 * 2. **`BarcodeDetector`** — встроенный в браузер декодер. Есть в Chrome
 *    и в Android WebView; требует `getUserMedia`, то есть разрешения;
 * 3. **ручной ввод** — если камеры нет или в доступе к ней отказано.
 *    Не «красивое падение», а рабочий путь: под кодом на экране можно
 *    напечатать его же текстом.
 *
 * Тяжёлая библиотека вроде jsQR сюда не подключается. Она весит больше
 * всего остального приложения вместе взятого, а нужна ровно на том
 * устройстве, где уже есть первый или второй путь.
 *
 * Камера включается ТОЛЬКО по действию человека. Ни одного вызова
 * `getUserMedia` при открытии экрана: приложение, спрашивающее камеру
 * само по себе, выглядит подозрительно и справедливо.
 */

import { atLeast, onShellEvent } from './telegram';

/** Что вернуло сканирование. */
export type ScanOutcome =
  | { kind: 'code'; value: string }
  /** Человек закрыл сканер сам. Не ошибка. */
  | { kind: 'cancelled' }
  /** Доступ к камере не дали. */
  | { kind: 'denied' }
  /** Камеры нет или браузер не умеет — остаётся ручной ввод. */
  | { kind: 'unavailable' };

/** Метка нашего кода. По ней чужой QR отсеивается без запроса к серверу. */
export const CODE_PREFIX = 'HT1';

interface TelegramScanner {
  showScanQrPopup?: (
    params: { text?: string },
    callback: (text: string) => boolean | void,
  ) => void;
  closeScanQrPopup?: () => void;
}

export function telegramScanner(source: Window = window): TelegramScanner | null {
  const app = (source as never as { Telegram?: { WebApp?: TelegramScanner } })
    .Telegram?.WebApp;
  return app && typeof app.showScanQrPopup === 'function' ? app : null;
}

/**
 * Готов ли родной сканер Telegram прямо сейчас.
 *
 * Проверяется и наличие метода, и версия клиента. Одного метода мало:
 * на части сборок он объявлен и бросает, а `showScanQrPopup` появился
 * в Bot API 6.4 — до неё окна сканера нет вовсе.
 */
export const SCANNER_SINCE = '6.4';

export function nativeScannerReady(source: Window = window): boolean {
  return telegramScanner(source) !== null && atLeast(SCANNER_SINCE, source);
}

export function hasBarcodeDetector(source: Window = window): boolean {
  return 'BarcodeDetector' in source;
}

/** Есть ли хоть какой-то способ сканировать, кроме ручного ввода. */
export function cameraAvailable(source: Window = window): boolean {
  return telegramScanner(source) !== null || hasBarcodeDetector(source);
}

/**
 * Похоже ли это на наш код.
 *
 * Проверка нужна сканеру Telegram: его окно возвращает подряд всё, что
 * попало в кадр, и без фильтра оно закроется на первой же чужой наклейке.
 */
export function looksLikeOurCode(value: string): boolean {
  if (typeof value !== 'string') return false;
  const text = value.trim();
  return text.startsWith(CODE_PREFIX) || STICKER.test(text);
}

/**
 * Печатный это код или меняющийся на экране.
 *
 * Разница не косметическая: наклейка висит у двери круглосуточно и сама
 * по себе не доказывает ничего — её можно сфотографировать и показать
 * из дома. Поэтому сервер принимает печатный код ТОЛЬКО с координатами,
 * а меняющийся на экране — и без них.
 *
 * Отсюда и отдельная проверка: приложению нужно знать заранее, ждать ли
 * геопозицию, потому что узнать это из ответа сервера означало бы
 * потратить впустую и запрос, и время человека у двери.
 */
export function isSticker(value: string): boolean {
  if (typeof value !== 'string') return false;
  return STICKER.test(value.trim());
}

/**
 * Печатный код офиса: ссылка на бота с нагрузкой `qr_…` или сама нагрузка.
 *
 * Разбирает её сервер — здесь только узнавание по виду, чтобы окно
 * сканера закрылось на наклейке у двери, а не ждало кода с экрана.
 */
const STICKER = /^(?:https:\/\/(?:www\.)?(?:t|telegram)\.me\/[A-Za-z0-9_]{3,64}\?start=)?qr_[A-Za-z0-9_-]{32,61}$/;

/**
 * Сканер Telegram. Окно закрывается только на НАШЕМ коде.
 *
 * `callback` возвращает `true`, чтобы Telegram закрыл окно. На чужом коде
 * возвращаем `false` — человек продолжает наводить камеру, а не получает
 * отказ из-за случайно попавшего в кадр штрихкода на кофейном стакане.
 *
 * `signal` нужен на уход с экрана. Без него подписка на закрытие окна
 * снималась только вместе с разрешением промиса — то есть держалась,
 * пока окно открыто. Уйти с экрана с открытым окном можно (кнопка
 * «назад» Telegram, переход в кабинет), и тогда обработчик оставался
 * висеть, а следующее сканирование добавляло второй.
 */
export function scanWithTelegram(
  source: Window = window,
  signal?: AbortSignal,
): Promise<ScanOutcome> {
  const app = telegramScanner(source);
  if (!app?.showScanQrPopup) {
    return Promise.resolve({ kind: 'unavailable' });
  }
  if (signal?.aborted) {
    return Promise.resolve({ kind: 'cancelled' });
  }

  return new Promise((resolve) => {
    let settled = false;
    const settle = (outcome: ScanOutcome) => {
      if (settled) return;
      settled = true;
      stopWatchingClose();
      signal?.removeEventListener('abort', abandon);
      resolve(outcome);
    };
    const abandon = () => settle({ kind: 'cancelled' });

    // Закрытие окна человеком приходит СОБЫТИЕМ, а не вызовом обработчика
    // текста. Без этой подписки промис не разрешался никогда: закрыл окно
    // крестиком — и ожидание висит до конца жизни экрана.
    const stopWatchingClose = onShellEvent(
      'scanQrPopupClosed',
      abandon,
      source,
    );
    signal?.addEventListener('abort', abandon, { once: true });

    app.showScanQrPopup!({ text: 'Наведите камеру на код у входа' }, (text) => {
      if (!looksLikeOurCode(text)) {
        return false;
      }
      settle({ kind: 'code', value: text.trim() });
      return true;
    });
  });
}

/**
 * Закрыть окно сканера принудительно.
 *
 * Нужно на уходе с экрана: окно Telegram живёт своей жизнью и камеру
 * держит оно. Если экран сменился, а окно осталось открытым, камера
 * продолжает работать — этого быть не должно.
 */
export function closeScanner(source: Window = window): void {
  try {
    telegramScanner(source)?.closeScanQrPopup?.();
  } catch {
    // Окно уже закрыто или клиент не умеет — не повод падать.
  }
}

/**
 * Ключ повтора для одной попытки отметки.
 *
 * Нужен против двойной отправки: телефон в вебвью легко отправляет один
 * скан дважды — от подрагивания пальца или от повторного нажатия, пока
 * идёт запрос. Сервер по этому ключу узнает вторую отправку той же
 * попытки и не создаст вторую отметку.
 */
export function newAttemptId(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Что показать человеку по итогу отметки. */
export const SCAN_RESULTS: Record<string, { title: string; hint?: string }> = {
  ENTERED: { title: 'Вход отмечен' },
  EXITED: { title: 'Выход отмечен' },
  ALREADY_INSIDE: {
    title: 'Вы уже отмечены как в офисе',
    hint: 'Открытая сессия одна. Чтобы закрыть её, отсканируйте код на выходе.',
  },
  NOT_INSIDE: {
    title: 'Открытой сессии нет',
    hint: 'Выход отмечается только после входа.',
  },
  QR_EXPIRED: {
    title: 'Код устарел',
    hint: 'Код на экране меняется. Отсканируйте новый.',
  },
  QR_ALREADY_USED: {
    title: 'Этот код уже использован',
    hint: 'Дождитесь следующего кода на экране.',
  },
  QR_INVALID: {
    title: 'Код не распознан',
    hint: 'Отсканируйте код на экране у входа.',
  },
  QR_POINT_INACTIVE: {
    title: 'Точка отметки выключена',
    hint: 'Обратитесь в отдел кадров.',
  },
  OFFICE_NOT_ALLOWED: {
    title: 'Этот офис вам не назначен',
    hint: 'Отметиться можно там, где вы числитесь.',
  },
  NETWORK_REQUIRED: {
    title: 'Нужно быть в сети офиса',
    hint: 'Подключитесь к рабочему Wi-Fi и попробуйте снова.',
  },
  GEOLOCATION_REQUIRED: {
    title: 'Нужно разрешить геопозицию',
    hint:
      'Печатный код у двери принимается только вместе с местоположением. '
      + 'Разрешите Telegram доступ к геопозиции и отсканируйте ещё раз.',
  },
  // Дальше — отказы, которые сервер умел возвращать и раньше, а
  // приложение показывало одним «Не получилось». Человеку у двери это
  // ничего не объясняет: «слишком далеко» и «точка выключена» требуют
  // совершенно разных действий, а общий текст предлагает одно и то же —
  // попробовать ещё раз, что в обоих случаях не поможет.
  OUTSIDE_GEOFENCE: {
    title: 'Вы слишком далеко от офиса',
    hint: 'Подойдите ближе и отсканируйте код ещё раз.',
  },
  LOCATION_TOO_VAGUE: {
    title: 'Местоположение определилось слишком приблизительно',
    hint:
      'Такое бывает в помещении и при отключённом GPS. Выйдите к окну или '
      + 'к двери, подождите несколько секунд и попробуйте снова.',
  },
  QR_REVOKED: {
    title: 'Наклейка больше не действует',
    hint: 'Её перевыпустили. Отдел кадров распечатает новую.',
  },
  GEOFENCE_NOT_CONFIGURED: {
    title: 'У офиса не задано расположение',
    hint:
      'Пока на карте не отмечено, где находится офис, отметку по печатному '
      + 'коду подтвердить нечем. Сообщите в отдел кадров.',
  },
  TOO_SOON: {
    title: 'Вы только что отметились',
    hint: 'Повторная отметка по этому коду принимается не сразу.',
  },
};

/**
 * Итог отметки словами, с уточнением от самого ответа.
 *
 * Отдельно от таблицы: «слишком далеко» без числа — это спор, в котором
 * человеку нечем проверить, кто прав. «Вы в 340 м, нужно ближе 100 м»
 * уже понятно: либо подойти, либо идти к HR с конкретной цифрой.
 */
export function scanView(result: {
  status: string;
  distance_m?: number | null;
  radius_m?: number | null;
}): { title: string; hint?: string } {
  const view = SCAN_RESULTS[result.status] ?? SCAN_UNKNOWN;
  if (result.status !== 'OUTSIDE_GEOFENCE') return view;

  const { distance_m: distance, radius_m: radius } = result;
  if (typeof distance !== 'number' || typeof radius !== 'number') return view;
  return {
    title: view.title,
    hint:
      `До офиса ${Math.round(distance)} м, а отметиться можно в пределах `
      + `${radius} м. Подойдите ближе и отсканируйте код ещё раз. Если вы `
      + 'уже у двери — значит, телефон определил место неточно: так бывает '
      + 'в помещении. Выйдите наружу и попробуйте снова.',
  };
}

export const SCAN_UNKNOWN = {
  title: 'Не получилось',
  hint: 'Попробуйте ещё раз. Если не выходит — обратитесь в отдел кадров.',
};
