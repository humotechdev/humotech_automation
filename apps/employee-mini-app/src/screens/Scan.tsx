/**
 * Экран отметки по QR.
 *
 * Камера включается только по нажатию — ни одного запроса к ней при
 * открытии экрана. Ручной ввод не «на всякий случай», а полноценный путь:
 * на устройстве без камеры или с закрытым доступом человек всё равно
 * должен уметь отметиться.
 *
 * Двойная отправка закрыта дважды: кнопка блокируется на время запроса,
 * и каждая попытка несёт свой ключ повтора, по которому сервер узнаёт
 * ту же самую отметку.
 *
 * Код никуда не сохраняется. Ни в состоянии после отправки, ни в
 * `localStorage`: он рабочий секрет ровно полминуты. Отметок «в офлайне»
 * здесь нет и быть не может — решение принимает сервер, а накопить
 * отметки, чтобы отправить позже, означало бы разрешить отметиться
 * задним числом.
 *
 * Отклик телефона — только на итог отметки, прошедшей или отказанной.
 * Это как раз тот случай, когда человек смотрит не на экран, а на дверь.
 *
 * Полноэкранный режим (Bot API 8.0) запрашивается только здесь и больше
 * нигде. Он раздвигает НАШ экран до краёв — саму камеру показывает окно
 * Telegram, которое и так накрывает всё; ни рамки, ни тёмной области с
 * живым изображением у нас нет и рисовать их нельзя, это выглядело бы
 * как включённая камера при выключенной.
 *
 * Запрос ровно один на открытие экрана. Ответ приходит событием, и на
 * `fullscreenFailed` повтора нет: следующая попытка — только после того,
 * как человек снова откроет экран. Иначе отказ превращается в цикл.
 */

import { useEffect, useRef, useState } from 'react';

import { api, type ScanResponse } from '../api';
import { time } from '../format';
import {
  cameraAvailable,
  closeScanner,
  isSticker,
  looksLikeOurCode,
  newAttemptId,
  scanView,
  scanWithTelegram,
} from '../scanner';
import {
  type Position,
  exitFullscreen,
  fullscreenSupported,
  haptic,
  requestFullscreen,
  requestPosition,
} from '../telegram';
import { Field } from '../ui/fields';
import { AlertIcon, CameraIcon, CheckIcon, QrIcon } from '../ui/icons';
import {
  Card,
  PrimaryButton,
  SecondaryButton,
  SectionHeader,
} from '../ui/primitives';

type Phase =
  | { kind: 'idle'; error?: string }
  /** Ждём телефон: печатный код без координат сервер не примет. */
  | { kind: 'locating' }
  | { kind: 'working' }
  | { kind: 'done'; result: ScanResponse };

export function Scan({
  timeZone,
  wide = false,
  onDone,
  onHome,
}: {
  timeZone: string;
  /** Идёт ли полноэкранный режим. Состоянием владеет App: от него же
      зависит, показывать ли нижнюю навигацию. */
  wide?: boolean;
  /** Обновить статус и статистику после прошедшей отметки. */
  onDone: () => void;
  onHome: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [manual, setManual] = useState('');
  const [showManual, setShowManual] = useState(false);

  // Запрошен ли уже режим на этом открытии экрана. Через ссылку, а не
  // через состояние: `fullscreenChanged` перерисовывает экран, и запрос,
  // зависящий от состояния, ушёл бы по кругу. После отказа повтора нет
  // до следующего открытия экрана — то есть до действия человека.
  const asked = useRef(false);
  // Та же причина, что и на экране быстрой отметки: уйти отсюда можно с
  // открытым окном сканера, и подписка на его закрытие должна уйти
  // вместе с экраном, а не ждать ответа, которого уже не будет.
  const leaving = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!asked.current && fullscreenSupported()) {
      asked.current = true;
      requestFullscreen();
    }

    return () => {
      // Единственная точка ухода с экрана: нижняя навигация, кнопка
      // «назад» Telegram, внутренняя кнопка «на главную» — всё это
      // размонтирует экран, потому что App рисует его по условию вкладки.
      leaving.current?.abort();
      closeScanner();
      exitFullscreen();
    };
  }, []);

  /**
   * Отправка отметки. Одна на оба пути — и камеру, и ручной ввод.
   *
   * `locating` — уже запущенный запрос геопозиции. Камера передаёт его
   * сюда, потому что запускает заранее: ждать местоположение ПОСЛЕ
   * сканирования значило бы добавить человеку у двери лишние секунды
   * там, где их можно было потратить, пока он наводит телефон.
   */
  async function submit(code: string, locating?: Promise<Position | null>) {
    if (phase.kind === 'working' || phase.kind === 'locating') return;
    const text = code.trim();

    // Печатный код у двери висит круглосуточно: сам по себе он говорит
    // только «этот стикер существует». Поэтому сервер принимает его
    // исключительно с координатами, и спрашивать их надо здесь — иначе
    // запрос уйдёт заведомо в отказ.
    let position: Position | null = null;
    if (isSticker(text)) {
      setPhase({ kind: 'locating' });
      position = await (locating ?? requestPosition());
      if (!position) {
        haptic('error');
        setPhase({
          kind: 'idle',
          error:
            'Телефон не сообщил, где вы. Печатный код у двери принимается '
            + 'только вместе с местоположением: включите геолокацию, '
            + 'разрешите её Telegram и попробуйте снова.',
        });
        return;
      }
    }

    setPhase({ kind: 'working' });

    const response = await api.scan(text, newAttemptId(), position);
    if (!response.ok) {
      haptic('error');
      setPhase({ kind: 'idle', error: response.message });
      return;
    }

    // Результат показывается ВНУТРИ этого же экрана, размонтирования не
    // происходит, и очистка эффекта не сработает. Поэтому камера и режим
    // закрываются здесь явно — иначе итог отметки читался бы поверх
    // работающей камеры.
    closeScanner();
    exitFullscreen();

    setPhase({ kind: 'done', result: response.value });
    haptic(response.value.accepted ? 'success' : 'error');
    if (response.value.accepted) {
      onDone();
    }
  }

  async function startCamera() {
    setPhase({ kind: 'idle' });

    leaving.current?.abort();
    const stop = new AbortController();
    leaving.current = stop;

    // Геопозиция запрашивается ОДНОВРЕМЕННО с открытием камеры, а не
    // после неё. Пока человек наводит телефон на код, телефон успевает
    // определить место — и к моменту отправки ждать уже нечего.
    const locating = requestPosition();
    const outcome = await scanWithTelegram(window, stop.signal);

    if (outcome.kind === 'code') {
      await submit(outcome.value, locating);
      return;
    }
    if (outcome.kind === 'cancelled') return;

    setShowManual(true);
    setPhase({
      kind: 'idle',
      error:
        outcome.kind === 'denied'
          ? 'Доступ к камере закрыт. Разрешите камеру в настройках Telegram или введите код под QR вручную.'
          : 'Сканер недоступен на этом устройстве. Введите код под QR вручную.',
    });
  }

  if (phase.kind === 'done') {
    return (
      <ScanResult
        result={phase.result}
        timeZone={timeZone}
        onHome={onHome}
        onAgain={() => setPhase({ kind: 'idle' })}
      />
    );
  }

  const busy = phase.kind === 'working' || phase.kind === 'locating';
  // Два разных ожидания подряд: сперва телефон ищет место, потом ответ
  // ждёт сервер. Подписать их одинаково значило бы показать человеку
  // «Отправляем…» в тот момент, когда ещё ничего не отправлено.
  const waiting = phase.kind === 'locating'
    ? 'Определяем, где вы…'
    : 'Отправляем…';

  return (
    <div className={`scan-screen${wide ? ' scan-screen-wide' : ''}`}>
      <SectionHeader title="Отметка" />
      <p className="muted">
        Наведите камеру на код у входа. Вход это или выход, определит
        сервер — выбирать ничего не нужно.
      </p>

      <div className="scan-frame">
        {busy ? (
          <span className="muted">{waiting}</span>
        ) : (
          <QrIcon size={40} />
        )}
      </div>

      {phase.kind === 'idle' && phase.error && (
        <Card>
          <p className="field-error" role="alert">
            {phase.error}
          </p>
        </Card>
      )}

      {cameraAvailable() && (
        <PrimaryButton
          onClick={() => void startCamera()}
          disabled={busy}
          wide
          icon={<CameraIcon size={20} />}
        >
          {busy ? waiting : 'Открыть камеру'}
        </PrimaryButton>
      )}

      {showManual || !cameraAvailable() ? (
        <Card>
          <Field
            label="Код под QR"
            htmlFor="manual-code"
            hint="Шесть строк под кодом на экране у входа."
          >
            <input
              id="manual-code"
              type="text"
              value={manual}
              onChange={(event) => setManual(event.target.value)}
              placeholder="HT1…"
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
          </Field>
          <PrimaryButton
            onClick={() => void submit(manual)}
            disabled={busy || !looksLikeOurCode(manual)}
            wide
          >
            Отметиться
          </PrimaryButton>
        </Card>
      ) : (
        <SecondaryButton onClick={() => setShowManual(true)} disabled={busy} wide>
          Ввести код вручную
        </SecondaryButton>
      )}

      {wide && (
        // Своя кнопка возврата нужна только в полноэкранном режиме: там
        // нижняя навигация скрыта. Это внутреннее возвращение на главную,
        // а не крестик Telegram — крестик закрывает приложение целиком,
        // и подделывать его нельзя.
        <SecondaryButton onClick={onHome} wide>
          На главную
        </SecondaryButton>
      )}
    </div>
  );
}

/**
 * Итог отметки.
 *
 * Зелёного во весь экран нет: достаточно значка и слова. Время — то,
 * которое вернул сервер, а не то, что показывают часы телефона.
 */
export function ScanResult({
  result,
  timeZone,
  onHome,
  onAgain,
}: {
  result: ScanResponse;
  timeZone: string;
  onHome: () => void;
  onAgain: () => void;
}) {
  const view = scanView(result);

  return (
    <div className="scan-result">
      <span className={`scan-mark${result.accepted ? '' : ' scan-mark-no'}`}>
        {result.accepted ? <CheckIcon size={34} /> : <AlertIcon size={34} />}
      </span>

      <p className="scan-title">{view.title}</p>

      {result.occurred_at && (
        <p className="status-duration status-duration-light">
          {time(result.occurred_at, timeZone)}
        </p>
      )}

      {result.office_name && (
        <p className="muted">
          {result.office_name}
          {result.point_name ? `, ${result.point_name}` : ''}
        </p>
      )}

      {view.hint && <p className="state-text">{view.hint}</p>}

      <PrimaryButton onClick={onHome} wide>
        На главную
      </PrimaryButton>
      {!result.accepted && (
        <SecondaryButton onClick={onAgain} wide>
          Сканировать снова
        </SecondaryButton>
      )}
    </div>
  );
}
