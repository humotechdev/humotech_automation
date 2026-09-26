/**
 * Карточка учётной записи: профиль, назначения и история изменений.
 *
 * Три вещи, которые здесь принципиально разделены и часто путаются:
 *
 *   — учётная запись CRM и сотрудник. Это разные сущности. Заведение
 *     записи не создаёт человека в штате, а отключение записи не
 *     означает увольнение;
 *   — активность записи и наличие доступа. Активная запись со всеми
 *     истёкшими назначениями войдёт в CRM и не увидит ничего;
 *   — разрешения РОЛИ и итоговый доступ ЧЕЛОВЕКА. Роль — это набор
 *     прав; доступ — набор прав в конкретной области. Право управления
 *     в одном регионе не становится правом управления везде.
 *
 * Пароль сюда не приходит никогда. Форма его установки живёт отдельно,
 * значение уходит одним запросом и стирается сразу после ответа.
 */

import { useCallback, useRef, useState } from 'react';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppIcon } from './AppIcon';
import { useBlock, type Block } from '../features/dashboard/data';
import {
  STATUS,
  accessNote,
  changes,
  actionTitle,
  entityTitle,
  initials,
  userSubtitle,
  userTitle,
} from '../features/admin/model';
import { moment } from '../features/time/zone';

type Props = {
  user: api.CrmUser;
  zone: string;
  onClose: () => void;
  /** Что-то изменилось: перечитать строку, список и счётчики. */
  onChanged: (user?: api.CrmUser) => void;
};

type Inner = 'profile' | 'history';

const INNER: Array<{ key: Inner; title: string }> = [
  { key: 'profile', title: 'Профиль' },
  { key: 'history', title: 'История' },
];

export function UserCard({ user, zone, onClose, onChanged }: Props) {
  const [tab, setTab] = useState<Inner>('profile');
  const [form, setForm] = useState<'password' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  // Замок на ссылке, а не в состоянии: `setActing` применяется к
  // следующему рендеру, и три быстрых нажатия успевают пройти все три.
  const busy = useRef(false);

  const at = useCallback((value: string) => moment(value, zone, false), [zone]);

  const act = useCallback(
    async (run: () => Promise<api.CrmUser>) => {
      if (busy.current) return;
      busy.current = true;
      setActing(true);
      setError(null);
      try {
        onChanged(await run());
      } catch (failure) {
        setError(
          failure instanceof ApiFailure
            ? messageFor(failure)
            : 'Не удалось выполнить действие.',
        );
      } finally {
        busy.current = false;
        setActing(false);
      }
    },
    [onChanged],
  );

  const note = accessNote(user);

  return (
    <section className="panel panel--view" aria-label="Учётная запись">
      <div className="view__head">
        <span className="avatar avatar--big" aria-hidden="true">
          {initials(userTitle(user))}
        </span>
        <div className="view__who">
          <h2 className="view__title">{userTitle(user)}</h2>
          <p className="view__sub">{userSubtitle(user) ?? user.email}</p>
          <div className="view__badges">
            <span className="state">
              <i className="state__dot" />
              {STATUS[user.status] ?? user.status}
            </span>
            <span className="muted">Создана {at(user.created_at)}</span>
          </div>
        </div>
        <div className="view__actions">
          <button type="button" className="btn btn--small"
                  onClick={() => { setForm('password'); setError(null); }}>
            <AppIcon name="key" size={16} />
            Задать пароль
          </button>
          <button type="button" className="tool" aria-label="Закрыть карточку"
                  onClick={onClose}>
            <AppIcon name="cross" size={16} />
          </button>
        </div>
      </div>

      <div className="tabs tabs--inner" role="tablist" aria-label="Разделы карточки">
        {INNER.map((item) => {
          const on = item.key === tab;
          return (
            <button key={item.key} type="button" role="tab" aria-selected={on}
                    className={on ? 'tab tab--on' : 'tab'}
                    onClick={() => setTab(item.key)}>
              {item.title}
            </button>
          );
        })}
      </div>

      <div className="view__body">
        {error && <p className="empty empty--bad" role="alert">{error}</p>}
        {note && <p className="note note--dim">{note}</p>}

        {tab === 'profile' && (
          <Profile user={user} at={at} acting={acting} act={act} />
        )}
        {tab === 'history' && <History user={user} at={at} />}
      </div>

      {form === 'password' && (
        <PasswordForm user={user} onClose={() => setForm(null)}
                      onDone={(fresh) => { setForm(null); onChanged(fresh); }} />
      )}
    </section>
  );
}

// --- профиль ----------------------------------------------------------------

function Profile({ user, at, acting, act }: {
  user: api.CrmUser;
  at: (value: string) => string;
  acting: boolean;
  act: (run: () => Promise<api.CrmUser>) => Promise<void>;
}) {
  const active = user.status === 'ACTIVE';
  return (
    <>
      <dl className="facts">
        <div className="facts__row">
          <dt>Логин</dt>
          <dd className="mono">{user.email}</dd>
        </div>
        <div className="facts__row">
          <dt>Статус</dt>
          <dd>{STATUS[user.status] ?? user.status}</dd>
        </div>
        <div className="facts__row">
          <dt>Создана</dt>
          <dd>{at(user.created_at)}</dd>
        </div>
        <div className="facts__row">
          <dt>Последний вход</dt>
          <dd>{user.last_login ? at(user.last_login) : 'Ещё не входил'}</dd>
        </div>
        <div className="facts__row">
          <dt>Сотрудник</dt>
          <dd>
            {user.employee_id
              ? (user.full_name ?? 'Привязан')
              : 'Не привязан — техническая запись'}
          </dd>
        </div>
      </dl>

      {/* Двухфакторную проверку показываем как СВЕДЕНИЕ из модели, а не
          как работающую настройку: включать её этим интерфейсом нельзя,
          и кнопка означала бы обещание, которого backend не даёт. */}
      <p className="muted">
        Двухфакторная проверка: {user.mfa_enabled ? 'включена' : 'не включена'}.
        Управление ею в этот раздел не входит.
      </p>

      <div className="side-panel__actions">
          {active ? (
            <button type="button" className="btn" disabled={acting}
                    onClick={() => void act(() => api.deactivateCrmUser(user.id))}>
              Отключить доступ
            </button>
          ) : (
            <button type="button" className="btn btn--dark" disabled={acting}
                    onClick={() => void act(() => api.activateCrmUser(user.id))}>
              Восстановить доступ
            </button>
        )}
      </div>
      <p className="muted">
        {active
          ? 'Отключение закрывает вход в CRM. Сотрудник и его отметки не '
            + 'затрагиваются: это учётная запись, а не трудовые отношения.'
          : 'Восстановление откроет вход, если у записи есть пароль.'}
      </p>
    </>
  );
}

function History({ user, at }: {
  user: api.CrmUser;
  at: (value: string) => string;
}) {
  const [block, reload] = useBlock(
    async (signal) => {
      // Назначения принадлежат человеку, но записаны как отдельная
      // сущность, и у каждого свой идентификатор. Поэтому сначала
      // спрашиваем, какие назначения у него вообще были — включая
      // отозванные, — а потом просим журнал ровно про них.
      //
      // Отбирать чужие записи на клиенте нельзя: страница журнала
      // ограничена, и в активной организации до старых записей этого
      // человека дело просто не дошло бы. Пустая история выглядела бы
      // как «изменений не было».
      const all = await api.userGrants(user.id, true, signal);
      const ids = all.items.map((grant) => grant.id);
      const [own, grants] = await Promise.all([
        api.auditLogs(
          { entity_type: 'users', entity_id: user.id, limit: '50' },
          signal,
        ),
        ids.length
          ? api.auditLogs(
              {
                entity_type: 'user_role_scopes',
                entity_ids: ids.join(','),
                limit: '100',
              },
              signal,
            )
          : Promise.resolve({ items: [] as api.AuditEntry[] }),
      ]);
      return {
        items: [...own.items, ...grants.items].sort(
          (a, b) => b.occurred_at.localeCompare(a.occurred_at),
        ),
      };
    },
    `history|${user.id}`,
  );

  if (block.state === 'loading') return <p className="empty">Читаем историю…</p>;
  if (block.state === 'denied') return <p className="empty">Сессия истекла.</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить историю.{' '}
        <button type="button" className="link" onClick={reload}>Повторить</button>
      </p>
    );
  }
  if (block.data.items.length === 0) {
    return <p className="empty">Изменений по этой записи пока нет.</p>;
  }

  return (
    <ol className="timeline">
      {block.data.items.map((entry) => (
        <li key={entry.id}>
          <p className="timeline__when">{at(entry.occurred_at)}</p>
          <p className="timeline__title">{actionTitle(entry.action)}</p>
          <p className="timeline__note">
            {entry.actor_email ?? 'Система'} · {entityTitle(entry.entity_type)}
          </p>
          <Diff entry={entry} at={at} />
        </li>
      ))}
    </ol>
  );
}

/** «Было → стало». Секретов здесь нет: их вырезал сервер при записи. */
export function Diff({ entry, at }: {
  entry: api.AuditEntry;
  /** Формат времени в поясе организации. Без него моменты остаются сырыми. */
  at?: (value: string) => string;
}) {
  const rows = changes(entry, at);
  if (rows.length === 0) return null;
  return (
    <ul className="diff">
      {rows.map((row) => (
        <li key={row.field} className="diff__row">
          <span className="diff__field">{row.field}</span>
          <span className="diff__was">{row.before}</span>
          <AppIcon name="arrow" size={16} />
          <span className="diff__now">{row.after}</span>
        </li>
      ))}
    </ul>
  );
}

// --- установка пароля -------------------------------------------------------

function PasswordForm({ user, onClose, onDone }: {
  user: api.CrmUser;
  onClose: () => void;
  onDone: (user: api.CrmUser) => void;
}) {
  const [value, setValue] = useState('');
  const [again, setAgain] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);

  const submit = useCallback(async () => {
    if (busy.current) return;
    if (value !== again) {
      setError('Пароли не совпадают.');
      return;
    }
    busy.current = true;
    setSending(true);
    setError(null);
    try {
      const fresh = await api.setCrmUserPassword(user.id, value);
      // Стираем сразу: значение не должно пережить успешный ответ
      // ни в состоянии формы, ни в полях ввода.
      setValue('');
      setAgain('');
      onDone(fresh);
    } catch (failure) {
      // Требования проверяет сервер. Ослаблять их здесь ради
      // прохождения формы нельзя: проверка в браузере — подсказка,
      // а не правило.
      setError(
        failure instanceof ApiFailure
          ? messageFor(failure)
          : 'Не удалось установить пароль.',
      );
    } finally {
      busy.current = false;
      setSending(false);
    }
  }, [again, onDone, user.id, value]);

  return (
    <Overlay title="Пароль учётной записи" onClose={onClose}>
      <p className="muted">
        Пароль уходит на сервер один раз и обратно не возвращается. В журнале
        остаётся только факт установки.
      </p>
      <label className="form-grid__field">
        <span className="form-grid__label">Новый пароль</span>
        <input className="form-grid__input" type="password" value={value}
               autoComplete="new-password" aria-label="Новый пароль"
               onChange={(event) => setValue(event.target.value)} />
      </label>
      <label className="form-grid__field">
        <span className="form-grid__label">Ещё раз</span>
        <input className="form-grid__input" type="password" value={again}
               autoComplete="new-password" aria-label="Повтор пароля"
               onChange={(event) => setAgain(event.target.value)} />
      </label>
      {error && <p className="form-grid__error" role="alert">{error}</p>}
      <div className="side-panel__actions">
        <button type="button" className="btn" onClick={onClose}>Отмена</button>
        <button type="button" className="btn btn--dark"
                disabled={sending || !value || !again}
                onClick={() => void submit()}>
          {sending ? 'Сохраняем…' : 'Установить пароль'}
        </button>
      </div>
    </Overlay>
  );
}

// --- назначение роли --------------------------------------------------------

export function Overlay({ title, children, onClose }: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="veil veil--center" role="dialog" aria-modal="true"
         aria-label={title}
         onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}
         onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="dialog">
        <div className="drawer__head">
          <b className="drawer__name">{title}</b>
          <button type="button" className="tool" aria-label="Закрыть"
                  onClick={onClose}>
            <AppIcon name="cross" size={16} />
          </button>
        </div>
        <div className="dialog__body">{children}</div>
      </div>
    </div>
  );
}

export type { Block };
