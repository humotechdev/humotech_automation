/**
 * Маршруты приложения.
 *
 * Разделы кабинета вложены в один маршрут-раскладку: оболочка CRM
 * монтируется один раз, а при переходах меняется только страница.
 * Страница входа и перенаправление неизвестных адресов — снаружи.
 */

import { useEffect } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { DashboardPage } from '../pages/DashboardPage';
import { EmployeesPage } from '../pages/EmployeesPage';
import { EmployeePage } from '../pages/EmployeePage';
import { NewEmployeePage } from '../pages/NewEmployeePage';
import { RequestsPage } from '../pages/RequestsPage';
import { AttendancePage } from '../pages/AttendancePage';
import { OfficesPage } from '../pages/OfficesPage';
import { OfficeSetupPage } from '../pages/OfficeSetupPage';
import { QuestionsPage } from '../pages/QuestionsPage';
import { SurveysPage } from '../pages/SurveysPage';
import { SurveyCampaignPage } from '../pages/SurveyCampaignPage';
import { AnalyticsPage } from '../pages/AnalyticsPage';
import { ReportsPage } from '../pages/ReportsPage';
import { NotificationsPage } from '../pages/NotificationsPage';
import { AdministrationPage } from '../pages/AdministrationPage';
import { AuditPage } from '../pages/admin/AuditPage';
import { LoginPage } from '../pages/LoginPage';
import { useSession } from '../features/auth/session';
import { ShellLayout } from '../components/AppShell';
import { forgetNavigation } from '../features/shell/memory';

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<GuestOnly />} />
      {/*
        * Все разделы кабинета — внутри одного маршрута-раскладки. Меню,
        * верхняя панель, колокольчик и поиск стоят в нём и при переходе
        * между разделами не пересоздаются: меняется только страница в
        * рабочей области.
        */}
      <Route element={<Protected />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/employees" element={<EmployeesPage />} />
        {/* Полная карточка. Собственный адрес — чтобы ссылка на человека
            передавалась, а «Назад» возвращал в список с его фильтрами. */}
        {/* Приём стоит ВЫШЕ маршрута карточки: иначе `/employees/new`
            совпал бы с `:id` и страница пыталась бы открыть сотрудника с
            идентификатором «new». */}
        <Route path="/employees/new" element={<NewEmployeePage />} />
        {/* Правка — та же форма, что и приём, только с данными человека
            и с «Сохранить изменения» вместо «Добавить». Тоже выше
            маршрута карточки: «edit» не должен стать идентификатором. */}
        <Route path="/employees/:id/edit" element={<NewEmployeePage />} />
        <Route path="/employees/:id" element={<EmployeePage />} />
        <Route path="/requests" element={<RequestsPage />} />
        <Route path="/attendance" element={<AttendancePage />} />
        <Route path="/offices" element={<OfficesPage />} />
        <Route path="/offices/:id/setup" element={<OfficeSetupPage />} />
        <Route path="/questions" element={<QuestionsPage />} />
        {/* Опросы: список рассылок и шаблонов, карточка одной рассылки
            с именными ответами. */}
        <Route path="/surveys" element={<SurveysPage />} />
        <Route path="/surveys/:id" element={<SurveyCampaignPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        {/* Адресов `/knowledge` и `/settings` больше нет: материалы
            ассистента живут на сервере и кадровику отдельным разделом не
            показываются, а настройки стоят рядом с тем, что настраивают.
            Старые ссылки попадают в общее перенаправление на главную. */}
        <Route path="/notifications" element={<NotificationsPage />} />
        {/* «Администрирование» — ОДИН экран: пять справочников
            раскрываются на месте. Отдельные страницы были ошибкой:
            справочник из трёх строк не стоит перехода. Офисов среди них
            нет — они настраиваются в разделе «Офисы и регионы».
            Старый адрес `/admin` ведёт сюда же: ссылки на него
            остались в закладках. */}
        <Route path="/administration" element={<AdministrationPage />} />
        <Route path="/administration/audit" element={<AuditPage />} />
        <Route path="/admin" element={<Navigate to="/administration" replace />} />
        <Route path="/admin/*" element={<Navigate to="/administration" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

/** Кабинет: без сессии сюда нельзя. */
function Protected() {
  const session = useSession();

  // Вышедшему не должно достаться ничего от прежнего: ни ответов
  // сервера в кэше, ни фильтров, ни адресов разделов.
  useEffect(() => {
    if (session.status === 'anonymous') forgetNavigation();
  }, [session.status]);

  if (session.status === 'checking') return <Checking />;
  // Сначала сбой связи, потом отсутствие доступа: перепутать их значит
  // объявить человека вышедшим из-за перезапуска сервера. Сессия на
  // сервере при этом цела, и «Повторить» это показывает.
  if (session.status === 'unavailable') {
    return <Unavailable onRetry={session.recheck} />;
  }
  if (session.status === 'anonymous') return <Navigate to="/login" replace />;
  return <ShellLayout />;
}

/**
 * Страница входа: вошедшему её показывать не за чем.
 *
 * Иначе после обновления вкладки человек с живой сессией снова видит
 * форму и вводит пароль там, где он уже не нужен.
 */
function GuestOnly() {
  const session = useSession();
  if (session.status === 'checking') return <Checking />;
  if (session.status === 'authenticated') return <Navigate to="/" replace />;
  return <LoginPage />;
}

/**
 * Backend не ответил. Про сессию ничего не известно, и форма входа
 * здесь была бы неправдой: вводить пароль незачем, войти всё равно не
 * выйдет, а старая сессия скорее всего жива.
 */
function Unavailable({ onRetry }: { onRetry: () => void }) {
  return (
    <main className="page page--plain">
      <p className="checking" role="alert">
        Сервер не отвечает. Проверить, вошли ли вы, сейчас не у кого —
        выходить из системы для этого не нужно.{' '}
        <button type="button" className="link" onClick={onRetry}>
          Повторить
        </button>
      </p>
    </main>
  );
}

function Checking() {
  return (
    <main className="page page--plain">
      <p className="checking" role="status">
        Проверяем сессию…
      </p>
    </main>
  );
}
