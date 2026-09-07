/**
 * Каталог ролей: что за роль, какие права в ней и можно ли её править.
 *
 * Границу «системная / роль организации» проводит сервер, а не этот
 * файл: у системной роли `organization_id` пуст, она общая для всех
 * организаций, и правка её из одной означала бы изменение прав
 * остальным. Здесь это только показывается — и объясняется.
 *
 * Права не додумываются. Ни одно из них не «включается заодно»: если в
 * действующей политике нет зависимости «управление подразумевает
 * чтение», то и здесь её нет. Каталог приходит с сервера целиком, и
 * второго списка названий рядом с ним не заводится.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { Icon } from './nav-icons';
import { Overlay } from './UserCard';
import { permissionTitle, sections } from '../features/admin/model';

type Props = {
  roles: api.RoleFull[];
  catalog: api.PermissionRow[];
  /** Может ли этот человек вообще править каталог. Решает сервер. */
  mayManage: boolean;
  /** Права самого смотрящего: вложить можно только своё. */
  mine: ReadonlySet<string>;
  onChanged: () => void;
};

export function RolesTab({ roles, catalog, mayManage, mine, onChanged }: Props) {
  const [search, setSearch] = useState('');
  const [picked, setPicked] = useState<string | null>(roles[0]?.id ?? null);
  const [editing, setEditing] = useState<'new' | 'edit' | null>(null);

  const found = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return roles;
    return roles.filter(
      (role) =>
        role.name.toLowerCase().includes(needle)
        || role.code.toLowerCase().includes(needle),
    );
  }, [roles, search]);

  const role = roles.find((item) => item.id === picked) ?? found[0];

  return (
    <div className={role ? 'split split--open' : 'split'}>
      <section className="panel panel--list" aria-label="Роли">
        <div className="toolbar">
          <label className="find find--wide">
            <Icon name="search" size={16} />
            <input type="search" value={search} placeholder="Поиск роли"
                   aria-label="Поиск роли"
                   onChange={(event) => setSearch(event.target.value)} />
          </label>
          {mayManage && (
            <button type="button" className="btn btn--dark btn--small"
                    onClick={() => setEditing('new')}>
              <Icon name="plus" size={15} />
              Роль
            </button>
          )}
        </div>

        {found.length === 0 ? (
          <p className="empty">По этому запросу ролей нет.</p>
        ) : (
          <ul className="roster">
            {found.map((item) => {
              const on = item.id === role?.id;
              return (
                <li key={item.id}>
                  <button type="button"
                          className={on ? 'roster__go roster__go--on' : 'roster__go'}
                          aria-current={on}
                          onClick={() => setPicked(item.id)}>
                    <span className="who">
                      <span className="who__text">
                        <span className="who__name">{item.name}</span>
                        <span className="who__id mono">{item.code}</span>
                      </span>
                    </span>
                    <span className="role-meta">
                      <span className="chip">
                        {item.is_system ? 'Системная' : 'Своя'}
                      </span>
                      <span className="muted">{item.permissions.length} прав</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <div className="sheet__foot">
          <p className="sheet__hint">
            Системные роли общие для всех организаций и здесь только читаются.
          </p>
        </div>
      </section>

      {role && (
        <section className="panel panel--view" aria-label="Роль">
          <div className="view__head">
            <span className="view__icon" aria-hidden="true">
              <Icon name="admin" size={20} />
            </span>
            <div className="view__who">
              <h2 className="view__title">{role.name}</h2>
              <p className="view__sub mono">{role.code}</p>
              <div className="view__badges">
                <span className="chip">
                  {role.is_system ? 'Системная роль' : 'Роль организации'}
                </span>
                {!role.grantable && (
                  <span className="chip">Недоступна для выдачи</span>
                )}
              </div>
            </div>
            {mayManage && !role.is_system && (
              <div className="view__actions">
                <button type="button" className="btn btn--small"
                        onClick={() => setEditing('edit')}>
                  <Icon name="pencil" size={15} />
                  Изменить
                </button>
              </div>
            )}
          </div>

          <div className="view__body">
            {role.description && <p className="side-panel__text">{role.description}</p>}

            {role.is_system && (
              <p className="note note--dim">
                Системную роль изменить нельзя: она общая для всех организаций,
                и правка отсюда поменяла бы права остальным. Чтобы получить
                другой набор, заведите роль организации.
              </p>
            )}
            {!role.grantable && (
              <p className="note note--dim">
                Выдать эту роль вы не можете: у вас самих нет прав{' '}
                {role.missing_permissions.join(', ')}.
              </p>
            )}

            <p className="side-panel__label">
              Разрешения <span className="tab__count">{role.permissions.length}</span>
            </p>
            <RolePermissions codes={role.permissions} catalog={catalog} />
          </div>
        </section>
      )}

      {editing && (
        <RoleForm
          role={editing === 'edit' ? role : undefined}
          catalog={catalog}
          mine={mine}
          onClose={() => setEditing(null)}
          onDone={() => { setEditing(null); onChanged(); }}
        />
      )}
    </div>
  );
}

function RolePermissions({ codes, catalog }: {
  codes: string[];
  catalog: api.PermissionRow[];
}) {
  const granted = useMemo(() => new Set(codes), [codes]);
  const groups = useMemo(() => sections(catalog), [catalog]);
  return (
    <ul className="perms">
      {groups.map((group) => {
        const inside = group.items.filter((item) => granted.has(item.code));
        if (inside.length === 0) return null;
        return (
          <li key={group.prefix} className="perms__group">
            <p className="perms__title">{group.title}</p>
            <ul className="perms__list">
              {inside.map((item) => (
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
        );
      })}
    </ul>
  );
}

// --- создание и правка ------------------------------------------------------

function RoleForm({ role, catalog, mine, onClose, onDone }: {
  role?: api.RoleFull | undefined;
  catalog: api.PermissionRow[];
  mine: ReadonlySet<string>;
  onClose: () => void;
  onDone: () => void;
}) {
  const [code, setCode] = useState(role?.code ?? '');
  const [name, setName] = useState(role?.name ?? '');
  const [description, setDescription] = useState(role?.description ?? '');
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(role?.permissions ?? []),
  );
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);
  const start = useRef({
    name: role?.name ?? '',
    description: role?.description ?? '',
    permissions: [...(role?.permissions ?? [])].sort().join(','),
  });

  const dirty =
    name !== start.current.name
    || description !== start.current.description
    || [...chosen].sort().join(',') !== start.current.permissions
    || (!role && code !== '');

  // Предупреждение при закрытии вкладки с несохранёнными правками.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const close = useCallback(() => {
    if (dirty && !window.confirm('Изменения не сохранены. Закрыть форму?')) return;
    onClose();
  }, [dirty, onClose]);

  const toggle = useCallback((permission: string) => {
    setChosen((was) => {
      const next = new Set(was);
      // Ровно одно разрешение за нажатие. Ни одно другое не включается
      // «заодно»: связей между правами в действующей политике нет, и
      // выдумывать их здесь значило бы выдать не то, что прочитали.
      if (next.has(permission)) next.delete(permission);
      else next.add(permission);
      return next;
    });
  }, []);

  const submit = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setSending(true);
    setError(null);
    try {
      if (role) {
        await api.updateRole(role.id, {
          name,
          description,
          permissions: [...chosen],
          // Редакция, которую правим: сервер откажет конфликтом, если
          // роль успел изменить кто-то ещё.
          expected_updated_at: role.updated_at,
        });
      } else {
        await api.createRole({
          code,
          name,
          description,
          permissions: [...chosen],
        });
      }
      onDone();
    } catch (failure) {
      // Введённое не стирается: человек поправит одно поле, а не
      // наберёт форму заново.
      setError(
        failure instanceof ApiFailure
          ? messageFor(failure)
          : 'Не удалось сохранить роль.',
      );
    } finally {
      busy.current = false;
      setSending(false);
    }
  }, [chosen, code, description, name, onDone, role]);

  const groups = sections(catalog);

  return (
    <Overlay title={role ? `Роль «${role.name}»` : 'Новая роль'} onClose={close}>
      {!role && (
        <label className="form-grid__field">
          <span className="form-grid__label">Код</span>
          <input className="form-grid__input" value={code} aria-label="Код роли"
                 placeholder="OFFICE_VIEWER"
                 onChange={(event) => setCode(event.target.value)} />
          <span className="field__hint">
            Приводится к верхнему регистру и потом не меняется.
          </span>
        </label>
      )}
      <label className="form-grid__field">
        <span className="form-grid__label">Название</span>
        <input className="form-grid__input" value={name} aria-label="Название роли"
               onChange={(event) => setName(event.target.value)} />
      </label>
      <label className="form-grid__field">
        <span className="form-grid__label">Описание</span>
        <input className="form-grid__input" value={description}
               aria-label="Описание роли"
               onChange={(event) => setDescription(event.target.value)} />
      </label>

      <p className="side-panel__label">Разрешения</p>
      <p className="muted">
        Вложить можно только те права, которые есть у вас самих: иначе роль
        стала бы способом выдать себе больше в два шага. Недоступные
        показаны и отключены.
      </p>
      <ul className="perms perms--pick">
        {groups.map((group) => (
          <li key={group.prefix} className="perms__group">
            <p className="perms__title">{group.title}</p>
            <ul className="perms__list">
              {group.items.map((item) => {
                const allowed = mine.has(item.code);
                return (
                  <li key={item.code}>
                    <label className={allowed ? 'perms__pick' : 'perms__pick perms__pick--off'}>
                      <input type="checkbox" checked={chosen.has(item.code)}
                             disabled={!allowed}
                             onChange={() => toggle(item.code)} />
                      <span className="perms__text">
                        <span className="perms__name">{permissionTitle(item)}</span>
                        <span className="perms__code mono">{item.code}</span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
      </ul>

      {error && <p className="form-grid__error" role="alert">{error}</p>}
      <div className="side-panel__actions">
        <button type="button" className="btn" onClick={close}>Отмена</button>
        <button type="button" className="btn btn--dark"
                disabled={sending || !name || (!role && !code)}
                onClick={() => void submit()}>
          {sending ? 'Сохраняем…' : role ? 'Сохранить' : 'Создать роль'}
        </button>
      </div>
    </Overlay>
  );
}
