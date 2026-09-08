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

import { useCallback, useMemo, useRef, useState } from 'react';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { Icon } from './nav-icons';
import { useBlock, type Block } from '../features/dashboard/data';
import {
  PHASE_TITLE,
  STATUS,
  accessNote,
  changes,
  actionTitle,
  entityTitle,
  grantBlockedBecause,
  grantPhase,
  initials,
  permissionTitle,
  scopeTitle,
  sections,
  userSubtitle,
  userTitle,
  scopeHint,
  validityTitle,
  type Phase,
} from '../features/admin/model';
import { dayInZone, moment } from '../features/time/zone';

type Rights = { users: boolean; roles: boolean; audit: boolean };

type Props = {
  user: api.CrmUser;
  zone: string;
  rights: Rights;
  scopes: api.AssignableScopes;
  /** Прочитан ли список областей. Отказ сети — не пустая область. */
  scopesKnown: boolean;
  roles: api.RoleFull[];
  catalog: api.PermissionRow[];
  onClose: () => void;
  /** Что-то изменилось: перечитать строку, список и счётчики. */
  onChanged: (user?: api.CrmUser) => void;
};

type Inner = 'profile' | 'grants' | 'history';

const INNER: Array<{ key: Inner; title: string }> = [
  { key: 'profile', title: 'Профиль' },
  { key: 'grants', title: 'Роли и области' },
  { key: 'history', title: 'История' },
];

export function UserCard({
  user, zone, rights, scopes, scopesKnown, roles, catalog,
  onClose, onChanged,
}: Props) {
  const [tab, setTab] = useState<Inner>('profile');
  const [form, setForm] = useState<'password' | 'assign' | null>(null);
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
          {rights.users && (
            <button type="button" className="btn btn--small"
                    onClick={() => { setForm('password'); setError(null); }}>
              <Icon name="key" size={15} />
              Задать пароль
            </button>
          )}
          <button type="button" className="tool" aria-label="Закрыть карточку"
                  onClick={onClose}>
            <Icon name="cross" size={16} />
          </button>
        </div>
      </div>

      <div className="tabs tabs--inner" role="tablist" aria-label="Разделы карточки">
        {INNER.map((item) => {
          const on = item.key === tab;
          if (item.key === 'history' && !rights.audit) return null;
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
          <Profile user={user} rights={rights} at={at} acting={acting} act={act} />
        )}
        {tab === 'grants' && (
          <Grants user={user} zone={zone} rights={rights} roles={roles}
                  catalog={catalog}
                  onOpenAssign={() => { setForm('assign'); setError(null); }}
                  onChanged={onChanged} />
        )}
        {tab === 'history' && rights.audit && (
          <History user={user} at={at} />
        )}
      </div>

      {form === 'password' && (
        <PasswordForm user={user} onClose={() => setForm(null)}
                      onDone={(fresh) => { setForm(null); onChanged(fresh); }} />
      )}
      {form === 'assign' && (
        <AssignForm user={user} roles={roles} scopes={scopes}
                    scopesKnown={scopesKnown}
                    onClose={() => setForm(null)}
                    onDone={() => { setForm(null); onChanged(); }} />
      )}
    </section>
  );
}

// --- профиль ----------------------------------------------------------------

function Profile({ user, rights, at, acting, act }: {
  user: api.CrmUser;
  rights: Rights;
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

      {rights.users && (
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
      )}
      <p className="muted">
        {active
          ? 'Отключение закрывает вход в CRM. Сотрудник и его отметки не '
            + 'затрагиваются: это учётная запись, а не трудовые отношения.'
          : 'Восстановление откроет вход, если у записи есть пароль. Роли '
            + 'при отключении не отзывались.'}
      </p>
    </>
  );
}

// --- роли и области ---------------------------------------------------------

function Grants({
  user, zone, rights, roles, catalog, onOpenAssign, onChanged,
}: {
  user: api.CrmUser;
  zone: string;
  rights: Rights;
  roles: api.RoleFull[];
  catalog: api.PermissionRow[];
  onOpenAssign: () => void;
  onChanged: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [dated, setDated] = useState<api.Grant | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);

  // Два запроса намеренно: «действует» решает сервер, а не сравнение дат
  // здесь. Клиент лишь раскладывает остальные на «ещё не началось» и
  // «уже закончилось».
  const [block] = useBlock(
    (signal) =>
      Promise.all([
        api.userGrants(user.id, true, signal),
        api.userGrants(user.id, false, signal),
      ]).then(([all, live]) => ({
        items: all.items,
        active: new Set(live.items.map((item) => item.id)),
      })),
    `grants|${user.id}|${attempt}`,
    rights.roles,
  );

  const revoke = useCallback(
    async (id: string) => {
      if (busy.current) return;
      busy.current = true;
      setError(null);
      try {
        await api.revokeGrant(id);
        setAttempt((n) => n + 1);
        onChanged();
      } catch (failure) {
        setError(
          failure instanceof ApiFailure
            ? messageFor(failure)
            : 'Не удалось отозвать назначение.',
        );
      } finally {
        busy.current = false;
      }
    },
    [onChanged],
  );

  if (!rights.roles) {
    return (
      <p className="empty">
        Нет права управлять ролями. Назначения этого человека скрыты — не
        потому, что их нет.
      </p>
    );
  }
  if (block.state === 'loading') return <p className="empty">Читаем назначения…</p>;
  if (block.state === 'denied') return <p className="empty">Сессия истекла.</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить назначения.{' '}
        <button type="button" className="link" onClick={() => setAttempt((n) => n + 1)}>
          Повторить
        </button>
      </p>
    );
  }

  const { items, active } = block.data;
  const phases = new Map<string, Phase>(
    items.map((item) => [item.id, grantPhase(item, active)]),
  );
  const live = items.filter((item) => phases.get(item.id) === 'active');
  const rest = items.filter((item) => phases.get(item.id) !== 'active');
  const role = picked ? roles.find((item) => item.id === picked) : undefined;

  return (
    <>
      {error && <p className="empty empty--bad" role="alert">{error}</p>}

      <p className="side-panel__label">
        Назначения доступа <span className="tab__count">{live.length}</span>
      </p>

      {live.length === 0 && (
        <p className="empty">
          Действующих назначений нет. Учётная запись войдёт в CRM и не увидит
          ни одного раздела.
        </p>
      )}

      <ul className="grants">
        {live.map((grant) => (
          <li key={grant.id} className="grant">
            <div className="grant__head">
              <Icon name="admin" size={16} />
              <b className="grant__name">{grant.role_name}</b>
              {roles.find((item) => item.id === grant.role_id)?.is_system && (
                <span className="chip">Системная роль</span>
              )}
            </div>
            <dl className="facts facts--tight">
              <div className="facts__row">
                <dt>Область</dt>
                <dd>{scopeTitle(grant)}</dd>
              </div>
              <div className="facts__row">
                <dt>Действует с</dt>
                <dd>{moment(grant.valid_from, zone, false)}</dd>
              </div>
              <div className="facts__row">
                <dt>Срок</dt>
                <dd>
                  {validityTitle(grant, (value) => moment(value, zone, false))}
                </dd>
              </div>
            </dl>
            <div className="grant__actions">
              <button type="button" className="btn btn--small"
                      onClick={() => setPicked(
                        picked === grant.role_id ? null : grant.role_id,
                      )}>
                {picked === grant.role_id ? 'Скрыть права' : 'Права роли'}
              </button>
              <button type="button" className="btn btn--small"
                      onClick={() => setDated(grant)}>
                Изменить срок
              </button>
              <button type="button" className="btn btn--small"
                      onClick={() => void revoke(grant.id)}>
                Отозвать
              </button>
            </div>
          </li>
        ))}
      </ul>

      {rest.length > 0 && (
        <>
          <p className="side-panel__label">Недействующие</p>
          <ul className="grants grants--dim">
            {rest.map((grant) => (
              <li key={grant.id} className="grant">
                <div className="grant__head">
                  <Icon name="clock" size={15} />
                  <b className="grant__name">{grant.role_name}</b>
                  <span className="chip">{PHASE_TITLE[phases.get(grant.id)!]}</span>
                </div>
                <p className="muted">
                  {scopeTitle(grant)} · с {moment(grant.valid_from, zone, false)}
                  {grant.valid_to
                    ? ` по ${moment(grant.valid_to, zone, false)}`
                    : ''}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}

      {role && (
        <>
          <p className="side-panel__label">Разрешения роли «{role.name}»</p>
          <p className="muted">
            Это права САМОЙ роли. Человеку они действуют только в области
            своего назначения.
          </p>
          <PermissionList codes={role.permissions} catalog={catalog} />
        </>
      )}

      <div className="side-panel__actions">
        <button type="button" className="btn btn--dark btn--wide" onClick={onOpenAssign}>
          <Icon name="plus" size={16} />
          Назначить роль
        </button>
      </div>
      <p className="muted">Изменения прав записываются в журнал.</p>

      {dated && (
        <ValidityForm
          grant={dated}
          zone={zone}
          onClose={() => setDated(null)}
          onDone={() => {
            setDated(null);
            setAttempt((n) => n + 1);
            onChanged();
          }}
        />
      )}
    </>
  );
}

// --- срок назначения --------------------------------------------------------

/**
 * Продлить или закрыть назначение, не отзывая его.
 *
 * Отправляется не только новый срок, но и прежний — тот, что был на
 * экране, — вместе с признаком сверки. Признак нужен отдельно: `null`
 * здесь означает законное «бессрочно», и по одному значению не отличить
 * «срока не было» от «срок не передали». Если тем временем назначение
 * изменил другой администратор, сервер отвечает конфликтом, а не молча
 * затирает его правку.
 */
function ValidityForm({ grant, zone, onClose, onDone }: {
  grant: api.Grant;
  zone: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [until, setUntil] = useState(() => (grant.valid_to ? dayInZone(grant.valid_to, zone) : ''));
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);

  const submit = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setSending(true);
    setError(null);
    try {
      await api.setGrantValidity(grant.id, {
        // Пусто — «бессрочно»; иначе дата, а границу суток ставит сервер.
        ...(until ? { valid_to_date: until } : { valid_to: null }),
        expected_valid_to: grant.valid_to,
        check_expected: true,
      });
      onDone();
    } catch (failure) {
      setError(
        failure instanceof ApiFailure
          ? messageFor(failure)
          : 'Не удалось изменить срок.',
      );
    } finally {
      busy.current = false;
      setSending(false);
    }
  }, [grant.id, grant.valid_to, onDone, until]);

  return (
    <Overlay title={`Срок роли «${grant.role_name}»`} onClose={onClose}>
      <p className="muted">
        Область: {scopeTitle(grant)}. Действует с{' '}
        {moment(grant.valid_from, zone, false)}.
      </p>
      <label className="form-grid__field">
        <span className="form-grid__label">Действует по</span>
        <input className="form-grid__input" type="date" value={until}
               aria-label="Действует по"
               onChange={(event) => setUntil(event.target.value)} />
        <span className="field__hint">
          Пусто — бессрочно. Прошедшая дата закрывает доступ.
        </span>
      </label>
      {error && <p className="form-grid__error" role="alert">{error}</p>}
      <div className="side-panel__actions">
        <button type="button" className="btn" onClick={onClose}>Отмена</button>
        <button type="button" className="btn btn--dark" disabled={sending}
                onClick={() => void submit()}>
          {sending ? 'Сохраняем…' : 'Сохранить срок'}
        </button>
      </div>
    </Overlay>
  );
}

/** Список разрешений с человеческими названиями из каталога. */
export function PermissionList({ codes, catalog }: {
  codes: string[];
  catalog: api.PermissionRow[];
}) {
  const granted = useMemo(() => new Set(codes), [codes]);
  const groups = useMemo(
    () => sections(catalog.filter((item) => granted.has(item.code))),
    [catalog, granted],
  );
  if (groups.length === 0) {
    return <p className="empty">У роли нет ни одного разрешения.</p>;
  }
  return (
    <ul className="perms">
      {groups.map((group) => (
        <li key={group.prefix} className="perms__group">
          <p className="perms__title">{group.title}</p>
          <ul className="perms__list">
            {group.items.map((item) => (
              <li key={item.code} className="perms__row">
                <Icon name="check" size={15} />
                <span className="perms__text">
                  <span className="perms__name">{permissionTitle(item)}</span>
                  <span className="perms__code mono">{item.code}</span>
                </span>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

// --- история ----------------------------------------------------------------

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
          <Icon name="arrow" size={13} />
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

function AssignForm({
  user, roles, scopes, scopesKnown, onClose, onDone,
}: {
  user: api.CrmUser;
  roles: api.RoleFull[];
  scopes: api.AssignableScopes;
  /** Прочитан ли список областей. */
  scopesKnown: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [roleId, setRoleId] = useState('');
  const [region, setRegion] = useState('');
  const [office, setOffice] = useState('');
  const [until, setUntil] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);

  const role = roles.find((item) => item.id === roleId);
  const blocked = role ? grantBlockedBecause(role) : null;
  // Область проверяется отдельно от роли — ровно так же, как на
  // сервере: `_require_grantable` смотрит на права роли,
  // `_require_within_own_scope` — на территорию, и одно от другого
  // не зависит.
  const wrongScope = scopeHint(scopes, region, office);

  const submit = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setSending(true);
    setError(null);
    try {
      await api.assignRole({
        user_id: user.id,
        role_id: roleId,
        ...(office ? { office_id: office } : {}),
        ...(!office && region ? { region_id: region } : {}),
        // Дата, а не момент: конец суток ставит сервер в поясе
        // организации. Собирать момент здесь значило бы считать его по
        // поясу браузера — и показанная дата разошлась бы с сохранённой.
        ...(until ? { valid_to_date: until } : {}),
      });
      onDone();
    } catch (failure) {
      setError(
        failure instanceof ApiFailure
          ? messageFor(failure)
          : 'Не удалось выдать роль.',
      );
    } finally {
      busy.current = false;
      setSending(false);
    }
  }, [office, onDone, region, roleId, until, user.id]);

  return (
    <Overlay title="Назначить роль" onClose={onClose}>
      <label className="form-grid__field">
        <span className="form-grid__label">Роль</span>
        <select className="form-grid__input" value={roleId} aria-label="Роль"
                onChange={(event) => setRoleId(event.target.value)}>
          <option value="">Выберите роль</option>
          {roles.map((item) => (
            <option key={item.id} value={item.id} disabled={!item.grantable}>
              {item.name}
              {item.is_system ? ' · системная' : ''}
              {item.grantable ? '' : ' · недоступна'}
            </option>
          ))}
        </select>
      </label>
      {blocked && <p className="form-grid__error">{blocked}</p>}

      <ScopePicker scopes={scopes} known={scopesKnown} hint={wrongScope}
                   region={region} office={office}
                   onRegion={setRegion} onOffice={setOffice} />

      <label className="form-grid__field">
        <span className="form-grid__label">Действует по</span>
        <input className="form-grid__input" type="date" value={until}
               aria-label="Действует по"
               onChange={(event) => setUntil(event.target.value)} />
        <span className="field__hint">Пусто — без ограничения срока.</span>
      </label>

      {error && <p className="form-grid__error" role="alert">{error}</p>}
      <div className="side-panel__actions">
        <button type="button" className="btn" onClick={onClose}>Отмена</button>
        <button type="button" className="btn btn--dark"
                disabled={
                  sending || !roleId || Boolean(blocked) || Boolean(wrongScope)
                }
                onClick={() => void submit()}>
          {sending ? 'Выдаём…' : 'Назначить'}
        </button>
      </div>
    </Overlay>
  );
}

/**
 * Выбор области назначения. Один на обе формы — и в карточке, и при
 * заведении записи.
 *
 * Варианты приходят с сервера (`/grants/scopes`) и совпадают с
 * проверкой при выдаче: они считаются тем же кодом. Собирать регионы
 * из видимых офисов, как делала форма раньше, нельзя — регион без
 * офисов пропал бы вместе с возможностью выдать назначение на него,
 * а у технического администратора справочника регионов нет вовсе.
 *
 * «Вся организация» появляется только у того, кто вправе её выдать.
 * Раньше этот вариант стоял первым у всех, был выбран по умолчанию —
 * и администратор одного региона узнавал об отказе после нажатия
 * кнопки.
 */
export function ScopePicker({
  scopes, known, hint, region, office, onRegion, onOffice,
}: {
  scopes: api.AssignableScopes;
  known: boolean;
  /** Почему выбранное отправить нельзя. Считает `scopeHint`. */
  hint: string | null;
  region: string;
  office: string;
  onRegion: (id: string) => void;
  onOffice: (id: string) => void;
}) {
  // Офисы выбранного региона. Без региона — все доступные: область
  // «офис» самостоятельна и региона не требует.
  const here = region
    ? scopes.offices.filter((item) => item.region_id === region)
    : scopes.offices;

  return (
    <>
      {!known && (
        <p className="note note--dim">
          Список доступных областей не загрузился. Выбрать область сейчас
          нельзя; закройте форму и откройте её заново.
        </p>
      )}

      <label className="form-grid__field">
        <span className="form-grid__label">Регион</span>
        <select className="form-grid__input" value={region} aria-label="Регион"
                onChange={(event) => {
                  // Офис из другого региона несовместим с выбором:
                  // показанное обязано совпадать с отправляемым.
                  onRegion(event.target.value);
                  onOffice('');
                }}>
          <option value="">
            {scopes.all_organization ? 'Вся организация' : 'Не выбран'}
          </option>
          {scopes.regions.map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
      </label>

      <label className="form-grid__field">
        <span className="form-grid__label">Офис</span>
        <select className="form-grid__input" value={office} aria-label="Офис"
                onChange={(event) => onOffice(event.target.value)}>
          <option value="">
            {region || scopes.all_organization
              ? 'Весь выбранный уровень'
              : 'Не выбран'}
          </option>
          {here.map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
      </label>

      {hint && <p className="note note--dim">{hint}</p>}
    </>
  );
}

/**
 * Небольшое окно поверх карточки. Закрывается по Esc и по фону.
 *
 * Собственный класс `.dialog`, а не `.editor`: та в этом проекте —
 * многострочное ПОЛЕ ВВОДА, и окно, надевшее её имя, разъезжалось во всю
 * ширину экрана. `.veil` при этом остаётся общей: это ровно затемнение
 * фона, и второй такой заводить незачем.
 */
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
            <Icon name="cross" size={16} />
          </button>
        </div>
        <div className="dialog__body">{children}</div>
      </div>
    </div>
  );
}

export type { Rights };
export type { Block };
