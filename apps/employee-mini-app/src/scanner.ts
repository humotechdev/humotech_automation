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

import { onShellEvent } from './telegram';

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
  return typeof value === 'string' && value.trim().startsWith(CODE_PREFIX);
}

/**
 * Сканер Telegram. Окно закрывается только на НАШЕМ коде.
 *
 * `callback` возвращает `true`, чтобы Telegram закрыл окно. На чужом коде
 * возвращаем `false` — человек продолжает наводить камеру, а не получает
 * отказ из-за случайно попавшего в кадр штрихкода на кофейном стакане.
 */
export function scanWithTelegram(source: Window = window): Promise<ScanOutcome> {
  const app = telegramScanner(source);
  if (!app?.showScanQrPopup) {
    return Promise.resolve({ kind: 'unavailable' });
  }

  return new Promise((resolve) => {
    let settled = false;
    const settle = (outcome: ScanOutcome) => {
      if (settled) return;
      settled = true;
      stopWatchingClose();
      resolve(outcome);
    };

    // Закрытие окна человеком приходит СОБЫТИЕМ, а не вызовом обработчика
    // текста. Без этой подписки промис не разрешался никогда: закрыл окно
    // крестиком — и ожидание висит до конца жизни экрана.
    const stopWatchingClose = onShellEvent(
      'scanQrPopupClosed',
      () => settle({ kind: 'cancelled' }),
      source,
    );

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
  },
};

export const SCAN_UNKNOWN = {
  title: 'Не получилось',
  hint: 'Попробуйте ещё раз. Если не выходит — обратитесь в отдел кадров.',
};
