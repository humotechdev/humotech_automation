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
import { AnalyticsPage } from '../pages/AnalyticsPage';
import { ReportsPage } from '../pages/ReportsPage';
import { KnowledgePage } from '../pages/KnowledgePage';
import { NotificationsPage } from '../pages/NotificationsPage';
import { AdminPage } from '../pages/AdminPage';
import { SettingsPage } from '../pages/SettingsPage';
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
        <Route path="/employees/:id" element={<EmployeePage />} />
        <Route path="/requests" element={<RequestsPage />} />
        <Route path="/attendance" element={<AttendancePage />} />
        <Route path="/offices" element={<OfficesPage />} />
        <Route path="/offices/:id/setup" element={<OfficeSetupPage />} />
        <Route path="/questions" element={<QuestionsPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/settings" element={<SettingsPage />} />
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
