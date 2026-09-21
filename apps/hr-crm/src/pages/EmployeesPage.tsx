/**
 * Страница «Сотрудники».
 *
 * Список во всю ширину. Правая колонка «Сегодня в команде» убрана: она
 * повторяла главную, а списку оставляла две трети экрана — при трёх
 * карточках в ряд фамилии обрывались.
 *
 * Отборы многозначные: офисы, регионы, отделы, должности и занятость
 * принимают по несколько значений сразу. Кадровик спрашивает «двое
 * стажёров в Самарканде и Бухаре», а не «по одному офису за раз».
 * Внутри одного отбора значения складываются, между отборами —
 * пересекаются; офисы и регионы — один географический отбор, и они
 * складываются между собой (иначе «Головной офис + Самаркандская
 * область» дало бы пусто и читалось как поломка).
 *
 * Выбранное видно строкой плашек под отборами: свёрнутый в «Офисы · 2»
 * список не говорит, ЧТО именно выбрано, а ошибка отбора дороже строки.
 *
 * Состояние списка живёт в адресе: вернувшись из карточки, человек видит
 * тот же поиск, отборы, вид и страницу. Значения в адресе — через
 * запятую, их же понимает сервер.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { AppMultiSelect } from '../components/AppSelect';
import { useBlock, type Block } from '../features/dashboard/data';
import {
  EMPLOYMENT_STATUS,
  employmentStatus,
  employmentTitle,
  isWorking,
} from '../features/employees/status';
import '../styles/employees.css';

const ACTIVE_STATUSES = ['ACTIVE', 'PROBATION'];
/** Сколько сотрудников на странице. Пятнадцать, а не шестнадцать: в ряду
 *  три карточки, и шестнадцатая оставляла последний ряд с одной
 *  карточкой и двумя дырами. Значение одно и не настраивается — выбор
 *  «8 / 16 / 32» ничего не решал, а место в строке занимал. */
const PAGE_SIZE = '15';

/** Занятость — отбор по трудовому статусу. Названия те же, что в плашке. */
const EMPLOYMENT = Object.entries(EMPLOYMENT_STATUS)
  .map(([value, [label]]) => ({ value, label }));

const TELEGRAM_TITLE: Record<string, string> = {
  ACTIVE: 'Привязан',
  PENDING: 'Ожидает HR',
  PENDING_CONFIRMATION: 'Ожидает HR',
  REVOKED: 'Отозван',
  BLOCKED: 'Заблокирован',
};

export function EmployeesPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const place = useLocation();

  /*
   * «Открыть профиль» ведёт на карточку сотрудника, а не открывает
   * боковое окно поверх списка: человек просит карточку и должен
   * получить карточку. Адрес возврата уходит с собой — «Назад» в
   * карточке возвращает в тот же список с теми же фильтрами.
   */
  const openProfile = useCallback(
    (id: string) => {
      const back = encodeURIComponent(place.pathname + place.search);
      navigate(`/employees/${id}?back=${back}`);
    },
    [navigate, place.pathname, place.search],
  );
  /*
   * `?employee=<id>` открывал боковое окно. Окна больше нет, а ссылки на
   * него остались — в уведомлениях, в закладках, в чужих сообщениях.
   * Такой адрес молча ведёт на саму карточку.
   */
  const stale = params.get('employee') ?? '';
  useEffect(() => {
    if (stale) navigate(`/employees/${stale}`, { replace: true });
  }, [stale, navigate]);

  const search = params.get('search') ?? '';
  const offices = listOf(params.get('office_id'));
  const regions = listOf(params.get('region_id'));
  const departments = listOf(params.get('department_id'));
  const positions = listOf(params.get('position_id'));
  const employment = listOf(params.get('status'));
  const limit = PAGE_SIZE;
  const page = Math.max(1, Number(params.get('page') ?? '1'));

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

  /** Отбор уходит в адрес через запятую — ровно так его читает сервер. */
  const setList = useCallback(
    (key: string, values: string[]) => patch({ [key]: values.join(',') || null }),
    [patch],
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
      ...(offices.length ? { office_id: offices.join(',') } : {}),
      ...(regions.length ? { region_id: regions.join(',') } : {}),
      ...(departments.length ? { department_id: departments.join(',') } : {}),
      ...(positions.length ? { position_id: positions.join(',') } : {}),
    }),
    // Строки, а не массивы: массив каждый раз новый, и `useMemo` терял смысл.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [search, offices.join(','), regions.join(','), departments.join(','), positions.join(',')],
  );
  const offset = (page - 1) * Number(limit);
  const key = [search, offices.join('+'), regions.join('+'), departments.join('+'),
               positions.join('+'), employment.join('+')].join('|');

  const [list] = useBlock(
    (signal) =>
      api.employees(
        {
          ...filters,
          limit,
          ...(offset ? { offset: String(offset) } : {}),
          ...(employment.length ? { status: employment.join(',') } : {}),
        },
        signal,
      ),
    `people|${key}|${page}`,
  );

  const [counts] = useBlock((signal) => api.employeeCounts(filters, signal), `counts|${key}`);

  const [directory] = useBlock(
    (signal) =>
      Promise.all([
        api.offices(signal),
        api.regions(signal),
        api.departments(signal),
        api.positions(signal),
      ]).then(([o, r, d, p]) => ({
        offices: o.items.filter((one) => one.status === 'ACTIVE'),
        regions: r.items,
        departments: d.items,
        positions: p.items,
      })),
    'directory|employees',
  );

  /*
   * Состояние дня — одним запросом на всех, а не по сотруднику.
   *
   * В подвале карточки стоит «В офисе» или «На больничном»; спрашивать
   * это по одному значило бы шестнадцать запросов на страницу.
   */
  const [presence] = useBlock((signal) => api.presenceDay({}, signal), 'presence|day');
  const today = useMemo(() => {
    const map = new Map<string, api.PresenceRow>();
    if (presence.state === 'ready') {
      for (const row of presence.data.items) map.set(row.employee_id, row);
    }
    return map;
  }, [presence]);

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
      await api.orderExport({
        kind: 'employees',
        fmt: 'xlsx',
        ...(offices.length === 1 ? { office_id: offices[0] as string } : {}),
      });
      setOrdered('Выгрузка поставлена в очередь');
    } catch (error) {
      setOrdered(messageFor(error));
    } finally {
      setOrdering(false);
    }
  }

  const books = directory.state === 'ready' ? directory.data : null;
  const officeOptions = (books?.offices ?? []).map((one) => ({
    value: one.id, label: one.name, ...(one.region_name ? { note: one.region_name } : {}),
  }));
  const regionOptions = (books?.regions ?? []).map((one) => ({ value: one.id, label: one.name }));
  const departmentOptions = (books?.departments ?? []).map((one) => ({ value: one.id, label: one.name }));
  const positionOptions = (books?.positions ?? []).map((one) => ({ value: one.id, label: one.name }));

  /** Что выбрано — плашками, по одной на значение. */
  const chips: { group: string; key: string; value: string; label: string }[] = [
    ...pick('Офисы', 'office_id', offices, officeOptions),
    ...pick('Регионы', 'region_id', regions, regionOptions),
    ...pick('Отделы', 'department_id', departments, departmentOptions),
    ...pick('Должности', 'position_id', positions, positionOptions),
    ...pick('Занятость', 'status', employment, EMPLOYMENT),
  ];

  const drop = (key: string, value: string) => {
    const was = listOf(params.get(key));
    setList(key, was.filter((one) => one !== value));
  };

  const clearAll = () =>
    patch({
      search: null, office_id: null, region_id: null,
      department_id: null, position_id: null, status: null,
    });

  const total = counts.state === 'ready'
    ? count(counts.data, employment)
    : 0;
  const pages = Math.max(1, Math.ceil(total / Number(limit)));
  const items = list.state === 'ready' ? list.data.items : [];
  const anything = Boolean(search || chips.length);

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
              {books && (
                <>
                  <i className="emp-head__dot">•</i>
                  <span>{plural(books.offices.length, 'офис')}</span>
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

        <section className="emp-list" aria-label="Список сотрудников">
          <div className="emp-filters">
            <label className="emp-search">
              <AppIcon name="search" size={18} />
              <input
                type="search"
                value={draft}
                placeholder="Поиск по имени или должности"
                aria-label="Поиск по имени, должности или Telegram"
                onChange={(event) => setDraft(event.target.value)}
              />
            </label>
            <Pick title="Офисы" empty="Все офисы" heading="Выберите офисы" find="Найти офис"
                  value={offices} options={officeOptions}
                  onChange={(next) => setList('office_id', next)} />
            <Pick title="Регионы" empty="Все регионы" heading="Выберите регионы" find="Найти регион"
                  value={regions} options={regionOptions}
                  onChange={(next) => setList('region_id', next)} />
            <Pick title="Отделы" empty="Все отделы" heading="Выберите отделы" find="Найти отдел"
                  value={departments} options={departmentOptions}
                  onChange={(next) => setList('department_id', next)} />
            <Pick title="Должности" empty="Все должности" heading="Выберите должности"
                  find="Найти должность" value={positions} options={positionOptions}
                  onChange={(next) => setList('position_id', next)} />
            <Pick title="Занятость" empty="Любая занятость" heading="Выберите занятость"
                  find="Найти" value={employment} options={EMPLOYMENT}
                  onChange={(next) => setList('status', next)} />
          </div>

          {anything && (
            <div className="emp-chips">
              {groupsOf(chips).map(([group, rows]) => (
                <span key={group} className="emp-chips__group">
                  <span className="emp-chips__label">{group}:</span>
                  {rows.map((chip) => (
                    <span key={`${chip.key}:${chip.value}`} className="emp-chip">
                      {chip.label}
                      <button type="button" aria-label={`Убрать «${chip.label}»`}
                              onClick={() => drop(chip.key, chip.value)}>
                        <AppIcon name="close" size={16} />
                      </button>
                    </span>
                  ))}
                </span>
              ))}
              {search && (
                <span className="emp-chips__group">
                  <span className="emp-chips__label">Поиск:</span>
                  <span className="emp-chip">
                    {search}
                    <button type="button" aria-label="Очистить поиск"
                            onClick={() => patch({ search: null })}>
                      <AppIcon name="close" size={16} />
                    </button>
                  </span>
                </span>
              )}
              <button type="button" className="emp-chips__clear" onClick={clearAll}>
                Сбросить всё
              </button>
            </div>
          )}

          <div className="emp-body">
            <Rows block={list}>
              {(data) =>
                data.items.length === 0 ? (
                  <p className="emp-empty">
                    {anything
                      ? 'По этим условиям никого не нашлось.'
                      : 'В доступной области нет сотрудников.'}
                  </p>
                ) : (
                  <div className="emp-cards">
                    {data.items.map((person) => (
                      <PersonCard key={person.id} person={person} today={today.get(person.id)}
                                  onOpen={() => openProfile(person.id)} />
                    ))}
                  </div>
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
            <Pages page={page} pages={pages}
                   onGo={(next) => patch({ page: next === 1 ? null : String(next) }, true)} />
          </footer>
        </section>
      </div>
    </AppShell>
  );
}

/** Один многозначный отбор. Ширину задаёт страница, не компонент. */
function Pick({ title, empty, heading, find, value, options, onChange }: {
  title: string;
  empty: string;
  heading: string;
  find: string;
  value: string[];
  options: { value: string; label: string; note?: string }[];
  onChange: (value: string[]) => void;
}) {
  return (
    <AppMultiSelect className="emp-pick" label={title} title={title} empty={empty}
                    heading={heading} findLabel={find} value={value} options={options}
                    disabled={options.length === 0} onChange={onChange} />
  );
}

/** Значения отбора из адреса: «a,b» → ['a','b']. */
function listOf(raw: string | null): string[] {
  return (raw ?? '').split(',').map((one) => one.trim()).filter(Boolean);
}

/** Плашки одного отбора: только те значения, для которых есть название. */
function pick(
  group: string,
  key: string,
  values: string[],
  options: { value: string; label: string }[],
): { group: string; key: string; value: string; label: string }[] {
  return values.map((value) => ({
    group,
    key,
    value,
    label: options.find((one) => one.value === value)?.label ?? value,
  }));
}

/** Плашки по отборам, в порядке появления. */
function groupsOf<T extends { group: string }>(rows: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const row of rows) {
    const kept = groups.get(row.group);
    if (kept) kept.push(row);
    else groups.set(row.group, [row]);
  }
  return [...groups];
}

// --- список ----------------------------------------------------------------

function PersonCard({ person, today, onOpen }: {
  person: api.EmployeeRow;
  /** Что у человека сегодня. `undefined` — отметок дня нет вовсе. */
  today: api.PresenceRow | undefined;
  onOpen: () => void;
}) {
  const at = person.current_assignment;
  const telegram = person.telegram_username;
  const state = dayState(today);
  return (
    <article className="emp-card">
      <div className="emp-card__top">
        <Photo id={person.id} name={person.full_name} has={person.photo} className="emp-card__photo" />
        <div className="emp-card__body">
          {/* Имя и состояние — одной строкой. Состояние стояло отдельным
              слоем поверх карточки, и под ним приходилось держать пустое
              поле справа: при трёх карточках в ряд фамилия в это поле
              уже не помещалась и обрывалась. */}
          <div className="emp-card__title">
            <h3 className="emp-card__name">{shortName(person.full_name)}</h3>
            <Status person={person} className="emp-card__status" />
          </div>
          <p className="emp-card__role">
            {at?.position_name
              ? <>Должность: <b>{at.position_name}</b></>
              : 'Должность не назначена'}
          </p>
          <p className="emp-card__meta">
            <Bit icon="pin" text={[at?.region_name, at?.office_name].filter(Boolean).join(' · ') || '—'} />
            <Bit icon="clock" text={scheduleLine(person.current_schedule)} />
            {telegram
              ? <Bit icon="send" text={`@${telegram}`} tone="link"
                     href={`https://t.me/${telegram.replace(/^@/, '')}`} />
              : <Bit icon="doc" text={telegramShort(person.telegram_state)} />}
          </p>
        </div>
      </div>

      {/* Подвал: что с человеком сегодня и дорога в карточку. Трудовой
          статус вверху — «кем числится», здесь — «где он сейчас». */}
      <footer className="emp-card__foot">
        <span className={`emp-day emp-day--${state.tone}`}>
          <i aria-hidden="true" />
          {state.title}
        </span>
        <button type="button" className="emp-card__go" onClick={onOpen}>
          Открыть профиль
          <AppIcon name="next" size={16} />
        </button>
      </footer>
    </article>
  );
}

/** Порядок и цвет состояний дня — те же, что на «Посещаемости». */
function dayState(row: api.PresenceRow | undefined): {
  title: string; tone: 'ok' | 'warn' | 'idle' | 'blue' | 'violet';
} {
  if (!row) return { title: 'Нет данных', tone: 'idle' };
  const late = row.late_minutes ?? 0;
  switch (row.state) {
    case 'IN_OFFICE':
      return late > 0 ? { title: `Опоздал на ${late} мин`, tone: 'warn' } : { title: 'В офисе', tone: 'ok' };
    case 'LEFT': return { title: 'Ушёл', tone: 'idle' };
    case 'LATE': return { title: 'Опаздывает', tone: 'warn' };
    case 'NOT_COME':
      return row.notice_kind === 'ABSENT'
        ? { title: 'Не придёт', tone: 'warn' }
        : { title: 'Нет отметки', tone: 'idle' };
    case 'VACATION': return { title: 'В отпуске', tone: 'blue' };
    case 'SICK_LEAVE': return { title: 'На больничном', tone: 'violet' };
    case 'OTHER_ABSENCE': return { title: row.absence_name ?? 'Отсутствует', tone: 'violet' };
    case 'DAY_OFF': return { title: 'Выходной', tone: 'idle' };
    case 'NO_SCHEDULE': return { title: 'Без графика', tone: 'idle' };
    default: return { title: 'Нет отметки', tone: 'idle' };
  }
}

/**
 * Строчка сведений карточки: значок и текст.
 *
 * С `href` становится ссылкой — так открывается Telegram сотрудника.
 * Адрес ведёт на сам Telegram и уходит из CRM, поэтому новая вкладка и
 * `rel="noreferrer"`: чужая страница не должна получить доступ к нашей.
 */
function Bit({ icon, text, tone, href }: {
  icon: AppIconName; text: string; tone?: 'link'; href?: string;
}) {
  const shape = tone ? `emp-bit emp-bit--${tone}` : 'emp-bit';
  const inside = (
    <>
      <AppIcon name={icon} size={16} />
      <span className="emp-bit__text">{text}</span>
    </>
  );
  if (!href) return <span className={shape}>{inside}</span>;
  return (
    <a className={`${shape} emp-bit--go`} href={href} target="_blank" rel="noreferrer"
       title={`Открыть ${text} в Telegram`}>
      {inside}
    </a>
  );
}

/**
 * Состояние одной плашкой. Показывается то, что мешает раньше всего:
 * уволенного не называют «без графика».
 */
function Status({ person, className }: { person: api.EmployeeRow; className: string }) {
  const status = person.employment_status;
  // Трудовой статус сильнее помех: уволенного не называют «без графика».
  // А вот работающему полезнее увидеть то, что мешает ему прямо сейчас,
  // чем слово «Работает», которое он и так знает.
  const [tone, title] =
    !isWorking(status) ? [employmentStatus(status)[1], employmentTitle(status)]
      : !person.current_schedule ? ['warn', 'Без графика']
        : person.telegram_state !== 'ACTIVE' ? ['warn', 'Не подключён']
          : [employmentStatus(status)[1], employmentTitle(status)];
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
        {/* Парная стрелка к «назад»: `next` — узкий шеврон из другого
            набора, и рядом с ней он читался как другая кнопка. */}
        <AppIcon name="arrow" size={16} />
      </button>
    </nav>
  );
}

// --- мелочи ------------------------------------------------------------------

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
