/**
 * Быстрая отметка: один экран, три действия человека.
 *
 * Открывается синей кнопкой у поля ввода — нажал, навёл камеру, увидел
 * результат. Ни главного экрана, ни разделов, ни нижней навигации: сюда
 * приходят на пятнадцать секунд дважды в день, и всё, что не относится
 * к отметке, здесь только мешает.
 *
 * Сканер открывается САМ, сразу после того, как сервер подтвердил
 * сотрудника. Кнопки «открыть камеру» тут нет намеренно: человек уже
 * нажал кнопку в чате, и просить его нажать ещё раз — это лишнее
 * действие ровно там, где мы их убираем.
 *
 * Ни одного нового способа сканировать или отмечаться здесь не заведено.
 * Сканер — тот же `scanWithTelegram`, ключ повтора — тот же
 * `newAttemptId`, тексты исходов — тот же `SCAN_RESULTS`, запрос — тот
 * же `/me/attendance/scan`. Отличается только оболочка вокруг них.
 *
 * Полноэкранный режим не запрашивается. На экране отметки в кабинете он
 * нужен, потому что там вокруг есть интерфейс, который в него
 * раскрывается; здесь раскрывать нечего — четыре строки и значок, а
 * лишний переход режима видно рывком при открытии и при закрытии.
 *
 * Что отправляется на сервер: строка кода и идентификатор попытки.
 * Больше ничего. Ни сотрудника, ни офиса, ни направления, ни времени —
 * таких параметров у запроса просто нет, и подделать их поэтому негде.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { api, type ScanResponse } from '../api';
import { duration, time } from '../format';
import {
  SCAN_RESULTS,
  SCAN_UNKNOWN,
  closeScanner,
  nativeScannerReady,
  newAttemptId,
  scanWithTelegram,
} from '../scanner';
import { closeApp, haptic } from '../telegram';
import { AlertIcon, CheckIcon, QrIcon } from '../ui/icons';
import { PrimaryButton, SecondaryButton } from '../ui/primitives';

/**
 * Сколько окно живёт после удачной отметки.
 *
 * Достаточно, чтобы прочитать время и офис, и мало, чтобы не держать
 * человека у двери. Закрывается только УДАЧНАЯ отметка: отказ надо
 * прочитать и решить, что делать, а исчезающий отказ выглядит так,
 * будто всё прошло.
 */
export const CLOSE_AFTER_MS = 1800;

type Phase =
  | { kind: 'opening' }
  | { kind: 'sending' }
  | { kind: 'done'; result: ScanResponse }
  | { kind: 'failed'; message: string }
  /** Клиент слишком старый: своего сканера у него нет. */
  | { kind: 'unsupported' };

export function QuickScan({ onOpenCabinet }: { onOpenCabinet: () => void }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'opening' });
  const [timeZone, setTimeZone] = useState<string | null>(null);

  // Замок на весь путь «скан -> запрос -> ответ». Через ссылку, а не
  // через состояние: сканер Telegram отдаёт кадры подряд, и два вызова
  // успевают попасть в один кадр отрисовки, где состояние ещё прежнее.
  // Две отметки подряд — это вход и мгновенный выход из него.
  const busy = useRef(false);
  const alive = useRef(true);
  // Уход с экрана обрывает ожидание сканера. Иначе подписка на закрытие
  // окна остаётся висеть: снимается она только вместе с ответом, а
  // ответа при открытом окне ещё нет.
  const leaving = useRef<AbortController | null>(null);

  const scan = useCallback(async () => {
    if (busy.current) return;
    if (!nativeScannerReady()) {
      setPhase({ kind: 'unsupported' });
      return;
    }
    busy.current = true;
    setPhase({ kind: 'opening' });

    leaving.current?.abort();
    const stop = new AbortController();
    leaving.current = stop;

    const outcome = await scanWithTelegram(window, stop.signal);
    if (!alive.current) return;

    if (outcome.kind !== 'code') {
      busy.current = false;
      // Закрыл окно сам — это не ошибка и не повод чем-то мигать.
      setPhase(
        outcome.kind === 'cancelled'
          ? { kind: 'failed', message: 'Сканирование отменено.' }
          : {
              kind: 'failed',
              message:
                outcome.kind === 'denied'
                  ? 'Telegram не пустил к камере. Разрешите камеру в настройках Telegram и попробуйте снова.'
                  : 'Сканер не открылся. Попробуйте ещё раз.',
            },
      );
      return;
    }

    setPhase({ kind: 'sending' });
    const response = await api.scan(outcome.value, newAttemptId());
    if (!alive.current) return;

    // Окно сканера живёт своей жизнью и камеру держит оно. Результат
    // показывается на этом же экране, размонтирования не происходит —
    // значит, закрыть окно надо явно, иначе итог читается поверх
    // работающей камеры.
    closeScanner();
    busy.current = false;

    if (!response.ok) {
      haptic('error');
      setPhase({ kind: 'failed', message: response.message });
      return;
    }
    haptic(response.value.accepted ? 'success' : 'error');
    setPhase({ kind: 'done', result: response.value });
  }, []);

  useEffect(() => {
    alive.current = true;
    // Пояс офиса нужен, чтобы показать время отметки так, как его видит
    // сотрудник. Запрос идёт ПАРАЛЛЕЛЬНО открытию сканера, а не до
    // него: ждать ответа сервера, чтобы включить камеру, значило бы
    // добавить секунду ровно туда, откуда её убирали.
    void api.status().then((result) => {
      if (alive.current && result.ok) setTimeZone(result.value.timezone);
    });
    void scan();

    return () => {
      alive.current = false;
      leaving.current?.abort();
      closeScanner();
    };
  }, [scan]);

  // Закрытие после удачной отметки. Отдельным эффектом, чтобы таймер
  // снимался при уходе с экрана: сработавший после закрытия таймер
  // закрыл бы уже кабинет, куда человек успел перейти.
  useEffect(() => {
    if (phase.kind !== 'done' || !phase.result.accepted) return;
    const timer = setTimeout(closeApp, CLOSE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [phase]);

  return (
    <div className="quick-scan">
      <p className="quick-scan-brand">HUMOTECH</p>
      <div className="quick-scan-body">{body()}</div>
    </div>
  );

  function body() {
    if (phase.kind === 'opening' || phase.kind === 'sending') {
      return (
        <>
          <span className="quick-scan-mark" role="status">
            <QrIcon size={34} />
          </span>
          <p className="quick-scan-title">
            {phase.kind === 'opening' ? 'Открываем сканер…' : 'Проверяем код…'}
          </p>
          <p className="state-text">
            Наведите камеру на код у входа. Вход это или уход, определит
            сервер — выбирать ничего не нужно.
          </p>
        </>
      );
    }

    if (phase.kind === 'unsupported') {
      return (
        <>
          <span className="quick-scan-mark quick-scan-mark-no">
            <AlertIcon size={34} />
          </span>
          <p className="quick-scan-title">Этот Telegram не умеет сканировать</p>
          <p className="state-text">
            Обновите Telegram — или откройте кабинет: на экране отметки
            есть другие способы.
          </p>
          <SecondaryButton onClick={onOpenCabinet} wide>
            Открыть кабинет
          </SecondaryButton>
        </>
      );
    }

    if (phase.kind === 'failed') {
      return (
        <>
          <span className="quick-scan-mark quick-scan-mark-no">
            <AlertIcon size={34} />
          </span>
          <p className="quick-scan-title">Не отметились</p>
          <p className="state-text" role="alert">
            {phase.message}
          </p>
          <PrimaryButton onClick={() => void scan()} wide>
            Сканировать повторно
          </PrimaryButton>
          <SecondaryButton onClick={onOpenCabinet} wide>
            Открыть кабинет
          </SecondaryButton>
        </>
      );
    }

    const { result } = phase;
    const view = SCAN_RESULTS[result.status] ?? SCAN_UNKNOWN;
    const worked = result.accepted;
    const closed = result.session?.status === 'CLOSED';

    return (
      <>
        <span className={`quick-scan-mark${worked ? '' : ' quick-scan-mark-no'}`}>
          {worked ? <CheckIcon size={34} /> : <AlertIcon size={34} />}
        </span>
        <p className="quick-scan-title">{view.title}</p>

        {/* Время только из ответа сервера и только в поясе офиса. Часы
            телефона сюда не попадают: они у всех свои. */}
        {result.occurred_at && timeZone && (
          <p className="quick-scan-time">{time(result.occurred_at, timeZone)}</p>
        )}

        {result.office_name && (
          <p className="muted">
            {result.office_name}
            {result.point_name ? `, ${result.point_name}` : ''}
          </p>
        )}

        {closed && result.session?.duration_seconds != null && (
          <p className="state-text">
            Сегодня в офисе: {duration(result.session.duration_seconds)}
          </p>
        )}

        {view.hint && <p className="state-text">{view.hint}</p>}

        {!worked && (
          <PrimaryButton onClick={() => void scan()} wide>
            Сканировать повторно
          </PrimaryButton>
        )}
        <SecondaryButton onClick={onOpenCabinet} wide>
          Открыть кабинет
        </SecondaryButton>
      </>
    );
  }
}
