/**
 * Состояние сессии на всё приложение.
 *
 * Ничего не хранится на клиенте: единственный источник правды — cookie
 * сессии, которую видит только браузер, и ответ `/auth/me`. Поэтому
 * при каждом запуске приложение спрашивает сервер, а не свою память:
 * сессия могла истечь, её могли снять администратором, а вкладка всё
 * это время была открыта.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import * as api from '../../api/auth';
import type { CurrentUser } from '../../api/auth';
import { ApiFailure } from '../../api/errors';

/** Пока идёт первая проверка, показывать нельзя ни форму, ни кабинет. */
export type SessionState =
  | { status: 'checking' }
  | { status: 'anonymous' }
  /**
   * Сервер не ответил. Про сессию НИЧЕГО не известно — ни что она
   * жива, ни что её нет.
   *
   * Отдельное состояние, а не разновидность `anonymous`: перезапуск
   * dev-сервера, перезагрузка backend и оборванная сеть отвечают
   * отказом транспорта, а не отказом в доступе. Свести их к «войдите»
   * значит объявить человека вышедшим из-за чужой заминки — сессия на
   * сервере при этом цела, и следующий же успешный `/auth/me` это
   * подтверждает.
   */
  | { status: 'unavailable' }
  | { status: 'authenticated'; user: CurrentUser };

type Session = SessionState & {
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** Спросить сервер заново. Нужна экрану «backend не отвечает». */
  recheck: () => void;
};

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: 'checking' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const stop = new AbortController();
    setState({ status: 'checking' });
    api
      .currentUser(stop.signal)
      .then((user) => setState({ status: 'authenticated', user }))
      .catch((failure: unknown) => {
        if (stop.signal.aborted) return;
        // «Доступа нет» и «сервер не ответил» — разные ответы, и
        // склеивать их нельзя. 401 и 403 означают, что войти
        // действительно надо: сессии нет, она истекла или запись
        // отключили. Оборванный запрос и 5xx не означают ничего о
        // сессии вовсе — она жива, просто спросить не у кого.
        const broken =
          failure instanceof ApiFailure
          && (failure.kind === 'offline' || failure.kind === 'server');
        setState({ status: broken ? 'unavailable' : 'anonymous' });
      });
    return () => stop.abort();
  }, [attempt]);

  const recheck = useCallback(() => setAttempt((n) => n + 1), []);

  const signIn = useCallback(async (email: string, password: string) => {
    const user = await api.login(email, password);
    setState({ status: 'authenticated', user });
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Отказ не пробрасываем: наверху с ним нечего делать, а
      // непойманное обещание в браузере превращается в ошибку в консоли
      // на ровном месте.
    } finally {
      // Даже если сервер не ответил, держать в интерфейсе вошедшего
      // человека нельзя: он нажал «выйти». Сессия на сервере при этом
      // могла уцелеть — и тогда следующий `/auth/me` честно покажет её
      // живой, вместо того чтобы врать про выход.
      setState({ status: 'anonymous' });
    }
  }, []);

  const value = useMemo<Session>(
    () => ({ ...state, signIn, signOut, recheck }),
    [state, signIn, signOut, recheck],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (session === null) {
    throw new Error('useSession вызван вне SessionProvider');
  }
  return session;
}
