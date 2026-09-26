/**
 * Вкладка «Сотрудники» в настройке офиса.
 *
 * Состав офиса читают целиком: кто в каком отделе, на какой должности, по
 * какому графику и что у него сегодня. Поэтому таблица, а не список имён:
 * взглядом сравнивают столбцы, а не карточки.
 *
 * Состав офиса берётся одним запросом, до двухсот человек, — а поиск,
 * отбор по отделу, сортировка и страницы считаются в браузере. Так поиск
 * отвечает на каждую букву без похода на сервер, «Показано N из M» не
 * врёт, и сортировка идёт по всему офису, а не по видимой странице.
 * Офисов крупнее двухсот человек не бывает; если такой появится, таблица
 * честно скажет, что показала первых, и попросит сузить отбор.
 *
 * Столбец «Сегодня» — из присутствия за текущий день: то же состояние и
 * теми же словами, что на «Посещаемости».
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import * as api from '../api/crm';
import { useBlock } from '../features/dashboard/data';
import { AppIcon } from './AppIcon';
import { AppSelect } from './AppSelect';

/** Сколько человек в офисе читаем за раз. Больше сервер и не отдаст. */
const BATCH = '200';
const PER_PAGE = 10;

type SortKey = 'name' | 'department' | 'position' | 'schedule' | 'today';
type Tone = 'ok' | 'warn' | 'idle' | 'blue' | 'violet';

export function OfficeStaff({ officeId }: { officeId: string }) {
  const [search, setSearch] = useState('');
  const [department, setDepartment] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'name', desc: false });
  const [page, setPage] = useState(1);

  const [list] = useBlock(
    (signal) => api.employees({ office_id: officeId, limit: BATCH }, signal),
    `office-staff|${officeId}`,
  );
  // Присутствие за сегодня — отдельным запросом: список сотрудников про
  // отметки не знает, а столбец «Сегодня» без них пустой.
  const [presence] = useBlock(
    (signal) => api.presenceDay({ office_id: officeId }, signal),
    `office-staff-today|${officeId}`,
  );

  const people = useMemo(() => (list.state === 'ready' ? list.data.items : []), [list]);
  const today = useMemo(() => {
    const map = new Map<string, api.PresenceRow>();
    if (presence.state === 'ready') {
      for (const row of presence.data.items) map.set(row.employee_id, row);
    }
    return map;
  }, [presence]);

  /*
   * Отделы — из справочника офиса, а не из его состава.
   *
   * Собранные по людям, они исчезали там, где людей ещё нет: в пустом
   * офисе список оказывался пустым и не открывался вовсе. К ним
   * добавляются отделы, найденные у сотрудников: человек может
   * числиться в общем отделе компании, которого нет среди отделов
   * этого офиса.
   */
  const [book] = useBlock(
    (signal) => api.officeDepartments(officeId, signal),
    `office-staff-dept|${officeId}`,
  );

  const departments = useMemo(() => {
    const seen = new Map<string, string>();
    if (book.state === 'ready') {
      for (const one of book.data.items) seen.set(one.id, one.name);
    }
    for (const person of people) {
      const id = person.current_assignment?.department_id;
      const name = person.current_assignment?.department_name;
      if (id && name) seen.set(id, name);
    }
    return [...seen].map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label, 'ru'));
  }, [book, people]);

  const rows = useMemo(() => {
    const text = search.trim().toLocaleLowerCase('ru');
    const picked = people.filter((person) => {
      if (department && person.current_assignment?.department_id !== department) return false;
      if (text && !person.full_name.toLocaleLowerCase('ru').includes(text)) return false;
      return true;
    });
    const sorted = [...picked].sort((a, b) => compare(a, b, sort.key, today));
    return sort.desc ? sorted.reverse() : sorted;
  }, [people, department, search, sort, today]);

  const pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
  const current = Math.min(page, pages);
  const shown = rows.slice((current - 1) * PER_PAGE, current * PER_PAGE);
  const filtered = Boolean(search.trim() || department);

  const order = (key: SortKey) => {
    setSort((was) => (was.key === key ? { key, desc: !was.desc } : { key, desc: false }));
    setPage(1);
  };

  return (
    <div className="ofs-staff">
      <div className="ofs-staff__head">
        <h2 className="ofs-staff__title">
          Сотрудники офиса <span className="ofs-staff__count">· {people.length}</span>
        </h2>
        <label className="ofs-staff__find">
          <AppIcon name="search" size={18} />
          <input type="search" value={search} placeholder="Поиск по имени"
                 aria-label="Поиск сотрудника офиса"
                 onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
        </label>
        <AppSelect label="Отдел" value={department} empty="Все отделы" options={departments}
                   disabled={departments.length === 0}
                   className="ofs-staff__dept" minWidth={210}
                   onChange={(value) => { setDepartment(value); setPage(1); }} />
      </div>

      {list.state === 'loading' && <p className="ofs-empty">Загружаем сотрудников…</p>}
      {list.state === 'denied' && <p className="ofs-empty">Нет доступа к сотрудникам.</p>}
      {list.state === 'error' && (
        <p className="ofs-empty ofs-empty--bad">Не удалось загрузить сотрудников.</p>
      )}

      {list.state === 'ready' && people.length === 0 && <Blank kind="none" />}
      {list.state === 'ready' && people.length > 0 && rows.length === 0 && (
        <Blank kind="filter" onReset={() => { setSearch(''); setDepartment(''); setPage(1); }} />
      )}

      {shown.length > 0 && (
        <>
          <div className="ofs-staff__wrap">
            <table className="ofs-staff__table">
              <thead>
                <tr>
                  <Head title="Сотрудник" name="name" sort={sort} onSort={order} />
                  <Head title="Отдел" name="department" sort={sort} onSort={order} />
                  <Head title="Должность" name="position" sort={sort} onSort={order} />
                  <Head title="График" name="schedule" sort={sort} onSort={order} />
                  <Head title="Сегодня" name="today" sort={sort} onSort={order} />
                  <th className="ofs-staff__open" aria-label="Профиль" />
                </tr>
              </thead>
              <tbody>
                {shown.map((person) => {
                  const state = stateOf(today.get(person.id));
                  return (
                    <tr key={person.id}>
                      <td>
                        <span className="ofs-staff__who">
                          <span className="ofs-staff__face" aria-hidden="true">
                            {person.photo
                              ? <img src={api.employeePhotoUrl(person.id)} alt="" />
                              : initials(person.full_name)}
                          </span>
                          <b title={person.full_name}>{person.full_name}</b>
                        </span>
                      </td>
                      <td>{person.current_assignment?.department_name ?? '—'}</td>
                      <td>{person.current_assignment?.position_name ?? '—'}</td>
                      <td className="ofs-staff__plan">{scheduleLine(person.current_schedule)}</td>
                      <td>
                        <span className={`ofs-staff__now ofs-staff__now--${state.tone}`}>
                          <i aria-hidden="true" />
                          {state.title}
                        </span>
                      </td>
                      <td className="ofs-staff__open">
                        <Link className="ofs-staff__link" to={`/employees/${person.id}`}>
                          Открыть профиль
                          <AppIcon name="next" size={16} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="ofs-staff__foot">
            <span className="ofs-staff__shown">
              Показано {shown.length} из {rows.length}
              {filtered && people.length !== rows.length ? ` (всего ${people.length})` : ''}
            </span>
            {pages > 1 && <Pager page={current} pages={pages} onPage={setPage} />}
          </div>

          {list.state === 'ready' && list.data.has_more && (
            <p className="ofs-staff__note">
              Показаны первые 200 сотрудников офиса. Сузьте поиск или выберите отдел.
            </p>
          )}
        </>
      )}
    </div>
  );
}

// --- заголовок столбца ------------------------------------------------------------

function Head({ title, name, sort, onSort }: {
  title: string;
  name: SortKey;
  sort: { key: SortKey; desc: boolean };
  onSort: (key: SortKey) => void;
}) {
  const on = sort.key === name;
  return (
    <th aria-sort={on ? (sort.desc ? 'descending' : 'ascending') : 'none'}>
      <button type="button" className={on ? 'ofs-staff__sort ofs-staff__sort--on' : 'ofs-staff__sort'}
              onClick={() => onSort(name)}>
        {title}
        <Arrows up={on && !sort.desc} down={on && sort.desc} />
      </button>
    </th>
  );
}

/** Две стрелки: сейчас выбранная — тёмная, вторая бледная. */
function Arrows({ up, down }: { up: boolean; down: boolean }) {
  return (
    <svg className="ofs-staff__arrows" width="10" height="14" viewBox="0 0 10 14" aria-hidden="true">
      <path d="M5 1 L9 6 L1 6 Z" opacity={up ? 1 : down ? 0.25 : 0.45} />
      <path d="M5 13 L1 8 L9 8 Z" opacity={down ? 1 : up ? 0.25 : 0.45} />
    </svg>
  );
}

// --- страницы ---------------------------------------------------------------------

function Pager({ page, pages, onPage }: {
  page: number; pages: number; onPage: (page: number) => void;
}) {
  return (
    <nav className="ofs-staff__pager" aria-label="Страницы списка">
      <button type="button" className="ofs-staff__step" disabled={page === 1}
              aria-label="Предыдущая страница" onClick={() => onPage(page - 1)}>
        <AppIcon name="back" size={16} />
      </button>
      {numbers(page, pages).map((item, index) =>
        item === 0 ? (
          <span key={`gap-${index}`} className="ofs-staff__gap">…</span>
        ) : (
          <button key={item} type="button"
                  className={item === page ? 'ofs-staff__page ofs-staff__page--on' : 'ofs-staff__page'}
                  aria-current={item === page ? 'page' : undefined}
                  onClick={() => onPage(item)}>
            {item}
          </button>
        ),
      )}
      <button type="button" className="ofs-staff__step" disabled={page === pages}
              aria-label="Следующая страница" onClick={() => onPage(page + 1)}>
        {/* Пара к «назад»: у `next` другой рисунок, и две стрелки по
            краям строки выглядели из разных наборов. */}
        <AppIcon name="arrow" size={16} />
      </button>
    </nav>
  );
}

/** Номера страниц с многоточием. `0` — место разрыва. */
function numbers(page: number, pages: number): number[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, index) => index + 1);
  const near = [page - 1, page, page + 1].filter((one) => one > 1 && one < pages);
  const all = [1, ...(near[0] && near[0] > 2 ? [0] : []), ...near,
               ...(near.at(-1) && near.at(-1)! < pages - 1 ? [0] : []), pages];
  return all;
}

// --- пусто --------------------------------------------------------------------------

function Blank({ kind, onReset }: { kind: 'none' | 'filter'; onReset?: () => void }) {
  return (
    <div className="ofs-staff__blank">
      <AppIcon name="users" size={20} />
      {kind === 'none' ? (
        <>
          <h3>В этом офисе пока нет сотрудников</h3>
          <p>Назначьте офис в карточке сотрудника —<br />он появится в этом списке.</p>
          <Link className="ofs-staff__ghost" to="/employees">Открыть сотрудников</Link>
          <small>Перевод между офисами сохраняется в истории сотрудника.</small>
        </>
      ) : (
        <>
          <h3>Никого не нашлось</h3>
          <p>По этому поиску и отделу в офисе никого нет.</p>
          <button type="button" className="ofs-staff__ghost" onClick={onReset}>Показать всех</button>
        </>
      )}
    </div>
  );
}

// --- мелочи --------------------------------------------------------------------------

/** Порядок «Сегодня»: сначала те, кем стоит заняться. */
const RANK: Record<Tone, number> = { warn: 0, ok: 1, blue: 2, violet: 3, idle: 4 };

function compare(
  a: api.EmployeeRow,
  b: api.EmployeeRow,
  key: SortKey,
  today: Map<string, api.PresenceRow>,
): number {
  const text = (value: string | null | undefined) => value ?? '';
  switch (key) {
    case 'department':
      return text(a.current_assignment?.department_name)
        .localeCompare(text(b.current_assignment?.department_name), 'ru');
    case 'position':
      return text(a.current_assignment?.position_name)
        .localeCompare(text(b.current_assignment?.position_name), 'ru');
    case 'schedule':
      return scheduleLine(a.current_schedule).localeCompare(scheduleLine(b.current_schedule), 'ru');
    case 'today': {
      const one = stateOf(today.get(a.id));
      const two = stateOf(today.get(b.id));
      return RANK[one.tone] - RANK[two.tone] || one.title.localeCompare(two.title, 'ru');
    }
    default:
      return a.full_name.localeCompare(b.full_name, 'ru');
  }
}

/**
 * Что у человека сегодня. Слова те же, что на «Посещаемости»: одно и то
 * же состояние не должно называться в двух местах по-разному.
 */
function stateOf(row: api.PresenceRow | undefined): { title: string; tone: Tone } {
  if (!row) return { title: 'Нет данных', tone: 'idle' };
  const late = row.late_minutes ?? 0;
  switch (row.state) {
    case 'IN_OFFICE':
      return late > 0
        ? { title: `Опоздал${row.first_entry_at ? ` в ${clock(row.first_entry_at)}` : ` на ${late} мин`}`, tone: 'warn' }
        : { title: 'В офисе', tone: 'ok' };
    case 'LEFT':
      return { title: 'Ушёл', tone: 'idle' };
    case 'LATE':
      return { title: 'Опаздывает', tone: 'warn' };
    case 'NOT_COME':
      return row.notice_kind === 'ABSENT'
        ? { title: 'Не придёт', tone: 'warn' }
        : { title: 'Нет отметки', tone: 'idle' };
    case 'VACATION':
      return { title: 'В отпуске', tone: 'blue' };
    case 'SICK_LEAVE':
      return { title: 'На больничном', tone: 'violet' };
    case 'OTHER_ABSENCE':
      return { title: row.absence_name ?? 'Отсутствует', tone: 'violet' };
    case 'DAY_OFF':
      return { title: 'Выходной', tone: 'idle' };
    case 'NO_SCHEDULE':
      return { title: 'Без графика', tone: 'idle' };
    default:
      return { title: 'Нет отметки', tone: 'idle' };
  }
}

function clock(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime())
    ? '—'
    : at.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

/** «Пн–Пт · 09:00–18:00» из дней и часов графика, а не из его названия. */
function scheduleLine(schedule: api.Schedule | null): string {
  if (!schedule) return 'Нет графика';
  const names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const days = schedule.weekdays ?? [];
  const label = (weekday: number | undefined) =>
    weekday === undefined ? undefined : names.at(weekday - 1);
  const first = label(days.at(0));
  const last = label(days.at(-1));
  const range = !first ? '' : days.length === 1 ? first : `${first}–${last ?? first}`;
  const hours = schedule.start_time && schedule.end_time
    ? `${schedule.start_time.slice(0, 5)}–${schedule.end_time.slice(0, 5)}`
    : '';
  return [range, hours].filter(Boolean).join(' · ') || schedule.name;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((part) => part.charAt(0).toUpperCase()).join('') || '—';
}
