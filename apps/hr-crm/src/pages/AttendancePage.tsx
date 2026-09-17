/**
 * Страница «Посещаемость».
 *
 * Вёрстка повторяет эталон 1672×941 (`ChatGPT Image Sep 13, 2026,
 * 04_02_47 PM.png`): сверху панель «Сегодня», под ней таблица состава
 * смены, справа «Требует внимания» и день выбранного сотрудника. Размеры —
 * в `styles/attendance.css`, классы с префиксом `att-`.
 *
 * Ни одна величина здесь не считается заново: присутствие, время в офисе,
 * опоздания и отсутствия считает сервер. Страница не называет «нет
 * отметки» прогулом и не считает опозданием отсутствие графика.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { DatePicker } from '../components/DatePicker';
import { AppFilterButton, AppSegmentedControl, Dropdown } from '../components/AppSelect';
import { DayCard } from '../components/DayCard';
import { formatTime, longDate, today, useBlock, type Block } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import { clock, clockOnDay } from '../features/time/zone';
import '../styles/attendance.css';
import { useStickyState } from '../features/shell/sticky';

const PAGE = 16;

type Tone = 'ok' | 'idle' | 'warn' | 'violet' | 'blue' | 'grey';

/** Как строка называется и каким цветом. Опоздавший в офисе — «Опоздал». */
function stateOf(row: api.PresenceRow): { title: string; tone: Tone } {
  const late = row.late_minutes ?? 0;
  switch (row.state) {
    case 'IN_OFFICE':
      return late > 0 ? { title: `Опоздал на ${late} мин`, tone: 'warn' } : { title: 'В офисе', tone: 'ok' };
    case 'LEFT':
      return { title: 'Ушёл', tone: 'idle' };
    case 'NOT_COME':
      return { title: 'Нет отметки', tone: 'warn' };
    case 'VACATION':
      return { title: 'В отпуске', tone: 'violet' };
    case 'SICK_LEAVE':
      return { title: 'На больничном', tone: 'blue' };
    case 'OTHER_ABSENCE':
      return { title: row.absence_name ?? 'Отсутствует', tone: 'violet' };
    case 'DAY_OFF':
      return { title: 'Выходной', tone: 'grey' };
    case 'NO_SCHEDULE':
      return { title: 'Без графика', tone: 'grey' };
    default:
      return { title: row.state, tone: 'grey' };
  }
}

const STATUS_OPTIONS = [
  { id: 'IN_OFFICE', name: 'В офисе' },
  { id: 'LEFT', name: 'Ушли' },
  { id: 'NOT_COME', name: 'Нет отметки' },
  { id: 'VACATION', name: 'В отпуске' },
  { id: 'SICK_LEAVE', name: 'На больничном' },
  { id: 'OTHER_ABSENCE', name: 'Отсутствуют' },
  { id: 'DAY_OFF', name: 'Выходной' },
  { id: 'NO_SCHEDULE', name: 'Без графика' },
];

/**
 * Отбор таблицы. Один на чипы, плитки панели и «Требует внимания»:
 * нажатие в любом месте ставит одно и то же, и подсветка совпадает.
 * `state` понимает сервер; `flag` — признак строки, он считается по
 * уже полученным строкам (опоздавший бывает и в офисе, и ушедшим).
 */
type Quick = {
  id: string;
  title: string;
  state?: string;
  flag?: 'late' | 'open' | 'geo';
};

const QUICK: (Quick & { tone: string; count: (c: Counts) => number })[] = [
  { id: 'all', title: 'Все', tone: 'blue', count: (c) => c.expected },
  { id: 'now', title: 'Сейчас', state: 'IN_OFFICE', tone: 'green', count: (c) => c.here },
  { id: 'none', title: 'Нет отметки', state: 'NOT_COME', tone: 'orange', count: (c) => c.none },
  { id: 'late', title: 'Опоздали', flag: 'late', tone: 'orange', count: (c) => c.late },
];

type Counts = {
  expected: number;
  here: number;
  came: number;
  left: number;
  none: number;
  late: number;
  open: number;
  geo: number;
};

export function AttendancePage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);

  const [params, setParams] = useSearchParams();
  const day = params.get('date') ?? today();
  const tab = params.get('tab') === 'log' ? 'log' : 'day';
  const search = params.get('search') ?? '';
  const office = params.get('office_id') ?? '';
  const state = params.get('state') ?? '';
  const rawFlag = params.get('flag');
  const flag = rawFlag === 'late' || rawFlag === 'open' || rawFlag === 'geo' ? rawFlag : '';
  const page = Math.max(1, Number(params.get('page') ?? '1'));
  const picked = params.get('employee') ?? '';

  const [draft, setDraft] = useState(search);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [fixing, setFixing] = useState(false);
  useEffect(() => setDraft(search), [search]);

  const patch = useCallback(
    (changes: Record<string, string | null>, keepPage = false) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          if (!keepPage) next.delete('page');
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  useEffect(() => {
    if (draft === search) return;
    const timer = setTimeout(() => patch({ search: draft || null }), 350);
    return () => clearTimeout(timer);
  }, [draft, search, patch]);

  const scope = useMemo(
    () => ({ date: day, ...(office ? { office_id: office } : {}) }),
    [day, office],
  );
  const wide = `${day}|${office}|${attempt}`;
  const narrowed = Boolean(state || search);

  // Показатели дня — у дашборда: он считает их по всему составу.
  const [cards] = useBlock(
    (signal) => api.dashboard(scope, signal).then((body) => {
      setUpdated(new Date());
      return body;
    }),
    wide,
  );

  // Полный состав дня: по нему строится динамика приходов и «вне геозоны».
  const [base] = useBlock((signal) => api.presenceDay(scope, signal), wide, tab === 'day');
  const [narrow] = useBlock(
    (signal) => api.presenceDay(
      { ...scope, ...(state ? { state } : {}), ...(search ? { search } : {}) },
      signal,
    ),
    `${wide}|${state}|${search}`,
    tab === 'day' && narrowed,
  );
  const shift = narrowed ? narrow : base;

  const [directory] = useBlock(
    (signal) => api.offices(signal).then((o) => o.items.filter((one) => one.status === 'ACTIVE')),
    'offices',
  );

  const card = cards.state === 'ready'
    ? Object.fromEntries(cards.data.cards.map((c) => [c.key, c.value]))
    : {};
  const everyone = base.state === 'ready' ? base.data.items : [];
  const past = day < today();
  const counts: Counts = {
    expected: card['should_work_today'] ?? 0,
    here: past ? (card['came'] ?? 0) : (card['in_office'] ?? 0),
    came: card['came'] ?? 0,
    left: card['left'] ?? 0,
    none: card['not_come'] ?? 0,
    late: card['late'] ?? 0,
    open: card['open_sessions'] ?? 0,
    geo: everyone.filter((row) => row.outside_geofence).length,
  };

  const found = shift.state === 'ready' ? shift.data.items : [];
  const rows = flag === 'late' ? found.filter((row) => (row.late_minutes ?? 0) > 0)
    : flag === 'open' ? found.filter((row) => row.open_session_id !== null)
      : flag === 'geo' ? found.filter((row) => row.outside_geofence)
        : found;
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const slice = rows.slice((page - 1) * PAGE, page * PAGE);
  const chosenRow = rows.find((row) => row.employee_id === picked) ?? slice[0] ?? null;
  const zone = shift.state === 'ready' ? shift.data.timezone : '';

  const chosen = flag
    ? flag
    : state === 'IN_OFFICE' ? 'now' : state === 'NOT_COME' ? 'none' : state === 'LEFT' ? 'left' : state ? '' : 'all';
  const choose = (item: Quick) =>
    patch(chosen === item.id && item.id !== 'all'
      ? { state: null, flag: null }
      : { state: item.state ?? null, flag: item.flag ?? null });

  // Выгрузка посещаемости за день — в общую очередь отчётов.
  const [ordered, setOrdered] = useState<string | null>(null);
  async function order() {
    setOrdered(null);
    try {
      await api.orderExport({
        kind: 'attendance', fmt: 'xlsx', date_from: day, date_to: day,
        ...(office ? { office_id: office } : {}),
      });
      setOrdered('Выгрузка поставлена в очередь');
    } catch (error) {
      setOrdered(messageFor(error));
    }
  }

  return (
    <AppShell breadcrumb="Посещаемость" section="attendance">
      <div className="attp">
        <header className="att-head">
          <div>
            <h1 className="att-head__title">Посещаемость</h1>
            <p className="att-head__sub">{longDate(day)} <i>·</i> По данным отметок</p>
          </div>
          <div className="att-head__side">
            <div className="att-head__tools">
              <DatePicker label="Дата" value={day} now={today()} allowEmpty
                onChange={(value) => patch({ date: value || null, employee: null })} />
              {can('reports.export') && (
                <button type="button" className="att-btn att-btn--light att-btn--export"
                        onClick={() => void order()}>
                  <AppIcon name="download" size={18} />
                  Экспорт
                </button>
              )}
              <button type="button" className="att-btn att-btn--light att-btn--icon" aria-label="Обновить"
                      onClick={() => setAttempt((n) => n + 1)}>
                <AppIcon name="refresh" size={18} />
              </button>
            </div>
            <p className="att-head__updated">
              {updated ? `Обновлено в ${formatTime(updated)}` : 'Загружаем…'}
            </p>
          </div>
        </header>

        {ordered && (
          <p className="att-note" role="status">
            {ordered} — <Link to="/reports">файл появится в отчётах</Link>
          </p>
        )}

        <AppSegmentedControl className="att-tabs" role="tablist" label="Раздел посещаемости" value={tab}
          options={[{ value: 'day', label: 'За день' }, { value: 'log', label: 'Журнал отметок' }]}
          onChange={(value) => patch({ tab: value === 'day' ? null : value })} />

        {tab === 'log' ? (
          <Journal day={day} office={office} />
        ) : (
          <>
            <Today counts={counts} past={past} rows={everyone} zone={zone}
                   chosen={chosen} onPick={choose} block={cards} />

            <div className="att-grid">

              <section className="att-list" aria-label="Состав смены">
                <div className="att-filters">
                  <div className="att-chips" role="group" aria-label="Быстрый отбор">
                    {QUICK.map((item) => <AppFilterButton key={item.id} className={`att-chip att-chip--${item.tone}`} active={chosen === item.id} count={item.count(counts)} onClick={() => choose(item)}>{item.title}</AppFilterButton>)}
                  </div>
                  <label className="att-search">
                    <AppIcon name="search" size={16} />
                    <input type="search" value={draft} placeholder="Поиск"
                           aria-label="Поиск сотрудника"
                           onChange={(event) => setDraft(event.target.value)} />
                  </label>
                  <Select label="Офис" empty="Все офисы" value={office}
                          options={directory.state === 'ready' ? directory.data : []}
                          onChange={(value) => patch({ office_id: value || null })} />
                  <Select label="Статус" empty="Все статусы" value={state} options={STATUS_OPTIONS}
                          onChange={(value) => patch({ state: value || null, flag: null })} />
                </div>

                <div className="att-table">
                  <div className="att-table__head" role="row">
                    <span>Сотрудник</span>
                    <span>Офис / график</span>
                    <span>Рабочий день</span>
                    <span>В офисе</span>
                    <span>Статус</span>
                  </div>
                  <Section block={shift} name="состав смены">
                    {(data) => rows.length === 0 ? (
                      <p className="att-empty">
                        {state || flag || search || office ? 'По этим условиям никого нет.' : 'На выбранный день отметок нет.'}
                      </p>
                    ) : (
                      <>
                        {data.truncated && (
                          <p className="att-empty att-empty--bad">
                            Показаны не все: состав больше одного ответа. Сузьте фильтры.
                          </p>
                        )}
                        {slice.map((row) => (
                          <Row key={row.employee_id} row={row} zone={data.timezone} day={day}
                               on={row.employee_id === chosenRow?.employee_id}
                               onPick={() => { setFixing(false); patch({ employee: row.employee_id }, true); }} />
                        ))}
                      </>
                    )}
                  </Section>
                </div>

                <footer className="att-pager">
                  <p>{shift.state === 'ready' ? `Показано ${slice.length} из ${plural(rows.length)}` : ''}</p>
                  <Pages page={page} pages={pages}
                         onGo={(next) => patch({ page: next === 1 ? null : String(next) }, true)} />
                </footer>
              </section>

              <div className="att-rail">
                <Attention counts={counts} chosen={chosen} onPick={choose} />
                {chosenRow && fixing ? (
                  <div className="att-fix">
                    <DayCard row={chosenRow} day={day} timezone={zone} canAdd={can('attendance.manual')}
                             onClose={() => setFixing(false)}
                             onChanged={() => { setFixing(false); setAttempt((n) => n + 1); }} />
                  </div>
                ) : (
                  <Person row={chosenRow} day={day} zone={zone}
                          canFix={can('attendance.manual')} onFix={() => setFixing(true)} />
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}

// --- панель «Сегодня» ----------------------------------------------------------

const METRICS: (Quick & { icon: AppIconName; tone: string; count: (c: Counts) => number })[] = [
  { id: 'left', title: 'Уже ушли', state: 'LEFT', icon: 'logout', tone: 'navy', count: (c) => c.left },
  { id: 'none', title: 'Нет отметки', state: 'NOT_COME', icon: 'alert', tone: 'orange', count: (c) => c.none },
  { id: 'late', title: 'Опоздали', flag: 'late', icon: 'clock', tone: 'orange', count: (c) => c.late },
  { id: 'open', title: 'Незакрытые', flag: 'open', icon: 'doc', tone: 'blue', count: (c) => c.open },
];

function Today({ counts, past, rows, zone, chosen, onPick, block }: {
  counts: Counts;
  past: boolean;
  rows: api.PresenceRow[];
  zone: string;
  chosen: string;
  onPick: (item: Quick) => void;
  block: Block<unknown>;
}) {
  // Доля — от тех, кого ждали сегодня, а не от всей организации.
  const share = counts.expected > 0 ? (counts.here / counts.expected) * 100 : 0;
  return (
    <section className="att-today" aria-label="Сегодня">
      <div className="att-today__share">
        <h2 className="att-today__title">{past ? 'Итоги дня' : 'Сегодня'}</h2>
        <div className="att-today__ring">
          <Donut share={share} />
          <p className="att-today__sum">
            {counts.here > 0 && <i className="att-today__live" aria-hidden="true" />}
            <strong>{counts.here} из {counts.expected}</strong>
            <span>{past ? 'пришли' : 'в офисе'}</span>
            <small>{block.state === 'loading' ? 'Загружаем…' : past ? 'За выбранный день' : 'Сейчас в офисе'}</small>
          </p>
        </div>
      </div>

      <Arrivals rows={rows} zone={zone} past={past} />

      <div className="att-metrics">
        {METRICS.map((item) => (
          <button key={item.id} type="button" aria-pressed={chosen === item.id}
                  className={`att-metric att-metric--${item.tone}${chosen === item.id ? ' att-metric--on' : ''}`}
                  onClick={() => onPick(item)}>
            <AppIcon name={item.icon} size={20} />
            <span>{item.title}</span>
            <strong>{item.count(counts)}</strong>
          </button>
        ))}
      </div>
    </section>
  );
}

function Donut({ share }: { share: number }) {
  const radius = 42;
  const length = 2 * Math.PI * radius;
  const filled = (Math.min(Math.max(share, 0), 100) / 100) * length;
  return (
    <svg className="att-donut" viewBox="0 0 96 96" width={96} height={96}
         role="img" aria-label={`В офисе ${share.toFixed(1)} процента`}>
      <circle className="att-donut__track" cx="48" cy="48" r={radius} />
      <circle className="att-donut__fill" cx="48" cy="48" r={radius}
              strokeDasharray={`${filled} ${length}`} transform="rotate(-90 48 48)" />
      <text className="att-donut__text" x="48" y="49">{share.toFixed(1).replace('.', ',')}%</text>
    </svg>
  );
}

/**
 * Динамика приходов: первый вход каждого человека по пятиминуткам.
 * Все входы подряд сместили бы картину к обеду — возвращение тоже вход.
 */
function Arrivals({ rows, zone, past }: { rows: api.PresenceRow[]; zone: string; past: boolean }) {
  const step = 5;
  const from = 8 * 60;
  const to = 11 * 60;
  const values = rows
    .map((row) => minutesOf(row.first_entry_at, zone))
    .filter((one): one is number => one !== null && one >= from && one < to + step);
  const buckets = Array.from({ length: (to - from) / step + 1 }, (_, at) => {
    const start = from + at * step;
    return values.filter((one) => one >= start && one < start + step).length;
  });
  const top = Math.max(1, ...buckets);
  return (
    <div className="att-arrivals">
      <p className="att-arrivals__title">{past ? 'Динамика приходов' : 'Динамика приходов сегодня'}</p>
      <div className="att-arrivals__bars" role="img"
           aria-label={`Приходов с 08:00 до 11:00: ${values.length}`}>
        {buckets.map((count, at) => (
          <span key={at} style={{ height: `${Math.max((count / top) * 100, 4)}%` }}
                title={`${hhmm(from + at * step)} · ${count}`} />
        ))}
      </div>
      <div className="att-arrivals__axis">
        {[0, 30, 60, 90, 120, 150, 180].map((shift) => <span key={shift}>{hhmm(from + shift)}</span>)}
      </div>
    </div>
  );
}

// --- таблица ---------------------------------------------------------------------

function Row({ row, zone, day, on, onPick }: {
  row: api.PresenceRow;
  zone: string;
  day: string;
  on: boolean;
  onPick: () => void;
}) {
  const status = stateOf(row);
  return (
    <div className={on ? 'att-row att-row--on' : 'att-row'} role="row" tabIndex={0}
         onClick={onPick}
         onKeyDown={(event) => { if (event.key === 'Enter') onPick(); }}>
      <span className="att-row__who">
        <Face id={row.employee_id} name={row.full_name} className="att-row__face" />
        <span className="att-row__text">
          <b>{shortName(row.full_name)}</b>
          <small>{row.employee_number ?? '—'}</small>
        </span>
      </span>
      <span className="att-row__text">
        <span>{row.office_name ?? '—'}</span>
        <small>{hours(row)}</small>
      </span>
      <DayLine row={row} zone={zone} day={day} />
      <span className="att-row__time">{present(row, day) ? span(present(row, day)) : '—'}</span>
      <span className={`att-status att-status--${status.tone}`}>{status.title}</span>
      <AppIcon name="next" size={18} className="att-row__go" />
    </div>
  );
}

/**
 * Шкала рабочего дня: отрезки присутствия на отрезке графика.
 * Промежуток между посещениями не называется обедом — это просто
 * время вне офиса.
 */
function DayLine({ row, zone, day, wide = false }: {
  row: api.PresenceRow;
  zone: string;
  day: string;
  wide?: boolean;
}) {
  if (row.state === 'VACATION' || row.state === 'SICK_LEAVE' || row.state === 'OTHER_ABSENCE') {
    return (
      <span className={`att-line att-line--absent${wide ? ' att-line--wide' : ''}`}>
        <span className="att-line__hatch">{stateOf(row).title}</span>
      </span>
    );
  }
  if (row.intervals.length === 0) {
    return (
      <span className={`att-line${wide ? ' att-line--wide' : ''}`}>
        <span className="att-line__track att-line__track--empty" />
      </span>
    );
  }

  const start = toMinutes(row.scheduled_start) ?? 9 * 60;
  const end = toMinutes(row.scheduled_end) ?? 18 * 60;
  const points = row.intervals.flatMap((one) => [
    minutesOf(one.started_at, zone),
    one.ended_at ? minutesOf(one.ended_at, zone) : nowMinutes(zone),
  ]).filter((one): one is number => one !== null);
  const low = Math.min(start - 20, ...points);
  const high = Math.max(end + 20, ...points);
  const at = (minute: number) => ((minute - low) / (high - low)) * 100;

  const labels: { left: number; text: string }[] = [];
  const late = (row.late_minutes ?? 0) > 0;
  row.intervals.forEach((one, index) => {
    const from = minutesOf(one.started_at, zone);
    const to = one.ended_at ? minutesOf(one.ended_at, zone) : null;
    if (from !== null) labels.push({ left: at(from), text: clockOnDay(one.started_at, zone, day) });
    if (to !== null && index < row.intervals.length - 1) {
      labels.push({ left: at(to), text: clock(one.ended_at, zone) });
    } else if (to !== null) {
      labels.push({ left: at(to), text: clock(one.ended_at, zone) });
    } else {
      labels.push({ left: 100, text: 'сейчас' });
    }
  });
  // Подписи не налезают друг на друга: ближе 16 % ширины — пропуск.
  const shown = labels.filter((one, index) =>
    labels.slice(0, index).every((was) => Math.abs(was.left - one.left) > 16));

  return (
    <span className={`att-line${wide ? ' att-line--wide' : ''}`}>
      <span className="att-line__track">
        {row.intervals.map((one, index) => {
          const from = minutesOf(one.started_at, zone);
          const to = one.ended_at ? minutesOf(one.ended_at, zone) : nowMinutes(zone);
          if (from === null || to === null) return null;
          return (
            <i key={index} className="att-line__part"
               style={{ left: `${at(from)}%`, width: `${Math.max(at(to) - at(from), 1)}%` }} />
          );
        })}
        {late && <i className="att-line__late" style={{ left: `${at(start)}%` }} />}
        {row.open_session_id && <i className="att-line__now" />}
      </span>
      <span className="att-line__labels">
        {shown.map((one) => (
          <small key={`${one.left}-${one.text}`}
                 style={{ left: `${Math.min(Math.max(one.left, 0), 100)}%` }}>{one.text}</small>
        ))}
      </span>
    </span>
  );
}

function Pages({ page, pages, onGo }: { page: number; pages: number; onGo: (next: number) => void }) {
  const shown = Array.from({ length: Math.min(pages, 5) }, (_, at) => at + 1);
  return (
    <nav className="att-pages" aria-label="Страницы">
      <button type="button" className="att-pages__arrow" aria-label="Предыдущая"
              disabled={page <= 1} onClick={() => onGo(page - 1)}>
        <AppIcon name="back" size={16} />
      </button>
      {shown.map((one) => (
        <button key={one} type="button" aria-current={one === page ? 'page' : undefined}
                className={one === page ? 'att-pages__one att-pages__one--on' : 'att-pages__one'}
                onClick={() => onGo(one)}>
          {one}
        </button>
      ))}
      <button type="button" className="att-pages__next" aria-label="Следующая"
              disabled={page >= pages} onClick={() => onGo(page + 1)}>
        <AppIcon name="next" size={18} />
      </button>
    </nav>
  );
}

// --- правая колонка --------------------------------------------------------------

const ATTENTION: (Quick & { icon: AppIconName; tone: string; count: (c: Counts) => number })[] = [
  { id: 'none', title: 'Нет отметки', state: 'NOT_COME', icon: 'alert', tone: 'orange', count: (c) => c.none },
  { id: 'late', title: 'Опоздали', flag: 'late', icon: 'clock', tone: 'orange', count: (c) => c.late },
  { id: 'open', title: 'Незакрытые', flag: 'open', icon: 'doc', tone: 'navy', count: (c) => c.open },
  { id: 'geo', title: 'Вне геозоны', flag: 'geo', icon: 'pin', tone: 'navy', count: (c) => c.geo },
];

function Attention({ counts, chosen, onPick }: {
  counts: Counts;
  chosen: string;
  onPick: (item: Quick) => void;
}) {
  const total = ATTENTION.reduce((sum, one) => sum + one.count(counts), 0);
  return (
    <section className="att-attention" aria-label="Требует внимания">
      <h2 className="att-attention__title">
        Требует внимания
        <span className="att-attention__total">{total}</span>
        <AppIcon name="next" size={18} className="att-attention__go" />
      </h2>
      {ATTENTION.map((item) => (
        <button key={item.id} type="button" aria-pressed={chosen === item.id}
                className={`att-attention__row att-attention__row--${item.tone}${chosen === item.id ? ' att-attention__row--on' : ''}`}
                onClick={() => onPick(item)}>
          <AppIcon name={item.icon} size={20} />
          <span>{item.title}</span>
          <b>{item.count(counts)}</b>
          <AppIcon name="next" size={18} />
        </button>
      ))}
    </section>
  );
}

function Person({ row, day, zone, canFix, onFix }: {
  row: api.PresenceRow | null;
  day: string;
  zone: string;
  canFix: boolean;
  onFix: () => void;
}) {
  const [events] = useBlock(
    (signal) => row
      ? api.events({
        employee_id: row.employee_id, date_from: day, date_to: day,
        verification_status: 'ACCEPTED', limit: '20',
      }, signal)
      : Promise.resolve({ items: [], has_more: false, next_cursor: null } as unknown as api.Cursored<api.EventRow>),
    `events|${row?.employee_id ?? ''}|${day}`,
  );

  if (!row) {
    return (
      <section className="att-person">
        <p className="att-empty">Выберите сотрудника, чтобы увидеть его день.</p>
      </section>
    );
  }
  const status = stateOf(row);
  const list = events.state === 'ready'
    ? [...events.data.items].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at)).slice(0, 3)
    : [];
  // Отметок нет и время в офисе не набрано — показывать нечего, кроме
  // самого факта. Пока события грузятся, пустым состоянием не мигаем.
  const nothingYet = events.state === 'ready' && list.length === 0 && !present(row, day);

  return (
    <section className="att-person" aria-label="Выбранный сотрудник">
      <div className="att-person__who">
        <Face id={row.employee_id} name={row.full_name} className="att-person__face" />
        <div>
          <p className="att-person__name">{shortName(row.full_name)}</p>
          <p className="att-person__number">{row.employee_number ?? '—'}</p>
          <span className={`att-status att-status--${status.tone}`}>{status.title}</span>
        </div>
      </div>

      {/* Прокручивается середина карточки, а не панель целиком: «Открыть
          карточку» и «Исправить отметку» должны оставаться на виду. */}
      <div className="att-person__scroll">
        <dl className="att-person__facts">
          <dt><AppIcon name="building" size={18} />Офис</dt>
          <dd>{row.office_name ?? '—'}</dd>
          <dt><AppIcon name="users" size={18} />Отдел</dt>
          <dd>{row.department_name ?? '—'}</dd>
          <dt><AppIcon name="calendar" size={18} />График</dt>
          <dd>{hours(row)}</dd>
        </dl>

        {/*
          * День без единой отметки — это не пустая панель с прочерками.
          * Прочерк на месте времени и пустая полоса графика выглядят как
          * сбой загрузки; вместо них — прямая фраза о том, что человек
          * ещё не отмечался. Офис, отдел и график при этом остаются: они
          * известны и нужны тому, кто разбирается.
          */}
        {nothingYet ? (
          <div className="att-person__blank">
            <span className="att-person__blankIcon" aria-hidden="true">
              <AppIcon name="clock" size={20} />
            </span>
            <p className="att-person__blankTitle">
              {day === today() ? 'Нет отметок за сегодня' : 'Нет отметок за этот день'}
            </p>
            <p className="att-person__blankText">Сотрудник ещё не отметил вход или выход</p>
            <span className="att-status att-status--warn">Нет отметки</span>
          </div>
        ) : (
          <>
            <div className="att-person__day">
              <p className="att-person__label">{day === today() ? 'Сегодня в офисе' : 'В офисе за день'}</p>
              <p className="att-person__total">{present(row, day) ? span(present(row, day)) : '—'}</p>
              <DayLine row={row} zone={zone} day={day} wide />
            </div>

            <p className="att-person__label">{day === today() ? 'События сегодня' : 'События дня'}</p>
            <ul className="att-events">
              {events.state === 'loading' && <li className="att-events__none">Загружаем…</li>}
              {list.map((one) => (
                <li key={one.id} className={one.event_type === 'ENTRY' ? 'att-events__in' : 'att-events__out'}>
                  <b>{clock(one.occurred_at, zone)}</b>
                  <span>
                    {one.event_type === 'ENTRY' ? 'Вход' : 'Выход'}
                    {one.qr_point_name ? ` · ${one.qr_point_name}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <div className="att-person__actions">
        <Link className="att-btn att-btn--outline" to={`/employees/${row.employee_id}?tab=attendance`}>
          <AppIcon name="doc" size={18} />
          Открыть карточку
        </Link>
        {/* Исправление — это добавление ручной отметки с причиной, а не
            правка существующей: форма открывается в карточке дня. */}
        <button type="button" className="att-btn att-btn--blue" disabled={!canFix} onClick={onFix}>
          <AppIcon name="pencil" size={18} />
          Исправить отметку
        </button>
      </div>
    </section>
  );
}

// --- журнал отметок ------------------------------------------------------------

function Journal({ day, office }: { day: string; office: string }) {
  const [cursor, setCursor] = useState('');
  const [direction, setDirection] = useStickyState('attendance.log.direction', '');
  const [onlyAccepted, setOnlyAccepted] = useStickyState('attendance.log.accepted', true);

  const [log] = useBlock(
    (signal) => api.events({
      date_from: day, date_to: day, limit: '20',
      ...(office ? { office_id: office } : {}),
      ...(direction ? { event_type: direction } : {}),
      // Успешные отметки и отклонённые попытки — разные вещи.
      ...(onlyAccepted ? { verification_status: 'ACCEPTED' } : {}),
      ...(cursor ? { cursor } : {}),
    }, signal),
    `log|${day}|${office}|${direction}|${onlyAccepted}|${cursor}`,
  );

  return (
    <section className="att-list att-list--log">
      <div className="att-filters">
        <Select label="Направление" empty="Вход и выход" value={direction}
                options={[{ id: 'ENTRY', name: 'Только входы' }, { id: 'EXIT', name: 'Только выходы' }]}
                onChange={(value) => { setDirection(value); setCursor(''); }} />
        <label className="att-check">
          <input type="checkbox" checked={onlyAccepted}
                 onChange={(event) => { setOnlyAccepted(event.target.checked); setCursor(''); }} />
          Только успешные отметки
        </label>
      </div>
      <Section block={log} name="журнал">
        {(data) => data.items.length === 0 ? (
          <p className="att-empty">За выбранный день отметок нет.</p>
        ) : (
          <table className="att-log table-cards">
            <thead>
              <tr><th>Время</th><th>Офис и точка</th><th>Направление</th><th>Источник</th><th>Состояние</th></tr>
            </thead>
            <tbody>
              {data.items.map((one) => (
                <tr key={one.id}>
                  <td data-label="Время">{one.occurred_at.slice(11, 16)}</td>
                  <td data-label="Офис и точка">{one.office_name ?? '—'}{one.qr_point_name ? ` · ${one.qr_point_name}` : ''}</td>
                  <td data-label="Направление">{one.event_type === 'ENTRY' ? 'Вход' : 'Выход'}</td>
                  <td data-label="Источник">{one.source}</td>
                  <td data-label="Состояние">{one.verification_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
      <footer className="att-pager">
        <p />
        <div className="att-pages">
          <button type="button" className="att-btn att-btn--light" disabled={!cursor} onClick={() => setCursor('')}>
            В начало
          </button>
          <button type="button" className="att-btn att-btn--blue"
                  disabled={log.state !== 'ready' || !log.data.has_more}
                  onClick={() => log.state === 'ready' && setCursor(log.data.next_cursor ?? '')}>
            Далее
          </button>
        </div>
      </footer>
    </section>
  );
}

// --- мелочи ------------------------------------------------------------------------

/**
 * Фото сотрудника или инициалы. Признака «фото есть» в строке состава
 * нет, поэтому снимок запрашивается, а отказ возвращает инициалы.
 */
function Face({ id, name, className }: { id: string; name: string; className: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return <span className={`att-face att-face--none ${className}`}>{initials(name)}</span>;
  return (
    <img className={`att-face ${className}`} src={api.employeePhotoUrl(id)} alt=""
         onError={() => setBroken(true)}
         onLoad={(event) => { if (event.currentTarget.naturalWidth < 32) setBroken(true); }} />
  );
}

function Select({ label, empty, value, options, onChange }: {
  label: string;
  empty: string;
  value: string;
  options: { id: string; name: string }[];
  onChange: (value: string) => void;
}) {
  return <Dropdown label={label} empty={empty} value={value} options={options} onChange={onChange} />;
}

function Section<T>({ block, name, children }: {
  block: Block<T>;
  name: string;
  children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="att-empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="att-empty">Нет доступа к разделу «{name}».</p>;
  if (block.state === 'error') {
    return <p className="att-empty att-empty--bad">Не удалось загрузить {name}.</p>;
  }
  return <>{children(block.data)}</>;
}

function shortName(full: string): string {
  return full.split(' ').slice(0, 2).join(' ');
}

/**
 * Время в офисе. Число сервера — главное: он знает про несколько
 * посещений за день, и разница входа и выхода его не подменяет.
 *
 * Досчёт только в одном случае: сегодня, открытое посещение, а сервер
 * прислал ноль. Иначе человек, который сейчас в офисе, выглядел бы
 * пришедшим только что. У прошедшего дня открытое посещение до «сейчас»
 * не тянется — там это были бы сутки.
 */
function present(row: api.PresenceRow, day: string): number {
  if (row.seconds > 0 || day !== today()) return row.seconds;
  return row.intervals.reduce((sum, one) => {
    if (one.ended_at) return sum + one.seconds;
    const started = new Date(one.started_at).getTime();
    return Number.isNaN(started) ? sum : sum + Math.max(0, Math.round((Date.now() - started) / 1000));
  }, 0);
}

function hours(row: api.PresenceRow): string {
  return row.scheduled_start && row.scheduled_end
    ? `${row.scheduled_start.slice(0, 5)} – ${row.scheduled_end.slice(0, 5)}`
    : 'График не задан';
}

function toMinutes(time: string | null): number | null {
  if (!time) return null;
  const [h, m] = time.split(':').map(Number);
  return h === undefined || m === undefined || Number.isNaN(h) || Number.isNaN(m) ? null : h * 60 + m;
}

function hhmm(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}

/** Минуты от полуночи в поясе офиса. */
function minutesOf(at: string | null, zone: string): number | null {
  if (!at) return null;
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return null;
  return partsInZone(date, zone);
}

function nowMinutes(zone: string): number {
  return partsInZone(new Date(), zone) ?? 0;
}

function partsInZone(date: Date, zone: string): number | null {
  const text = new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit', minute: '2-digit', hour12: false, ...(zone ? { timeZone: zone } : {}),
  }).format(date);
  return toMinutes(text);
}

function plural(n: number): string {
  const form = n % 10 === 1 && n % 100 !== 11 ? 'сотрудника'
    : 'сотрудников';
  return `${n} ${form}`;
}

/** «6 ч 18 мин». Используется и карточкой дня. */
export function span(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h} ч ${m} мин` : `${m} мин`;
}

export { clock, clockOnDay };
