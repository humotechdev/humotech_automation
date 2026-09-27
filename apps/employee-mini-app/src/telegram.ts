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

  /**
   * Отправить боту служебное сообщение и закрыть приложение.
   *
   * Работает ТОЛЬКО у Mini App, открытого кнопкой нижней клавиатуры, —
   * и ровно там, где подписи запуска нет. Это не запасной канал, а
   * единственный: доказать серверу, кто пришёл, приложение в этом
   * режиме не может, поэтому решение принимает бот, получив от Telegram
   * настоящий `message.from.id`.
   */
  sendData?: (data: string) => void;

  /** Геопозиция средствами Telegram. Bot API 8.0. */
  LocationManager?: {
    init?: (callback?: () => void) => void;
    isInited?: boolean;
    isLocationAvailable?: boolean;
    isAccessGranted?: boolean;
    getLocation?: (callback: (location: TelegramLocation | null) => void) => void;
  };
}

export interface TelegramLocation {
  latitude?: number;
  longitude?: number;
  horizontal_accuracy?: number | null;
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
/** Умеет ли этот запуск отдать данные боту. */
export function canSendData(source: Window = window): boolean {
  return typeof webApp(source)?.sendData === 'function';
}

/**
 * Отдать боту одну попытку отметки.
 *
 * Telegram закрывает приложение сам сразу после вызова, поэтому
 * показывать что-либо после него бессмысленно — и невозможно показать
 * ложный успех: результат придёт сообщением бота, когда его подтвердит
 * сервер.
 */
export function sendToBot(payload: string, source: Window = window): boolean {
  const app = webApp(source);
  if (typeof app?.sendData !== 'function') return false;
  try {
    app.sendData(payload);
    return true;
  } catch {
    return false;
  }
}

/** Где человек находится — точка, которой можно верить. */
export interface Position {
  latitude: number;
  longitude: number;
  /** Погрешность в метрах. Всегда сообщённая телефоном, не придуманная. */
  accuracy: number;
  /** Когда точка получена, по часам телефона (мс). */
  capturedAt: number;
  /** Откуда: `LocationManager` Telegram или браузер. */
  source: 'telegram' | 'browser';
}

/** Почему точки нет. У каждой причины — своя просьба к человеку. */
export type FixProblem = 'unavailable' | 'no_accuracy' | 'stale';

export type Fix =
  | { ok: true; position: Position }
  | { ok: false; problem: FixProblem };

/**
 * Что сказать человеку, когда точки нет.
 *
 * Три разные беды — три разных действия. «Не видно, где вы» при
 * включённой геолокации отправляет человека в настройки, где всё и так
 * включено; а телефону без точности нужно время и открытое место.
 */
export const FIX_PROBLEMS: Record<FixProblem, { title: string; message: string }> = {
  unavailable: {
    title: 'Не видно, где вы',
    message:
      'Телефон не сообщил местоположение. Включите геолокацию, разрешите её '
      + 'Telegram и попробуйте снова.',
  },
  no_accuracy: {
    title: 'Место определилось без точности',
    message:
      'Телефон прислал местоположение, но не сказал, насколько оно точное, — '
      + 'по такой точке нельзя проверить, что вы на месте. Включите точную '
      + 'геолокацию (GPS), подождите несколько секунд и получите позицию ещё раз.',
  },
  stale: {
    title: 'Телефон прислал старое место',
    message:
      'Геопозиция не обновилась с прошлого раза. Подождите несколько секунд, '
      + 'чтобы телефон определил место заново, и попробуйте снова.',
  },
};

/**
 * Сколько точка остаётся «сейчас».
 *
 * Сервер принимает не старше тридцати секунд (`QR.MAX_LOCATION_AGE_SECONDS`);
 * здесь строже, чтобы между «получили» и «дошло» оставался запас на сеть.
 */
export const FIX_MAX_AGE_MS = 15_000;

/**
 * Насколько браузерная точка может быть старше момента, с которого её
 * ждём. Пара секунд — на расхождение часов GPS и системы, не больше.
 */
const TIMESTAMP_SLACK_MS = 2_000;

/**
 * Геопозиция: сначала средствами Telegram, потом браузером.
 *
 * `LocationManager` появился в Bot API 8.0 и требует `init` до первого
 * запроса. Где его нет, остаётся `navigator.geolocation` — он внутри
 * вебвью Telegram работает не везде и умеет висеть, поэтому ожидание
 * ограничено по времени: экран, застрявший на «получаем геопозицию»,
 * человеку у двери бесполезен.
 *
 * `notBefore` — момент, раньше которого точка считается прошлой. Точка
 * отметки должна быть получена во время скана или сразу после него:
 * место, где человек стоял до того, как навёл камеру, — не доказательство
 * того, где он сейчас.
 *
 * Точка без сообщённой погрешности не возвращается никогда. Раньше на её
 * место подставлялись «30 м», и грубая точка по вышкам превращалась в
 * уверенную — ровно то, что сервер потом сравнивал с радиусом.
 */
export function locate(
  { timeoutMs = 12_000, notBefore = 0, source = window }: {
    timeoutMs?: number;
    notBefore?: number;
    source?: Window;
  } = {},
): Promise<Fix> {
  const seen: Seen = { noAccuracy: false, stale: false };

  const work = (async (): Promise<Fix> => {
    const manager = webApp(source)?.LocationManager;
    let native: Position | null = null;
    if (manager?.getLocation && atLeast('8.0', source)) {
      const raw = await fromTelegram(manager);
      if (raw && raw.accuracy === null) {
        // Telegram вернул `horizontal_accuracy: null`. Точку не берём, а
        // спрашиваем браузер: он погрешность сообщает всегда.
        seen.noAccuracy = true;
      } else if (raw && repeatsLastFix(raw, source)) {
        // Та же точка, до сантиметра и до сотой метра, что и в прошлый
        // раз, — это кэш, а не новое измерение. У `LocationManager` нет
        // времени точки, и повтор — единственный признак её возраста.
        seen.stale = true;
      } else if (raw) {
        native = raw as Position;
      }
      if (native && native.accuracy <= GOOD_ENOUGH_M) return settle(native, source);
    }

    const browser = await fromBrowser(source, notBefore, seen);
    const best = !native ? browser
      : !browser ? native
      : browser.accuracy < native.accuracy ? browser : native;
    return best ? settle(best, source) : failure(seen);
  })();

  return Promise.race([
    work,
    new Promise<Fix>((resolve) => setTimeout(() => resolve(failure(seen)), timeoutMs)),
  ]);
}

/** Годится ли точка для отправки прямо сейчас. */
export function isFresh(position: Position, at: number = Date.now()): boolean {
  return at - position.capturedAt <= FIX_MAX_AGE_MS;
}

/**
 * Точка для отметки после того, как код отсканирован.
 *
 * `pending` — запрос, запущенный вместе с камерой: пока человек наводит
 * телефон, место определяется само, и чаще всего ждать потом нечего. Но
 * если человек держал камеру дольше, чем точка остаётся свежей, или та
 * точка оказалась кэшем, место спрашивается заново — уже после скана.
 *
 * `timeoutMs` здесь — бюджет, а не вежливость: у кода с экрана срок
 * тридцать секунд, и ожидание места дольше этого срока превратило бы
 * верную отметку в «код устарел».
 */
export async function fixAfterScan(
  pending: Promise<Fix> | null,
  scannedAt: number,
  { timeoutMs = 12_000, source = window }: { timeoutMs?: number; source?: Window } = {},
): Promise<Fix> {
  const deadline = Date.now() + timeoutMs;
  const first = pending
    ? await Promise.race([
        pending,
        new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs)),
      ])
    : null;
  if (first?.ok && isFresh(first.position)) return first;

  // Бюджет общий на оба ожидания: запрос, начатый с камерой, уже мог
  // съесть его почти целиком, и второй получает только остаток.
  const left = deadline - Date.now();
  if (left < 1_000) return first && !first.ok ? first : { ok: false, problem: 'unavailable' };
  return locate({ notBefore: scannedAt, timeoutMs: left, source });
}

interface Seen {
  noAccuracy: boolean;
  stale: boolean;
}

function failure(seen: Seen): Fix {
  if (seen.noAccuracy) return { ok: false, problem: 'no_accuracy' };
  if (seen.stale) return { ok: false, problem: 'stale' };
  return { ok: false, problem: 'unavailable' };
}

function settle(position: Position, source: Window): Fix {
  rememberFix(position, source);
  return { ok: true, position };
}

/* --- повтор прежней точки ---------------------------------------------------
 *
 * Хранится не сама точка, а её отпечаток: координаты человека незачем
 * держать в хранилище вебвью дольше, чем длится отметка.
 */

const LAST_FIX_KEY = 'humotech.lastFix';

function fingerprint(position: { latitude: number; longitude: number; accuracy: number | null }): string {
  const text = `${position.latitude.toFixed(6)}|${position.longitude.toFixed(6)}|${position.accuracy}`;
  let hash = 5381;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash * 33) ^ text.charCodeAt(index)) >>> 0;
  }
  return hash.toString(36);
}

function storage(source: Window): Storage | null {
  try {
    return source.localStorage ?? null;
  } catch {
    return null;
  }
}

function repeatsLastFix(
  position: { latitude: number; longitude: number; accuracy: number | null; capturedAt: number },
  source: Window,
): boolean {
  try {
    const saved = storage(source)?.getItem(LAST_FIX_KEY);
    if (!saved) return false;
    const [print, at] = saved.split(':');
    // Тот же отпечаток в пределах пары секунд — это одна и та же точка,
    // отданная дважды за одну попытку, а не кэш с прошлого раза.
    return print === fingerprint(position)
      && position.capturedAt - Number(at) > TIMESTAMP_SLACK_MS;
  } catch {
    return false;
  }
}

function rememberFix(position: Position, source: Window): void {
  try {
    storage(source)?.setItem(LAST_FIX_KEY, `${fingerprint(position)}:${position.capturedAt}`);
  } catch {
    // Без хранилища повтор просто не распознаётся — отметка от этого не ломается.
  }
}

/* --- источники ------------------------------------------------------------ */

type RawFix = Omit<Position, 'accuracy'> & { accuracy: number | null };

function fromTelegram(
  manager: NonNullable<TelegramWebApp['LocationManager']>,
): Promise<RawFix | null> {
  return new Promise((resolve) => {
    const ask = () => {
      try {
        manager.getLocation!((location) => resolve(readTelegram(location)));
      } catch {
        resolve(null);
      }
    };
    if (manager.isInited) {
      ask();
      return;
    }
    try {
      manager.init?.(ask);
    } catch {
      resolve(null);
    }
  });
}

function readTelegram(location: TelegramLocation | null): RawFix | null {
  if (!location) return null;
  const { latitude, longitude, horizontal_accuracy: accuracy } = location;
  if (typeof latitude !== 'number' || typeof longitude !== 'number') return null;
  return {
    latitude,
    longitude,
    // Не сообщил — значит, неизвестно. Не тридцать, не ноль: ноль на
    // сервере — признак подделки, а придуманное число — неправда.
    accuracy: typeof accuracy === 'number' && Number.isFinite(accuracy) && accuracy > 0
      ? accuracy
      : null,
    // У `LocationManager` нет времени точки. Момент ответа — лучшее, что
    // есть, а от кэша защищает проверка повтора.
    capturedAt: Date.now(),
    source: 'telegram',
  };
}

/**
 * Точность, при которой ждать дальше нечего.
 *
 * Тридцать пять метров — это уверенный спутниковый приём. На таком фоне
 * разница между двадцатью метрами и тридцатью ни на что не влияет: ждать
 * ради неё ещё несколько секунд значит держать человека у двери без
 * всякой пользы.
 */
const GOOD_ENOUGH_M = 35;

/** Сколько ждать спутники, прежде чем отдать лучшее, что есть. */
const SETTLE_MS = 9_000;

/**
 * Место браузером. Не первое, какое дали, а лучшее СВЕЖЕЕ за несколько секунд.
 *
 * Первая точка почти всегда приходит не со спутников, а от вышек и
 * Wi-Fi: она появляется мгновенно и врёт на сотню метров. Спутниковая
 * приходит следом, через несколько секунд. Поэтому здесь `watchPosition`,
 * а не `getCurrentPosition`: точки приходят одна за другой, мы держим
 * самую точную и прекращаем ждать, как только она стала достаточно
 * хорошей.
 *
 * «Самая точная» выбирается только среди свежих. Вебвью отдаёт и
 * запомненную точку, несмотря на `maximumAge: 0`, — с хорошей
 * погрешностью и старым временем. Выбрать её за точность значило бы
 * предъявить вчерашнее место как сегодняшнее.
 */
function fromBrowser(
  source: Window,
  notBefore: number,
  seen: Seen,
  settleMs = SETTLE_MS,
): Promise<Position | null> {
  const api = source.navigator?.geolocation;
  if (!api?.getCurrentPosition) return Promise.resolve(null);

  const options: PositionOptions = {
    enableHighAccuracy: true,
    timeout: settleMs,
    maximumAge: 0,
  };

  /** Точка, если ей можно верить; иначе отметка, почему нельзя. */
  const read = (position: GeolocationPosition): Position | null => {
    const arrived = Date.now();
    const stamp = typeof position.timestamp === 'number' && position.timestamp > 0
      ? position.timestamp
      : arrived;
    if (stamp < notBefore - TIMESTAMP_SLACK_MS) {
      seen.stale = true;
      return null;
    }
    const accuracy = position.coords.accuracy;
    if (typeof accuracy !== 'number' || !Number.isFinite(accuracy) || accuracy <= 0) {
      seen.noAccuracy = true;
      return null;
    }
    return {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      accuracy,
      // Время точки, но не из будущего: часы GPS и системы расходятся.
      capturedAt: Math.min(stamp, arrived),
      source: 'browser',
    };
  };

  if (!api.watchPosition) {
    // Старый вебвью: одна попытка, что дадут — то и проверяем.
    return new Promise((resolve) => {
      try {
        api.getCurrentPosition(
          (position) => resolve(read(position)),
          () => resolve(null),
          options,
        );
      } catch {
        resolve(null);
      }
    });
  }

  return new Promise((resolve) => {
    let best: Position | null = null;
    let watch: number | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let settled = false;

    /** Снять наблюдение. Отдельно от `finish`: точка может прийти
        синхронно, ещё до того, как `watchPosition` вернул свой номер, —
        и тогда снимать в `finish` нечего, а GPS останется включённым. */
    const stopWatching = () => {
      if (watch === null) return;
      try {
        api.clearWatch(watch);
      } catch {
        // Наблюдение уже снято — не повод падать.
      }
      watch = null;
    };

    const finish = () => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      stopWatching();
      resolve(best);
    };

    try {
      watch = api.watchPosition(
        (position) => {
          const next = read(position);
          if (!next) return;
          if (best === null || next.accuracy < best.accuracy) best = next;
          if (best.accuracy <= GOOD_ENOUGH_M) finish();
        },
        // Отказ после уже полученной точки не отменяет её: «дальше не
        // получилось» — это не «того, что было, не было».
        finish,
        options,
      );
    } catch {
      resolve(null);
      return;
    }

    // Точка могла прийти синхронно и закрыть ожидание раньше, чем мы
    // узнали номер наблюдения. Тогда снимаем его здесь — иначе GPS
    // останется работать до ухода с экрана.
    if (settled) {
      stopWatching();
      return;
    }

    timer = setTimeout(finish, settleMs);
  });
}

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
