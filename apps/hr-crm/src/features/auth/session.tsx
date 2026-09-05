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

/** Пока идёт первая проверка, показывать нельзя ни форму, ни кабинет. */
export type SessionState =
  | { status: 'checking' }
  | { status: 'anonymous' }
  | { status: 'authenticated'; user: CurrentUser };

type Session = SessionState & {
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: 'checking' });

  useEffect(() => {
    const stop = new AbortController();
    api
      .currentUser(stop.signal)
      .then((user) => setState({ status: 'authenticated', user }))
      // Любой отказ здесь означает одно: показывать надо форму входа.
      // Различать причины незачем — человеку всё равно предстоит войти.
      .catch(() => {
        if (!stop.signal.aborted) setState({ status: 'anonymous' });
      });
    return () => stop.abort();
  }, []);

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

  const value = useMemo<Session>(() => ({ ...state, signIn, signOut }), [state, signIn, signOut]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (session === null) {
    throw new Error('useSession вызван вне SessionProvider');
  }
  return session;
}
