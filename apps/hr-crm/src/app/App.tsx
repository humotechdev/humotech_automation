/**
 * Маршруты приложения.
 *
 * Их сейчас два, и это не заготовка на будущее: пустые пункты меню и
 * ведущие в никуда ссылки создают впечатление готовой системы там, где
 * её нет. Следующие разделы (`/employees`, `/attendance`, `/reports` и
 * прочие) появятся вместе со своими экранами — план описан в
 * `docs/architecture/hr-crm.md`.
 */

import { Navigate, Route, Routes } from 'react-router-dom';

import { DashboardPage } from '../pages/DashboardPage';
import { EmployeesPage } from '../pages/EmployeesPage';
import { RequestsPage } from '../pages/RequestsPage';
import { AttendancePage } from '../pages/AttendancePage';
import { OfficesPage } from '../pages/OfficesPage';
import { QuestionsPage } from '../pages/QuestionsPage';
import { AnalyticsPage } from '../pages/AnalyticsPage';
import { ReportsPage } from '../pages/ReportsPage';
import { KnowledgePage } from '../pages/KnowledgePage';
import { NotificationsPage } from '../pages/NotificationsPage';
import { AdminPage } from '../pages/AdminPage';
import { LoginPage } from '../pages/LoginPage';
import { useSession } from '../features/auth/session';

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<GuestOnly />} />
      <Route path="/" element={<Protected />} />
      <Route path="/employees" element={<Protected page="employees" />} />
      <Route path="/requests" element={<Protected page="requests" />} />
      <Route path="/attendance" element={<Protected page="attendance" />} />
      <Route path="/offices" element={<Protected page="offices" />} />
      <Route path="/questions" element={<Protected page="questions" />} />
      <Route path="/analytics" element={<Protected page="analytics" />} />
      <Route path="/reports" element={<Protected page="reports" />} />
      <Route path="/knowledge" element={<Protected page="knowledge" />} />
      <Route path="/notifications" element={<Protected page="notifications" />} />
      <Route path="/admin" element={<Protected page="admin" />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

/** Кабинет: без сессии сюда нельзя. */
function Protected({ page }: {
  page?: 'employees' | 'requests' | 'attendance' | 'offices' | 'questions'
       | 'analytics' | 'reports' | 'knowledge' | 'notifications' | 'admin';
}) {
  const session = useSession();
  if (session.status === 'checking') return <Checking />;
  // Сначала сбой связи, потом отсутствие доступа: перепутать их значит
  // объявить человека вышедшим из-за перезапуска сервера. Сессия на
  // сервере при этом цела, и «Повторить» это показывает.
  if (session.status === 'unavailable') {
    return <Unavailable onRetry={session.recheck} />;
  }
  if (session.status === 'anonymous') return <Navigate to="/login" replace />;
  if (page === 'employees') return <EmployeesPage />;
  if (page === 'requests') return <RequestsPage />;
  if (page === 'attendance') return <AttendancePage />;
  if (page === 'offices') return <OfficesPage />;
  if (page === 'questions') return <QuestionsPage />;
  if (page === 'analytics') return <AnalyticsPage />;
  if (page === 'reports') return <ReportsPage />;
  if (page === 'knowledge') return <KnowledgePage />;
  if (page === 'notifications') return <NotificationsPage />;
  if (page === 'admin') return <AdminPage />;
  return <DashboardPage />;
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
