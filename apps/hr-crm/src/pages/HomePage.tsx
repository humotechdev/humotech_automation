/**
 * Временная страница после входа.
 *
 * Здесь намеренно нет ни сводки, ни меню, ни карточек с цифрами: ни
 * одного экрана CRM ещё не существует, и нарисованный дашборд с
 * выдуманными числами выглядел бы как работающая система. Пусто —
 * честнее.
 *
 * Выход отсюда нужен не для красоты: без него полный круг сессии
 * (вход -> проверка -> выход) не проверить руками.
 */

import { useState } from 'react';

import { Wordmark } from '../components/Logo';
import { useSession } from '../features/auth/session';

export function HomePage() {
  const session = useSession();
  const [leaving, setLeaving] = useState(false);

  if (session.status !== 'authenticated') return null;

  async function leave() {
    setLeaving(true);
    await session.signOut();
    setLeaving(false);
  }

  return (
    <main className="page page--plain">
      <div className="stub">
        <Wordmark size={56} />
        <p className="stub__user">{session.user.email}</p>
        <p className="stub__note">Интерфейс CRM будет добавлен следующим этапом</p>
        <button type="button" className="stub__exit" onClick={leave} disabled={leaving}
                aria-busy={leaving}>
          {leaving ? 'Выходим…' : 'Выйти'}
        </button>
      </div>
    </main>
  );
}
