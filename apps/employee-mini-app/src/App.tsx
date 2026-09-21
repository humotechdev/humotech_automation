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
  type Note,
  type OpenSession,
  type Profile as ProfileData,
} from './api';
import {
  authenticate,
  forgetToken,
  readInitData,
  rememberToken,
  type AuthResult,
} from './auth';
import { History } from './screens/History';
import { Home, type TodayData, type WeekData } from './screens/Home';
import { KeyboardScan, insideTelegram } from './screens/KeyboardScan';
import { Notes } from './screens/Notes';
import { Profile } from './screens/Profile';
import { QuickScan } from './screens/QuickScan';
import { Requests, type AbsenceKind } from './screens/Requests';
import { Scan } from './screens/Scan';
import { Survey } from './screens/Survey';
import { Stats } from './screens/Stats';
import {
  backButton,
  isFullscreen,
  onShellEvent,
  prepare,
  watchViewport,
} from './telegram';
import { useSection } from './sections';
import { BottomNavigation, type Tab } from './ui/BottomNavigation';
import { TopBar } from './ui/TopBar';
import { PageContainer, SecondaryButton } from './ui/primitives';
import {
  ErrorState,
  LoadingScreen,
  OfflineBanner,
  StaleBanner,
} from './ui/states';

const API_URL = import.meta.env.VITE_API_URL ?? '/api/v1';
const VERSION = '1.0';

/**
 * Быстрая отметка живёт по своему адресу.
 *
 * Своего маршрутизатора у приложения нет и не нужно: адресов ровно два,
 * и различить их — одна строка. Библиотека маршрутизации весила бы
 * больше, чем всё, ради чего её взяли бы.
 *
 * На этот адрес смотрит синяя кнопка бота. Раздаёт его тот же
 * index.html (`try_files` в nginx), поэтому отдельной страницы и
 * отдельной сборки не появляется.
 */
export const QUICK_SCAN_PATH = '/scan';

export function isQuickScanRoute(
  pathname: string = window.location.pathname,
): boolean {
  return pathname.replace(/\/+$/, '') === QUICK_SCAN_PATH;
}

/**
 * Опрос открывается по своему адресу: `/survey/<получатель>`.
 *
 * На этот адрес смотрит кнопка «Пройти опрос» под сообщением бота.
 * Идентификатор именно в адресе, а не «последний непройденный опрос»:
 * человек может нажать кнопку через день, когда пришёл ещё один, и
 * открыться должен тот, который он открыл.
 */
export const SURVEY_PATH = '/survey';

export function surveyOf(
  pathname: string = window.location.pathname,
): string | null {
  const parts = pathname.replace(/\/+$/, '').split('/').filter(Boolean);
  if (parts.length !== 2 || `/${parts[0]}` !== SURVEY_PATH) return null;
  return parts[1] ?? null;
}

/**
 * Режим нижней кнопки Telegram.
 *
 * Отличается ТОЛЬКО транспортом: подписи запуска у такого Mini App нет,
 * поэтому данные уходят боту через `sendData`, а не на сервер напрямую.
 *
 * `source=keyboard` — не авторизация и не доказательство чего-либо.
 * Подставить его в адресную строку может кто угодно, и всё, что он
 * включает, — путь, на котором личность всё равно устанавливает бот по
 * подтверждённому Telegram `message.from.id`.
 */
export const KEYBOARD_SOURCE = 'keyboard';

export function isKeyboardScan(
  pathname: string = window.location.pathname,
  search: string = window.location.search,
): boolean {
  if (!isQuickScanRoute(pathname)) return false;
  return new URLSearchParams(search).get('source') === KEYBOARD_SOURCE;
}

type Phase = { kind: 'loading' } | { kind: 'done'; result: AuthResult };

export default function App() {
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  // Режим нижней кнопки решается ДО всего остального: обменивать
  // подпись там не на что, и запрос авторизации был бы обращением к
  // серверу с заведомо пустыми руками.
  const [keyboard, setKeyboard] = useState(() => isKeyboardScan());
  // Куда открыли приложение. Состоянием, а не чтением адреса при каждой
  // отрисовке: с экрана быстрой отметки можно уйти в кабинет, и адрес
  // при этом не меняется — переписывать историю браузера в вебвью
  // Telegram значило бы ломать его же кнопку «назад».
  const [quick, setQuick] = useState(isQuickScanRoute);
  // Какой опрос открыть. Состоянием по той же причине, что и быстрая
  // отметка: из опроса можно уйти в кабинет, а переписывать историю
  // браузера в вебвью Telegram значит ломать его же «назад».
  const [survey, setSurvey] = useState(surveyOf);

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
    // Пересчёт безопасных зон и устойчивой высоты живёт всё время работы
    // приложения: они меняются от поворота экрана, клавиатуры и входа в
    // полноэкранный режим.
    const stop = watchViewport();
    if (!keyboard) void run();
    return stop;
  }, [run, keyboard]);

  if (keyboard) {
    // Ни одного запроса на сервер отсюда не уходит: ни авторизации, ни
    // отметки. Всё, что делает экран, — собирает попытку и отдаёт её
    // боту, а решает всё равно сервер по его запросу.
    if (!insideTelegram()) {
      return (
        <Standalone title="Откройте кнопкой в чате">
          <p className="state-text">
            Так отметиться можно только из чата с ботом HUMOTECH: нажмите
            внизу «📷 Отметиться». В обычном браузере отметку принять
            не у кого.
          </p>
        </Standalone>
      );
    }
    return <KeyboardScan onOpenCabinet={() => setKeyboard(false)} />;
  }

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
      if (survey) {
        return (
          <Survey
            recipientId={survey}
            onDone={() => setSurvey(null)}
            onExit={() => setSurvey(null)}
          />
        );
      }
      return quick ? (
        <QuickScan onOpenCabinet={() => setQuick(false)} />
      ) : (
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
            {quick
              ? 'Откройте сканер кнопкой «📷 Отметиться» внизу чата с ботом HUMOTECH.'
              : 'Кабинет открывают кнопкой «Открыть личный кабинет» в чате с ботом HUMOTECH.'}{' '}
            По обычной ссылке — даже открытой внутри Telegram — приложение
            не получает подпись запуска, а без неё оно не знает, кто
            пришёл, и отметиться за него не может.
          </p>
          {quick && <ScanWayOut />}
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
          {quick && <ScanWayOut />}
        </Standalone>
      );
  }
}

/**
 * Путь вперёд, когда сканер открылся, а войти не удалось.
 *
 * Отказов у входа два, и приводят они на РАЗНЫЕ экраны: пустая подпись —
 * на «откройте кнопкой в чате», отвергнутая — на «не получилось».
 * Подсказка нужна на обоих. По документации Telegram кнопка нижней
 * клавиатуры подписи не приносит вовсе, то есть это самый вероятный
 * исход её нажатия, и человек должен уйти отсюда с рабочим действием,
 * а не с объяснением.
 */
function ScanWayOut() {
  return (
    <p className="state-text">
      Отправьте боту команду <b>/scan</b> — он пришлёт кнопку, которая
      открывает сканер наверняка.
    </p>
  );
}

/**
 * Сам кабинет: данные, разделы и навигация.
 *
 * Данные грузятся четырьмя независимыми кусками, а не одним запросом:
 * статус и сегодняшний журнал, неделя, заявки, уведомления. Упавшая
 * неделя не гасит статус, а обновление одного куска не стирает
 * остальные — человек открывает приложение, чтобы увидеть, отметился
 * ли он, и мигающий целиком экран отвечает на это хуже, чем
 * устаревшая на минуту карточка.
 *
 * Профиль — исключение: без имени и офиса показывать нечего вовсе,
 * поэтому он один держит первый экран загрузки.
 */
function Cabinet({ onSignOut }: { onSignOut: () => void }) {
  const [tab, setTab] = useState<Tab>('home');
  const [wide, setWide] = useState(false);
  const [absenceForm, setAbsenceForm] = useState<AbsenceKind | null>(null);
  const [offline, setOffline] = useState(!navigatorOnline());

  const profile = useSection<ProfileData>(() => api.profile());

  /*
   * Статус и сегодняшние сессии — одна секция: карточка статуса и
   * журнал дня отвечают на один вопрос и обязаны обновляться вместе.
   * Разъехавшись, они показали бы «в офисе» без входа в списке.
   */
  const today = useSection<TodayData>(async () => {
    const [now, log] = await Promise.all([
      api.status(),
      api.history({ period: 'today' }),
    ]);
    if (!now.ok) return now;
    // Журнал дня — не обязателен: статус важнее, и без списка событий
    // карточка «В офисе» всё равно должна появиться. Отсюда же защита
    // от неполного ответа: белый экран из-за отсутствующего поля хуже,
    // чем пустой список.
    const day = log.ok ? log.value.days?.[0] : undefined;
    return {
      ok: true as const,
      value: { status: now.value, sessions: day?.sessions ?? [] },
    };
  });

  /*
   * Неделя: нормы берутся из статистики (там все семь дней), сессии —
   * из истории (там время каждой). История отдаёт только дни, о которых
   * есть что сказать, поэтому склеиваются они по дате, а не по порядку.
   */
  const week = useSection<WeekData>(async () => {
    const [stats, log] = await Promise.all([
      api.statistics({ period: 'week' }),
      api.history({ period: 'week', limit: 62 }),
    ]);
    if (!stats.ok) return stats;
    const sessions: Record<string, OpenSession[]> = {};
    if (log.ok) {
      for (const day of log.value.days ?? []) {
        if (day.sessions?.length) sessions[day.day] = day.sessions;
      }
    }
    return { ok: true as const, value: { days: stats.value.days, sessions } };
  });

  const requests = useSection(async () => {
    const answer = await api.absences();
    return answer.ok
      ? { ok: true as const, value: answer.value.requests }
      : answer;
  });

  const notes = useSection(() => api.notifications(10));

  /*
   * Что обновить после удачной отметки: статус, сегодняшний журнал и
   * неделя. Заявки и уведомления сканирование не меняет, и дёргать их
   * значило бы заставлять экран моргать без повода.
   *
   * Ссылки на секции берутся из ref: сами объекты пересоздаются на
   * каждой отрисовке, а обработчик должен остаться одним и тем же —
   * его держит у себя экран сканера.
   */
  const parts = useRef({ profile, today, week, requests, notes });
  parts.current = { profile, today, week, requests, notes };

  const afterScan = useCallback(() => {
    parts.current.today.reload();
    parts.current.week.reload();
  }, []);

  /**
   * Отметка о прочтении уходит на сервер, а не гасится на месте:
   * счётчик на колокольчике обязан погаснуть и на втором устройстве.
   * Уже прочитанное второй раз не отправляем — запрос идемпотентен,
   * но лишний.
   */
  const markRead = useCallback((note: Note) => {
    if (note.is_read) return;
    void api
      .readNotification(note.id)
      .then(() => parts.current.notes.reload());
  }, []);

  const reloadAll = useCallback(() => {
    const all = parts.current;
    all.profile.reload();
    all.today.reload();
    all.week.reload();
    all.requests.reload();
    all.notes.reload();
  }, []);

  // Сеть вернулась — обновляем данные молча, без вопросов к человеку.
  useEffect(() => {
    const back = () => {
      setOffline(false);
      reloadAll();
    };
    const gone = () => setOffline(true);
    window.addEventListener('online', back);
    window.addEventListener('offline', gone);
    return () => {
      window.removeEventListener('online', back);
      window.removeEventListener('offline', gone);
    };
  }, [reloadAll]);

  /*
   * Полноэкранный режим просит только экран QR, но знать о нём должна и
   * навигация: панель поверх камеры не нужна. Подписка одна, здесь, а не
   * в каждом экране по копии.
   */
  useEffect(() => {
    const update = () => setWide(isFullscreen());
    const off = [
      onShellEvent('fullscreenChanged', update),
      // Отказ — не ошибка человека и показывать её нечего. Экран просто
      // остаётся обычным, а навигация — на месте.
      onShellEvent('fullscreenFailed', () => setWide(false)),
    ];
    return () => off.forEach((stop) => stop());
  }, []);

  /*
   * Родная кнопка «назад» Telegram: на главной её нет, на всех остальных
   * экранах есть и ведёт внутрь приложения, а не закрывает его.
   *
   * Вкладки отметок, заявок и профиля тоже считаются «не главной»
   * намеренно. На Android эта кнопка совмещена с системной, и без
   * обработчика нажатие на вкладке «Заявки» закрывало бы кабинет
   * целиком — вместо ожидаемого возврата на главную.
   */
  useEffect(() => {
    if (tab !== 'home') return backButton(() => setTab('home'));
    return backButton(null);
  }, [tab]);

  /*
   * Экран ошибки — только на том, без чего кабинета нет: на профиле.
   *
   * Разбор по виду отказа, а не по факту отказа, и различие здесь
   * принципиальное.
   *
   * 401 и 403 ведут на экран ошибки ВСЕГДА, даже в уже открытом
   * кабинете. Первое значит, что сеанс истёк, второе — что привязку
   * Telegram отозвали; полоска «данные могли устареть» с кнопкой «Ещё
   * раз» отвечала бы тем же отказом бесконечно, а выйти и войти заново
   * можно только отсюда.
   *
   * 5xx в уже открытом кабинете — полоска. Сразу после отметки данные
   * перечитывает сам сканер, и стереть с экрана «Вход отмечен, 08:54»
   * из-за упавшего запроса значило бы заставить человека приложить
   * пропуск второй раз — а сервер ответит, что код уже использован.
   *
   * Обрыв связи — не отказ в доступе: уже загруженное остаётся на
   * экране под полоской «нет связи».
   */
  const fatal =
    profile.error !== null &&
    profile.kind !== 'network' &&
    (profile.kind !== 'server' || profile.data === null);

  /*
   * Незавершённое ознакомление — не поломка и не потеря доступа.
   * Кабинет закрыт до тех пор, пока человек не дочитает материалы в
   * чате с ботом, и здесь ему нужно ровно это сказать. Прежний экран
   * назывался «Не получилось» и предлагал «Выйти» — обе подсказки вели
   * не туда: повторный вход ничего не меняет.
   */
  if (fatal && (profile.reason ?? '').startsWith('onboarding_')) {
    return (
      <Standalone title="Ознакомление">
        <ErrorState
          message={profile.error ?? undefined}
          onRetry={() => profile.reload()}
        />
      </Standalone>
    );
  }

  if (fatal) {
    return (
      <Standalone title="Не получилось">
        <ErrorState
          message={profile.error ?? undefined}
          onRetry={() => profile.reload()}
        />
        <SecondaryButton onClick={onSignOut} wide>
          Выйти
        </SecondaryButton>
      </Standalone>
    );
  }

  if (!profile.data) {
    return (
      <Standalone title="Загружаем">
        <LoadingScreen cards={3} />
      </Standalone>
    );
  }

  const me = profile.data;
  // Полоской показывается сбой сервера — чей угодно из двух главных
  // запросов. Остальные секции показывают свой сбой внутри себя.
  const stale =
    (profile.kind === 'server' && profile.error) ||
    (today.kind === 'server' && today.error) ||
    null;

  return (
    <div className="app-shell">
      <TopBar
        fullName={me.employee.full_name}
        unread={notes.data?.unread ?? 0}
        onNotifications={() => setTab('notes')}
        onProfile={() => setTab('profile')}
      />

      {offline && <OfflineBanner onRetry={reloadAll} />}
      {!offline && stale && (
        <StaleBanner message={stale} onRetry={reloadAll} />
      )}

      <div className="app-scroll">
        {/* Разрядка 12 px — только на главной: остальные экраны свою
            не меняют. */}
        <PageContainer className={tab === 'home' ? 'page-home' : ''}>
          {tab === 'home' && (
            <Home
              fullName={me.employee.full_name}
              office={me.office.name}
              today={today}
              week={week}
              requests={requests}
              notes={notes}
              onScan={() => setTab('scan')}
              onHistory={() => setTab('history')}
              onRequests={() => setTab('requests')}
              onNewRequest={(kind) => {
                setAbsenceForm(kind);
                setTab('requests');
              }}
              onCorrection={() => setTab('history')}
              onQuestion={() => setTab('profile')}
              onNote={(note) => {
                markRead(note);
                setTab('notes');
              }}
              onWeek={() => setTab('stats')}
            />
          )}

          {/* Пояс берётся из профиля, а не из статуса: сканер обязан
              открываться и тогда, когда статус не загрузился. Ждать его
              значило бы не дать отметиться из-за упавшего запроса,
              который к отметке отношения не имеет. */}
          {tab === 'notes' && (
            <Notes
              section={notes}
              timeZone={me.office.timezone}
              onRead={markRead}
            />
          )}

          {tab === 'stats' && <Stats />}

          {tab === 'scan' && (
            <Scan
              timeZone={me.office.timezone}
              wide={wide}
              onDone={afterScan}
              onHome={() => setTab('home')}
            />
          )}

          {tab === 'history' && <History />}

          {tab === 'requests' && <Requests openForm={absenceForm} />}

          {/* Профиль не ждёт статуса: без него он просто не покажет
              строку «сегодня», а не останется пустым экраном. */}
          {tab === 'profile' && (
            <Profile
              profile={me}
              status={today.data?.status ?? null}
              version={VERSION}
              onSignOut={onSignOut}
            />
          )}
        </PageContainer>
      </div>

      {/* В полноэкранном режиме панели нет вовсе: она стояла бы поверх
          окна сканера. Возврат на главную — внутренней кнопкой на самом
          экране и родной кнопкой «назад». */}
      {wide ? null : (
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
