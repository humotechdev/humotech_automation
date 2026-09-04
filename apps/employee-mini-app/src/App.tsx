/**
 * Экраны авторизации Mini App.
 *
 * Полного личного кабинета здесь нет и не должно быть — это следующий этап.
 * Задача этого экрана одна: довести человека от запуска до понятного ответа
 * и не соврать ни в одном из пяти исходов.
 *
 * Решения принимает `auth.ts`, компонент только выбирает, что показать.
 */

import { useCallback, useEffect, useState } from 'react';

import {
  authenticate,
  forgetToken,
  readInitData,
  rememberToken,
  type AuthResult,
} from './auth';

const API_URL = import.meta.env.VITE_API_URL ?? '/api/v1';

type Phase = { kind: 'loading' } | { kind: 'done'; result: AuthResult };

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
    void run();
  }, [run]);

  if (phase.kind === 'loading') {
    return <Screen title="Проверяем доступ…" />;
  }

  const { result } = phase;

  switch (result.state) {
    case 'authenticated':
      return (
        <Screen title={result.employee.full_name}>
          <p className="muted">Табельный номер: {result.employee.employee_number}</p>
          <p>
            Вход выполнен. Отметки, отпуска и больничные появятся здесь на
            следующем этапе.
          </p>
          <button
            type="button"
            onClick={() => {
              // Выход стирает только эту сессию. Отключить привязку целиком
              // может лишь отдел кадров — иначе доступ отзывался бы с того
              // самого устройства, которое мог взять кто угодно.
              forgetToken();
              void run();
            }}
          >
            Выйти
          </button>
        </Screen>
      );

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

function Screen({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <main className="screen">
      <h1>{title}</h1>
      {children}
    </main>
  );
}
