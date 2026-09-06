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
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

/** Кабинет: без сессии сюда нельзя. */
function Protected({ page }: { page?: 'employees' | 'requests' | 'attendance' | 'offices' | 'questions' }) {
  const session = useSession();
  if (session.status === 'checking') return <Checking />;
  if (session.status === 'anonymous') return <Navigate to="/login" replace />;
  if (page === 'employees') return <EmployeesPage />;
  if (page === 'requests') return <RequestsPage />;
  if (page === 'attendance') return <AttendancePage />;
  if (page === 'offices') return <OfficesPage />;
  if (page === 'questions') return <QuestionsPage />;
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

function Checking() {
  return (
    <main className="page page--plain">
      <p className="checking" role="status">
        Проверяем сессию…
      </p>
    </main>
  );
}
