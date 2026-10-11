/**
 * Отметка из нижней кнопки Telegram — без сессии Mini App.
 *
 * Зачем отдельный режим. Кнопка нижней клавиатуры не даёт приложению
 * подписи запуска: по документации Bot API такой Mini App умеет только
 * `sendData`. Доказать серверу, кто пришёл, отсюда нечем — значит,
 * и пытаться не надо: экран не ходит ни в `/telegram/mini-app/auth`,
 * ни в `/me/attendance/scan` и вообще не делает ни одного запроса.
 *
 * Кто пришёл, решает бот. Telegram присылает ему служебное сообщение с
 * настоящим `message.from.id` — тем самым, который подтвердил сам
 * Telegram, а не тем, что написали в теле. Бот идёт в backend прежним
 * путём: общий секрет плюс подтверждённый Telegram ID.
 *
 * Что уходит боту: строка кода, ключ попытки и координаты. Всё. Ни
 * сотрудника, ни офиса, ни организации, ни направления, ни времени —
 * таких полей в разрешённом списке нет, и лишнее бот отвергает целиком,
 * а не игнорирует молча.
 *
 * Ложного успеха здесь быть не может по устройству: `sendData` закрывает
 * приложение, и показывать после него нечего. Итог приходит сообщением
 * бота, когда его подтвердил сервер.
 *
 * `source=keyboard` — не авторизация. Он только переключает транспорт, и
 * доказательством личности не является ни в какой момент.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  closeScanner,
  isSticker,
  nativeScannerReady,
  newAttemptId,
  scanWithTelegram,
} from '../scanner';
import {
  FIX_PROBLEMS,
  canSendData,
  fixAfterScan,
  haptic,
  locate,
  sendToBot,
  webApp,
  type Position,
} from '../telegram';
import { AlertIcon, CheckIcon, QrIcon } from '../ui/icons';
import { PrimaryButton, SecondaryButton } from '../ui/primitives';

/** Версия формата. Бот принимает ровно её и отвергает всё остальное. */
export const PAYLOAD_VERSION = 1;
export const PAYLOAD_ACTION = 'attendance_scan';

/** Потолок из Bot API. Больше Telegram и сам не отправит. */
export const MAX_PAYLOAD_BYTES = 4096;

/**
 * Сколько ждать место ПОСЛЕ скана кода с экрана.
 *
 * Код на экране живёт тридцать секунд (`QR_TOKEN_TTL_SECONDS`) с момента
 * выпуска, и к скану часть срока уже прошла. Восемь секунд ожидания
 * плюс дорога через бота укладываются в остаток; дольше — и верная
 * отметка вернулась бы отказом «код устарел». У печатного кода срока
 * нет, и ждать можно столько же, сколько везде.
 */
export const DYNAMIC_CODE_LOCATE_MS = 8_000;
export const STICKER_LOCATE_MS = 12_000;

type Phase =
  | { kind: 'locating' }
  | { kind: 'scanning' }
  | { kind: 'sending' }
  | { kind: 'sent' }
  | { kind: 'failed'; title: string; message: string };

export function KeyboardScan({ onOpenCabinet }: { onOpenCabinet: () => void }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'scanning' });

  // Одна попытка — один ключ. Он переживает повтор внутри этой же
  // попытки: сервер по нему узнаёт ту же отправку и не создаёт вторую
  // отметку. Через ссылку, а не через состояние: между кадрами
  // отрисовки его значение меняться не должно.
  const attempt = useRef<string>(newAttemptId());
  const sent = useRef(false);
  const alive = useRef(true);
  const leaving = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    if (sent.current) return;

    if (!canSendData()) {
      setPhase({
        kind: 'failed',
        title: 'Этот Telegram так не умеет',
        message:
          'Обновите Telegram или откройте кабинет — там отметка работает по-другому.',
      });
      return;
    }
    if (!nativeScannerReady()) {
      setPhase({
        kind: 'failed',
        title: 'Этот Telegram не умеет сканировать',
        message: 'Обновите Telegram или откройте кабинет.',
      });
      return;
    }

    // Место определяется ВМЕСТЕ со сканом, а не до него. Раньше его
    // спрашивали до камеры и отправляли после: человек мог получить
    // точку у парковки, дойти до двери за минуту — и сервер сравнивал
    // с офисом место, где его уже не было.
    setPhase({ kind: 'scanning' });
    leaving.current?.abort();
    const stop = new AbortController();
    leaving.current = stop;

    const locating = locate();
    const outcome = await scanWithTelegram(window, stop.signal);
    const scannedAt = Date.now();
    if (!alive.current) return;

    if (outcome.kind !== 'code') {
      setPhase({
        kind: 'failed',
        title: 'Не отсканировали',
        message:
          outcome.kind === 'cancelled'
            ? 'Сканирование отменено.'
            : 'Сканер не открылся. Попробуйте ещё раз.',
      });
      return;
    }

    closeScanner();
    setPhase({ kind: 'locating' });
    // Точка — полученная во время скана, если она ещё свежая, иначе
    // новая, уже после него. Ожидание ограничено сроком кода с экрана.
    const fix = await fixAfterScan(locating, scannedAt, {
      timeoutMs: isSticker(outcome.value) ? STICKER_LOCATE_MS : DYNAMIC_CODE_LOCATE_MS,
    });
    if (!alive.current) return;

    if (!fix.ok) {
      setPhase({
        kind: 'failed',
        title: FIX_PROBLEMS[fix.problem].title,
        message:
          fix.problem === 'unavailable'
            ? 'Разрешите Telegram доступ к местоположению и попробуйте снова. Без него отметка из этой кнопки не отправляется.'
            : FIX_PROBLEMS[fix.problem].message,
      });
      return;
    }

    setPhase({ kind: 'sending' });
    const payload = buildPayload(outcome.value, attempt.current, fix.position);
    if (payload === null) {
      setPhase({
        kind: 'failed',
        title: 'Код не подошёл',
        message: 'Отсканируйте код на экране у входа.',
      });
      return;
    }

    // Ровно один раз на попытку. Второй вызов Telegram отправил бы
    // вторым служебным сообщением, и бот сходил бы в backend дважды.
    sent.current = true;
    haptic('success');
    if (!sendToBot(payload)) {
      sent.current = false;
      setPhase({
        kind: 'failed',
        title: 'Не отправилось',
        message: 'Попробуйте ещё раз или откройте кабинет.',
      });
      return;
    }
    // Telegram закрывает приложение сам. Этот экран человек, скорее
    // всего, не увидит — он на случай, если закрытие задержалось.
    setPhase({ kind: 'sent' });
  }, []);

  useEffect(() => {
    alive.current = true;
    void run();
    return () => {
      alive.current = false;
      leaving.current?.abort();
      closeScanner();
    };
  }, [run]);

  function again() {
    // Новая попытка — новый ключ: это другая отметка, а не повтор той же.
    attempt.current = newAttemptId();
    sent.current = false;
    void run();
  }

  return (
    <div className="quick-scan">
      <p className="quick-scan-brand">HUMOTECH</p>
      <div className="quick-scan-body">{body()}</div>
    </div>
  );

  function body() {
    if (phase.kind === 'failed') {
      return (
        <>
          <span className="quick-scan-mark quick-scan-mark-no">
            <AlertIcon size={34} />
          </span>
          <p className="quick-scan-title">{phase.title}</p>
          <p className="state-text" role="alert">
            {phase.message}
          </p>
          <PrimaryButton onClick={again} wide>
            Попробовать снова
          </PrimaryButton>
          <SecondaryButton onClick={onOpenCabinet} wide>
            Открыть кабинет
          </SecondaryButton>
        </>
      );
    }

    if (phase.kind === 'sent') {
      return (
        <>
          <span className="quick-scan-mark">
            <CheckIcon size={34} />
          </span>
          {/* Не «отметка прошла»: решает сервер, и ответ придёт в чат. */}
          <p className="quick-scan-title">Отправлено</p>
          <p className="state-text">Результат придёт сообщением от бота.</p>
        </>
      );
    }

    return (
      <>
        <span className="quick-scan-mark" role="status">
          <QrIcon size={34} />
        </span>
        <p className="quick-scan-title">{TITLES[phase.kind]}</p>
        <p className="state-text">
          {phase.kind === 'locating'
            ? 'Нужно подтвердить, что вы в офисе.'
            : 'Вход это или уход, определит сервер — выбирать ничего не нужно.'}
        </p>
      </>
    );
  }
}

const TITLES: Record<'locating' | 'scanning' | 'sending', string> = {
  locating: 'Получаем геопозицию…',
  scanning: 'Наведите камеру на QR-код',
  sending: 'Отправляем…',
};

/**
 * Строгий разрешённый список. Ни одного поля сверх него.
 *
 * `employee_id`, `organization_id`, `office_id`, `telegram_user_id`,
 * направление и время сюда не попадают не потому, что мы их вычищаем, а
 * потому что собирать их здесь нечем: этот код — единственное место,
 * где payload появляется на свет.
 */
export function buildPayload(
  qr: string,
  clientEventId: string,
  position: Pick<Position, 'latitude' | 'longitude' | 'accuracy'>,
): string | null {
  const code = qr.trim();
  if (!code || code.length > 512) return null;

  const payload = {
    version: PAYLOAD_VERSION,
    action: PAYLOAD_ACTION,
    qr: code,
    client_event_id: clientEventId,
    location: {
      latitude: round(position.latitude, 6),
      longitude: round(position.longitude, 6),
      accuracy: round(position.accuracy, 2),
    },
  };

  const text = JSON.stringify(payload);
  // Лишний байт Telegram отбросит молча, и бот получит обрезанный JSON.
  return new TextEncoder().encode(text).length > MAX_PAYLOAD_BYTES ? null : text;
}

function round(value: number, places: number): number {
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
}

/** Открыт ли экран внутри Telegram вообще. */
export function insideTelegram(source: Window = window): boolean {
  return webApp(source) !== null;
}
