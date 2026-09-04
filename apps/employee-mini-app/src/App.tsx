/**
 * Каркас личного кабинета.
 *
 * Авторизация решается первой и отдельно (`auth.ts`): пока неизвестно,
 * кто пришёл, показывать нечего. Пять исходов входа остались прежними —
 * поменялось только их оформление.
 *
 * Навигация плоская: пять разделов внизу, профиль за аватаром. Вложенных
 * путей нет намеренно — приложение открывают на минуту, чтобы отметиться,
 * а не листать. Единственный вложенный экран, профиль, закрывается родной
 * кнопкой Telegram: своей стрелки нет, потому что на Android кнопка
 * Telegram совмещена с системной, и две разные «назад» на одном экране —
 * способ закрыть приложение вместо возврата.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  api,
  type Profile as ProfileData,
  type Status,
  type Summary,
} from './api';
import {
  authenticate,
  forgetToken,
  readInitData,
  rememberToken,
  type AuthResult,
} from './auth';
import { History } from './screens/History';
import { Home } from './screens/Home';
import { Profile } from './screens/Profile';
import { Requests, type AbsenceKind } from './screens/Requests';
import { Scan } from './screens/Scan';
import { Stats } from './screens/Stats';
import { backButton, prepare } from './telegram';
import { AppHeader } from './ui/AppHeader';
import { BottomNavigation, type Tab } from './ui/BottomNavigation';
import { PageContainer, PrimaryButton, SecondaryButton } from './ui/primitives';
import {
  ErrorState,
  LoadingScreen,
  OfflineBanner,
  StaleBanner,
} from './ui/states';

const API_URL = import.meta.env.VITE_API_URL ?? '/api/v1';
const VERSION = '1.0';

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
    prepare();
    void run();
  }, [run]);

  if (phase.kind === 'loading') {
    return (
      <Standalone title="Проверяем доступ">
        <LoadingScreen label="Проверяем доступ" cards={2} />
      </Standalone>
    );
  }

  const { result } = phase;

  switch (result.state) {
    case 'authenticated':
      return (
        <Cabinet
          onSignOut={() => {
            forgetToken();
            void run();
          }}
        />
      );

    case 'pending':
      return (
        <Standalone title="Привязка ожидает подтверждения">
          <p className="state-text">
            Вы перешли по ссылке, и заявка ушла в отдел кадров. Доступ
            появится после подтверждения — открывать приложение заново
            не нужно.
          </p>
          <SecondaryButton onClick={() => void run()} wide>
            Проверить ещё раз
          </SecondaryButton>
        </Standalone>
      );

    case 'unlinked':
      return (
        <Standalone title="Telegram не привязан">
          <p className="state-text">
            Этот Telegram не связан с учётной записью сотрудника. Попросите
            персональную ссылку в отделе кадров — она открывается один раз.
          </p>
        </Standalone>
      );

    case 'outside-telegram':
      return (
        <Standalone title="Откройте кнопкой в чате">
          <p className="state-text">
            Кабинет открывают кнопкой «Открыть личный кабинет» в чате с ботом
            HUMOTECH. По обычной ссылке — даже открытой внутри Telegram —
            приложение не получает подпись запуска, а без неё оно не знает,
            кто пришёл.
          </p>
          <p className="state-text muted">
            Кнопки в чате нет? Отправьте боту /start. Она появляется только
            после того, как отдел кадров подтвердил привязку.
          </p>
        </Standalone>
      );

    case 'error':
      return (
        <Standalone title="Не получилось">
          <ErrorState message={result.message} onRetry={() => void run()} />
        </Standalone>
      );
  }
}

/**
 * Сам кабинет: данные, разделы и навигация.
 *
 * Профиль и статус грузятся один раз на весь кабинет, а не на каждом
 * экране: имя и офис не меняются между вкладками, и перезапрашивать их
 * при каждом переключении значило бы моргать содержимым на ровном месте.
 */
function Cabinet({ onSignOut }: { onSignOut: () => void }) {
  const [tab, setTab] = useState<Tab>('home');
  const [profileOpen, setProfileOpen] = useState(false);
  const [absenceForm, setAbsenceForm] = useState<AbsenceKind | null>(null);

  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [today, setToday] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Неудачное обновление уже открытого кабинета — полоской, не экраном. */
  const [stale, setStale] = useState<string | null>(null);
  const [offline, setOffline] = useState(!navigatorOnline());

  // Через ссылку, а не через зависимость: `refresh` должен остаться
  // одним и тем же на всё время жизни кабинета — его вызывают из
  // сканера и из обработчиков сети.
  const loaded = useRef(false);
  loaded.current = profile !== null;

  const refresh = useCallback(async () => {
    const [me, now, stats] = await Promise.all([
      api.profile(),
      api.status(),
      api.statistics({ period: 'today' }),
    ]);

    if (!me.ok) {
      // Обрыв связи — не отказ в доступе. Уже загруженное остаётся
      // на экране: смотреть вчерашние отметки без сети безопасно.
      if (me.kind === 'network') {
        setOffline(true);
        return;
      }
      // Кабинет уже открыт — значит, это неудачное обновление, а не
      // неудачный вход. Подменять экран ошибкой здесь нельзя: сразу
      // после отметки `refresh` вызывает сам сканер, и упавший запрос
      // стёр бы с экрана «Вход отмечен, 08:54». Человек решил бы, что
      // отметка не прошла, и приложил бы пропуск второй раз — а сервер
      // ответил бы, что код уже использован.
      if (loaded.current) {
        setStale(me.message);
        return;
      }
      setError(me.message);
      return;
    }

    setOffline(false);
    setError(null);
    setStale(null);
    setProfile(me.value);
    if (now.ok) setStatus(now.value);
    if (stats.ok) setToday(stats.value.summary);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Сеть вернулась — обновляем данные молча, без вопросов к человеку.
  useEffect(() => {
    const back = () => {
      setOffline(false);
      void refresh();
    };
    const gone = () => setOffline(true);
    window.addEventListener('online', back);
    window.addEventListener('offline', gone);
    return () => {
      window.removeEventListener('online', back);
      window.removeEventListener('offline', gone);
    };
  }, [refresh]);

  // Родная кнопка «назад» Telegram — только на вложенном экране.
  useEffect(() => {
    if (!profileOpen) return backButton(null);
    return backButton(() => setProfileOpen(false));
  }, [profileOpen]);

  if (error) {
    return (
      <Standalone title="Не получилось">
        <ErrorState message={error} onRetry={() => void refresh()} />
        <SecondaryButton onClick={onSignOut} wide>
          Выйти
        </SecondaryButton>
      </Standalone>
    );
  }

  if (!profile || !status) {
    return (
      <Standalone title="Загружаем">
        <LoadingScreen cards={3} />
      </Standalone>
    );
  }

  return (
    <div className="app-shell">
      {offline && <OfflineBanner onRetry={() => void refresh()} />}
      {!offline && stale && (
        <StaleBanner message={stale} onRetry={() => void refresh()} />
      )}

      <div className="app-scroll">
        <PageContainer>
          {profileOpen ? (
            <Profile
              profile={profile}
              status={status}
              version={VERSION}
              onSignOut={onSignOut}
            />
          ) : (
            <>
              {tab === 'home' && (
                <>
                  <AppHeader
                    fullName={profile.employee.full_name}
                    office={profile.office.name}
                    position={profile.position?.name}
                    timeZone={status.timezone}
                    onProfile={() => setProfileOpen(true)}
                  />
                  <Home
                    profile={profile}
                    status={status}
                    today={today}
                    onScan={() => setTab('scan')}
                    onHistory={() => setTab('history')}
                    onSickLeave={() => {
                      setAbsenceForm('SICK_LEAVE');
                      setTab('requests');
                    }}
                    onVacation={() => {
                      setAbsenceForm('ANNUAL_LEAVE');
                      setTab('requests');
                    }}
                  />
                </>
              )}

              {tab === 'stats' && <Stats />}

              {tab === 'scan' && (
                <Scan
                  timeZone={status.timezone}
                  onDone={() => void refresh()}
                  onHome={() => setTab('home')}
                />
              )}

              {tab === 'history' && <History />}

              {tab === 'requests' && <Requests openForm={absenceForm} />}
            </>
          )}
        </PageContainer>
      </div>

      {profileOpen ? (
        <div className="bottom-nav bottom-nav-single">
          <PrimaryButton onClick={() => setProfileOpen(false)} wide>
            Вернуться в кабинет
          </PrimaryButton>
        </div>
      ) : (
        <BottomNavigation
          active={tab}
          onChange={(next) => {
            if (next !== 'requests') setAbsenceForm(null);
            setTab(next);
          }}
        />
      )}
    </div>
  );
}

/** Экран без навигации: вход, ошибки, ожидание. */
function Standalone({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="app-shell">
      <div className="app-scroll">
        <PageContainer>
          <h1>{title}</h1>
          {children}
        </PageContainer>
      </div>
    </div>
  );
}

/** `navigator.onLine` есть не везде; отсутствие считаем «сеть есть». */
function navigatorOnline(): boolean {
  return typeof navigator === 'undefined' || navigator.onLine !== false;
}
