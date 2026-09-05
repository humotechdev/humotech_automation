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

/** Прямоугольник отступов, который отдаёт Telegram. */
export interface Inset {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

/** Часть Telegram WebApp, которой пользуется кабинет. */
export interface TelegramWebApp {
  initData?: string;
  ready?: () => void;
  expand?: () => void;
  version?: string;
  platform?: string;
  isVersionAtLeast?: (version: string) => boolean;

  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  setBottomBarColor?: (color: string) => void;

  isFullscreen?: boolean;
  requestFullscreen?: () => void;
  exitFullscreen?: () => void;

  safeAreaInset?: Inset;
  contentSafeAreaInset?: Inset;
  viewportHeight?: number;
  viewportStableHeight?: number;

  onEvent?: (event: string, handler: () => void) => void;
  offEvent?: (event: string, handler: () => void) => void;

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

  close?: () => void;
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
 * Цвета оболочки. Ровно те же значения, что и в палитре приложения:
 * `--bg` для шапки и фона, `--surface` для нижней панели.
 *
 * Смысл в том, чтобы родная верхняя панель Telegram с названием бота
 * продолжала фон приложения, а не стояла над ним чужой полосой. Панель
 * при этом остаётся телеграмной: своей мы не рисуем и эту не прячем.
 */
export const SHELL_COLORS = {
  header: '#F7F9FC',
  background: '#F7F9FC',
  bottomBar: '#FFFFFF',
} as const;

/**
 * Достаточно ли новый клиент.
 *
 * Своего сравнения версий нет намеренно: Telegram считает их сам и знает
 * про свои сборки больше, чем мы. Нет метода — считаем, что нет и всего
 * остального, что появилось вместе с ним.
 */
export function atLeast(version: string, source: Window = window): boolean {
  try {
    return webApp(source)?.isVersionAtLeast?.(version) === true;
  } catch {
    return false;
  }
}

/**
 * Подготовка оболочки при запуске.
 *
 * `setBottomBarColor` появился в Bot API 7.10 — проверяется версией, а не
 * наличием метода: на части клиентов метод объявлен, но бросает. Поэтому
 * поверх проверки версии всё равно стоит try/catch. Цвет — отделка;
 * падать из-за неё при запуске нельзя ни на одном клиенте.
 */
export function prepare(source: Window = window): void {
  const app = webApp(source);
  if (!app) return;
  app.ready?.();
  app.expand?.();
  paintShell(source);
  syncViewport(source);
}

/** Вернуть оболочке обычные цвета: после полноэкранного режима тоже. */
export function paintShell(source: Window = window): void {
  const app = webApp(source);
  if (!app) return;
  try {
    app.setBackgroundColor?.(SHELL_COLORS.background);
    app.setHeaderColor?.(SHELL_COLORS.header);
    if (atLeast('7.10', source)) {
      app.setBottomBarColor?.(SHELL_COLORS.bottomBar);
    }
  } catch {
    // Старый клиент отвечает на неизвестный метод исключением.
  }
}

/**
 * События оболочки. Возвращает функцию отписки — снимать обязательно.
 *
 * `onEvent`/`offEvent` есть не у всех клиентов, и подписка на несуществующее
 * событие ничего не ломает: Telegram просто никогда его не пришлёт.
 */
export function onShellEvent(
  event: string,
  handler: () => void,
  source: Window = window,
): () => void {
  const app = webApp(source);
  if (!app?.onEvent) return () => undefined;
  try {
    app.onEvent(event, handler);
  } catch {
    return () => undefined;
  }
  return () => {
    try {
      app.offEvent?.(event, handler);
    } catch {
      // Отписка на закрывающемся клиенте — не повод падать.
    }
  };
}

/* --- безопасные зоны и высота окна ---------------------------------------
 *
 * Telegram сам объявляет `--tg-safe-area-inset-*` и
 * `--tg-content-safe-area-inset-*` на корне документа, и `tokens.css`
 * складывает их с `env()`. Перезаписывать эти имена из JS нельзя: мы
 * заслонили бы живые значения Telegram своим устаревшим снимком.
 *
 * Поэтому адаптер пишет СВОИ переменные, а CSS берёт максимум из двух
 * источников. Это не дублирование: у части клиентов есть JS-объекты, но
 * нет переменных, и наоборот. А `viewportStableHeight` Telegram переменной
 * не отдаёт вовсе — это единственная величина, которая без адаптера
 * недоступна CSS совсем.
 */

function px(value: number | undefined): string {
  return `${Math.max(0, Math.round(value ?? 0))}px`;
}

export function syncViewport(source: Window = window): void {
  const app = webApp(source);
  const root = source.document?.documentElement;
  if (!root) return;

  // Вне Telegram переменные не выставляются вовсе: пусть работает
  // откат на env() и обычную высоту окна, а не наши нули.
  if (!app) return;

  const safe = app.safeAreaInset;
  const content = app.contentSafeAreaInset;

  // Складываются, а не выбираются: `safeAreaInset` — вырез и полоса жеста
  // телефона, `contentSafeAreaInset` — сколько занимает шапка самого
  // Telegram. В полноэкранном режиме шапка стоит поверх выреза.
  root.style.setProperty(
    '--app-safe-top',
    px((safe?.top ?? 0) + (content?.top ?? 0)),
  );
  root.style.setProperty(
    '--app-safe-bottom',
    px((safe?.bottom ?? 0) + (content?.bottom ?? 0)),
  );

  const stable = app.viewportStableHeight ?? app.viewportHeight;
  if (typeof stable === 'number' && stable > 0) {
    // Именно устойчивая высота: обычная скачет вместе с клавиатурой, и
    // на 100vh нижняя панель уезжает за край при открытой клавиатуре.
    root.style.setProperty('--app-viewport-height', px(stable));
  }
}

/**
 * Следить за изменениями зон и размера окна.
 *
 * Все три события приходят в разные моменты: поворот экрана, открытие
 * клавиатуры, вход и выход из полноэкранного режима. Обработчик один —
 * он просто пересчитывает переменные.
 */
export function watchViewport(source: Window = window): () => void {
  const update = () => syncViewport(source);
  update();

  const off = [
    onShellEvent('viewportChanged', update, source),
    onShellEvent('safeAreaChanged', update, source),
    onShellEvent('contentSafeAreaChanged', update, source),
    onShellEvent('fullscreenChanged', update, source),
  ];
  return () => off.forEach((stop) => stop());
}

/* --- полноэкранный режим -------------------------------------------------- */

/** Умеет ли клиент полноэкранный режим (Bot API 8.0). */
export function fullscreenSupported(source: Window = window): boolean {
  return (
    atLeast('8.0', source) &&
    typeof webApp(source)?.requestFullscreen === 'function'
  );
}

/** Идёт ли сейчас полноэкранный режим. */
export function isFullscreen(source: Window = window): boolean {
  return webApp(source)?.isFullscreen === true;
}

/**
 * Запросить полноэкранный режим. Возвращает, ушёл ли запрос.
 *
 * Повторами не занимается: решение о повторе принимает вызывающий по
 * действию человека. Ответ приходит событием `fullscreenChanged` или
 * `fullscreenFailed`, а не возвратом.
 */
export function requestFullscreen(source: Window = window): boolean {
  if (!fullscreenSupported(source)) return false;
  try {
    webApp(source)!.requestFullscreen!();
    return true;
  } catch {
    return false;
  }
}

/**
 * Выйти из полноэкранного режима и вернуть обычные цвета.
 *
 * Вызывается из очистки эффекта, то есть на любом пути ухода с экрана —
 * нижней навигацией, кнопкой «назад», внутренней кнопкой. Ошибка выхода
 * навигацию ломать не должна: экран уже сменился.
 */
export function exitFullscreen(source: Window = window): void {
  const app = webApp(source);
  if (!app?.exitFullscreen) return;
  try {
    if (app.isFullscreen === false) return;
    app.exitFullscreen();
  } catch {
    // Ничего: экран уже уходит.
  }
  paintShell(source);
  syncViewport(source);
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
/**
 * Закрыть Mini App.
 *
 * Вызывается только после УДАЧНОЙ отметки: человеку больше нечего здесь
 * делать, и лишнее нажатие «закрыть» на пороге офиса ни к чему. Отказ
 * так не закрывается — его надо прочитать.
 *
 * Вне Telegram метода нет, и это не ошибка: в обычном браузере окно
 * закрывать нечему и незачем.
 */
export function closeApp(source: Window = window): void {
  try {
    webApp(source)?.close?.();
  } catch {
    // Старый клиент или запрет — не повод падать на экране успеха.
  }
}

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
