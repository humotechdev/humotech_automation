/**
 * Личный кабинет сотрудника в Telegram.
 *
 * Авторизация решается первой и отдельно (`auth.ts`): пока неизвестно,
 * кто пришёл, показывать нечего. Пять исходов входа остались прежними,
 * к ним добавились экраны самого кабинета.
 *
 * Навигация плоская: главный экран и пять разделов, из каждого — «Назад».
 * Вложенных путей нет намеренно: приложение открывают на минуту, чтобы
 * отметиться, а не листать.
 */

import { useCallback, useEffect, useState } from 'react';

import { api, type Profile, type Status } from './api';
import {
  authenticate,
  forgetToken,
  readInitData,
  rememberToken,
  type AuthResult,
} from './auth';
import { Absences } from './screens/Absences';
import { Home } from './screens/Home';
import { Scan } from './screens/Scan';
import { History, Stats } from './screens/Stats';

const API_URL = import.meta.env.VITE_API_URL ?? '/api/v1';

type Phase = { kind: 'loading' } | { kind: 'done'; result: AuthResult };

type Screen =
  | 'home'
  | 'scan'
  | 'stats'
  | 'history'
  | 'sick-leave'
  | 'vacation';

export default function App() {
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });

  const run = useCallback(async () => {
    setPhase({ kind: 'loading' });
    const result = await authenticate(readInitData(), { apiUrl: API_URL });
    if (result.state === 'authenticated') {
      rememberToken(result.token);
    }
    setPhase({ kind: 'done', result });
  }, []);

  useEffect(() => {
    window.Telegram?.WebApp?.ready?.();
    window.Telegram?.WebApp?.expand?.();
    void run();
  }, [run]);

  if (phase.kind === 'loading') {
    return <Screen title="Проверяем доступ…" />;
  }

  const { result } = phase;

  switch (result.state) {
    case 'authenticated':
      return <Cabinet onSignOut={() => { forgetToken(); void run(); }} />;

    case 'pending':
      return (
        <Screen title="Привязка ожидает подтверждения">
          <p>
            Вы перешли по ссылке, и заявка ушла в отдел кадров. Доступ появится
            после подтверждения — открывать приложение заново не нужно.
          </p>
          <button type="button" onClick={() => void run()}>
            Проверить ещё раз
          </button>
        </Screen>
      );

    case 'unlinked':
      return (
        <Screen title="Telegram не привязан">
          <p>
            Этот Telegram не связан с учётной записью сотрудника. Попросите
            персональную ссылку в отделе кадров — она открывается один раз.
          </p>
        </Screen>
      );

    case 'outside-telegram':
      return (
        <Screen title="Откройте из Telegram">
          <p>
            Приложение работает только внутри Telegram: подтвердить, кто вы,
            иначе нечем. Откройте его из чата с ботом HUMOTECH.
          </p>
        </Screen>
      );

    case 'error':
      return (
        <Screen title="Не получилось">
          <p>{result.message}</p>
          <button type="button" onClick={() => void run()}>
            Попробовать снова
          </button>
        </Screen>
      );
  }
}

function Cabinet({ onSignOut }: { onSignOut: () => void }) {
  const [screen, setScreen] = useState<Screen>('home');
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [me, now] = await Promise.all([api.profile(), api.status()]);
    if (!me.ok) {
      setError(me.message);
      return;
    }
    setProfile(me.value);
    if (now.ok) setStatus(now.value);
    setError(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (error) {
    return (
      <Screen title="Не получилось">
        <p>{error}</p>
        <button type="button" onClick={() => void refresh()}>
          Обновить
        </button>
        <button type="button" onClick={onSignOut}>
          Выйти
        </button>
      </Screen>
    );
  }

  if (!profile || !status) {
    return <Screen title="Загружаем…" />;
  }

  const back = () => {
    setScreen('home');
    void refresh();
  };

  return (
    <main className="app">
      {screen === 'home' && (
        <>
          <Home
            profile={profile}
            status={status}
            onScan={() => setScreen('scan')}
            onHistory={() => setScreen('history')}
            onSickLeave={() => setScreen('sick-leave')}
            onVacation={() => setScreen('vacation')}
          />
          <div className="grid">
            <button type="button" onClick={() => setScreen('stats')}>
              Статистика
            </button>
            <button type="button" className="quiet" onClick={onSignOut}>
              Выйти
            </button>
          </div>
        </>
      )}

      {screen === 'scan' && (
        <Scan onDone={() => void refresh()} onBack={back} />
      )}
      {screen === 'stats' && <Stats onBack={back} />}
      {screen === 'history' && <History onBack={back} />}
      {screen === 'sick-leave' && <Absences kind="SICK_LEAVE" onBack={back} />}
      {screen === 'vacation' && <Absences kind="ANNUAL_LEAVE" onBack={back} />}
    </main>
  );
}

function Screen({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <main className="app screen">
      <h1>{title}</h1>
      {children}
    </main>
  );
}
