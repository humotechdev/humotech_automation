/**
 * Журнал действий.
 *
 * Карточкой раздела не стал: журнал — не настройка, а наблюдение за
 * тем, что уже сделано. Среди шести карточек «Администрирования» он
 * выглядел бы седьмой настройкой, которой не существует. Но и удалять
 * работающий журнал ради чистоты оглавления нельзя — он остаётся
 * отдельной страницей со ссылкой из шапки.
 */

import { AppShell } from '../../components/AppShell';
import { AuditTab } from '../../components/AuditTab';
import { SectionHead } from '../../components/admin/Parts';
import { useSession } from '../../features/auth/session';
import '../../styles/admin.css';

export function AuditPage() {
  const session = useSession();
  // Пояс организации приходит вместе с сессией. Пустая строка означала
  // бы пояс браузера смотрящего — и журнал начал бы утверждать не тот
  // час, в который действие произошло на самом деле.
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  return (
    <AppShell breadcrumb="Журнал действий" section="admin">
      <SectionHead
        title="Журнал действий"
        about="Кто и что изменил в системе. Записи журнала не правятся и не удаляются"
      />
      <AuditTab zone={zone} />
    </AppShell>
  );
}
