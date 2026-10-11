/**
 * Администрирование: один экран, четыре справочника на вкладках.
 *
 * Раньше это было оглавление с переходом на шесть отдельных страниц, а
 * потом — четыре раскрывающихся блока. Справочник из трёх строк не стоит
 * отдельной страницы: человек заводит отдел и должность подряд, и два
 * перехода между ними — это два ожидания загрузки ради двух полей.
 *
 * Поэтому всё здесь: вкладки переключают список на месте, строка
 * раскрывается внутри таблицы, отдел и должность переименовываются
 * прямо в строке, добавление и правка доступа открывают небольшое окно
 * по центру. Со страницы никуда не уходят.
 *
 * Выбранная вкладка живёт в адресе (`?open=…`): ссылка «открыть отделы»
 * и возврат назад приводят туда же, где человек был.
 *
 * Офисов среди вкладок нет. Адрес, карта, геозона и QR-точки — это
 * настройка места, и она живёт в «Офисах и регионах»; два места, где
 * правят один и тот же офис, дают два разных ответа на один вопрос.
 *
 * Движение (переход вкладок, раскрытие строк) только сопровождает
 * действие: ни загрузка, ни сохранение его не ждут, и «Сохранено»
 * появляется только после ответа сервера.
 */

import {
  useCallback,
  useDeferredValue,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppSelectField } from '../components/AppSelect';
import { SlideTabs } from '../components/SlideTabs';
import { AnimatedCollapse } from '../components/admin/AnimatedCollapse';
import { AnimatedTabContent } from '../components/admin/AnimatedTabContent';
import { Confirm, Refusal, useSaving } from '../components/admin/Parts';
import { Modal, ModalField } from '../components/admin/Modal';
import { useBlock } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import {
  OFFERED_ROLES,
  SECTIONS,
  WEEKDAYS,
  counted,
  roleTitle,
  scheduleLine,
  scheduleSummary,
} from '../features/admin/catalog';
import type { SectionKey } from '../features/admin/catalog';
import {
  STATUS,
  accessNote,
  demoMode,
  initials,
  scopeSummary,
  scopeTitle,
} from '../features/admin/model';
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

const ORDER = SECTIONS.map((one) => one.key);

const isSection = (value: string | null): value is SectionKey =>
  value !== null && (ORDER as string[]).includes(value);

/** Сколько должностей на одной странице списка. */
const POSITIONS_PAGE = 8;

export function AdministrationPage() {
  const session = useSession();
  const me = session.status === 'authenticated' ? session.user.id : '';

  const [params, setParams] = useSearchParams();
  const asked = params.get('open');
  const tab: SectionKey = isSection(asked) ? asked : 'admins';
  const setTab = useCallback(
    (key: SectionKey) => {
      setParams(
        (was) => {
          const copy = new URLSearchParams(was);
          copy.set('open', key);
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
  const [notice, announce] = useNotice();

  // Все четыре справочника грузятся сразу: числа стоят на вкладках, и
  // «—» вместо количества до открытия ничего не сообщает. Переключение
  // вкладок поэтому ничего не перезапрашивает.
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

  const done = useCallback((message?: string) => {
    setAdding(null);
    setEditing(null);
    setRemoving(null);
    again();
    reloadAdmins();
    reloadDepartments();
    reloadPositions();
    reloadSchedules();
    if (message) announce(message);
  }, [again, announce, reloadAdmins, reloadDepartments, reloadPositions, reloadSchedules]);

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

  const failed: Record<SectionKey, boolean> = {
    admins: admins.state === 'error' || admins.state === 'denied',
    departments: departments.state === 'error' || departments.state === 'denied',
    positions: positions.state === 'error' || positions.state === 'denied',
    schedules: schedules.state === 'error' || schedules.state === 'denied',
  };
  const retry: Record<SectionKey, () => void> = {
    admins: reloadAdmins,
    departments: reloadDepartments,
    positions: reloadPositions,
    schedules: reloadSchedules,
  };

  const common = (key: SectionKey) => ({
    section: SECTIONS.find((one) => one.key === key)!,
    rows: rows[key],
    failed: failed[key],
    onRetry: retry[key],
    onAdd: () => setAdding(key),
    onEdit: (id: string) => setEditing({ key, id }),
    onRemove: (row: Row) => setRemoving({ key, row }),
  });

  const pane = (key: SectionKey): ReactNode => {
    switch (key) {
      case 'admins':
        return (
          <AdminsPane {...common('admins')}
                      users={admins.state === 'ready' ? admins.data.items : null} />
        );
      case 'departments':
        return (
          <NamesPane {...common('departments')}
                     which="departments"
                     details={departments.state === 'ready' ? departments.data.items : null}
                     onRenamed={(name) => {
                       reloadDepartments();
                       announce(`Отдел переименован: «${name}»`);
                     }} />
        );
      case 'positions':
        return (
          <NamesPane {...common('positions')}
                     which="positions"
                     pageSize={POSITIONS_PAGE}
                     details={positions.state === 'ready' ? positions.data.items : null}
                     onRenamed={(name) => {
                       reloadPositions();
                       announce(`Должность переименована: «${name}»`);
                     }} />
        );
      default:
        return <SchedulesPane {...common('schedules')} version={attempt} />;
    }
  };

  return (
    <AppShell breadcrumb="Администрирование" section="admin">
      <div className="adp">
        <header className="adm-top adp-top">
          <h1 className="adm-top__title">
            Администрирование
            {demoMode() && <span className="chip chip--demo">Демо-данные</span>}
          </h1>
          <p className="adm-top__sub">
            Доступ к CRM и справочники, которые используются при добавлении
            сотрудников.
          </p>
        </header>

        <section className="adp-sheet">
          <SlideTabs
            label="Справочники"
            value={tab}
            onPick={(key) => setTab(key as SectionKey)}
            classes={{ list: 'adp-tabs', tab: 'adp-tab', on: 'adp-tab--on', ink: 'adp-tabs__ink' }}
            items={SECTIONS.map((section) => {
              const alive = rows[section.key]?.filter((one) => !one.archived) ?? null;
              return {
                key: section.key,
                title: (
                  <>
                    <span className="adp-tab__title">{section.title}</span>
                    {/* Пока ответ не пришёл — прочерк: ноль означал бы,
                        что записей нет, а мы этого ещё не знаем. */}
                    <span className="adp-tab__count"
                          aria-label={alive === null
                            ? 'загружается'
                            : counted(alive.length, section.unit)}>
                      {alive === null ? '—' : alive.length}
                    </span>
                  </>
                ),
              };
            })}
          />

          <AnimatedTabContent
            activeKey={tab}
            order={ORDER}
            label={(key) => SECTIONS.find((one) => one.key === key)?.title ?? key}
            render={(key) => pane(key as SectionKey)}
          />

          <p className="adm-foot adp-foot">
            <AppIcon name="info" size={16} />
            <span>
              Офисы, карта и QR-точки настраиваются в разделе{' '}
              <Link to="/offices" className="adp-foot__link">«Офисы и регионы»</Link>.
            </span>
          </p>
        </section>

        {/* Подтверждение — только после ответа сервера: сообщение ставит
            `done` и `onRenamed`, которые вызываются из удачного запроса. */}
        <div className="adp-notice-slot" aria-live="polite">
          {notice && (
            <p key={notice.n} className="adp-notice" role="status">
              <AppIcon name="check" size={16} /> {notice.text}
            </p>
          )}
        </div>
      </div>

      <Refusal text={act.refusal} />

      {adding && (
        <AddForm which={adding} onClose={() => setAdding(null)}
                 onSaved={() => done(ADDED[adding])} />
      )}

      {editing && (
        <EditForm
          which={editing.key}
          id={editing.id}
          onClose={() => setEditing(null)}
          onSaved={() => done(editing.key === 'admins' ? 'Доступ сохранён' : 'Изменения сохранены')}
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
            const what = removing;
            void act.run(
              () => remove(what.key, what.row),
              () => done(
                what.key === 'admins'
                  ? `Доступ «${what.row.title}» закрыт`
                  : what.row.used
                    ? `«${what.row.title}» перенесено в архив`
                    : `«${what.row.title}» удалено`,
              ),
            );
          }}
        />
      )}
    </AppShell>
  );
}

const ADDED: Record<SectionKey, string> = {
  admins: 'Администратор добавлен',
  departments: 'Отдел добавлен',
  positions: 'Должность добавлена',
  schedules: 'График добавлен',
};

/**
 * Короткое подтверждение после удачного запроса.
 *
 * Своей системы уведомлений у CRM нет — у разделов свои строки статуса,
 * и вторую общую здесь не заводим. Сообщение ставится только из
 * обработчика успешного ответа; таймер его лишь убирает.
 */
function useNotice(): [{ text: string; n: number } | null, (text: string) => void] {
  const [notice, setNotice] = useState<{ text: string; n: number } | null>(null);
  const announce = useCallback(
    (text: string) => setNotice((was) => ({ text, n: (was?.n ?? 0) + 1 })),
    [],
  );
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);
  return [notice, announce];
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

// --- общая рамка вкладки -----------------------------------------------------

type PaneProps = {
  section: (typeof SECTIONS)[number];
  rows: Row[] | null;
  failed: boolean;
  onRetry: () => void;
  onAdd: () => void;
  onEdit: (id: string) => void;
  onRemove: (row: Row) => void;
};

const SEARCH_LABEL: Record<SectionKey, string> = {
  admins: 'Поиск пользователя',
  departments: 'Поиск отдела',
  positions: 'Поиск должности',
  schedules: 'Поиск графика',
};

/** Шапка вкладки, поиск и кнопка добавления; ниже — таблица. */
function PaneFrame({ section, search, onSearch, onAdd, head, children, foot }: {
  section: (typeof SECTIONS)[number];
  search: string;
  onSearch: (value: string) => void;
  onAdd: () => void;
  /** Подписи колонок; на узком экране скрываются. */
  head: ReactNode;
  children: ReactNode;
  foot?: ReactNode;
}) {
  return (
    <div className={`adp-panel adp-panel--${section.key}`}>
      <div className="adp-panel__head">
        <div className="adp-panel__text">
          <h2 className="adp-panel__title">{section.title}</h2>
          <p className="adp-panel__about">{section.about}</p>
        </div>
        <div className="adp-panel__tools">
          <label className="adp-search">
            <AppIcon name="search" size={16} />
            <input type="search" value={search}
                   placeholder={SEARCH_LABEL[section.key]}
                   aria-label={SEARCH_LABEL[section.key]}
                   onChange={(event) => onSearch(event.target.value)} />
          </label>
          <button type="button" className="btn btn--primary adp-add" onClick={onAdd}>
            <AppIcon name="plus" size={16} /> {section.add}
          </button>
        </div>
      </div>

      <div className={`adp-table adp-table--${section.key}`}>
        <div className="adp-table__head" aria-hidden="true">{head}</div>
        {children}
      </div>

      {foot && <div className="adp-panel__foot">{foot}</div>}
    </div>
  );
}

/** Загрузка, сбой и пустой список — одинаково на всех вкладках. */
function PaneState({ rows, failed, onRetry, empty, found, columns }: {
  rows: unknown[] | null;
  failed: boolean;
  onRetry: () => void;
  empty: string;
  /** Сколько строк осталось после поиска. */
  found: number;
  columns: number;
}) {
  if (rows === null && failed) {
    return (
      <p className="adp-state adp-state--bad" role="alert">
        Не удалось загрузить список. Это ошибка запроса, а не пустой список.{' '}
        <button type="button" className="link" onClick={onRetry}>Повторить</button>
      </p>
    );
  }
  if (rows === null) {
    return (
      <div className="adp-skeleton" aria-busy="true" aria-label="Загружаем…">
        {[0, 1, 2].map((n) => (
          <span key={n} className="adp-skeleton__row"
                style={{ ['--adp-cols' as string]: columns }} />
        ))}
      </div>
    );
  }
  if (rows.length === 0) return <p className="adp-state">{empty}</p>;
  if (found === 0) return <p className="adp-state">По этому запросу ничего не нашлось.</p>;
  return null;
}

/** Совпадает ли строка с поиском. Регистр и лишние пробелы не важны. */
const matches = (query: string, ...values: Array<string | null | undefined>): boolean => {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return values.some((one) => one?.toLowerCase().includes(needle));
};

/**
 * Строка таблицы, которая раскрывается.
 *
 * Раскрывает её кнопка с названием и стрелкой — с `aria-expanded` и
 * ссылкой на раскрываемую часть. Кнопки действий стоят отдельно и
 * строку не раскрывают: «Удалить» не должно попутно что-то открывать.
 */
function TableRow({ title, open, onToggle, off, cells, tools, children, lead, meta }: {
  title: string;
  open: boolean;
  onToggle: () => void;
  off?: boolean;
  /** Ячейка перед названием внутри кнопки — например, инициалы. */
  lead?: ReactNode;
  /** Короткая приписка в той же строке: «3 чел.», «в архиве». Строки
      остаются одной высоты — приписка не уходит второй строкой. */
  meta?: ReactNode;
  /** Остальные ячейки строки. */
  cells: ReactNode;
  tools: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  const classes = ['adp-row'];
  if (open) classes.push('adp-row--open');
  if (off) classes.push('adp-row--off');
  return (
    <li className={classes.join(' ')}>
      <div className="adp-row__line">
        <button type="button" className="adp-row__toggle" aria-expanded={open}
                aria-controls={`${id}-more`} onClick={onToggle}>
          <span className="adp-chevron" aria-hidden="true">
            <AppIcon name="chevron" size={16} />
          </span>
          {lead}
          <span className="adp-row__title">{title}</span>
          {meta && <span className="adp-row__meta">{meta}</span>}
        </button>
        {cells}
        <span className="adp-row__tools">{tools}</span>
      </div>
      <AnimatedCollapse open={open} id={`${id}-more`}>
        <div className="adp-row__more">{children}</div>
      </AnimatedCollapse>
    </li>
  );
}

/** Карандаш и корзина/архив — те же подписи, что и раньше. */
function RowTools({ row, onEdit, onRemove }: {
  row: Row;
  onEdit: () => void;
  onRemove: () => void;
}) {
  return (
    <>
      <button type="button" className="adp-tool"
              aria-label={`Изменить «${row.title}»`}
              onClick={onEdit}>
        <AppIcon name="pencil" size={16} />
      </button>
      <button type="button" className="adp-tool adp-tool--bad"
              aria-label={
                row.locked
                  ? `«${row.title}» — это вы: убрать нельзя`
                  : `${row.used ? 'Архивировать' : 'Удалить'} «${row.title}»`
              }
              disabled={row.locked}
              title={row.locked ? 'Это вы: убрать себя нельзя' : undefined}
              onClick={onRemove}>
        <AppIcon name={row.used ? 'archive' : 'cross'} size={16} />
      </button>
    </>
  );
}

/** Подпись и значение в раскрытой строке. */
function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="adp-fact">
      <span className="adp-fact__label">{label}</span>
      <span className="adp-fact__value">{children}</span>
    </div>
  );
}

/**
 * Кнопка сохранения, которая не меняет ширину.
 *
 * «Сохраняем…» короче или длиннее подписи — и кнопка прыгала бы под
 * курсором. Вторая подпись лежит в той же ячейке невидимой (через
 * `data-alt`), и ширина всегда по большей из двух.
 */
function SubmitButton({ busy, label, busyLabel = 'Сохраняем…', type = 'submit', onClick }: {
  busy: boolean;
  label: string;
  busyLabel?: string;
  type?: 'submit' | 'button';
  onClick?: () => void;
}) {
  return (
    <button type={type} className={busy ? 'btn btn--primary adp-btn adp-btn--busy' : 'btn btn--primary adp-btn'}
            disabled={busy} aria-busy={busy || undefined}
            data-alt={busy ? label : busyLabel}
            onClick={onClick}>
      <span className="adp-btn__label">{busy ? busyLabel : label}</span>
    </button>
  );
}

// --- администраторы ----------------------------------------------------------

function AdminsPane({ section, rows, failed, onRetry, onAdd, onEdit, onRemove, users }: PaneProps & {
  users: api.CrmUser[] | null;
}) {
  const [search, setSearch] = useState('');
  const query = useDeferredValue(search);
  const [openId, setOpenId] = useState<string | null>(null);

  const shown = useMemo(
    () => (users ?? []).filter((one) => matches(query, one.full_name, one.email)),
    [users, query],
  );
  const byId = useMemo(
    () => new Map((rows ?? []).map((one) => [one.id, one])),
    [rows],
  );

  return (
    <PaneFrame
      section={section}
      search={search}
      onSearch={setSearch}
      onAdd={onAdd}
      head={(
        <>
          <span>Пользователь</span>
          <span>Роль</span>
          <span>Область доступа</span>
          <span>Статус</span>
          <span className="adp-table__actions">Действия</span>
        </>
      )}
      foot={users && users.length > 0 ? (
        <>
          <span>{counted(users.length, ['учётная запись', 'учётные записи', 'учётных записей'])}</span>
          <span className="adp-panel__hint">
            Роль определяет действия, область — доступные данные.
          </span>
        </>
      ) : null}
    >
      <PaneState rows={users} failed={failed} onRetry={onRetry} empty={section.empty}
                 found={shown.length} columns={5} />
      {shown.length > 0 && (
        <ul className="adp-rows">
          {shown.map((user) => {
            const row = byId.get(user.id);
            if (!row) return null;
            const title = user.full_name ?? user.email;
            const grants = user.active_grants;
            const role = !user.grants_visible
              ? '—'
              : grants.length
                ? roleTitle(grants[0]!.role_code, grants[0]!.role_name)
                  + (grants.length > 1 ? ` +${grants.length - 1}` : '')
                : 'Без роли';
            const scope = user.grants_visible ? scopeSummary(grants) : '—';
            const active = user.status === 'ACTIVE';
            const note = accessNote(user);
            const open = openId === user.id;
            return (
              <TableRow
                key={user.id}
                title={title}
                open={open}
                off={!active}
                onToggle={() => setOpenId(open ? null : user.id)}
                lead={<span className="adp-avatar" aria-hidden="true">{initials(title) || '•'}</span>}
                cells={(
                  <>
                    {user.full_name && <span className="adp-row__sub">{user.email}</span>}
                    <span className="adp-cell" data-label="Роль">{role}</span>
                    <span className="adp-cell" data-label="Область">{scope}</span>
                    <span className="adp-cell" data-label="Статус">
                      <span className={active ? 'adp-status adp-status--on' : 'adp-status'}>
                        {STATUS[user.status] ?? user.status}
                      </span>
                    </span>
                  </>
                )}
                tools={(
                  <RowTools row={row} onEdit={() => onEdit(user.id)}
                            onRemove={() => onRemove(row)} />
                )}
              >
                <div className="adp-more adp-more--admin">
                  <p className="adp-more__title">Доступ пользователя</p>
                  <div className="adp-facts">
                    <Fact label="Логин">{user.email}</Fact>
                    <Fact label="Роль">
                      {!user.grants_visible
                        ? 'Назначения не показаны: на это нужны права'
                        : grants.length
                          ? grants.map((one) => roleTitle(one.role_code, one.role_name)).join(', ')
                          : 'Без роли'}
                    </Fact>
                    <Fact label="Область">
                      {!user.grants_visible
                        ? '—'
                        : grants.length
                          ? [...new Set(grants.map(scopeTitle))].join(', ')
                          : '—'}
                    </Fact>
                  </div>
                  <button type="button" className="btn adp-more__action"
                          aria-label={`Редактировать доступ «${title}»`}
                          onClick={() => onEdit(user.id)}>
                    <AppIcon name="pencil" size={16} /> Редактировать доступ
                  </button>
                  {note && <p className="adp-more__note"><AppIcon name="alert" size={16} /> {note}</p>}
                </div>
              </TableRow>
            );
          })}
        </ul>
      )}
    </PaneFrame>
  );
}

// --- отделы и должности ------------------------------------------------------

type NameDetail = { id: string; name: string; status: string; staff?: number;
                    office_name?: string | null };

function NamesPane({ section, rows, failed, onRetry, onAdd, onRemove, which, details,
                     pageSize, onRenamed }: PaneProps & {
  which: 'departments' | 'positions';
  details: NameDetail[] | null;
  pageSize?: number;
  onRenamed: (name: string) => void;
}) {
  const department = which === 'departments';
  const [search, setSearch] = useState('');
  const query = useDeferredValue(search);
  const [openId, setOpenId] = useState<string | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  /*
   * Имя, которое подтвердил сервер, — пока список не перезагрузился.
   * Без этого между закрытием формы и новым ответом в строке на миг
   * снова стояло бы старое название. Привязано к тому ответу списка,
   * поверх которого сохранили: пришёл новый — правда снова за ним.
   */
  const [renamed, setRenamed] = useState<{ id: string; name: string; base: unknown } | null>(null);

  const list = useMemo(() => {
    if (!rows) return null;
    return rows.map((row) =>
      renamed && renamed.base === details && renamed.id === row.id
        ? { ...row, title: renamed.name }
        : row);
  }, [rows, renamed, details]);

  const shown = useMemo(
    () => (list ?? []).filter((one) => matches(query, one.title, one.note)),
    [list, query],
  );

  // Поиск меняет набор — листание начинается сначала. Страница, которой
  // после удаления уже нет, сдвигается на последнюю существующую.
  const pages = pageSize ? Math.max(1, Math.ceil(shown.length / pageSize)) : 1;
  const [lastQuery, setLastQuery] = useState(query);
  if (query !== lastQuery) {
    setLastQuery(query);
    setPage(1);
  }
  const current = Math.min(page, pages);
  const visible = pageSize ? shown.slice((current - 1) * pageSize, current * pageSize) : shown;
  const byId = useMemo(() => new Map((details ?? []).map((one) => [one.id, one])), [details]);

  const toggle = (id: string) => {
    setEditId(null);
    setOpenId(openId === id ? null : id);
  };

  return (
    <PaneFrame
      section={section}
      search={search}
      onSearch={setSearch}
      onAdd={onAdd}
      head={(
        <>
          <span>{department ? 'Название отдела' : 'Название должности'}</span>
          <span className="adp-table__actions">Действия</span>
        </>
      )}
      foot={list && list.length > 0 ? (
        <>
          <span>
            {pageSize && shown.length > pageSize
              ? `Показано ${visible.length} из ${counted(shown.length, section.unit)}`
              : counted(shown.length, section.unit)}
          </span>
          {pageSize && pages > 1 && (
            <Pager page={current} pages={pages} onPage={(n) => {
              setOpenId(null);
              setEditId(null);
              setPage(n);
            }} />
          )}
        </>
      ) : null}
    >
      <PaneState rows={list} failed={failed} onRetry={onRetry} empty={section.empty}
                 found={shown.length} columns={2} />
      {visible.length > 0 && (
        <ul className="adp-rows adp-rows--page" key={current}>
          {visible.map((row) => {
            const open = openId === row.id;
            const editingRow = open && editId === row.id;
            const detail = byId.get(row.id);
            return (
              <TableRow
                key={row.id}
                title={row.title}
                open={open}
                off={row.archived}
                onToggle={() => toggle(row.id)}
                meta={(row.note || row.archived)
                  ? [row.note, row.archived ? 'в архиве' : null].filter(Boolean).join(' · ')
                  : null}
                cells={null}
                tools={(
                  <RowTools row={row}
                            onEdit={() => {
                              setOpenId(row.id);
                              setEditId(row.id);
                            }}
                            onRemove={() => onRemove(row)} />
                )}
              >
                {editingRow ? (
                  <NameEditor
                    which={which}
                    row={row}
                    onCancel={() => setEditId(null)}
                    onSaved={(name) => {
                      setRenamed({ id: row.id, name, base: details });
                      setEditId(null);
                      setOpenId(null);
                      onRenamed(name);
                    }}
                  />
                ) : (
                  <div className="adp-more adp-more--names">
                    <div className="adp-facts">
                      {department && (
                        <Fact label="Офис">{detail?.office_name ?? 'Общий для компании'}</Fact>
                      )}
                      <Fact label="Сотрудников">{detail?.staff ?? row.used}</Fact>
                      <Fact label="Состояние">{row.archived ? 'В архиве' : 'Используется'}</Fact>
                    </div>
                    <button type="button" className="btn adp-more__action"
                            onClick={() => setEditId(row.id)}>
                      <AppIcon name="pencil" size={16} /> Изменить название
                    </button>
                  </div>
                )}
              </TableRow>
            );
          })}
        </ul>
      )}
    </PaneFrame>
  );
}

/** Название обязательно и не короче двух знаков — как в окне добавления. */
function nameProblem(name: string): string | null {
  if (name.trim().length >= 2) return null;
  return name.trim() ? 'Слишком короткое название' : 'Название обязательно';
}

/**
 * Переименование прямо в строке.
 *
 * Тот же запрос и та же проверка, что в окне; отличается только место.
 * Поле получает фокус без прокрутки страницы. Строка закрывается только
 * после ответа сервера; при отказе форма остаётся открытой с введённым
 * текстом и причиной.
 */
function NameEditor({ which, row, onCancel, onSaved }: {
  which: 'departments' | 'positions';
  row: Row;
  onCancel: () => void;
  onSaved: (name: string) => void;
}) {
  const department = which === 'departments';
  const [name, setName] = useState(row.title);
  const [touched, setTouched] = useState(false);
  const saving = useSaving();
  const field = useRef<HTMLInputElement>(null);
  const fieldId = useId();

  /*
   * Форма исчезает — фокус не должен упасть на страницу целиком:
   * человек с клавиатуры продолжает с той же строки, с её кнопки.
   */
  const backToRow = () => {
    field.current?.closest('.adp-row')
      ?.querySelector<HTMLButtonElement>('.adp-row__toggle')
      ?.focus({ preventScroll: true });
  };
  const cancel = () => {
    backToRow();
    onCancel();
  };

  useEffect(() => {
    field.current?.focus({ preventScroll: true });
    field.current?.select();
  }, []);

  const problem = nameProblem(name);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (problem) return;
    const value = name.trim();
    let confirmed = value;
    void saving.run(
      async () => {
        const answer = department
          ? await api.updateDepartment(row.id, { name: value })
          : await api.updatePosition(row.id, { name: value });
        // Название берётся из ответа: сервер мог его нормализовать.
        if (answer && typeof answer.name === 'string') confirmed = answer.name;
      },
      () => {
        backToRow();
        onSaved(confirmed);
      },
    );
  };

  const shownError = touched && problem ? problem : null;

  return (
    <form className="adp-editor" onSubmit={submit}
          onKeyDown={(event) => {
            if (event.key === 'Escape' && !saving.busy) {
              event.stopPropagation();
              cancel();
            }
          }}>
      <div className={shownError ? 'adp-editor__field adm-field--bad' : 'adp-editor__field'}>
        <label className="adm-field__label" htmlFor={fieldId}>
          {department ? 'Новое название отдела' : 'Новое название должности'}
        </label>
        <input id={fieldId} ref={field} className="input" value={name} maxLength={255}
               aria-invalid={shownError ? true : undefined}
               onChange={(event) => setName(event.target.value)} />
        <span className="adp-editor__error" role={shownError ? 'alert' : undefined}>
          {shownError ?? ''}
        </span>
      </div>
      <div className="adp-editor__tools">
        <SubmitButton busy={saving.busy} label="Сохранить" />
        <button type="button" className="btn adp-editor__cancel" disabled={saving.busy}
                onClick={cancel}>
          Отмена
        </button>
      </div>
      {saving.refusal && (
        <div className="adp-editor__refusal"><Refusal text={saving.refusal} /></div>
      )}
    </form>
  );
}

/** Номера страниц: соседние с текущей, первая и последняя. */
function Pager({ page, pages, onPage }: {
  page: number;
  pages: number;
  onPage: (n: number) => void;
}) {
  const numbers: Array<number | 'gap'> = [];
  for (let n = 1; n <= pages; n += 1) {
    if (n === 1 || n === pages || Math.abs(n - page) <= 1 || pages <= 7) numbers.push(n);
    else if (numbers[numbers.length - 1] !== 'gap') numbers.push('gap');
  }
  return (
    <nav className="adp-pager" aria-label="Страницы списка">
      <button type="button" className="adp-pager__step" aria-label="Предыдущая страница"
              disabled={page <= 1} onClick={() => onPage(page - 1)}>
        <AppIcon name="back" size={16} />
      </button>
      {numbers.map((n, index) =>
        n === 'gap'
          ? <span key={`gap-${index}`} className="adp-pager__gap" aria-hidden="true">…</span>
          : (
            <button key={n} type="button"
                    className={n === page ? 'adp-pager__page adp-pager__page--on' : 'adp-pager__page'}
                    aria-label={`Страница ${n}`}
                    aria-current={n === page ? 'page' : undefined}
                    onClick={() => { if (n !== page) onPage(n); }}>
              {n}
            </button>
          ))}
      <button type="button" className="adp-pager__step" aria-label="Следующая страница"
              disabled={page >= pages} onClick={() => onPage(page + 1)}>
        <AppIcon name="next" size={16} />
      </button>
    </nav>
  );
}

// --- графики -----------------------------------------------------------------

function SchedulesPane({ section, rows, failed, onRetry, onAdd, onEdit, onRemove, version }: PaneProps & {
  version: number;
}) {
  const [search, setSearch] = useState('');
  const query = useDeferredValue(search);
  const [openId, setOpenId] = useState<string | null>(null);
  const shown = useMemo(
    () => (rows ?? []).filter((one) => matches(query, one.title)),
    [rows, query],
  );

  return (
    <PaneFrame
      section={section}
      search={search}
      onSearch={setSearch}
      onAdd={onAdd}
      head={(
        <>
          <span>Название</span>
          <span>Рабочие дни</span>
          <span>Время</span>
          <span>Перерыв</span>
          <span className="adp-table__actions">Действия</span>
        </>
      )}
      foot={rows && rows.length > 0 ? <span>{counted(shown.length, section.unit)}</span> : null}
    >
      <PaneState rows={rows} failed={failed} onRetry={onRetry} empty={section.empty}
                 found={shown.length} columns={5} />
      {shown.length > 0 && (
        <ul className="adp-rows">
          {shown.map((row) => (
            <ScheduleLine key={row.id} row={row} version={version}
                          open={openId === row.id}
                          onToggle={() => setOpenId(openId === row.id ? null : row.id)}
                          onEdit={() => onEdit(row.id)}
                          onRemove={() => onRemove(row)} />
          ))}
        </ul>
      )}
    </PaneFrame>
  );
}

/**
 * Строка графика.
 *
 * Список графиков не отдаёт дни и перерывы — они есть только в
 * карточке графика, поэтому каждая строка дочитывает свою. Пока ответа
 * нет, в колонках прочерк, а не выдуманное «Пн–Пт».
 *
 * Раскрытая строка только показывает сохранённое. Правят график в окне
 * «Редактировать график» — с той же проверкой и тем же запросом, что
 * были; что можно сохранить, решает сервер.
 */
function ScheduleLine({ row, version, open, onToggle, onEdit, onRemove }: {
  row: Row;
  version: number;
  open: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const [card] = useBlock(
    (signal) => api.workSchedule(row.id, signal),
    `adm-schedule-line|${row.id}|${version}`,
  );
  const summary = card.state === 'ready' && Array.isArray(card.data?.days)
    ? scheduleSummary(card.data.days)
    : null;
  const pending = card.state === 'loading';

  return (
    <TableRow
      title={row.title}
      open={open}
      off={row.archived}
      onToggle={onToggle}
      meta={row.archived ? 'в архиве' : null}
      cells={(
        <>
          <span className="adp-cell" data-label="Дни">{summary?.days ?? (pending ? '…' : '—')}</span>
          <span className="adp-cell" data-label="Время">
            {summary?.time ?? (pending ? '…' : '—')}
            {summary?.night && <span className="adp-badge" title="Окончание — на следующий день">+1</span>}
          </span>
          <span className="adp-cell" data-label="Перерыв">{summary?.rest ?? (pending ? '…' : '—')}</span>
        </>
      )}
      tools={<RowTools row={row} onEdit={onEdit} onRemove={onRemove} />}
    >
      <div className="adp-more adp-more--schedule">
        <div className="adp-more__group">
          <p className="adp-more__title">Параметры графика</p>
          <ul className="adp-weekdays" aria-label="Рабочие дни">
            {WEEKDAYS.map((day) => {
              const on = summary?.working.includes(day.n) ?? false;
              return (
                <li key={day.n} className={on ? 'adp-weekday adp-weekday--on' : 'adp-weekday'}
                    aria-label={`${day.full}: ${on ? 'рабочий' : 'выходной'}`}>
                  {day.short}
                </li>
              );
            })}
          </ul>
        </div>
        <div className="adp-facts adp-facts--boxed">
          <Fact label="Начало">{summary?.start ?? '—'}</Fact>
          <Fact label="Окончание">
            {summary?.end ?? '—'}
            {summary?.night && <span className="adp-badge">след. день</span>}
          </Fact>
          <Fact label="Перерыв">
            {summary ? (summary.restMinutes ? `${summary.restMinutes} мин` : 'Без перерыва') : '—'}
          </Fact>
        </div>
        <button type="button" className="btn adp-more__action"
                aria-label={`Редактировать график «${row.title}»`}
                onClick={onEdit}>
          <AppIcon name="pencil" size={16} /> Редактировать график
        </button>
      </div>
    </TableRow>
  );
}

/** Нижний ряд окна: те же кнопки, но без прыжка ширины при сохранении. */
function AdmModalTools({ busy, submitLabel, onCancel }: {
  busy: boolean;
  submitLabel: string;
  onCancel: () => void;
}) {
  return (
    <div className="adm-modal__tools">
      <button type="button" className="btn" onClick={onCancel} disabled={busy}>
        Отмена
      </button>
      <SubmitButton busy={busy} label={submitLabel} />
    </div>
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

  const problem = nameProblem(name);

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
        <AdmModalTools busy={saving.busy} onCancel={onClose}
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
        <AdmModalTools busy={saving.busy} onCancel={onClose}
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
        <AdmModalTools busy={saving.busy} onCancel={onClose}
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
