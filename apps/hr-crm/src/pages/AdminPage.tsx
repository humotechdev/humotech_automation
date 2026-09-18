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

import { useCallback, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AuditTab } from '../components/AuditTab';
import { AppIcon } from '../components/AppIcon';
import { AppSelectField } from '../components/AppSelect';
import { UserCard } from '../components/UserCard';
import { useSession } from '../features/auth/session';
import { useBlock } from '../features/dashboard/data';
import { moment } from '../features/time/zone';
import {
  STATUS,
  STATUS_TABS,
  demoMode,
  emptyText,
  initials,
  userLine,
  userSubtitle,
  userTitle,
} from '../features/admin/model';

type Tab = 'users' | 'audit';

const TABS: Array<{ key: Tab; title: string }> = [
  { key: 'users', title: 'Учётные записи' },
  { key: 'audit', title: 'Журнал действий' },
];

const PAGE = '25';

export function AdminPage() {
  const session = useSession();
  // Пояс организации приходит вместе с сессией. Пустая строка означала
  // бы пояс браузера смотрящего — и журнал начал бы утверждать не тот
  // час, в который действие произошло на самом деле.
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as Tab) || 'users';
  const search = params.get('search') ?? '';
  const status = params.get('status') ?? '';
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

  // --- список и сводка ------------------------------------------------------

  const filters = useMemo(
    () => ({
      ...(search ? { search } : {}),
      ...(status ? { status } : {}),
      limit: PAGE,
    }),
    [search, status],
  );

  const [list, reloadList] = useBlock(
    (signal) =>
      Promise.all([
        api.crmUsers(filters, signal),
        api.crmUserCounts(filters, signal),
      ]).then(([page, counts]) => ({ page, counts })),
    `admin-users|${JSON.stringify(filters)}|${attempt}`,
    tab === 'users',
  );

  const [card, reloadCard] = useBlock(
    (signal) => api.crmUser(picked, signal),
    `admin-user|${picked}|${attempt}`,
    Boolean(picked),
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

  return (
    <AppShell breadcrumb="Администрирование" section="admin">
      {/* Новых администраторов заводят на сервере: в интерфейсе нет ни
          ролей, ни выдачи доступа — есть один администратор, и ему
          открыто всё. */}
      <Header demo={demoMode()} />

      <div className="tabs tabs--top" role="tablist" aria-label="Разделы администрирования">
        {TABS.map((item) => {
          const on = item.key === tab;
          const count =
            item.key === 'users'
              ? (list.state === 'ready' ? list.data.counts.total : null)
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

      {tab === 'users' && (
        <>
          <ul className="summary" aria-label="Сводка по учётным записям">
            <Tile icon="users" title="Активные"
                  value={list.state === 'ready' ? list.data.counts.active : undefined} />
            <Tile icon="half" title="Неактивные"
                  value={list.state === 'ready' ? list.data.counts.inactive : undefined} />
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
                <AppSelectField className="toolbar-select" label="Статус" value={status} onChange={(value) => patch({ status: value || null })}>
                    {STATUS_TABS.map((item) => (
                      <option key={item.key} value={item.key}>{item.title}</option>
                    ))}
                </AppSelectField>
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
                  filtered={Boolean(status)}
                  picked={picked}
                  onPick={(id) => patch({ id })}
                  filters={filters}
                  zone={zone}
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
                onClose={() => patch({ id: null })}
                onChanged={changed}
              />
            )}
          </div>
        </>
      )}

      {tab === 'audit' && <AuditTab zone={zone} />}
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

function UserTable({ page, counts, search, filtered, picked, onPick, filters, zone }: {
  page: api.Cursored<api.CrmUser>;
  counts: api.CrmUserCounts;
  search: string;
  filtered: boolean;
  picked: string;
  onPick: (id: string) => void;
  filters: api.CrmUserQuery;
  /** Пояс организации: время последнего входа показывается в нём. */
  zone: string;
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
        <table className="grid-table table-cards" aria-label="Учётные записи">
          <thead>
            <tr>
              <th scope="col">Пользователь</th>
              <th scope="col">Последний вход</th>
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
                  <td data-label="Последний вход">
                    {user.last_login
                      ? moment(user.last_login, zone, false)
                      : <span className="muted">ещё не входил</span>}
                  </td>
                  <td data-label="Статус">
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
