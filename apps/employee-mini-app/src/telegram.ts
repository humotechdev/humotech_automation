/**
 * Всё общение с оболочкой Telegram — в одном месте.
 *
 * Каждый вызов через `?.`: часть возможностей появилась в разных версиях
 * Bot API (безопасные зоны — 7.7, цвет нижней панели — 7.10), и на клиенте
 * постарше приложение обязано остаться рабочим, а не сломаться на вызове
 * того, чего нет.
 *
 * `initDataUnsafe` здесь не читается вовсе — ни для аватара, ни для имени.
 * Это те же поля, что и в подписанной строке, но без подписи: подставить
 * туда чужого сотрудника может кто угодно, а выглядеть это будет так же.
 * Имя и офис приходят с backend, который подпись проверил.
 */

/** Часть Telegram WebApp, которой пользуется кабинет. */
export interface TelegramWebApp {
  initData?: string;
  ready?: () => void;
  expand?: () => void;
  version?: string;
  platform?: string;

  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  setBottomBarColor?: (color: string) => void;

  BackButton?: {
    show?: () => void;
    hide?: () => void;
    onClick?: (handler: () => void) => void;
    offClick?: (handler: () => void) => void;
  };

  HapticFeedback?: {
    notificationOccurred?: (type: 'error' | 'success' | 'warning') => void;
    impactOccurred?: (style: 'light' | 'medium' | 'heavy') => void;
  };

  showScanQrPopup?: (
    params: { text?: string },
    callback: (text: string) => boolean | void,
  ) => void;
  closeScanQrPopup?: () => void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function webApp(source: Window = window): TelegramWebApp | null {
  return source.Telegram?.WebApp ?? null;
}

/**
 * Подготовка оболочки при запуске.
 *
 * Цвета задаются под нашу палитру: шапка и фон — цвет фона приложения,
 * нижняя панель — белая, как навигация. Иначе на границе экрана видна
 * чужая полоса, и приложение выглядит вставленным в чужое окно.
 */
export function prepare(source: Window = window): void {
  const app = webApp(source);
  if (!app) return;
  app.ready?.();
  app.expand?.();
  try {
    app.setBackgroundColor?.('#f7f9fc');
    app.setHeaderColor?.('#f7f9fc');
    app.setBottomBarColor?.('#ffffff');
  } catch {
    // Старый клиент отвечает на неизвестный метод исключением. Цвет —
    // отделка; падать из-за неё при запуске нельзя.
  }
}

/**
 * Родная кнопка «назад» Telegram на вложенных экранах и в формах.
 *
 * Своей стрелки в интерфейсе нет намеренно: на Android кнопка Telegram
 * совмещена с системной, и две разные «назад» на одном экране — способ
 * закрыть приложение вместо возврата к списку.
 *
 * Обработчики складываются в стопку, и работает только верхний. Иначе
 * форма, открытая поверх экрана, зарегистрировала бы второй обработчик,
 * и одно нажатие закрыло бы сразу оба — вместе с заполненной заявкой.
 */
const handlers: Array<() => void> = [];
let attached: (() => void) | null = null;

function apply(source: Window): void {
  const button = webApp(source)?.BackButton;
  if (!button) return;

  if (attached) {
    button.offClick?.(attached);
    attached = null;
  }
  const top = handlers[handlers.length - 1];
  if (top) {
    button.onClick?.(top);
    attached = top;
    button.show?.();
  } else {
    button.hide?.();
  }
}

export function backButton(
  handler: (() => void) | null,
  source: Window = window,
): () => void {
  if (!handler) {
    // Экран без своей «назад» просто пересобирает состояние: если под
    // ним открыта форма, кнопка должна остаться её кнопкой.
    apply(source);
    return () => undefined;
  }

  handlers.push(handler);
  apply(source);

  return () => {
    const at = handlers.lastIndexOf(handler);
    if (at >= 0) handlers.splice(at, 1);
    apply(source);
  };
}

/**
 * Отклик телефона — только после того, что человек действительно сделал:
 * прошедшая отметка, отказ в отметке, отправленная заявка.
 *
 * Вибрация на каждое нажатие быстро становится шумом, который выключают
 * вместе с полезными сигналами.
 */
export function haptic(
  kind: 'success' | 'error' | 'warning',
  source: Window = window,
): void {
  try {
    webApp(source)?.HapticFeedback?.notificationOccurred?.(kind);
  } catch {
    // Отклик — не функциональность. Его отсутствие не должно ничего ломать.
  }
}
