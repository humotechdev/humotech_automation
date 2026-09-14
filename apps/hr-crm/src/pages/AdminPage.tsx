/**
 * Администрирование: учётные записи CRM, роли и журнал действий.
 *
 * Публичной регистрации в системе нет. Запись заводит уполномоченный
 * администратор, и она появляется без пароля и отключённой — пока
 * пароля нет, войти под ней нельзя вовсе.
 *
 * Всё, что здесь показано разрешённым, разрешено сервером: `grantable`
 * у роли, доступность области, отказ на последнем суперадминистраторе.
 * Скрытая кнопка защитой не является и никогда ею не была — она лишь
 * избавляет от отказа, который человек и так получил бы.
 *
 * Фильтры и выбранная строка живут в адресе. Это не украшение: закрытие
 * карточки не должно сбрасывать отбор, а ссылку на конкретную запись
 * можно передать.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AuditTab } from '../components/AuditTab';
import { AppIcon } from '../components/AppIcon';
import { RolesTab } from '../components/RolesTab';
import {
  Overlay,
  ScopePicker,
  UserCard,
  type Rights,
} from '../components/UserCard';
import { useSession } from '../features/auth/session';
import { useBlock } from '../features/dashboard/data';
import {
  STATUS,
  STATUS_TABS,
  demoMode,
  emptyText,
  initials,
  roleSummary,
  scopeHint,
  scopeSummary,
  userLine,
  userSubtitle,
  userTitle,
} from '../features/admin/model';

type Tab = 'users' | 'roles' | 'audit';

const TABS: Array<{ key: Tab; title: string }> = [
  { key: 'users', title: 'Пользователи' },
  { key: 'roles', title: 'Роли и права' },
  { key: 'audit', title: 'Журнал действий' },
];

const PAGE = '25';

/** Значение выполненного обещания или пустой список вместо отказа. */
function taken<T>(result: PromiseSettledResult<{ items: T[] }>): T[] {
  return result.status === 'fulfilled' ? result.value.items : [];
}

/**
 * Ничего не выбрать. Именно `all_organization: false`, а не «неизвестно»:
 * пустой список областей плюс запрет на всю организацию — это состояние,
 * в котором выдать нельзя ничего, и форма обязана сказать это прямо,
 * а не предлагать выбор из ничего.
 */
const NOTHING: api.AssignableScopes = {
  all_organization: false,
  regions: [],
  offices: [],
};

export function AdminPage() {
  const session = useSession();
  const mine = useMemo(
    () =>
      new Set(
        session.status === 'authenticated' ? session.user.permissions : [],
      ),
    [session],
  );
  const rights: Rights = {
    users: mine.has('users.manage'),
    roles: mine.has('roles.manage'),
    audit: mine.has('audit.read'),
  };
  // Пояс организации приходит вместе с сессией. Пустая строка означала
  // бы пояс браузера смотрящего — и журнал начал бы утверждать не тот
  // час, в который действие произошло на самом деле.
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as Tab) || 'users';
  const search = params.get('search') ?? '';
  const status = params.get('status') ?? '';
  const roleId = params.get('role_id') ?? '';
  const picked = params.get('id') ?? '';

  const patch = useCallback(
    (next: Record<string, string | null>) => {
      setParams(
        (was) => {
          const copy = new URLSearchParams(was);
          for (const [key, value] of Object.entries(next)) {
            if (value === null || value === '') copy.delete(key);
            else copy.set(key, value);
          }
          return copy;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const [attempt, setAttempt] = useState(0);
  const bump = useCallback(() => setAttempt((n) => n + 1), []);
  const [creating, setCreating] = useState(false);

  // --- справочники ----------------------------------------------------------

  // Справочники берутся по отдельности, а не одним `Promise.all`.
  // Права на них независимы, и общий отказ по одному оставил бы
  // страницу вообще без остальных — из-за списка, без которого прочее
  // прекрасно работает.
  //
  // Области берутся из `/grants/scopes`, а НЕ из `/regions/` и
  // `/offices/`. Те — справочники, и читаются они по `regions.read` и
  // `offices.read`; у технического администратора первого права нет
  // вовсе. Собирать регионы из видимых офисов, как было раньше, значит
  // терять регион без офисов — вместе с возможностью выдать назначение
  // на него.
  const [directory, reloadDirectory] = useBlock(
    (signal) =>
      Promise.allSettled([
        // Спрашивается только тогда, когда есть чем воспользоваться:
        // без `roles.manage` формы выдачи на странице нет.
        rights.roles
          ? api.assignableScopes(signal)
          : Promise.resolve(NOTHING),
        rights.roles
          ? api.roles(signal)
          : Promise.resolve({ items: [] as api.RoleFull[] }),
        rights.roles
          ? api.permissionCatalog(signal)
          : Promise.resolve({ items: [] as api.PermissionRow[] }),
      ]).then(([scopes, roles, catalog]) => ({
        scopes: scopes.status === 'fulfilled' ? scopes.value : NOTHING,
        scopesOwn: scopes.status === 'fulfilled',
        roles: taken(roles),
        catalog: taken(catalog),
        rolesOwn: roles.status === 'fulfilled',
      })),
    `admin-directory|${attempt}|${rights.roles}`,
    rights.users || rights.roles,
  );
  const book = directory.state === 'ready'
    ? directory.data
    : {
        scopes: NOTHING,
        scopesOwn: true,
        roles: [] as api.RoleFull[],
        catalog: [] as api.PermissionRow[],
        rolesOwn: true,
      };

  // --- список и сводка ------------------------------------------------------

  const filters = useMemo(
    () => ({
      ...(search ? { search } : {}),
      ...(status ? { status } : {}),
      ...(roleId ? { role_id: roleId } : {}),
      limit: PAGE,
    }),
    [roleId, search, status],
  );

  const [list, reloadList] = useBlock(
    (signal) =>
      Promise.all([
        api.crmUsers(filters, signal),
        api.crmUserCounts(filters, signal),
      ]).then(([page, counts]) => ({ page, counts })),
    `admin-users|${JSON.stringify(filters)}|${attempt}`,
    rights.users && tab === 'users',
  );

  const [card, reloadCard] = useBlock(
    (signal) => api.crmUser(picked, signal),
    `admin-user|${picked}|${attempt}`,
    rights.users && Boolean(picked),
  );

  const changed = useCallback(() => {
    // После любого изменения перечитываются и строка, и список, и
    // счётчики: устаревшая карточка рядом со свежим списком — это два
    // разных ответа на один вопрос.
    bump();
    reloadList();
    reloadCard();
  }, [bump, reloadCard, reloadList]);

  // --- страница -------------------------------------------------------------

  if (!rights.users && !rights.roles && !rights.audit) {
    return (
      <AppShell breadcrumb="Администрирование" section="admin">
        <Header demo={demoMode()} />
        <p className="empty empty--bad">
          Нет прав на администрирование. Управление учётными записями, ролями
          и журналом — отдельные разрешения; попросите их у того, кто уже
          администрирует систему.
        </p>
      </AppShell>
    );
  }

  return (
    <AppShell breadcrumb="Администрирование" section="admin">
      <Header
        demo={demoMode()}
        action={
          rights.users && tab === 'users' ? (
            <button type="button" className="btn btn--dark"
                    onClick={() => setCreating(true)}>
              <AppIcon name="plus" size={16} />
              Добавить пользователя
            </button>
          ) : null
        }
      />

      {directory.state === 'ready' && !book.rolesOwn && (
        <p className="empty empty--bad">
          Каталог ролей не загрузился. Пустой список ролей ниже — это ошибка
          запроса, а не отсутствие ролей.{' '}
          <button type="button" className="link" onClick={reloadDirectory}>
            Повторить
          </button>
        </p>
      )}

      <div className="tabs tabs--top" role="tablist" aria-label="Разделы администрирования">
        {TABS.map((item) => {
          if (item.key === 'roles' && !rights.roles) return null;
          const on = item.key === tab;
          const count =
            item.key === 'users'
              ? (list.state === 'ready' ? list.data.counts.total : null)
              : item.key === 'roles'
                ? (list.state === 'ready' ? list.data.counts.roles : book.roles.length)
                : null;
          return (
            <button key={item.key} type="button" role="tab" aria-selected={on}
                    className={on ? 'tab tab--on' : 'tab'}
                    onClick={() => patch({ tab: item.key === 'users' ? null : item.key })}>
              {item.title}
              {count !== null && count !== undefined && (
                <span className="tab__count">{count}</span>
              )}
            </button>
          );
        })}
      </div>

      {tab === 'users' && !rights.users && (
        <p className="empty empty--bad">
          Нет права вести учётные записи. Это отдельное разрешение{' '}
          <span className="mono">users.manage</span>.
        </p>
      )}

      {tab === 'users' && rights.users && (
        <>
          <ul className="summary" aria-label="Сводка по учётным записям">
            <Tile icon="users" title="Активные"
                  value={list.state === 'ready' ? list.data.counts.active : undefined} />
            <Tile icon="half" title="Неактивные"
                  value={list.state === 'ready' ? list.data.counts.inactive : undefined} />
            <Tile icon="admin" title="Ролей в каталоге"
                  value={
                    list.state === 'ready'
                      ? (list.data.counts.roles ?? undefined)
                      : undefined
                  }
                  note={
                    list.state === 'ready' && list.data.counts.roles === null
                      ? 'Каталог ролей вам не показан'
                      : undefined
                  } />
          </ul>

          <div className={picked ? 'split split--open' : 'split'}>
            <section className="panel panel--list" aria-label="Учётные записи">
              <div className="toolbar">
                <label className="find find--wide">
                  <AppIcon name="search" size={16} />
                  <input type="search" value={search}
                         placeholder="Поиск по имени или логину"
                         aria-label="Поиск по имени или логину"
                         onChange={(event) =>
                           patch({ search: event.target.value || null })} />
                </label>
                <label className="pick">
                  <span className="visually-hidden">Роль</span>
                  <select value={roleId} aria-label="Роль"
                          onChange={(event) => patch({ role_id: event.target.value || null })}>
                    <option value="">Все роли</option>
                    {book.roles.map((item) => (
                      <option key={item.id} value={item.id}>{item.name}</option>
                    ))}
                  </select>
                </label>
                <label className="pick">
                  <span className="visually-hidden">Статус</span>
                  <select value={status} aria-label="Статус"
                          onChange={(event) => patch({ status: event.target.value || null })}>
                    {STATUS_TABS.map((item) => (
                      <option key={item.key} value={item.key}>{item.title}</option>
                    ))}
                  </select>
                </label>
              </div>

              {list.state === 'loading' && <p className="empty">Загружаем список…</p>}
              {list.state === 'denied' && (
                <p className="empty">Сессия истекла. Войдите заново.</p>
              )}
              {list.state === 'error' && (
                <p className="empty empty--bad">
                  Не удалось загрузить список. Это ошибка запроса, а не пустой
                  список.{' '}
                  <button type="button" className="link" onClick={reloadList}>
                    Повторить
                  </button>
                </p>
              )}

              {list.state === 'ready' && (
                <UserTable
                  page={list.data.page}
                  counts={list.data.counts}
                  search={search}
                  filtered={Boolean(status || roleId)}
                  picked={picked}
                  onPick={(id) => patch({ id })}
                  filters={filters}
                />
              )}
            </section>

            {picked && card.state === 'loading' && (
              <section className="panel panel--view">
                <p className="empty">Открываем карточку…</p>
              </section>
            )}
            {picked && card.state === 'error' && (
              <section className="panel panel--view">
                <p className="empty empty--bad">
                  Не удалось открыть карточку.{' '}
                  <button type="button" className="link" onClick={reloadCard}>
                    Повторить
                  </button>
                </p>
              </section>
            )}
            {picked && card.state === 'ready' && (
              <UserCard
                user={card.data}
                zone={zone}
                rights={rights}
                scopes={book.scopes}
                scopesKnown={book.scopesOwn}
                roles={book.roles}
                catalog={book.catalog}
                onClose={() => patch({ id: null })}
                onChanged={changed}
              />
            )}
          </div>
        </>
      )}

      {tab === 'roles' && rights.roles && (
        <RolesTab roles={book.roles} catalog={book.catalog}
                  mayManage={rights.roles} mine={mine} onChanged={bump} />
      )}

      {tab === 'audit' && <AuditTab zone={zone} mayRead={rights.audit} />}

      {creating && (
        <CreateUser
          roles={book.roles}
          scopes={book.scopes}
          scopesKnown={book.scopesOwn}
          mayAssign={rights.roles}
          onClose={() => setCreating(false)}
          onDone={(id) => { setCreating(false); changed(); patch({ id }); }}
        />
      )}
    </AppShell>
  );
}

// --- шапка ------------------------------------------------------------------

function Header({ demo, action }: { demo: boolean; action?: React.ReactNode }) {
  return (
    <header className="head head--tight">
      <div>
        <h1 className="head__title">
          Администрирование
          {/* Метка только в явно включённом демонстрационном режиме:
              в рабочей установке она означала бы, что данным не верят. */}
          {demo && <span className="chip chip--demo">Демо-данные</span>}
        </h1>
        <p className="head__sub">Пользователи CRM, роли и области доступа</p>
      </div>
      {action && <div className="head__actions">{action}</div>}
    </header>
  );
}

function Tile({ icon, title, value, note }: {
  icon: Parameters<typeof AppIcon>[0]['name'];
  title: string;
  value: number | undefined;
  note?: string | undefined;
}) {
  return (
    <li className="tile">
      <span className="tile__icon" aria-hidden="true"><AppIcon name={icon} size={20} /></span>
      <span className="tile__text">
        <span className="tile__title">{title}</span>
        {/* Пока сводка не пришла — прочерк, а не ноль: ноль означал бы,
            что записей нет. */}
        <b className="tile__value">{value === undefined ? '—' : value}</b>
        {note && <span className="tile__note">{note}</span>}
      </span>
    </li>
  );
}

// --- таблица ----------------------------------------------------------------

function UserTable({ page, counts, search, filtered, picked, onPick, filters }: {
  page: api.Cursored<api.CrmUser>;
  counts: api.CrmUserCounts;
  search: string;
  filtered: boolean;
  picked: string;
  onPick: (id: string) => void;
  filters: api.CrmUserQuery;
}) {
  const [tail, setTail] = useState<api.CrmUser[]>([]);
  const [cursor, setCursor] = useState<string | null>(page.next_cursor);
  const [more, setMore] = useState(page.has_more);
  const [loading, setLoading] = useState(false);
  const busy = useRef(false);

  // Хвост принадлежит своему набору: смена фильтра делает его чужим.
  const key = JSON.stringify(filters);
  const shown = useRef(key);
  if (shown.current !== key) {
    shown.current = key;
    if (tail.length) setTail([]);
    setCursor(page.next_cursor);
    setMore(page.has_more);
  }

  const rows = [...page.items, ...tail];

  const loadMore = useCallback(async () => {
    if (busy.current || !cursor) return;
    busy.current = true;
    setLoading(true);
    try {
      const next = await api.crmUsers({ ...filters, cursor });
      setTail((was) => [...was, ...next.items]);
      setCursor(next.next_cursor);
      setMore(next.has_more);
    } catch {
      // Уже показанное остаётся: не дочитали — не значит «списка нет».
    } finally {
      busy.current = false;
      setLoading(false);
    }
  }, [cursor, filters]);

  if (rows.length === 0) {
    return <p className="empty">{emptyText(search, filtered)}</p>;
  }

  return (
    <>
      <div className="scroller">
        <table className="grid-table" aria-label="Учётные записи">
          <thead>
            <tr>
              <th scope="col">Пользователь</th>
              <th scope="col">Роль</th>
              <th scope="col">Область</th>
              <th scope="col">Статус</th>
              <th scope="col"><span className="visually-hidden">Открыть</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((user) => {
              const on = user.id === picked;
              return (
                <tr key={user.id} className={on ? 'row row--on' : 'row'}>
                  <td>
                    <button type="button" className="who who--go"
                            aria-current={on} onClick={() => onPick(user.id)}>
                      <span className="avatar avatar--sm" aria-hidden="true">
                        {initials(userTitle(user))}
                      </span>
                      <span className="who__text">
                        <span className="who__name">{userTitle(user)}</span>
                        <span className="who__id">
                          {userSubtitle(user) ?? user.email}
                        </span>
                      </span>
                    </button>
                  </td>
                  <td>
                    {user.grants_visible
                      ? roleSummary(user.active_grants)
                      : <span className="muted">Скрыто</span>}
                  </td>
                  <td>
                    {user.grants_visible
                      ? scopeSummary(user.active_grants)
                      : <span className="muted">—</span>}
                  </td>
                  <td>
                    <span className="state">
                      <i className="state__dot" />
                      {STATUS[user.status] ?? user.status}
                    </span>
                  </td>
                  <td className="num">
                    <button type="button" className="tool tool--ghost"
                            aria-label={`Открыть ${userTitle(user)}`}
                            onClick={() => onPick(user.id)}>
                      <AppIcon name="arrow" size={16} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="sheet__foot">
        <p className="sheet__hint">{userLine(rows.length, more, counts.total)}</p>
        <div className="pager__tools">
          {more && (
            <button type="button" className="btn btn--small" disabled={loading}
                    onClick={() => void loadMore()}>
              {loading ? 'Читаем…' : 'Показать ещё'}
            </button>
          )}
          <p className="sheet__hint">
            <AppIcon name="alert" size={16} /> Учётные записи создают администраторы.
          </p>
        </div>
      </div>
    </>
  );
}

// --- создание учётной записи -------------------------------------------------

/**
 * Заведение записи — это до трёх отдельных серверных операций: создать,
 * задать пароль, выдать роль. Частичный успех здесь обычное дело, и
 * форма обязана его различать: если запись создалась, а роль не выдалась,
 * второй заход НЕ создаёт человека заново — он продолжает с того места,
 * где остановился.
 */
function CreateUser({
  roles, scopes, scopesKnown, mayAssign, onClose, onDone,
}: {
  roles: api.RoleFull[];
  scopes: api.AssignableScopes;
  scopesKnown: boolean;
  mayAssign: boolean;
  onClose: () => void;
  onDone: (id: string) => void;
}) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [roleId, setRoleId] = useState('');
  const [region, setRegion] = useState('');
  const [office, setOffice] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);
  // Что уже получилось. Переживает ошибку следующего шага намеренно.
  //
  // Шагов четыре, и каждый отмечается отдельно. Объединять их нельзя:
  // включение записи — самостоятельный вызов после установки пароля, и
  // если отметить «пароль установлен» разом за оба, то отказ на
  // включении навсегда пропустил бы этот шаг при повторе. Запись
  // осталась бы отключённой с рабочим паролем, и молча.
  const [done, setDone] = useState<{
    id: string | null;
    password: boolean;
    active: boolean;
    role: boolean;
  }>({ id: null, password: false, active: false, role: false });

  // Область важна только вместе с ролью: без роли назначение не
  // отправляется вовсе, и требовать выбор области было бы придиркой.
  const wrongScope =
    roleId && mayAssign ? scopeHint(scopes, region, office) : null;

  // Введённое, но не отправленное. Незавершённый шаг тоже считается:
  // закрыть форму, когда запись уже создана, а роль ещё нет, значит
  // оставить человека без роли и не сказать об этом.
  const dirty =
    (!done.id && (email !== '' || password !== '' || roleId !== ''))
    || (done.id !== null && ((password !== '' && !done.password)
        || (roleId !== '' && mayAssign && !done.role)));

  // Предупреждение при закрытии вкладки с несохранённым.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const close = useCallback(() => {
    if (dirty && !window.confirm(
      done.id
        ? 'Запись создана, но не всё готово. Закрыть форму?'
        : 'Введённое не сохранено. Закрыть форму?',
    )) return;
    onClose();
  }, [dirty, done.id, onClose]);

  const submit = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setSending(true);
    setError(null);
    let id = done.id;
    try {
      if (!id) {
        const created = await api.createCrmUser({ email });
        id = created.id;
        setDone((was) => ({ ...was, id }));
      }
      if (password && !done.password) {
        await api.setCrmUserPassword(id, password);
        setDone((was) => ({ ...was, password: true }));
        // Значение не переживает успешный ответ.
        setPassword('');
      }
      // Включение — отдельный шаг и отдельная отметка: пароль уже
      // установлен, и повтор не должен ни требовать его снова, ни
      // пропускать включение.
      if ((password || done.password) && !done.active) {
        await api.activateCrmUser(id);
        setDone((was) => ({ ...was, active: true }));
      }
      if (roleId && mayAssign && !done.role) {
        await api.assignRole({
          user_id: id,
          role_id: roleId,
          ...(office ? { office_id: office } : {}),
          ...(!office && region ? { region_id: region } : {}),
        });
        setDone((was) => ({ ...was, role: true }));
      }
      onDone(id);
    } catch (failure) {
      setError(
        failure instanceof ApiFailure
          ? messageFor(failure)
          : 'Не удалось завершить создание.',
      );
    } finally {
      busy.current = false;
      setSending(false);
    }
  }, [done, email, mayAssign, office, onDone, password, region, roleId]);

  return (
    <Overlay title="Новая учётная запись" onClose={close}>
      <p className="muted">
        Учётная запись CRM — это доступ к системе, а не сотрудник. Заведение
        записи не создаёт человека в штате и не привязывает Telegram.
      </p>

      <label className="form-grid__field">
        <span className="form-grid__label">Логин (адрес почты)</span>
        <input className="form-grid__input" type="email" value={email}
               aria-label="Логин" autoComplete="off"
               disabled={Boolean(done.id)}
               onChange={(event) => setEmail(event.target.value)} />
      </label>

      <label className="form-grid__field">
        <span className="form-grid__label">Пароль</span>
        <input className="form-grid__input" type="password" value={password}
               aria-label="Пароль" autoComplete="new-password"
               onChange={(event) => setPassword(event.target.value)} />
        <span className="field__hint">
          Без пароля запись останется отключённой: войти под ней будет нельзя.
        </span>
      </label>

      {mayAssign && (
        <>
          <label className="form-grid__field">
            <span className="form-grid__label">Роль</span>
            <select className="form-grid__input" value={roleId} aria-label="Роль"
                    onChange={(event) => setRoleId(event.target.value)}>
              <option value="">Без роли</option>
              {roles.map((item) => (
                <option key={item.id} value={item.id} disabled={!item.grantable}>
                  {item.name}{item.grantable ? '' : ' · недоступна'}
                </option>
              ))}
            </select>
          </label>
          {/* Область спрашивается только вместе с ролью: без роли
              выдавать нечего, и назначение не отправляется вовсе. */}
          {roleId && (
            <ScopePicker scopes={scopes} known={scopesKnown}
                         hint={wrongScope}
                         region={region} office={office}
                         onRegion={setRegion} onOffice={setOffice} />
          )}
        </>
      )}

      {done.id && (
        <p className="note note--dim">
          Запись уже создана{done.password ? ', пароль установлен' : ''}
          {done.active ? ', доступ включён' : ''}
          {done.role ? ', роль выдана' : ''}. Повторное нажатие продолжит с
          незавершённого шага и не заведёт второго пользователя.
        </p>
      )}
      {error && <p className="form-grid__error" role="alert">{error}</p>}

      <div className="side-panel__actions">
        <button type="button" className="btn" onClick={close}>Отмена</button>
        <button type="button" className="btn btn--dark"
                disabled={sending || (!email && !done.id) || Boolean(wrongScope)}
                onClick={() => void submit()}>
          {sending ? 'Сохраняем…' : done.id ? 'Продолжить' : 'Создать'}
        </button>
      </div>
    </Overlay>
  );
}
