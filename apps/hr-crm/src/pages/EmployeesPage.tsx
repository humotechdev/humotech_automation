/**
 * Страница «Сотрудники».
 *
 * Вёрстка повторяет эталон 1672×941 (`ChatGPT Image Sep 13, 2026,
 * 03_29_40 PM.png`): слева панель со списком, справа панель «Сегодня в
 * команде». Все размеры — в `styles/employees.css`, классы с префиксом
 * `emp-`, чтобы правки этой страницы не задевали остальные.
 *
 * Состояние списка живёт в адресе: вернувшись из карточки, человек видит
 * тот же поиск, фильтр, вид и страницу.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { AppSegmentedControl, AppSelectField, Dropdown } from '../components/AppSelect';
import { EmployeeCard } from '../components/EmployeeCard';
import { longDate, today as todayIso, useBlock, type Block } from '../features/dashboard/data';
import '../styles/employees.css';

/** Вкладки. Одна вкладка может покрывать несколько состояний модели. */
const TABS = [
  { key: 'all', title: 'Все', statuses: [] as string[] },
  { key: 'active', title: 'Активные', statuses: ['ACTIVE', 'PROBATION'] },
  { key: 'inactive', title: 'Неактивные', statuses: ['SUSPENDED'] },
  { key: 'left', title: 'Уволенные', statuses: ['TERMINATED', 'ARCHIVED'] },
] as const;

const ACTIVE_STATUSES = ['ACTIVE', 'PROBATION'];
const SIZES = ['8', '16', '32'];
const WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

const TELEGRAM_TITLE: Record<string, string> = {
  ACTIVE: 'Привязан',
  PENDING: 'Ожидает HR',
  PENDING_CONFIRMATION: 'Ожидает HR',
  REVOKED: 'Отозван',
  BLOCKED: 'Заблокирован',
};

export function EmployeesPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.key === params.get('tab')) ?? TABS[0];
  const search = params.get('search') ?? '';
  const office = params.get('office_id') ?? '';
  const department = params.get('department_id') ?? '';
  const limit = params.get('limit') ?? '8';
  const page = Math.max(1, Number(params.get('page') ?? '1'));
  const opened = params.get('employee') ?? '';
  const picked = params.get('picked') ?? '';
  const view = params.get('view') === 'table' ? 'table' : 'cards';

  const [draft, setDraft] = useState(search);
  useEffect(() => setDraft(search), [search]);

  /** Меняем адрес, а не состояние: возврат из карточки ничего не теряет. */
  const patch = useCallback(
    (changes: Record<string, string | null>, keepPage = false) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          // Новый отбор начинается с первой страницы.
          if (!keepPage) next.delete('page');
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  // Поиск не бьёт по серверу на каждую букву.
  useEffect(() => {
    if (draft === search) return;
    const timer = setTimeout(() => patch({ search: draft || null }), 350);
    return () => clearTimeout(timer);
  }, [draft, search, patch]);

  const filters: api.EmployeeQuery = useMemo(
    () => ({
      ...(search ? { search } : {}),
      ...(office ? { office_id: office } : {}),
      ...(department ? { department_id: department } : {}),
    }),
    [search, office, department],
  );
  const offset = (page - 1) * Number(limit);

  const [list] = useBlock(
    (signal) =>
      api.employees(
        {
          ...filters,
          limit,
          ...(offset ? { offset: String(offset) } : {}),
          ...(tab.statuses.length ? { status: tab.statuses.join(',') } : {}),
        },
        signal,
      ),
    `${tab.key}|${search}|${office}|${department}|${limit}|${page}`,
  );

  const [counts] = useBlock(
    (signal) => api.employeeCounts(filters, signal),
    `counts|${search}|${office}|${department}`,
  );

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.offices(signal), api.departments(signal)]).then(([o, d]) => ({
        offices: o.items.filter((one) => one.status === 'ACTIVE'),
        departments: d.items,
      })),
    'directory',
  );

  const [presence] = useBlock((signal) => api.presence({}, signal), 'presence|today');

  const [highlights] = useBlock(
    (signal) => api.employeeHighlights(
      { ...(office ? { office_id: office } : {}), ...(department ? { department_id: department } : {}) },
      signal,
    ),
    `highlights|${office}|${department}`,
  );

  const [updated, setUpdated] = useState<Date | null>(null);
  useEffect(() => {
    if (list.state === 'ready') setUpdated(new Date());
  }, [list]);

  // Выгрузка ставится в общую очередь отчётов.
  const [ordering, setOrdering] = useState(false);
  const [ordered, setOrdered] = useState<string | null>(null);
  async function order() {
    if (ordering) return;
    setOrdering(true);
    setOrdered(null);
    try {
      await api.orderExport({ kind: 'employees', fmt: 'xlsx', ...(office ? { office_id: office } : {}) });
      setOrdered('Выгрузка поставлена в очередь');
    } catch (error) {
      setOrdered(messageFor(error));
    } finally {
      setOrdering(false);
    }
  }

  const total = counts.state === 'ready' ? count(counts.data, tab.statuses) : 0;
  const pages = Math.max(1, Math.ceil(total / Number(limit)));
  const items = list.state === 'ready' ? list.data.items : [];
  const chosen = items.find((one) => one.id === picked) ?? items[0] ?? null;

  return (
    <AppShell breadcrumb="Сотрудники" section="employees">
      <div className="emp">
        <header className="emp-head">
          <div className="emp-head__text">
            <h1 className="emp-head__title">Сотрудники</h1>
            <p className="emp-head__facts">
              {counts.state === 'ready' && (
                <>
                  <span>{plural(count(counts.data, []), 'сотрудник')}</span>
                  <i className="emp-head__dot">•</i>
                  <span>{count(counts.data, ACTIVE_STATUSES)} активных</span>
                </>
              )}
              {directory.state === 'ready' && (
                <>
                  <i className="emp-head__dot">•</i>
                  <span>{plural(directory.data.offices.length, 'офис')}</span>
                </>
              )}
              {updated && (
                <>
                  <i className="emp-head__bar" />
                  <span className="emp-head__when">{ago(updated)}</span>
                </>
              )}
            </p>
          </div>
          <div className="emp-head__actions">
            <button type="button" className="emp-btn emp-btn--light"
                    onClick={() => void order()} disabled={ordering}>
              <AppIcon name="download" size={20} />
              {ordering ? 'В очереди…' : 'Экспорт'}
            </button>
            <Link className="emp-btn emp-btn--blue" to="/employees/new">
              <AppIcon name="plus" size={20} />
              Добавить сотрудника
            </Link>
          </div>
        </header>

        {ordered && (
          <p className="emp-note" role="status">
            {ordered} — <Link to="/reports">файл появится в отчётах</Link>
          </p>
        )}

        <div className="emp-grid">
          <section className="emp-list" aria-label="Список сотрудников">
            <div className="emp-filters">
              <label className="emp-search">
                <AppIcon name="search" size={18} />
                <input
                  type="search"
                  value={draft}
                  placeholder="Поиск по ФИО, должности или Telegram"
                  aria-label="Поиск по ФИО, должности или Telegram"
                  onChange={(event) => setDraft(event.target.value)}
                />
              </label>
              <Select label="Офис" empty="Все офисы" value={office}
                      options={directory.state === 'ready' ? directory.data.offices : []}
                      onChange={(value) => patch({ office_id: value || null })} />
              <Select label="Отдел" empty="Все отделы" value={department}
                      options={directory.state === 'ready' ? directory.data.departments : []}
                      onChange={(value) => patch({ department_id: value || null })} />
              {/* Статус и вкладки — одна ось отбора: список меняет вкладку. */}
              <Select label="Статус" empty="Все статусы" value={tab.key === 'all' ? '' : tab.key}
                      options={TABS.filter((one) => one.key !== 'all')
                        .map((one) => ({ id: one.key, name: one.title }))}
                      onChange={(value) => patch({ tab: value || null })} />
              <AppSegmentedControl className="emp-view" label="Вид списка" value={view} options={[
                { value: 'table', label: 'Таблица', icon: 'list' },
                { value: 'cards', label: 'Карточки', icon: 'grid' },
              ]} onChange={(next) => patch({ view: next === 'cards' ? null : 'table' }, true)} />
            </div>

            <AppSegmentedControl className="emp-tabs" label="Статус сотрудников" role="tablist" value={tab.key}
              options={TABS.map((item) => ({ value: item.key, label: item.title, count: counts.state === 'ready' ? count(counts.data, item.statuses) : null }))}
              onChange={(key) => patch({ tab: key === 'all' ? null : key })} />

            <div className="emp-body">
              <Rows block={list}>
                {(data) =>
                  data.items.length === 0 ? (
                    <p className="emp-empty">
                      {search || office || department
                        ? 'По этим условиям никого не нашлось.'
                        : 'В доступной области нет сотрудников.'}
                    </p>
                  ) : view === 'cards' ? (
                    <div className="emp-cards">
                      {data.items.map((person) => (
                        <PersonCard
                          key={person.id}
                          person={person}
                          chosen={person.id === chosen?.id}
                          onPick={() => patch({ picked: person.id }, true)}
                          onOpen={() => patch({ employee: person.id }, true)}
                        />
                      ))}
                    </div>
                  ) : (
                    <PeopleTable rows={data.items} chosen={chosen?.id ?? ''}
                                 onPick={(id) => patch({ picked: id }, true)} />
                  )
                }
              </Rows>
            </div>

            <footer className="emp-pager">
              <p className="emp-pager__note">
                {list.state === 'ready'
                  ? `Показано ${items.length} из ${plural(total, 'сотрудник')}`
                  : ''}
              </p>
              <div className="emp-pager__tools">
                <AppSelectField className="emp-size" label="Строк на странице" value={limit} onChange={(value) => patch({ limit: value })}>
                    {SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
                </AppSelectField>
                <Pages page={page} pages={pages}
                       onGo={(next) => patch({ page: next === 1 ? null : String(next) }, true)} />
              </div>
            </footer>
          </section>

          <TeamPanel
            presence={presence}
            highlights={highlights}
            person={chosen}
            onOpen={() => chosen && patch({ employee: chosen.id }, true)}
          />
        </div>
      </div>

      {opened && <EmployeeCard id={opened} onClose={() => patch({ employee: null }, true)} />}
    </AppShell>
  );
}

// --- список ----------------------------------------------------------------

function PersonCard({ person, chosen, onPick, onOpen }: {
  person: api.EmployeeRow;
  chosen: boolean;
  onPick: () => void;
  onOpen: () => void;
}) {
  const at = person.current_assignment;
  const telegram = person.telegram_username;
  return (
    <article
      className={chosen ? 'emp-card emp-card--on' : 'emp-card'}
      tabIndex={0}
      aria-current={chosen || undefined}
      onClick={onPick}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onPick();
        }
      }}
    >
      <Photo id={person.id} name={person.full_name} has={person.photo} className="emp-card__photo" />
      <div className="emp-card__body">
        <h3 className="emp-card__name">{shortName(person.full_name)}</h3>
        <p className="emp-card__number">{person.employee_number ?? '—'}</p>
        <p className="emp-card__role">{at?.position_name ?? 'Должность не назначена'}</p>
        <p className="emp-card__dept">{at?.department_name ?? '—'}</p>
        <p className="emp-card__meta">
          <Bit icon="pin" text={at?.office_name ?? '—'} />
          <Bit icon="clock" text={scheduleLine(person.current_schedule)} />
          {telegram
            ? <Bit icon="send" text={`@${telegram}`} tone="link" />
            : <Bit icon="doc" text={telegramShort(person.telegram_state)} />}
        </p>
      </div>
      <Status person={person} className="emp-card__status" />
      <CardMenu onOpen={onOpen} />
    </article>
  );
}

function Bit({ icon, text, tone }: { icon: AppIconName; text: string; tone?: 'link' }) {
  return (
    <span className={tone ? `emp-bit emp-bit--${tone}` : 'emp-bit'}>
      <AppIcon name={icon} size={16} />
      <span className="emp-bit__text">{text}</span>
    </span>
  );
}

function CardMenu({ onOpen }: { onOpen: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="emp-menu">
      <button type="button" className="emp-menu__dots" aria-label="Действия" aria-expanded={open}
              onClick={(event) => { event.stopPropagation(); setOpen((was) => !was); }}>
        <i /><i /><i />
      </button>
      {open && (
        <span className="emp-menu__drop" role="menu">
          <button type="button" role="menuitem"
                  onClick={(event) => { event.stopPropagation(); setOpen(false); onOpen(); }}>
            Открыть профиль
          </button>
        </span>
      )}
    </span>
  );
}

/**
 * Состояние одной плашкой. Показывается то, что мешает раньше всего:
 * уволенного не называют «без графика».
 */
function Status({ person, className }: { person: api.EmployeeRow; className: string }) {
  const status = person.employment_status;
  const [tone, title] =
    status === 'TERMINATED' || status === 'ARCHIVED' ? ['off', 'Уволен']
      : status === 'SUSPENDED' ? ['off', 'Неактивен']
        : !person.current_schedule ? ['warn', 'Без графика']
          : person.telegram_state !== 'ACTIVE' ? ['warn', 'Не подключён']
            : ['ok', 'Активен'];
  return <span className={`emp-status emp-status--${tone} ${className}`}>{title}</span>;
}

/** Лицо или инициалы. Слот одного размера в обоих случаях. */
function Photo({ id, name, has, className }: {
  id: string;
  name: string;
  has: boolean;
  className: string;
}) {
  const [broken, setBroken] = useState(false);
  if (!has || broken) {
    return <span className={`emp-photo emp-photo--none ${className}`} aria-hidden="true">{initials(name)}</span>;
  }
  return (
    <img
      className={`emp-photo ${className}`}
      src={api.employeePhotoUrl(id)}
      alt=""
      onError={() => setBroken(true)}
      // Файл размером в точку — заглушка тестовой загрузки, а не портрет.
      onLoad={(event) => { if (event.currentTarget.naturalWidth < 32) setBroken(true); }}
    />
  );
}

function PeopleTable({ rows, chosen, onPick }: {
  rows: api.EmployeeRow[];
  chosen: string;
  onPick: (id: string) => void;
}) {
  return (
    <div className="emp-table-wrap">
      <table className="emp-table table-cards">
        <thead>
          <tr>
            <th>Сотрудник</th>
            <th>Должность / отдел</th>
            <th>Офис</th>
            <th>График</th>
            <th>Telegram</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((person) => (
            <tr key={person.id} tabIndex={0}
                className={person.id === chosen ? 'emp-table__on' : undefined}
                onClick={() => onPick(person.id)}
                onKeyDown={(event) => { if (event.key === 'Enter') onPick(person.id); }}>
              <td>
                <span className="emp-table__who">
                  <Photo id={person.id} name={person.full_name} has={person.photo} className="emp-table__photo" />
                  <span>
                    <b>{shortName(person.full_name)}</b>
                    <small>{person.employee_number ?? '—'}</small>
                  </span>
                </span>
              </td>
              <td data-label="Должность / отдел">
                {person.current_assignment?.position_name ?? '—'}
                <small>{person.current_assignment?.department_name ?? ''}</small>
              </td>
              <td data-label="Офис">{person.current_assignment?.office_name ?? '—'}</td>
              <td data-label="График">{scheduleLine(person.current_schedule)}</td>
              <td data-label="Telegram">{person.telegram_username ? `@${person.telegram_username}` : telegramShort(person.telegram_state)}</td>
              <td data-label="Статус"><Status person={person} className="" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Номера страниц: первые пять, соседи текущей и последняя. */
function Pages({ page, pages, onGo }: { page: number; pages: number; onGo: (next: number) => void }) {
  const shown: (number | 'gap')[] = [];
  for (let at = 1; at <= pages; at += 1) {
    if (at <= 5 || at === pages || Math.abs(at - page) <= 1) shown.push(at);
    else if (shown[shown.length - 1] !== 'gap') shown.push('gap');
  }
  return (
    <nav className="emp-pages" aria-label="Страницы списка">
      <button type="button" className="emp-pages__arrow" aria-label="Предыдущая"
              disabled={page <= 1} onClick={() => onGo(page - 1)}>
        <AppIcon name="back" size={16} />
      </button>
      {shown.map((item, at) =>
        item === 'gap' ? (
          <span key={`gap-${at}`} className="emp-pages__gap">…</span>
        ) : (
          <button key={item} type="button"
                  className={item === page ? 'emp-pages__one emp-pages__one--on' : 'emp-pages__one'}
                  aria-current={item === page ? 'page' : undefined}
                  onClick={() => onGo(item)}>
            {item}
          </button>
        ),
      )}
      <button type="button" className="emp-pages__arrow" aria-label="Следующая"
              disabled={page >= pages} onClick={() => onGo(page + 1)}>
        <AppIcon name="next" size={16} />
      </button>
    </nav>
  );
}

// --- правая панель -----------------------------------------------------------

function TeamPanel({ presence, highlights, person, onOpen }: {
  presence: Block<api.Presence>;
  highlights: Block<api.EmployeeHighlights>;
  person: api.EmployeeRow | null;
  onOpen: () => void;
}) {
  const counts = presence.state === 'ready' ? presence.data.counts : {};
  const here = counts['IN_OFFICE'] ?? 0;
  const left = counts['LEFT'] ?? 0;
  const away = (counts['NOT_COME'] ?? 0) + (counts['VACATION'] ?? 0)
    + (counts['SICK_LEAVE'] ?? 0) + (counts['OTHER_ABSENCE'] ?? 0);
  const total = here + left + away;
  const share = total ? Math.round((here / total) * 100) : 0;
  const day = todayIso();

  return (
    <aside className="emp-team" aria-label="Сегодня в команде">
      <div className="emp-team__head">
        <h2 className="emp-team__title">Сегодня в команде</h2>
        <span className="emp-team__date">
          {longDate(day)}, {WEEKDAYS[new Date(`${day}T12:00:00`).getDay()]}
        </span>
      </div>

      <div className="emp-sum">
        <Ring share={share} />
        <p className="emp-sum__main">
          <strong>{here} из {total}</strong>
          <span>сотрудников на месте</span>
        </p>
        <ul className="emp-sum__legend">
          <li><i className="emp-dot emp-dot--ok" />На месте<b>{here}</b></li>
          <li><i className="emp-dot emp-dot--idle" />Ушли<b>{left}</b></li>
          <li><i className="emp-dot emp-dot--warn" />Нет на месте<b>{away}</b></li>
        </ul>
      </div>

      {highlights.state === 'ready' ? (
        <>
          <Tile icon="user" tone="blue" title="Онбординг"
                line={plural(highlights.data.recent_hires, 'новый')}
                note="за последние 30 дней"
                // Ход приёма: у скольких новичков уже назначен график.
                progress={{
                  done: Math.max(highlights.data.recent_hires - highlights.data.without_schedule, 0),
                  of: highlights.data.recent_hires,
                }} />
          <Tile icon="calendar" tone="red" title="Дни рождения"
                line={highlights.data.birthdays_today
                  ? plural(highlights.data.birthdays_today, 'сотрудник') : 'Сегодня никого'}
                note={highlights.data.birthdays_today ? 'сегодня' : ''}
                faces={highlights.data.birthdays} total={highlights.data.birthdays_today} />
          <Tile icon="clock" tone="amber" title="Нет графика"
                line={highlights.data.without_schedule
                  ? plural(highlights.data.without_schedule, 'сотрудник') : 'У всех назначен'}
                note={highlights.data.without_schedule ? 'требуют настройки' : ''}
                faces={highlights.data.unscheduled} total={highlights.data.without_schedule} />
        </>
      ) : (
        <p className="emp-team__wait">
          {highlights.state === 'loading' ? 'Загружаем…' : 'Не удалось загрузить сводку.'}
        </p>
      )}

      <div className="emp-chosen">
        <div className="emp-chosen__head">
          <h2 className="emp-team__title">Выбранный сотрудник</h2>
          <button type="button" className="emp-chosen__go" aria-label="Открыть профиль"
                  onClick={onOpen} disabled={!person}>
            <AppIcon name="next" size={20} />
          </button>
        </div>
        {person ? <Chosen person={person} onOpen={onOpen} /> : (
          <p className="emp-team__wait">Выберите сотрудника в списке.</p>
        )}
      </div>
    </aside>
  );
}

function Ring({ share }: { share: number }) {
  const radius = 35;
  const length = 2 * Math.PI * radius;
  const filled = (Math.min(Math.max(share, 0), 100) / 100) * length;
  return (
    <svg className="emp-ring" viewBox="0 0 80 80" width={80} height={80}
         role="img" aria-label={`На месте ${share} процентов`}>
      <circle className="emp-ring__track" cx="40" cy="40" r={radius} />
      <circle className="emp-ring__fill" cx="40" cy="40" r={radius}
              strokeDasharray={`${filled} ${length}`} transform="rotate(-90 40 40)" />
      <text className="emp-ring__text" x="40" y="41">{share}%</text>
    </svg>
  );
}

function Tile({ icon, tone, title, line, note, faces, total, progress }: {
  icon: AppIconName;
  tone: 'blue' | 'red' | 'amber';
  title: string;
  line: string;
  note: string;
  faces?: api.EmployeeBrief[];
  total?: number;
  progress?: { done: number; of: number };
}) {
  return (
    <div className={progress ? 'emp-tile emp-tile--progress' : 'emp-tile'} role="group" aria-label={title}>
      <span className={`emp-tile__icon emp-tile__icon--${tone}`}>
        <AppIcon name={icon} size={20} />
      </span>
      <span className="emp-tile__text">
        <b>{title}</b>
        <span>{line}</span>
        {note && <small>{note}</small>}
      </span>
      {faces && <Faces rows={faces} total={total ?? 0} />}
      {progress && (
        <>
          <span className="emp-tile__of">{progress.done} из {progress.of}</span>
          <span className="emp-tile__bar" role="img"
                aria-label={`Оформлено ${progress.done} из ${progress.of}`}>
            <i style={{ width: `${progress.of ? Math.round((progress.done / progress.of) * 100) : 0}%` }} />
          </span>
        </>
      )}
      <AppIcon name="next" size={20} className="emp-tile__go" />
    </div>
  );
}

function Faces({ rows, total }: { rows: api.EmployeeBrief[]; total: number }) {
  if (rows.length === 0) return null;
  const shown = rows.slice(0, 3);
  const rest = total - shown.length;
  return (
    <span className={shown.length > 2 ? 'emp-faces emp-faces--tight' : 'emp-faces'}>
      {shown.map((one) => (
        <Photo key={one.id} id={one.id} name={one.full_name} has={one.photo} className="emp-faces__one" />
      ))}
      {rest > 0 && <span className="emp-faces__rest">+{rest}</span>}
    </span>
  );
}

function Chosen({ person, onOpen }: { person: api.EmployeeRow; onOpen: () => void }) {
  const at = person.current_assignment;
  const email = person.corporate_email;
  return (
    <>
      <div className="emp-chosen__who">
        <Photo id={person.id} name={person.full_name} has={person.photo} className="emp-chosen__photo" />
        <div className="emp-chosen__text">
          <p className="emp-chosen__top">
            <b>{shortName(person.full_name)}</b>
            <Status person={person} className="" />
          </p>
          <p className="emp-chosen__number">{person.employee_number ?? '—'}</p>
          <p className="emp-chosen__role">{at?.position_name ?? 'Должность не назначена'}</p>
          <p className="emp-chosen__dept">{at?.department_name ?? '—'}</p>
        </div>
      </div>

      <div className="emp-chosen__facts">
        <p className="emp-chosen__row">
          <Bit icon="pin" text={at?.office_name ?? '—'} />
          <Bit icon="clock" text={scheduleLine(person.current_schedule)} />
        </p>
        <p className="emp-chosen__row">
          <Bit icon="send" text={person.telegram_username
            ? `@${person.telegram_username}` : telegramShort(person.telegram_state)} />
        </p>
        <p className="emp-chosen__row">
          <Bit icon="inbox" text={email ?? 'Почта не указана'} />
        </p>
      </div>

      <div className="emp-chosen__actions">
        <button type="button" className="emp-btn emp-btn--outline" onClick={onOpen}>
          Открыть профиль
        </button>
        {/* «Написать» ведёт на почту: другого канала из CRM нет. */}
        <a className={email ? 'emp-btn emp-btn--blue' : 'emp-btn emp-btn--blue emp-btn--off'}
           href={email ? `mailto:${email}` : undefined}
           aria-disabled={email ? undefined : true}>
          Написать
        </a>
      </div>
    </>
  );
}

// --- мелочи ------------------------------------------------------------------

function Select({ label, empty, value, options, onChange }: {
  label: string;
  empty: string;
  value: string;
  options: { id: string; name: string }[];
  onChange: (value: string) => void;
}) {
  return <Dropdown label={label} empty={empty} value={value} options={options} onChange={onChange} />;
}

function Rows<T>({ block, children }: { block: Block<T>; children: (data: T) => React.ReactNode }) {
  if (block.state === 'loading') return <p className="emp-empty">Загружаем список…</p>;
  if (block.state === 'denied') return <p className="emp-empty">Нет доступа к списку сотрудников.</p>;
  if (block.state === 'error') {
    return <p className="emp-empty emp-empty--bad">Не удалось загрузить список.</p>;
  }
  return <>{children(block.data)}</>;
}

/** «Мирзаева Лола» из «Мирзаева Лола Азизовна»: отчество в карточку не входит. */
function shortName(full: string): string {
  return full.split(' ').slice(0, 2).join(' ');
}

/** «Пн–Пт 09:00–18:00» из дней и часов графика, а не из его названия. */
function scheduleLine(schedule: api.Schedule | null): string {
  if (!schedule) return 'Нет графика';
  const names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const days = schedule.weekdays ?? [];
  const label = (weekday: number | undefined) =>
    weekday === undefined ? undefined : names.at(weekday - 1);
  const first = label(days.at(0));
  const last = label(days.at(-1));
  const range = !first ? '' : days.length === 1 ? first : `${first}–${last ?? first}`;
  const clock = schedule.start_time && schedule.end_time
    ? `${schedule.start_time.slice(0, 5)}–${schedule.end_time.slice(0, 5)}`
    : '';
  return [range, clock].filter(Boolean).join(' ') || schedule.name;
}

function telegramShort(state: string | null | undefined): string {
  if (!state) return 'Не привязан';
  return TELEGRAM_TITLE[state] ?? state;
}

function ago(moment: Date): string {
  const minutes = Math.round((Date.now() - moment.getTime()) / 60000);
  if (minutes < 1) return 'Обновлено только что';
  if (minutes < 60) return `Обновлено ${minutes} мин назад`;
  return `Обновлено в ${moment.getHours()}:${String(moment.getMinutes()).padStart(2, '0')}`;
}

function count(counts: Record<string, number>, statuses: readonly string[]): number {
  if (statuses.length === 0) return counts['total'] ?? 0;
  return statuses.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
}

const FORMS: Record<string, [string, string, string]> = {
  'сотрудник': ['сотрудник', 'сотрудника', 'сотрудников'],
  'офис': ['офис', 'офиса', 'офисов'],
  'новый': ['новый сотрудник', 'новых сотрудника', 'новых сотрудников'],
};

/** «252 сотрудника», «12 офисов». */
function plural(n: number, word: string): string {
  const forms = FORMS[word] ?? [word, word, word];
  const form =
    n % 10 === 1 && n % 100 !== 11 ? forms[0]
      : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? forms[1]
        : forms[2];
  return `${n} ${form}`;
}
