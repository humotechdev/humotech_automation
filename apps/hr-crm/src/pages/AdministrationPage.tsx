/**
 * Администрирование: один экран, четыре справочника.
 *
 * Раньше это было оглавление с переходом на шесть отдельных страниц.
 * Справочник из трёх строк не стоит отдельной страницы: человек заводит
 * отдел и должность подряд, и два перехода между ними — это два
 * ожидания загрузки ради двух полей.
 *
 * Поэтому всё здесь: блок раскрывается на месте, список листается
 * внутри себя, добавление открывает небольшое окно по центру. Со
 * страницы никуда не уходят.
 *
 * Офисов среди блоков нет. Адрес, карта, геозона и QR-точки — это
 * настройка места, и она живёт в «Офисах и регионах»; два места, где
 * правят один и тот же офис, дают два разных ответа на один вопрос.
 */

import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppSelectField } from '../components/AppSelect';
import { Confirm, Refusal, useSaving } from '../components/admin/Parts';
import { Modal, ModalField, ModalTools } from '../components/admin/Modal';
import { useBlock } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import {
  OFFERED_ROLES,
  SECTIONS,
  WEEKDAYS,
  counted,
  roleTitle,
  scheduleLine,
} from '../features/admin/catalog';
import type { SectionKey } from '../features/admin/catalog';
import { demoMode } from '../features/admin/model';
import '../styles/admin.css';

/** Одна строка любого справочника, приведённая к общему виду. */
type Row = {
  id: string;
  title: string;
  /** Что показать второй строкой: роль, офис, часы, правила. */
  note: string | null;
  /** Сколько раз значение уже использовано. 0 — можно удалить совсем. */
  used: number;
  archived: boolean;
  /** Эту строку нельзя убрать: единственный вход в систему. */
  locked?: boolean;
};

export function AdministrationPage() {
  const session = useSession();
  const me = session.status === 'authenticated' ? session.user.id : '';

  const [params, setParams] = useSearchParams();
  const open = (params.get('open') as SectionKey | null) ?? null;
  const setOpen = useCallback(
    (key: SectionKey | null) => {
      setParams(
        (was) => {
          const copy = new URLSearchParams(was);
          if (key) copy.set('open', key);
          else copy.delete('open');
          return copy;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const [attempt, setAttempt] = useState(0);
  const again = useCallback(() => setAttempt((n) => n + 1), []);
  const [adding, setAdding] = useState<SectionKey | null>(null);
  const [editing, setEditing] = useState<{ key: SectionKey; id: string } | null>(null);
  const [removing, setRemoving] = useState<{ key: SectionKey; row: Row } | null>(null);
  const act = useSaving();

  // Все четыре справочника грузятся сразу: числа стоят в закрытых блоках,
  // и «—» вместо количества до раскрытия ничего не сообщает.
  const [admins, reloadAdmins] = useBlock(
    (signal) => api.crmUsers({ limit: '100' }, signal),
    `adm-admins|${attempt}`,
  );
  const [departments, reloadDepartments] = useBlock(
    (signal) => api.departmentsPage({}, signal),
    `adm-departments|${attempt}`,
  );
  const [positions, reloadPositions] = useBlock(
    (signal) => api.positionsPage({}, signal),
    `adm-positions|${attempt}`,
  );
  const [schedules, reloadSchedules] = useBlock(
    (signal) => api.schedulesPage({}, signal),
    `adm-schedules|${attempt}`,
  );

  const done = useCallback(() => {
    setAdding(null);
    setEditing(null);
    setRemoving(null);
    again();
    reloadAdmins();
    reloadDepartments();
    reloadPositions();
    reloadSchedules();
  }, [again, reloadAdmins, reloadDepartments, reloadPositions, reloadSchedules]);

  const rows: Record<SectionKey, Row[] | null> = useMemo(() => ({
    admins: admins.state === 'ready'
      ? admins.data.items.map((one) => ({
          id: one.id,
          title: one.full_name ?? one.email,
          note: [
            one.grants_visible && one.active_grants.length
              ? roleTitle(one.active_grants[0]!.role_code,
                          one.active_grants[0]!.role_name)
              : 'без роли',
            one.status === 'ACTIVE' ? null : 'доступ закрыт',
          ].filter(Boolean).join(' · '),
          used: 0,
          archived: one.status !== 'ACTIVE',
          // Себя убрать нельзя: это единственный способ остаться без
          // входа в собственную систему.
          locked: one.id === me,
        }))
      : null,
    departments: departments.state === 'ready'
      ? departments.data.items.map((one) => ({
          id: one.id,
          title: one.name,
          note: one.office_name,
          used: one.staff ?? 0,
          archived: one.status !== 'ACTIVE',
        }))
      : null,
    positions: positions.state === 'ready'
      ? positions.data.items.map((one) => ({
          id: one.id,
          title: one.name,
          note: one.staff ? `${one.staff} чел.` : null,
          used: one.staff ?? 0,
          archived: one.status !== 'ACTIVE',
        }))
      : null,
    schedules: schedules.state === 'ready'
      ? schedules.data.items.map((one) => ({
          id: one.id,
          title: one.name,
          note: one.weekly_minutes
            ? `${Math.round(one.weekly_minutes / 60)} ч в неделю`
            : null,
          // Сколько людей на графике, список не отдаёт; сервер откажет
          // сам, если график назначен, и объяснит причину.
          used: 0,
          archived: one.status !== 'ACTIVE',
        }))
      : null,
  }), [admins, departments, positions, schedules, me]);

  return (
    <AppShell breadcrumb="Администрирование" section="admin">
      <header className="adm-top">
        <h1 className="adm-top__title">
          Администрирование
          {demoMode() && <span className="chip chip--demo">Демо-данные</span>}
        </h1>
        <p className="adm-top__sub">
          Настройте списки, которые используются при добавлении сотрудников.
        </p>
      </header>

      <section className="adm-sheet" aria-label="Справочники">
        {SECTIONS.map((section) => (
          <Block
            key={section.key}
            section={section}
            rows={rows[section.key]}
            open={open === section.key}
            onToggle={() => setOpen(open === section.key ? null : section.key)}
            onAdd={() => setAdding(section.key)}
            onEdit={(id) => setEditing({ key: section.key, id })}
            onRemove={(row) => setRemoving({ key: section.key, row })}
          />
        ))}
      </section>

      <p className="adm-foot">
        <AppIcon name="alert" size={16} />
        Офисы, карта и QR-точки настраиваются в разделе «Офисы и регионы».
      </p>

      <Refusal text={act.refusal} />

      {adding && (
        <AddForm which={adding} onClose={() => setAdding(null)} onSaved={done} />
      )}

      {editing && (
        <EditForm
          which={editing.key}
          id={editing.id}
          onClose={() => setEditing(null)}
          onSaved={done}
        />
      )}

      {removing && (
        <Confirm
          title={removing.row.used ? 'Архивировать' : 'Удалить'}
          what={
            removing.row.used
              ? `«${removing.row.title}» пропадёт из выбора при добавлении сотрудника.`
              : `«${removing.row.title}» будет убрано совсем.`
          }
          consequence={
            removing.row.used
              ? `Значение уже используется (${removing.row.used}). Удалить его нельзя: это стёрло бы часть истории. Прежние записи останутся как есть.`
              : 'На это значение никто не ссылался, поэтому оно убирается целиком.'
          }
          confirmLabel={removing.row.used ? 'Архивировать' : 'Удалить'}
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setRemoving(null)}
          onConfirm={() => {
            void act.run(
              () => remove(removing.key, removing.row),
              done,
            );
          }}
        />
      )}
    </AppShell>
  );
}

/** Убрать значение: совсем, если им не пользовались, иначе — в архив. */
function remove(which: SectionKey, row: Row): Promise<unknown> {
  if (which === 'admins') return api.deactivateCrmUser(row.id);
  if (row.used > 0) {
    switch (which) {
      case 'departments':
        return api.setDepartmentActive(row.id, false);
      case 'positions':
        return api.setPositionActive(row.id, false);
      case 'schedules':
        return api.setScheduleActive(row.id, false);
      default:
        return api.setPositionActive(row.id, false);
    }
  }
  switch (which) {
    case 'departments':
      return api.deleteDepartment(row.id);
    case 'schedules':
      return api.deleteSchedule(row.id);
    default:
      return api.deletePosition(row.id);
  }
}

// --- блок -------------------------------------------------------------------

function Block({ section, rows, open, onToggle, onAdd, onEdit, onRemove }: {
  section: (typeof SECTIONS)[number];
  rows: Row[] | null;
  open: boolean;
  onToggle: () => void;
  onAdd: () => void;
  onEdit: (id: string) => void;
  onRemove: (row: Row) => void;
}) {
  const alive = rows?.filter((one) => !one.archived) ?? null;

  return (
    <article className={open ? 'adm-block adm-block--open' : 'adm-block'}>
      <div className="adm-block__head">
        <span className={`adm-block__icon adm-block__icon--${section.key}`}
              aria-hidden="true">
          <AppIcon name={section.icon} size={20} />
        </span>

        <button type="button" className="adm-block__hit" onClick={onToggle}
                aria-expanded={open}>
          <span className="adm-block__title">{section.title}</span>
          <span className="adm-block__about">{section.about}</span>
          <span className="adm-block__count">
            {/* Пока ответ не пришёл — прочерк: ноль означал бы, что
                записей нет, а мы этого ещё не знаем. */}
            {alive === null ? '—' : counted(alive.length, section.unit)}
          </span>
        </button>

        <button type="button" className="btn btn--primary adm-block__add"
                onClick={onAdd}>
          <AppIcon name="plus" size={16} /> {section.add}
        </button>

        <button type="button" className="adm-block__chevron" onClick={onToggle}
                aria-label={open ? 'Свернуть' : 'Развернуть'} aria-expanded={open}>
          <AppIcon name="chevron" size={18} />
        </button>
      </div>

      {open && (
        <div className="adm-block__body">
          {alive === null && <p className="adm-block__empty">Загружаем…</p>}
          {alive !== null && rows!.length === 0 && (
            <p className="adm-block__empty">{section.empty}</p>
          )}
          {alive !== null && rows!.length > 0 && (
            <ul className="adm-rows">
              {rows!.map((row) => (
                <li key={row.id}
                    className={row.archived ? 'adm-row adm-row--off' : 'adm-row'}>
                  <span className="adm-row__text">
                    <span className="adm-row__title">{row.title}</span>
                    {(row.note || row.archived) && (
                      <span className="adm-row__note">
                        {[row.note, row.archived ? 'в архиве' : null]
                          .filter(Boolean).join(' · ')}
                      </span>
                    )}
                  </span>
                  <span className="adm-row__tools">
                    <button type="button" className="adm-row__tool"
                            aria-label={`Изменить «${row.title}»`}
                            onClick={() => onEdit(row.id)}>
                      <AppIcon name="pencil" size={16} />
                    </button>
                    <button type="button" className="adm-row__tool adm-row__tool--bad"
                            aria-label={
                              row.locked
                                ? `«${row.title}» — это вы: убрать нельзя`
                                : `${row.used ? 'Архивировать' : 'Удалить'} «${row.title}»`
                            }
                            disabled={row.locked}
                            title={row.locked ? 'Это вы: убрать себя нельзя' : undefined}
                            onClick={() => onRemove(row)}>
                      <AppIcon name={row.used ? 'archive' : 'cross'} size={16} />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </article>
  );
}

// --- формы ------------------------------------------------------------------

function AddForm({ which, onClose, onSaved }: {
  which: SectionKey;
  onClose: () => void;
  onSaved: () => void;
}) {
  if (which === 'admins') return <AdminForm onClose={onClose} onSaved={onSaved} />;
  if (which === 'schedules') {
    return <ScheduleForm onClose={onClose} onSaved={onSaved} />;
  }
  return <NameForm which={which} onClose={onClose} onSaved={onSaved} />;
}

function EditForm({ which, id, onClose, onSaved }: {
  which: SectionKey;
  id: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  if (which === 'admins') {
    return <AdminForm id={id} onClose={onClose} onSaved={onSaved} />;
  }
  if (which === 'schedules') {
    return <ScheduleForm id={id} onClose={onClose} onSaved={onSaved} />;
  }
  return <NameForm which={which} id={id} onClose={onClose} onSaved={onSaved} />;
}

/** Отдел и должность: одно поле и ничего больше. */
function NameForm({ which, id, onClose, onSaved }: {
  which: SectionKey;
  id?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const department = which === 'departments';
  const [name, setName] = useState('');
  const [touched, setTouched] = useState(false);
  const saving = useSaving();

  const [current] = useBlock(
    (signal) =>
      id
        ? (department
            ? api.departmentsPage({}, signal).then(
                (page) => page.items.find((one) => one.id === id)?.name ?? '')
            : api.positionsPage({}, signal).then(
                (page) => page.items.find((one) => one.id === id)?.name ?? ''))
        : Promise.resolve(''),
    `adm-name|${which}|${id ?? 'new'}`,
  );

  // Значение подставляется один раз, когда оно пришло: иначе правка
  // затиралась бы при каждой перерисовке.
  const loaded = current.state === 'ready' ? current.data : null;
  const [filled, setFilled] = useState(false);
  if (loaded !== null && !filled) {
    setFilled(true);
    if (loaded) setName(loaded);
  }

  const problem = name.trim().length >= 2
    ? null
    : name.trim() ? 'Слишком короткое название' : 'Название обязательно';

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (problem) return;
    void saving.run(
      () => {
        const value = name.trim();
        if (department) {
          return id
            ? api.updateDepartment(id, { name: value })
            : api.createDepartment({ name: value });
        }
        return id
          ? api.updatePosition(id, { name: value })
          : api.createPosition({ name: value });
      },
      onSaved,
    );
  };

  const title = department
    ? id ? 'Отдел' : 'Новый отдел'
    : id ? 'Должность' : 'Новая должность';

  return (
    <Modal title={title} onClose={onClose}>
      <form className="adm-modal__form" onSubmit={submit}>
        <ModalField label={department ? 'Название отдела' : 'Название должности'}
                    error={touched && problem ? problem : undefined}>
          <input className="input" value={name} maxLength={255} autoFocus
                 placeholder={department ? 'Продажи' : 'Ведущий инженер'}
                 onChange={(event) => setName(event.target.value)} />
        </ModalField>

        <Refusal text={saving.refusal} />
        <ModalTools busy={saving.busy} onCancel={onClose}
                    submitLabel={
                      id ? 'Сохранить'
                        : department ? 'Добавить отдел' : 'Добавить должность'
                    } />
      </form>
    </Modal>
  );
}

/** Администратор: имя, логин, пароль и роль. */
function AdminForm({ id, onClose, onSaved }: {
  id?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState('');
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [shown, setShown] = useState(false);
  const [role, setRole] = useState(OFFERED_ROLES[1]!.code);
  const [touched, setTouched] = useState(false);
  const saving = useSaving();

  const [current] = useBlock(
    (signal) => (id ? api.crmUser(id, signal) : Promise.resolve(null)),
    `adm-admin|${id ?? 'new'}`,
  );
  const [roles] = useBlock((signal) => api.roles(signal), 'adm-roles');

  const loaded = current.state === 'ready' ? current.data : null;
  const [filled, setFilled] = useState(false);
  if (loaded && !filled) {
    setFilled(true);
    setName(loaded.full_name ?? '');
    setLogin(loaded.email);
    if (loaded.active_grants.length) setRole(loaded.active_grants[0]!.role_code);
  }

  const problems = useMemo(() => {
    const found: Record<string, string> = {};
    if (!login.trim()) found['login'] = 'Логин обязателен: по нему человек входит';
    else if (/\s/.test(login.trim())) found['login'] = 'Логин без пробелов';
    if (!id && !password) found['password'] = 'Пароль обязателен';
    return found;
  }, [login, password, id]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (Object.keys(problems).length > 0) return;
    void saving.run(async () => {
      if (id) {
        await api.updateCrmUser(id, {
          email: login.trim(),
          full_name: name.trim(),
        });
        if (password) await api.setCrmUserPassword(id, password);
        await changeRole();
        return;
      }
      const made = await api.createCrmUser({
        email: login.trim(),
        ...(name.trim() ? { full_name: name.trim() } : {}),
        password,
      });
      const chosen = roles.state === 'ready'
        ? roles.data.items.find((one) => one.code === role)
        : undefined;
      if (chosen) {
        await api.assignRole({ user_id: made.id, role_id: chosen.id });
      }
    }, onSaved);
  };

  /**
   * Сменить роль у уже заведённого администратора.
   *
   * Сначала выдать новую, потом отозвать прежнюю. Обратный порядок на
   * секунду оставил бы человека без роли, и если выдача упадёт — без
   * доступа насовсем. Последнего суперадминистратора сервер разжаловать
   * не даст и объяснит причину сам.
   */
  async function changeRole(): Promise<void> {
    if (!loaded || roles.state !== 'ready') return;
    const had = loaded.active_grants[0];
    if (had?.role_code === role) return;

    const wanted = roles.data.items.find((one) => one.code === role);
    if (!wanted) return;

    await api.assignRole({ user_id: loaded.id, role_id: wanted.id });
    if (had) await api.revokeRole(had.id);
  }

  const show = (key: string) => (touched ? problems[key] : undefined);

  return (
    <Modal title={id ? 'Администратор' : 'Новый администратор'} onClose={onClose}>
      <form className="adm-modal__form" onSubmit={submit}>
        <ModalField label="ФИО"
                    hint="Как человека зовут. Можно не заполнять — тогда его опознают по логину">
          <input className="input" value={name} maxLength={255} autoFocus
                 placeholder="Например, Малика Рахимова"
                 onChange={(event) => setName(event.target.value)} />
        </ModalField>

        <ModalField label="Логин для входа" error={show('login')}
                    hint="Адресом почты быть не обязан">
          <input className="input" value={login} maxLength={255} autoComplete="off"
                 placeholder="malika.hr"
                 onChange={(event) => setLogin(event.target.value)} />
        </ModalField>

        <ModalField label={id ? 'Новый пароль' : 'Пароль'} error={show('password')}
                    hint={id
                      ? 'Оставьте пустым, чтобы не менять'
                      : 'Проверяется правилами: длина, распространённость, сходство с логином'}>
          <span className="adm-secret">
            <input className="input" type={shown ? 'text' : 'password'}
                   value={password} autoComplete="new-password"
                   placeholder="Придумайте пароль"
                   onChange={(event) => setPassword(event.target.value)} />
            <button type="button" className="adm-secret__eye"
                    aria-label={shown ? 'Скрыть пароль' : 'Показать пароль'}
                    onClick={() => setShown(!shown)}>
              <AppIcon name="eye" size={16} />
            </button>
          </span>
        </ModalField>

        <ModalField label="Роль"
                    hint={OFFERED_ROLES.find((one) => one.code === role)?.about}>
          <AppSelectField label="Роль" value={role} onChange={setRole}>
            {OFFERED_ROLES.map((one) => (
              <option key={one.code} value={one.code}>{one.title}</option>
            ))}
          </AppSelectField>
        </ModalField>

        <Refusal text={saving.refusal} />
        <ModalTools busy={saving.busy} onCancel={onClose}
                    submitLabel={id ? 'Сохранить' : 'Добавить администратора'} />
      </form>
    </Modal>
  );
}

/** График: название, дни недели, часы и необязательный перерыв. */
function ScheduleForm({ id, onClose, onSaved }: {
  id?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState('');
  const [days, setDays] = useState<number[]>([1, 2, 3, 4, 5]);
  const [from, setFrom] = useState('09:00');
  const [to, setTo] = useState('18:00');
  const [restFrom, setRestFrom] = useState('');
  const [restTo, setRestTo] = useState('');
  const [touched, setTouched] = useState(false);
  const saving = useSaving();

  const [current] = useBlock(
    (signal) => (id ? api.workSchedule(id, signal) : Promise.resolve(null)),
    `adm-schedule|${id ?? 'new'}`,
  );

  const loaded = current.state === 'ready' ? current.data : null;
  const [filled, setFilled] = useState(false);
  if (loaded && !filled) {
    setFilled(true);
    setName(loaded.name);
    const working = loaded.days.filter((day) => day.is_working_day);
    if (working.length) {
      setDays(working.map((day) => day.weekday));
      const first = working[0]!;
      if (first.start_time) setFrom(first.start_time.slice(0, 5));
      if (first.end_time) setTo(first.end_time.slice(0, 5));
      const rest = first.breaks?.[0];
      if (rest) {
        setRestFrom(rest.start_time.slice(0, 5));
        setRestTo(rest.end_time.slice(0, 5));
      }
    }
  }

  const problems = useMemo(() => {
    const found: Record<string, string> = {};
    if (!name.trim()) found['name'] = 'Название обязательно';
    if (days.length === 0) found['days'] = 'Отметьте хотя бы один рабочий день';
    if (minutes(from, to) <= 0) found['time'] = 'Окончание должно быть позже начала';
    if (restFrom && restTo) {
      if (minutes(restFrom, restTo) <= 0) found['rest'] = 'Перерыв задан наоборот';
      else if (restFrom < from || restTo > to) {
        found['rest'] = 'Перерыв выходит за рабочий день';
      }
    } else if (restFrom || restTo) {
      found['rest'] = 'У перерыва нужны обе границы';
    }
    return found;
  }, [name, days, from, to, restFrom, restTo]);

  const weekly = useMemo(() => {
    const rest = restFrom && restTo ? Math.max(0, minutes(restFrom, restTo)) : 0;
    return days.length * Math.max(0, minutes(from, to) - rest);
  }, [days, from, to, restFrom, restTo]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (Object.keys(problems).length > 0) return;
    const body: api.ScheduleDraft = {
      name: name.trim(),
      // Пояс один на всю страну и в форме не спрашивается.
      timezone: 'Asia/Tashkent',
      weekly_minutes: weekly,
      days: WEEKDAYS.map((day) => {
        const working = days.includes(day.n);
        return {
          weekday: day.n,
          is_working_day: working,
          ...(working
            ? {
                start_time: `${from}:00`,
                end_time: `${to}:00`,
                breaks: restFrom && restTo
                  ? [{ name: 'Перерыв', start_time: `${restFrom}:00`,
                       end_time: `${restTo}:00`, is_paid: false }]
                  : [],
              }
            : {}),
        };
      }),
    };
    void saving.run(
      () => (id ? api.updateSchedule(id, body) : api.createSchedule(body)),
      onSaved,
    );
  };

  const show = (key: string) => (touched ? problems[key] : undefined);

  return (
    <Modal title={id ? 'График работы' : 'Новый график'} onClose={onClose}>
      <form className="adm-modal__form" onSubmit={submit}>
        <ModalField label="Название" error={show('name')}>
          <input className="input" value={name} maxLength={255} autoFocus
                 placeholder="Пн–Пт 09:00–18:00"
                 onChange={(event) => setName(event.target.value)} />
        </ModalField>

        <div className={show('days') ? 'adm-field adm-field--bad' : 'adm-field'}>
          <span className="adm-field__label">Дни недели</span>
          <div className="adm-week" role="group" aria-label="Рабочие дни">
            {WEEKDAYS.map((day) => {
              const on = days.includes(day.n);
              return (
                <button key={day.n} type="button" aria-pressed={on}
                        aria-label={day.full}
                        className={on ? 'adm-day adm-day--on' : 'adm-day'}
                        onClick={() =>
                          setDays(on
                            ? days.filter((one) => one !== day.n)
                            : [...days, day.n].sort((a, b) => a - b))}>
                  {day.short}
                </button>
              );
            })}
          </div>
          {show('days') && (
            <span className="adm-field__error" role="alert">{show('days')}</span>
          )}
        </div>

        <div className={show('time') ? 'adm-field adm-field--bad' : 'adm-field'}>
          <span className="adm-field__label">Время работы</span>
          <div className="adm-times">
            <input type="time" className="input input--time" value={from}
                   aria-label="Время начала"
                   onChange={(event) => setFrom(event.target.value)} />
            <span className="adm-times__dash">—</span>
            <input type="time" className="input input--time" value={to}
                   aria-label="Время окончания"
                   onChange={(event) => setTo(event.target.value)} />
          </div>
          {show('time') && (
            <span className="adm-field__error" role="alert">{show('time')}</span>
          )}
        </div>

        <div className={show('rest') ? 'adm-field adm-field--bad' : 'adm-field'}>
          <span className="adm-field__label">Перерыв — необязательно</span>
          <div className="adm-times">
            <input type="time" className="input input--time" value={restFrom}
                   aria-label="Начало перерыва"
                   onChange={(event) => setRestFrom(event.target.value)} />
            <span className="adm-times__dash">—</span>
            <input type="time" className="input input--time" value={restTo}
                   aria-label="Окончание перерыва"
                   onChange={(event) => setRestTo(event.target.value)} />
          </div>
          {show('rest') ? (
            <span className="adm-field__error" role="alert">{show('rest')}</span>
          ) : (
            <span className="adm-field__hint">
              Рабочих часов в неделю: {(weekly / 60).toFixed(1)}
            </span>
          )}
        </div>

        <Refusal text={saving.refusal} />
        <ModalTools busy={saving.busy} onCancel={onClose}
                    submitLabel={id ? 'Сохранить' : 'Добавить график'} />
      </form>
    </Modal>
  );
}

/** Минуты между «09:00» и «18:00». Ноль и меньше означают ошибку ввода. */
export function minutes(from: string, to: string): number {
  const [fh, fm] = from.split(':').map(Number);
  const [th, tm] = to.split(':').map(Number);
  if ([fh, fm, th, tm].some((one) => one === undefined || Number.isNaN(one))) return 0;
  return (th! * 60 + tm!) - (fh! * 60 + fm!);
}

export { scheduleLine };
