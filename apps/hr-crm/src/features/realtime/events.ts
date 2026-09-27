/**
 * Поток событий CRM: один на всё открытое приложение, а не на каждый чат.
 *
 * Сервер шлёт только «что изменилось» — какое обращение и что с ним, без
 * текста. Получив сигнал, страница перечитывает данные обычным запросом:
 * источник истины — база и HTTP API. Поэтому пропущенный сигнал не
 * теряет сообщение — оно придёт при следующем перечитывании, а
 * перечитывание случается и само: при переподключении, при возврате на
 * вкладку и после восстановления сети.
 *
 * Живёт вне React, как счётчики меню: переход между разделами поток не
 * рвёт. Открывается только у того, кто может читать обращения, и
 * закрывается при выходе из системы.
 *
 * Почему свой переподключатель, а не встроенный в EventSource. Браузер
 * переподключается сам только после обрыва уже открытого потока. Если же
 * сервер на время перезапуска ответил 502 или 503, EventSource переходит
 * в CLOSED навсегда — и CRM молча осталась бы без обновлений до F5.
 */

const BASE = (import.meta.env['VITE_API_URL'] as string | undefined) ?? '/api/v1';
const STREAM = `${BASE}/events/stream`;

/** Сигнал странице. `resync` — «перечитай всё»: сигналы могли потеряться. */
export type LiveSignal =
  | { type: 'question'; id: string; what: string; at: number | null; sent: number | null }
  | { type: 'resync' };

export type LiveState = 'off' | 'connecting' | 'live' | 'down';

/** Без пульса дольше этого соединение считается мёртвым (сон ноутбука). */
export const SILENCE_MS = 45_000;
/** Паузы переподключения: 1, 2, 4… до 30 секунд. */
const BACKOFF_MAX_MS = 30_000;

type Probe = () => Promise<'signed-in' | 'signed-out' | 'unknown'>;

const listeners = new Set<(signal: LiveSignal) => void>();
const stateListeners = new Set<(state: LiveState) => void>();

let source: EventSource | null = null;
let state: LiveState = 'off';
let wanted = false;
let opened = false;
let lastBeat = 0;
let attempt = 0;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let watchdog: ReturnType<typeof setInterval> | null = null;
let probe: Probe = async () => 'unknown';
let detach: (() => void) | null = null;

function setState(next: LiveState): void {
  if (state === next) return;
  state = next;
  for (const listener of stateListeners) listener(next);
}

function emit(signal: LiveSignal): void {
  for (const listener of listeners) {
    try {
      listener(signal);
    } catch {
      // Сбой одного подписчика не должен лишать сигнала остальных.
    }
  }
}

/** Подписаться на сигналы. Возвращает отписку. */
export function onLive(listener: (signal: LiveSignal) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Следить за состоянием потока — для запасного опроса. */
export function onLiveState(listener: (state: LiveState) => void): () => void {
  stateListeners.add(listener);
  return () => stateListeners.delete(listener);
}

export function liveState(): LiveState {
  return state;
}

/** Умеет ли браузер поток. В тестах (jsdom) и старых браузерах — нет. */
export function liveSupported(): boolean {
  return typeof EventSource !== 'undefined';
}

/**
 * Открыть поток. Повторный вызов ничего не делает.
 *
 * `check` — проверка сессии на случай, когда поток закрылся: вышедшему
 * пользователю переподключаться незачем, а сети, упавшей на минуту, —
 * нужно.
 */
export function startLive(check?: Probe): void {
  if (check) probe = check;
  if (wanted) return;
  wanted = true;
  if (!liveSupported()) {
    setState('down');
    return;
  }
  attach();
  connect();
}

/** Закрыть поток — выход из системы или нет права на обращения. */
export function stopLive(): void {
  wanted = false;
  opened = false;
  attempt = 0;
  if (retryTimer !== null) clearTimeout(retryTimer);
  retryTimer = null;
  if (watchdog !== null) clearInterval(watchdog);
  watchdog = null;
  detach?.();
  detach = null;
  source?.close();
  source = null;
  setState('off');
}

function connect(): void {
  if (!wanted) return;
  source?.close();
  setState(opened ? 'down' : 'connecting');
  const next = new EventSource(STREAM, { withCredentials: true });
  source = next;

  next.addEventListener('ready', () => {
    if (source !== next) return;
    lastBeat = Date.now();
    attempt = 0;
    const again = opened;
    opened = true;
    setState('live');
    // Переподключились — сигналы за время разрыва потеряны. Страница
    // перечитывает список, счётчики и открытый чат.
    if (again) emit({ type: 'resync' });
  });
  next.addEventListener('ping', () => {
    lastBeat = Date.now();
  });
  next.addEventListener('resync', () => {
    lastBeat = Date.now();
    emit({ type: 'resync' });
  });
  next.addEventListener('question', (event) => {
    lastBeat = Date.now();
    const data = parse((event as MessageEvent).data);
    if (!data || typeof data['id'] !== 'string') return;
    const at = typeof data['at'] === 'number' ? data['at'] : null;
    const signal: LiveSignal = {
      type: 'question',
      id: data['id'],
      what: typeof data['what'] === 'string' ? data['what'] : 'updated',
      at,
      sent: typeof data['sent'] === 'number' ? data['sent'] : null,
    };
    emit(signal);
  });
  next.addEventListener('bye', () => {
    // Сервер закрыл поток: сессия кончилась или право отозвано.
    if (source !== next) return;
    next.close();
    source = null;
    void recheck();
  });
  next.onerror = () => {
    if (source !== next) return;
    if (next.readyState === EventSource.CLOSED) {
      // Браузер сдался (ответ не 200) — дальше переподключаемся сами.
      source = null;
      void recheck();
    } else {
      // Браузер переподключается сам; сторож пульса подстрахует.
      setState('down');
    }
  };
}

async function recheck(): Promise<void> {
  setState('down');
  const verdict = await probe().catch(() => 'unknown' as const);
  if (!wanted) return;
  if (verdict === 'signed-out') {
    stopLive();
    return;
  }
  schedule();
}

function schedule(delay?: number): void {
  if (!wanted || retryTimer !== null) return;
  const wait = delay ?? Math.min(1000 * 2 ** attempt, BACKOFF_MAX_MS);
  attempt += 1;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    connect();
  }, wait);
}

/** Переподключиться сейчас: сеть вернулась, вкладку открыли снова. */
function reconnectNow(): void {
  if (!wanted) return;
  if (retryTimer !== null) clearTimeout(retryTimer);
  retryTimer = null;
  attempt = 0;
  connect();
}

function attach(): void {
  if (detach) return;
  const onOnline = () => {
    if (state !== 'live') reconnectNow();
    emit({ type: 'resync' });
  };
  const onVisible = () => {
    if (document.visibilityState !== 'visible') return;
    // Вкладка снова на экране: пока её не было, браузер мог усыпить
    // соединение. Проверяем поток и перечитываем то, что на экране.
    if (state !== 'live' || Date.now() - lastBeat > SILENCE_MS) reconnectNow();
    emit({ type: 'resync' });
  };
  window.addEventListener('online', onOnline);
  document.addEventListener('visibilitychange', onVisible);
  // Сторож: пульс раз в 15 секунд. Тишина втрое дольше — соединение
  // полумёртвое (сон ноутбука, смена сети), браузер этого не замечает.
  watchdog = setInterval(() => {
    if (state === 'live' && Date.now() - lastBeat > SILENCE_MS) reconnectNow();
  }, 10_000);
  detach = () => {
    window.removeEventListener('online', onOnline);
    document.removeEventListener('visibilitychange', onVisible);
  };
}

function parse(raw: unknown): Record<string, unknown> | null {
  if (typeof raw !== 'string') return null;
  try {
    const value = JSON.parse(raw) as unknown;
    return value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** Для тестов: сбросить всё состояние модуля. */
export function resetLiveForTests(): void {
  // Подписчики не стираются: страницы отписываются сами при размонтировании,
  // а общий счётчик меню подписан один раз на всё время жизни модуля.
  stopLive();
  probe = async () => 'unknown';
}
