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
 */

import { useState } from 'react';

import { api, type ScanResponse } from '../api';
import { time } from '../format';
import {
  SCAN_RESULTS,
  SCAN_UNKNOWN,
  cameraAvailable,
  looksLikeOurCode,
  newAttemptId,
  scanWithTelegram,
} from '../scanner';
import { haptic } from '../telegram';
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
  | { kind: 'working' }
  | { kind: 'done'; result: ScanResponse };

export function Scan({
  timeZone,
  onDone,
  onHome,
}: {
  timeZone: string;
  /** Обновить статус и статистику после прошедшей отметки. */
  onDone: () => void;
  onHome: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [manual, setManual] = useState('');
  const [showManual, setShowManual] = useState(false);

  async function submit(code: string) {
    if (phase.kind === 'working') return; // защита от двойного нажатия
    setPhase({ kind: 'working' });

    const response = await api.scan(code.trim(), newAttemptId());
    if (!response.ok) {
      haptic('error');
      setPhase({ kind: 'idle', error: response.message });
      return;
    }

    setPhase({ kind: 'done', result: response.value });
    haptic(response.value.accepted ? 'success' : 'error');
    if (response.value.accepted) {
      onDone();
    }
  }

  async function startCamera() {
    setPhase({ kind: 'idle' });
    const outcome = await scanWithTelegram();

    if (outcome.kind === 'code') {
      await submit(outcome.value);
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

  const busy = phase.kind === 'working';

  return (
    <>
      <SectionHeader title="Отметка" />
      <p className="muted">
        Наведите камеру на код у входа. Вход это или выход, определит
        сервер — выбирать ничего не нужно.
      </p>

      <div className="scan-frame">
        {busy ? (
          <span className="muted">Отправляем…</span>
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
          {busy ? 'Отправляем…' : 'Открыть камеру'}
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
    </>
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
  const view = SCAN_RESULTS[result.status] ?? SCAN_UNKNOWN;

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
