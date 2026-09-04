/**
 * Экран отметки по QR.
 *
 * Камера включается только по нажатию — ни одного запроса к ней при
 * открытии экрана. Ручной ввод не «на всякий случай», а полноценный
 * путь: на устройстве без камеры или с закрытым доступом человек всё
 * равно должен уметь отметиться.
 *
 * Двойная отправка закрыта двумя способами сразу: кнопка блокируется
 * на время запроса, и каждая попытка несёт свой ключ повтора, по
 * которому сервер узнаёт ту же самую отметку.
 *
 * Код никуда не сохраняется. Ни в состоянии после отправки, ни в
 * `localStorage`: он рабочий секрет ровно полминуты, и лишняя его копия
 * ни к чему. Отметки «в офлайне» здесь нет и быть не может — решение
 * принимает сервер, и накопить отметки, чтобы отправить их позже,
 * означало бы позволить отметиться задним числом.
 */

import { useState } from 'react';

import { api, type ScanResponse } from '../api';
import {
  SCAN_RESULTS,
  SCAN_UNKNOWN,
  cameraAvailable,
  looksLikeOurCode,
  newAttemptId,
  scanWithTelegram,
} from '../scanner';

type Phase =
  | { kind: 'idle'; error?: string }
  | { kind: 'working' }
  | { kind: 'done'; result: ScanResponse };

export function Scan({ onDone, onBack }: { onDone: () => void; onBack: () => void }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [manual, setManual] = useState('');
  const [showManual, setShowManual] = useState(false);

  async function submit(code: string) {
    if (phase.kind === 'working') return;   // защита от двойного нажатия
    setPhase({ kind: 'working' });

    const response = await api.scan(code.trim(), newAttemptId());
    if (!response.ok) {
      setPhase({ kind: 'idle', error: response.message });
      return;
    }

    setPhase({ kind: 'done', result: response.value });
    if (response.value.accepted) {
      // Статус и статистика обновляются сразу: человек должен увидеть
      // результат своей отметки, а не прежние цифры.
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
    // Камеры нет или доступ закрыт — предлагаем ввести код руками.
    setShowManual(true);
    setPhase({
      kind: 'idle',
      error:
        outcome.kind === 'denied'
          ? 'Доступ к камере закрыт. Введите код под QR вручную.'
          : 'Сканер недоступен на этом устройстве. Введите код вручную.',
    });
  }

  if (phase.kind === 'done') {
    const view = SCAN_RESULTS[phase.result.status] ?? SCAN_UNKNOWN;
    return (
      <div className="stack">
        <section className={`card result ${phase.result.accepted ? 'ok' : 'no'}`}>
          <h2>{view.title}</h2>
          {phase.result.office_name && (
            <p className="muted">
              {phase.result.office_name}
              {phase.result.point_name ? `, ${phase.result.point_name}` : ''}
            </p>
          )}
          {view.hint && <p className="muted">{view.hint}</p>}
        </section>
        <button type="button" className="primary" onClick={onBack}>
          Готово
        </button>
        {!phase.result.accepted && (
          <button type="button" onClick={() => setPhase({ kind: 'idle' })}>
            Попробовать снова
          </button>
        )}
      </div>
    );
  }

  const busy = phase.kind === 'working';

  return (
    <div className="stack">
      <section className="card">
        <h2>Отметка</h2>
        <p className="muted">
          Наведите камеру на код у входа. Вход это или выход, определит
          сервер — выбирать ничего не нужно.
        </p>
      </section>

      {busy && <p className="waiting">Отправляем…</p>}

      {phase.kind === 'idle' && phase.error && (
        <p className="error">{phase.error}</p>
      )}

      {cameraAvailable() && (
        <button
          type="button"
          className="primary big"
          onClick={() => void startCamera()}
          disabled={busy}
        >
          Открыть сканер
        </button>
      )}

      {showManual || !cameraAvailable() ? (
        <section className="card">
          <label htmlFor="code">Код под QR</label>
          <input
            id="code"
            type="text"
            value={manual}
            onChange={(event) => setManual(event.target.value)}
            placeholder="HT1…"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
          />
          <button
            type="button"
            className="primary"
            disabled={busy || !looksLikeOurCode(manual)}
            onClick={() => void submit(manual)}
          >
            Отметиться
          </button>
        </section>
      ) : (
        <button type="button" onClick={() => setShowManual(true)} disabled={busy}>
          Ввести код вручную
        </button>
      )}

      <button type="button" onClick={onBack} disabled={busy}>
        Назад
      </button>
    </div>
  );
}
