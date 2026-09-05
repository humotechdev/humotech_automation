// @vitest-environment jsdom
/**
 * Отметка из нижней кнопки Telegram — режим без сессии Mini App.
 *
 * Главное свойство этого экрана: он не ходит на сервер вовсе. Ни за
 * авторизацией, ни за отметкой. Подписи запуска у такого Mini App нет,
 * доказать серверу личность оттуда нечем, и попытка была бы обращением
 * с заведомо пустыми руками.
 *
 * Второе по важности: что именно уходит боту. Разрешённый список полей,
 * и ни одного сверх него — ни сотрудника, ни офиса, ни организации, ни
 * направления, ни времени.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App, { isKeyboardScan, KEYBOARD_SOURCE } from '../src/App';
import { forgetToken } from '../src/auth';
import { buildPayload, MAX_PAYLOAD_BYTES } from '../src/screens/KeyboardScan';
import { profile as profileStub, session, status as statusStub } from './fixtures';

afterEach(() => {
  cleanup();
  forgetToken();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as unknown as Record<string, unknown>).Telegram;
  window.history.replaceState({}, '', '/');
});

beforeEach(() => {
  forgetToken();
});

// --- признак режима ---------------------------------------------------------

describe('признак режима', () => {
  it('включается только на /scan и только с меткой транспорта', () => {
    expect(isKeyboardScan('/scan', '?source=keyboard')).toBe(true);
    expect(isKeyboardScan('/scan/', '?source=keyboard')).toBe(true);
    expect(isKeyboardScan('/scan', '')).toBe(false);
    expect(isKeyboardScan('/', '?source=keyboard')).toBe(false);
    expect(isKeyboardScan('/scan', '?source=inline')).toBe(false);
    expect(KEYBOARD_SOURCE).toBe('keyboard');
  });
});

// --- ни одного запроса ------------------------------------------------------

describe('на сервер отсюда не ходят', () => {
  it('не спрашивает авторизацию и не шлёт отметку', async () => {
    const tg = openTelegram();
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl);
    atKeyboardRoute();

    render(<App />);
    await waitFor(() => expect(tg.show).toHaveBeenCalled());
    tg.emit('HT1.код-с-экрана');
    await waitFor(() => expect(tg.sendData).toHaveBeenCalled());

    // Ни /telegram/mini-app/auth, ни /me/attendance/scan — вообще ничего.
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('не показывает «откройте кнопкой в чате»', async () => {
    // Подписи здесь нет по устройству, и жаловаться на её отсутствие
    // значило бы объявить сломанным нормальный путь.
    openTelegram();
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);

    await screen.findByText('Получаем геопозицию…');
    expect(screen.queryByText('Откройте кнопкой в чате')).toBeNull();
  });
});

// --- порядок действий -------------------------------------------------------

describe('порядок', () => {
  it('сначала геопозиция, потом сканер', async () => {
    const tg = openTelegram({ slowLocation: true });
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);

    await screen.findByText('Получаем геопозицию…');
    expect(tg.show).not.toHaveBeenCalled();

    tg.resolveLocation();
    await waitFor(() => expect(tg.show).toHaveBeenCalled());
  });

  it('без геопозиции сканер не открывается', async () => {
    const tg = openTelegram({ location: null });
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);

    await screen.findByText('Не видно, где вы');
    expect(tg.show).not.toHaveBeenCalled();
    expect(tg.sendData).not.toHaveBeenCalled();
  });

  it('старый клиент без sendData объясняет это словами', async () => {
    openTelegram({ withSendData: false });
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);

    await screen.findByText('Этот Telegram так не умеет');
    expect(
      screen.getByRole('button', { name: 'Открыть кабинет' }),
    ).toBeTruthy();
  });

  it('клиент без сканера тоже не остаётся ни с чем', async () => {
    openTelegram({ withScanner: false });
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);

    await screen.findByText('Этот Telegram не умеет сканировать');
  });

  it('в обычном браузере отметиться нечем и некому', async () => {
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl);
    atKeyboardRoute();

    render(<App />);

    await screen.findByText('Откройте кнопкой в чате');
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

// --- что уходит боту --------------------------------------------------------

describe('данные боту', () => {
  it('отправляются ровно один раз, сколько бы кадров ни пришло', async () => {
    const tg = openTelegram();
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);
    await waitFor(() => expect(tg.show).toHaveBeenCalled());

    tg.emit('HT1.код-с-экрана');
    tg.emit('HT1.код-с-экрана');
    tg.emit('HT1.код-с-экрана');

    await waitFor(() => expect(tg.sendData).toHaveBeenCalledTimes(1));
  });

  it('содержит только разрешённые поля', async () => {
    const tg = openTelegram();
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);
    await waitFor(() => expect(tg.show).toHaveBeenCalled());
    tg.emit('HT1.код-с-экрана');
    await waitFor(() => expect(tg.sendData).toHaveBeenCalled());

    const body = JSON.parse(tg.sendData.mock.calls[0][0] as string);
    expect(Object.keys(body).sort()).toEqual([
      'action', 'client_event_id', 'location', 'qr', 'version',
    ]);
    expect(Object.keys(body.location).sort()).toEqual([
      'accuracy', 'latitude', 'longitude',
    ]);
    expect(body.version).toBe(1);
    expect(body.action).toBe('attendance_scan');
    expect(body.qr).toBe('HT1.код-с-экрана');
  });

  it('не содержит ничего, чем можно было бы представиться другим', async () => {
    const tg = openTelegram();
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);
    await waitFor(() => expect(tg.show).toHaveBeenCalled());
    tg.emit('HT1.код-с-экрана');
    await waitFor(() => expect(tg.sendData).toHaveBeenCalled());

    const raw = tg.sendData.mock.calls[0][0] as string;
    for (const forbidden of [
      'employee_id', 'organization_id', 'office_id', 'telegram_user_id',
      'direction', 'event_type', 'occurred_at', 'token',
    ]) {
      expect(raw).not.toContain(forbidden);
    }
  });

  it('чужой код в кадре не отправляется', async () => {
    const tg = openTelegram();
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);
    await waitFor(() => expect(tg.show).toHaveBeenCalled());

    expect(tg.emit('https://посторонний.qr')).toBe(false);
    expect(tg.sendData).not.toHaveBeenCalled();
  });

  it('слишком длинный код не собирается в payload', () => {
    const position = { latitude: 38.56, longitude: 68.78, accuracy: 15 };

    expect(buildPayload('x'.repeat(5000), 'a1', position)).toBeNull();
    expect(buildPayload('   ', 'a1', position)).toBeNull();
  });

  it('payload помещается в предел Telegram', () => {
    const built = buildPayload('HT1.' + 'x'.repeat(400), 'a1', {
      latitude: 38.559772, longitude: 68.787038, accuracy: 15.5,
    });

    expect(built).not.toBeNull();
    expect(new TextEncoder().encode(built!).length).toBeLessThanOrEqual(
      MAX_PAYLOAD_BYTES,
    );
  });

  it('ложного успеха не показывает', async () => {
    // Решает сервер, и ответ придёт сообщением бота. «Отметка прошла»
    // на этом экране было бы обещанием, которого мы не знаем.
    const tg = openTelegram();
    vi.stubGlobal('fetch', vi.fn());
    atKeyboardRoute();

    render(<App />);
    await waitFor(() => expect(tg.show).toHaveBeenCalled());
    tg.emit('HT1.код-с-экрана');

    await screen.findByText('Отправлено');
    expect(screen.queryByText(/Приход отмечен|Вход отмечен/)).toBeNull();
    expect(screen.getByText(/Результат придёт сообщением/)).toBeTruthy();
  });
});

// --- обычный режим не тронут ------------------------------------------------

describe('обычный режим', () => {
  it('на /scan без метки по-прежнему авторизуется и шлёт отметку', async () => {
    // Здесь подпись есть: экран открыт inline-кнопкой или синей кнопкой.
    const tg = openTelegram({ initData: 'подписанная-строка' });
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string | URL | Request) => {
        const address = String(url);
        calls.push(address);
        if (address.includes('/telegram/mini-app/auth')) {
          return json({ access_token: 'токен', employee: profileStub.employee });
        }
        if (address.includes('/me/attendance/scan')) {
          return json({
            status: 'ENTERED', accepted: true,
            office_name: 'Ташкентский офис', point_name: 'Главный вход',
            occurred_at: '2026-09-05T04:03:00Z', session: null,
          });
        }
        if (address.includes('/me/status')) {
          return json(statusStub({ open_session: session() }));
        }
        return json({});
      }),
    );
    window.history.replaceState({}, '', '/scan');

    render(<App />);
    await waitFor(() => expect(tg.show).toHaveBeenCalled());
    tg.emit('HT1.код-с-экрана');

    await screen.findByText('Вход отмечен');
    expect(calls.some((url) => url.includes('/telegram/mini-app/auth'))).toBe(true);
    expect(calls.some((url) => url.includes('/me/attendance/scan'))).toBe(true);
    // И боту в этом режиме ничего не отправляется.
    expect(tg.sendData).not.toHaveBeenCalled();
  });
});

// --- вспомогательное --------------------------------------------------------

function atKeyboardRoute(): void {
  window.history.replaceState({}, '', '/scan?source=keyboard');
}

function openTelegram({
  withScanner = true,
  withSendData = true,
  location = { latitude: 38.5602, longitude: 68.7874, horizontal_accuracy: 15 },
  slowLocation = false,
  initData = '',
}: {
  withScanner?: boolean;
  withSendData?: boolean;
  location?: Record<string, number> | null;
  slowLocation?: boolean;
  /** Пустая строка — запуск нижней кнопкой: подписи там нет. */
  initData?: string;
} = {}) {
  const show = vi.fn();
  const sendData = vi.fn();
  let received: ((text: string) => boolean | void) | null = null;
  let release: (() => void) | null = null;

  show.mockImplementation(
    (_params: unknown, callback: (text: string) => boolean | void) => {
      received = callback;
    },
  );

  const app: Record<string, unknown> = {
    initData,
    ready: vi.fn(),
    expand: vi.fn(),
    version: '8.0',
    isVersionAtLeast: (want: string) => Number.parseFloat(want) <= 8.0,
    onEvent: vi.fn(),
    offEvent: vi.fn(),
    close: vi.fn(),
    LocationManager: {
      isInited: true,
      getLocation: (callback: (value: unknown) => void) => {
        if (slowLocation) {
          release = () => callback(location);
          return;
        }
        callback(location);
      },
    },
  };
  if (withScanner) {
    app.showScanQrPopup = show;
    app.closeScanQrPopup = vi.fn();
  }
  if (withSendData) app.sendData = sendData;

  (window as unknown as Record<string, unknown>).Telegram = { WebApp: app };

  return {
    show,
    sendData,
    emit: (text: string) => received?.(text),
    resolveLocation: () => release?.(),
  };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}
