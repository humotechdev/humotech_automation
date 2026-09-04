/**
 * Экран показа QR в офисе.
 *
 * Висит на стене и работает без человека: включили питание — через секунду
 * показывает код. Отсюда всё оформление: крупно, контрастно, ничего
 * лишнего. Читать его будут с полутора метров, наводя телефон.
 *
 * Персональных данных на экране нет и быть не может. Мимо него ходят
 * посетители; из офиса он знает только название — чтобы человек убедился,
 * что подошёл к своей двери, а не к соседней.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  fetchCode,
  forgetCredential,
  isFresh,
  loadCredential,
  pair,
  refreshDelay,
  retryDelay,
  saveCredential,
  type DisplayState,
} from './display';
import { QrCanvas } from './QrCanvas';

const API_URL = import.meta.env.VITE_API_URL ?? '/api/v1';

export default function App() {
  const [state, setState] = useState<DisplayState>(() =>
    loadCredential() ? { kind: 'starting' } : { kind: 'pairing' },
  );
  const timer = useRef<number | undefined>(undefined);
  const attempts = useRef(0);

  const schedule = useCallback((delay: number, run: () => void) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(run, delay);
  }, []);

  const poll = useCallback(async () => {
    const credential = loadCredential();
    if (!credential) {
      setState({ kind: 'pairing' });
      return;
    }

    const result = await fetchCode(credential, { apiUrl: API_URL });

    if (result.kind === 'revoked') {
      // Доступ снят. Старый код с экрана убираем сразу: он проработает
      // ещё полминуты и пропустит человека туда, куда уже нельзя.
      forgetCredential();
      setState({ kind: 'revoked' });
      return;
    }

    if (result.kind === 'offline') {
      attempts.current += 1;
      // Прежний код остаётся на экране, пока он действует: обрыв связи
      // на десять секунд не повод гасить работающий код.
      setState((current) =>
        current.kind === 'showing' && isFresh(current.code, Date.now())
          ? { ...current, online: false }
          : { kind: 'starting' },
      );
      schedule(retryDelay(attempts.current), () => void poll());
      return;
    }

    attempts.current = 0;
    setState({ kind: 'showing', code: result.code, online: true });
    schedule(refreshDelay(result.code, Date.now()), () => void poll());
  }, [schedule]);

  useEffect(() => {
    if (state.kind === 'pairing' || state.kind === 'revoked') return;
    void poll();
    return () => window.clearTimeout(timer.current);
    // Запускается один раз при переходе в рабочий режим: дальше цикл
    // поддерживает сам себя таймером.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.kind === 'pairing' || state.kind === 'revoked']);

  if (state.kind === 'pairing' || state.kind === 'revoked') {
    return (
      <PairingScreen
        revoked={state.kind === 'revoked'}
        onPaired={() => {
          attempts.current = 0;
          setState({ kind: 'starting' });
        }}
      />
    );
  }

  if (state.kind === 'starting') {
    return (
      <main className="screen">
        <p className="waiting">Получаем код…</p>
      </main>
    );
  }

  const { code, online } = state;
  return (
    <main className="screen">
      <header className="place">
        <h1>{code.officeName}</h1>
        <p>{code.pointName}</p>
      </header>

      <QrCanvas value={code.token} />

      <footer className={online ? 'link ok' : 'link lost'}>
        {online ? 'Код обновляется автоматически' : 'Нет связи с сервером'}
      </footer>
    </main>
  );
}

function PairingScreen({
  revoked,
  onPaired,
}: {
  revoked: boolean;
  onPaired: () => void;
}) {
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!code.trim() || busy) return;

    setBusy(true);
    setError(null);
    const result = await pair(code, { apiUrl: API_URL });
    setBusy(false);

    if (!result.ok) {
      setError(result.message);
      return;
    }
    saveCredential(result.credential);
    setCode('');
    onPaired();
  }

  return (
    <main className="screen pairing">
      <h1>Экран не подключён</h1>
      <p className="hint">
        {revoked
          ? 'Доступ этого экрана снят. Попросите новый код сопряжения в отделе кадров.'
          : 'Введите одноразовый код сопряжения из отдела кадров.'}
      </p>

      <form onSubmit={submit}>
        <input
          type="text"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          placeholder="Код сопряжения"
          autoFocus
          autoComplete="off"
          spellCheck={false}
        />
        <button type="submit" disabled={busy || !code.trim()}>
          {busy ? 'Подключаем…' : 'Подключить'}
        </button>
      </form>

      {error && <p className="error">{error}</p>}
    </main>
  );
}
