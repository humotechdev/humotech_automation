/**
 * Главная: оперативная картина дня.
 *
 * Страница отвечает на четыре вопроса и не больше: что с людьми сегодня,
 * что ждёт действия HR, как меняется явка и в каком офисе проблема.
 * Подробности — в своих разделах; отсюда к ним ведёт каждое число.
 *
 * Все числа приходят с сервера. Ни одно не пересчитывается здесь по
 * своим правилам: `/dashboard` отдаёт показатели вместе с адресом списка,
 * из которого они сложились, `/attendance/presence` — состояния по офису,
 * очередь заявок — их стадии. Отсутствием считается только то, что сервер
 * называет отсутствием: неподтверждённый больничный им не является.
 *
 * Каждый блок грузится сам за себя: упавшая таблица офисов не повод
 * прятать показатели, которые уже пришли. Ошибка нигде не превращается
 * в ноль — у блока своё состояние, и показывается оно.
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { DatePicker } from '../components/DatePicker';
import { Dropdown } from '../components/Dropdown';
import { longDate, shift, today, useBlock, type Block } from '../features/dashboard/data';
import { useStickyState } from '../features/shell/sticky';
import '../styles/home.css';

const RANGES = [
  { key: '7', title: '7 дней', days: 7 },
  { key: '14', title: '14 дней', days: 14 },
  { key: '30', title: 'Месяц', days: 30 },
] as const;
type Range = (typeof RANGES)[number];

/** Открытые заявки — те, по которым ещё не решили. */
const OPEN = 'SUBMITTED,IN_REVIEW';
const WEEKDAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

/**
 * Куда ведёт показатель. Адрес не собирается заново: `/dashboard` отдаёт
 * у каждого числа список, из которого оно сложилось. Здесь остаётся
 * перевести адрес API в адрес раздела и отбросить параметры, которых
 * раздел не понимает: ссылка, открывающая пустой список, хуже текста.
 */
const SECTION: Record<string, { route: string; accepts: string[] }> = {
  '/api/v1/attendance/presence': { route: '/attendance', accepts: ['date', 'region_id', 'office_id', 'state'] },
  '/api/v1/employees': { route: '/employees', accepts: ['region_id', 'office_id', 'department_id'] },
};

function cardLink(card: api.Card | undefined): string | null {
  if (!card?.endpoint) return null;
  const section = SECTION[card.endpoint];
  if (!section) return null;
  const kept = Object.fromEntries(
    Object.entries(card.params).filter(([name]) => section.accepts.includes(name)),
  );
  return section.route + api.query(kept);
}

export function DashboardPage() {
  // Отбор главной переживает уход в другой раздел: вернувшись, человек
  // видит тот же регион, офис, дату и период графика.
  const [day, setDay] = useStickyState('dashboard.day', today);
  const [region, setRegion] = useStickyState('dashboard.region', '');
  const [office, setOffice] = useStickyState('dashboard.office', '');
  const [range, setRange] = useStickyState<Range>('dashboard.range', RANGES[0]);
  const [attempt, setAttempt] = useState(0);

  const filters: api.Filters = useMemo(
    () => ({
      date: day,
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
    }),
    [day, region, office],
  );
  const place = useMemo(
    () => ({ ...(region ? { region_id: region } : {}), ...(office ? { office_id: office } : {}) }),
    [region, office],
  );
  const key = `${day}|${region}|${office}|${attempt}`;

  const [cards] = useBlock((signal) => api.dashboard(filters, signal), key);
  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items,
        offices: o.items,
      })),
    'directory',
  );

  const visibleOffices = useMemo(() => {
    if (directory.state !== 'ready') return [];
    const all = directory.data.offices.filter((o) => o.status === 'ACTIVE');
    const inRegion = region ? all.filter((o) => o.region_id === region) : all;
    return office ? inRegion.filter((o) => o.id === office) : inRegion;
  }, [directory, region, office]);
  const officeIds = useMemo(() => new Set(visibleOffices.map((o) => o.id)), [visibleOffices]);

  const from = shift(day, -(range.days - 1));
  const [chart, reloadChart, chartRefresh] = useBlock(
    (signal) => api.analytics(from, day, filters, signal).then((body) => body.series),
    `${key}|${range.key}`,
  );

  const [table] = useBlock(
    (signal) =>
      Promise.all(
        visibleOffices.map((item) =>
          api.presence({ date: day, office_id: item.id }, signal)
            .then((body) => ({ office: item, counts: body.counts })),
        ),
      ),
    `${key}|offices:${visibleOffices.map((o) => o.id).join(',')}`,
    directory.state === 'ready',
  );

  // Очередь заявок — с теми же регионом и офисом, что и вся страница.
  const [open] = useBlock(
    (signal) =>
      Promise.all([
        api.queue({ kind: 'absence', status: OPEN, limit: '200', ...place }, signal),
        api.queue({ kind: 'correction', status: OPEN, limit: '200', ...place }, signal)
          .catch(() => ({ items: [] as api.QueueItem[], next_cursor: null, has_more: false })),
      ]).then(([absences, fixes]) => ({
        absences: absences.items.map((item) => item.absence).filter((row): row is api.AbsenceRow => Boolean(row)),
        fixes: fixes.items.length,
      })),
    key,
  );

  const [events] = useBlock(
    (signal) =>
      Promise.all([
        api.feed({ limit: '30' }, signal).then((page) => page.items).catch(() => [] as api.FeedEvent[]),
        api.queue({ kind: 'absence', status: 'APPROVED,REJECTED', limit: '30', ...place }, signal)
          .then((page) => page.items).catch(() => [] as api.QueueItem[]),
      ]),
    key,
  );

  const counts = cards.state === 'ready' ? byKey(cards.data.cards) : {};
  const cardOf = (name: string) => (cards.state === 'ready' ? cards.data.cards.find((c) => c.key === name) : undefined);
  const isToday = day === today();

  const badges: Record<string, number> = {};
  if (open.state === 'ready' && open.data.absences.length + open.data.fixes > 0) {
    badges['requests'] = open.data.absences.length + open.data.fixes;
  }

  return (
    <AppShell breadcrumb="Главная" badges={badges}>
      <div className="hm">
        <section className="hm-sheet">
          {/* --- шапка --- */}
          <header className="hm-head">
            <div>
              <h1 className="hm-head__title">Обзор на сегодня</h1>
              <p className="hm-head__sub">{longDate(day)} · данные по отметкам</p>
            </div>
            <div className="hm-head__tools">
              <Dropdown
                label="Регион"
                value={region}
                empty="Все регионы"
                options={directory.state === 'ready' ? directory.data.regions.map((r) => ({ id: r.id, name: r.name })) : []}
                onChange={(value) => {
                  setRegion(value);
                  // Офис другого региона перестал быть допустимым выбором.
                  setOffice('');
                }}
              />
              <Dropdown
                label="Офис"
                value={office}
                empty={`Все офисы${visibleOffices.length ? ` · ${visibleOffices.length}` : ''}`}
                options={visibleOffices.map((o) => ({ id: o.id, name: o.name }))}
                onChange={setOffice}
              />
              <DatePicker label="Дата" value={day} now={today()} onChange={setDay} />
              <button type="button" className="hm-icon" aria-label="Обновить" onClick={() => setAttempt((n) => n + 1)}>
                <AppIcon name="refresh" size={18} />
              </button>
            </div>
          </header>

          {/* --- строка «Сегодня» --- */}
          <Guard block={cards} name="показатели" className="hm-today hm-today--state">
            {() => (
              <ul className="hm-today" aria-label="Сегодня">
                <Stat icon="users" value={counts['active_employees']} title="сотрудников"
                      note="в штате по выбранным офисам" to={cardLink(cardOf('active_employees'))} />
                <Stat icon="calendar" value={counts['should_work_today']}
                      title={isToday ? 'по графику сегодня' : 'было по графику'}
                      note={isToday ? 'должны отметиться сегодня' : 'должны были отметиться'}
                      to={cardLink(cardOf('should_work_today'))} />
                <Stat icon="building" value={counts['in_office']}
                      title={isToday ? 'сейчас в офисе' : 'в офисе'}
                      note={isToday ? 'на рабочих местах' : 'не отметили уход'}
                      to={cardLink(cardOf('in_office'))} />
                <Stat icon="alert" value={counts['not_come']} title="без отметки" warn
                      note={isToday ? 'смена уже началась' : 'смена прошла без отметки'}
                      to={cardLink(cardOf('not_come'))} />
              </ul>
            )}
          </Guard>

          {/* --- середина: ритм дня и фокус HR --- */}
          <div className="hm-row">
            <section className="hm-part" aria-label="Ритм дня">
              <div className="hm-part__head">
                <h2 className="hm-part__title">Ритм дня</h2>
                <div className="hm-switch" role="group" aria-label="Период">
                  {RANGES.map((item) => (
                    <button key={item.key} type="button" aria-pressed={item.key === range.key}
                            className={item.key === range.key ? 'hm-switch__on' : undefined}
                            onClick={() => setRange(item)}>
                      {item.title}
                    </button>
                  ))}
                </div>
              </div>
              {chartRefresh.failed && chart.state === 'ready' && (
                <p className="hm-retry" role="status">
                  Не удалось обновить явку — показан прежний период.
                  <button type="button" className="hm-link" onClick={reloadChart}>Повторить</button>
                </p>
              )}
              <Guard block={chart} name="явку">
                {(series) => {
                  const points = series.map((one) => ({
                    day: one.day,
                    attended: one.attended,
                    expected: one.expected,
                    value: one.expected > 0 ? Math.round((one.attended / one.expected) * 100) : null,
                  }));
                  const to = '/attendance' + api.query({ period: 'range', from, to: day, ...place });
                  return points.every((p) => p.value === null) ? (
                    <p className="hm-empty">За выбранный период по графику никто не работал — явку не с чем сравнить.</p>
                  ) : (
                    <Link className="hm-chart-link" to={to} aria-label="Открыть посещаемость за этот период">
                      <Rhythm points={points} />
                    </Link>
                  );
                }}
              </Guard>
              {cards.state === 'ready' && (
                <p className="hm-note">
                  <AppIcon name="info" size={16} />
                  {(counts['should_work_today'] ?? 0) === 0
                    ? (isToday ? 'Сегодня по выбранным условиям нет сотрудников по графику.' : 'В этот день по выбранным условиям никто не работал по графику.')
                    : <>{isToday ? 'Сегодня отметил' : 'В этот день отметил'}{people(counts['should_work_today'] ?? 0) === 'сотрудник' ? 'ся' : 'ись'}{' '}
                        <b>{counts['came'] ?? 0} из {counts['should_work_today']}</b> {genitive(counts['should_work_today'] ?? 0)}.</>}
                </p>
              )}
            </section>

            <section className="hm-part" aria-label="Фокус HR">
              <div className="hm-part__head">
                <h2 className="hm-part__title">Фокус HR</h2>
                <Link className="hm-link" to="/requests?status=open">Все задачи <AppIcon name="next" size={16} /></Link>
              </div>
              <Guard block={open} name="задачи">
                {(data) => {
                  const sick = data.absences.filter((row) => row.absence_type.code === 'SICK_LEAVE');
                  const waitPaper = sick.filter((row) => row.stage === 'WAITING_DOCUMENTS' || row.stage === 'NEEDS_FIX').length;
                  const review = sick.filter((row) => row.stage === 'HR_REVIEW').length;
                  const leave = data.absences.filter((row) => row.absence_type.code !== 'SICK_LEAVE' && row.kind !== 'CANCEL').length;
                  const missing = isToday ? counts['not_come'] ?? 0 : 0;
                  const all: FocusRow[] = [
                    { icon: 'doc', tone: 'bad', count: waitPaper, title: `${count(waitPaper, ['больничный ждёт', 'больничных ждут', 'больничных ждут'])} справку`,
                      note: 'До подтверждения не попадут в табель', to: '/requests?tab=sick&status=open' },
                    { icon: 'check', tone: 'plain', count: review, title: `${count(review, ['справка ждёт', 'справки ждут', 'справок ждут'])} проверки`,
                      note: 'Больничный подтверждается после проверки', to: '/requests?tab=sick&status=open' },
                    { icon: 'calendar', tone: 'plain', count: leave, title: `${count(leave, ['отпуск ждёт', 'отпуска ждут', 'отпусков ждут'])} решения`,
                      note: 'Проверить пересечение периодов', to: '/requests?tab=leave&status=open' },
                    { icon: 'late', tone: 'plain', count: data.fixes, title: `${count(data.fixes, ['исправление отметки', 'исправления отметок', 'исправлений отметок'])}`,
                      note: 'Сотрудники просят поправить время', to: '/requests?tab=fixes&status=open' },
                    { icon: 'alert', tone: 'warn', count: missing, title: `${count(missing, ['сотрудник', 'сотрудника', 'сотрудников'])} без отметки`,
                      note: 'Смена уже началась', to: cardLink(cardOf('not_come')) ?? '/attendance' },
                  ];
                  const rows = all.filter((row) => row.count > 0);
                  return rows.length === 0 ? (
                    <p className="hm-empty">Сейчас ничего не ждёт решения HR.</p>
                  ) : (
                    <ul className="hm-focus">
                      {rows.map((row) => (
                        <li key={row.title}>
                          <Link className="hm-focus__row" to={row.to}>
                            <span className={`hm-mark hm-mark--${row.tone}`} aria-hidden="true">
                              <AppIcon name={row.icon} size={18} />
                            </span>
                            <span className="hm-focus__text">
                              <b>{row.title}</b>
                              <small>{row.note}</small>
                            </span>
                            <span className={row.tone === 'warn' ? 'hm-focus__n hm-focus__n--warn' : 'hm-focus__n'}>{row.count}</span>
                            <AppIcon name="next" size={16} className="hm-go" />
                          </Link>
                        </li>
                      ))}
                    </ul>
                  );
                }}
              </Guard>
            </section>
          </div>

          {/* --- низ: офисы и события --- */}
          <div className="hm-row hm-row--low">
            <section className="hm-part" aria-label="Офисы сегодня">
              <div className="hm-part__head">
                <h2 className="hm-part__title">{isToday ? 'Офисы сегодня' : 'Офисы за день'}</h2>
                <Link className="hm-link" to="/analytics?tab=attendance">Сравнить офисы <AppIcon name="next" size={16} /></Link>
              </div>
              <Guard block={table} name="офисы">
                {(rows) => rows.length === 0 ? (
                  <p className="hm-empty">В выбранной области нет доступных офисов.</p>
                ) : (
                  <div className="hm-table" role="table" aria-label="Офисы">
                    <div className="hm-table__head" role="row">
                      <span role="columnheader">Офис</span>
                      <span role="columnheader">По графику</span>
                      <span role="columnheader">В офисе</span>
                      <span role="columnheader">Нет отметки</span>
                      <span role="columnheader">Отсутствуют</span>
                      <span aria-hidden="true" />
                    </div>
                    <div className="hm-table__body">
                      {rows.map(({ office: item, counts: c }, index) => {
                        const missing = c['NOT_COME'] ?? 0;
                        return (
                          <Link key={item.id} role="row" className="hm-table__row"
                                to={'/attendance' + api.query({ date: day, office_id: item.id })}>
                            <span role="cell" className="hm-table__name">
                              <i className={`hm-dot hm-dot--${index % 4}`} aria-hidden="true" />
                              {item.name}
                            </span>
                            <span role="cell">{expected(c)}</span>
                            <span role="cell">{c['IN_OFFICE'] ?? 0}</span>
                            <span role="cell" className={missing > 0 ? 'hm-warn' : undefined}>{missing}</span>
                            <span role="cell">{absent(c)}</span>
                            <AppIcon name="next" size={16} className="hm-go" />
                          </Link>
                        );
                      })}
                    </div>
                  </div>
                )}
              </Guard>
            </section>

            <section className="hm-part" aria-label="Последние события">
              <div className="hm-part__head">
                <h2 className="hm-part__title">Последние события</h2>
              </div>
              <Guard block={events} name="события">
                {([feed, decided]) => {
                  const list = timeline(feed, decided, officeIds, Boolean(region || office)).slice(0, 5);
                  return list.length === 0 ? (
                    <p className="hm-empty">Событий по выбранным офисам пока нет.</p>
                  ) : (
                    <ul className="hm-events">
                      {list.map((one) => (
                        <li key={one.id}>
                          <Link className="hm-events__row" to={one.to}>
                            <span className={`hm-mark hm-mark--sm hm-mark--${one.tone}`} aria-hidden="true">
                              <AppIcon name={one.icon} size={16} />
                            </span>
                            <span className="hm-events__text">
                              <b>{one.title}</b>
                              <small>{one.note}</small>
                            </span>
                            <time dateTime={one.at}>{moment(one.at)}</time>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  );
                }}
              </Guard>
            </section>
          </div>

          {/* --- быстрые переходы --- */}
          <nav className="hm-links" aria-label="Быстрые переходы">
            <Link to={'/attendance' + api.query({ date: day, ...place })}><AppIcon name="chart" size={18} />Открыть посещаемость<AppIcon name="next" size={16} /></Link>
            <Link to="/requests?status=open"><AppIcon name="doc" size={18} />Открыть заявки<AppIcon name="next" size={16} /></Link>
            <Link to="/analytics"><AppIcon name="report" size={18} />Открыть аналитику<AppIcon name="next" size={16} /></Link>
          </nav>
        </section>
      </div>
    </AppShell>
  );
}

// --- части ---------------------------------------------------------------------

function Stat({ icon, value, title, note, to, warn }: {
  icon: AppIconName;
  value: number | undefined;
  title: string;
  note: string;
  to: string | null;
  warn?: boolean;
}) {
  const hot = warn && (value ?? 0) > 0;
  const inside = (
    <>
      <span className={hot ? 'hm-stat__icon hm-stat__icon--warn' : 'hm-stat__icon'} aria-hidden="true">
        <AppIcon name={icon} size={20} />
      </span>
      <span className="hm-stat__body">
        <span className="hm-stat__line">
          <b className={hot ? 'hm-stat__value hm-warn' : 'hm-stat__value'}>{value ?? '—'}</b>
          <span className={hot ? 'hm-stat__title hm-warn' : 'hm-stat__title'}>{title}</span>
        </span>
        <small className="hm-stat__note">{note}</small>
      </span>
    </>
  );
  // Ноль ссылкой не делается: открывать пустой список незачем.
  return (
    <li>
      {to && (value ?? 0) > 0 ? <Link className="hm-stat hm-stat--go" to={to}>{inside}</Link> : <div className="hm-stat">{inside}</div>}
    </li>
  );
}

type FocusRow = { icon: AppIconName; tone: 'bad' | 'warn' | 'plain'; count: number; title: string; note: string; to: string };

type Point = { day: string; attended: number; expected: number; value: number | null };

/**
 * «Ритм дня»: доля отметившихся из тех, кто должен был работать.
 *
 * Своя тонкая линия вместо общего графика: здесь нужен силуэт недели, а
 * не инструмент анализа. День без людей по графику — разрыв линии, а не
 * ноль: ноль означал бы, что никто не пришёл.
 */
function Rhythm({ points }: { points: Point[] }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [height, setHeight] = useState(176);
  // Высота — от места, что осталось блоку: на высоком экране линия
  // занимает его, а не оставляет пустоту под собой.
  useEffect(() => {
    const node = box.current;
    if (!node) return;
    const measure = () => {
      setWidth(Math.max(280, node.clientWidth));
      setHeight(Math.max(170, Math.min(320, node.clientHeight)));
    };
    measure();
    const watcher = new ResizeObserver(measure);
    watcher.observe(node);
    return () => watcher.disconnect();
  }, []);

  const left = 34;
  const right = 24;
  const top = 10;
  const bottom = 38;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const step = points.length > 1 ? plotW / (points.length - 1) : 0;
  const x = (i: number) => left + (points.length > 1 ? i * step : plotW / 2);
  const y = (v: number) => top + plotH - (v / 100) * plotH;
  const labelEvery = points.length > 16 ? 5 : points.length > 8 ? 2 : 1;

  // Отрезки без разрывов: день без графика рвёт линию.
  const runs: { i: number; v: number }[][] = [];
  let run: { i: number; v: number }[] = [];
  points.forEach((p, i) => {
    if (p.value === null) {
      if (run.length) runs.push(run);
      run = [];
    } else run.push({ i, v: p.value });
  });
  if (run.length) runs.push(run);

  const curve = (seg: { i: number; v: number }[]) =>
    seg.map((p, k) => {
      if (k === 0) return `M${x(p.i)},${y(p.v)}`;
      const prev = seg[k - 1]!;
      const mid = (x(prev.i) + x(p.i)) / 2;
      return `C${mid},${y(prev.v)} ${mid},${y(p.v)} ${x(p.i)},${y(p.v)}`;
    }).join(' ');

  return (
    <div className="hm-rhythm" ref={box}>
      <svg width={width} height={height} role="img"
           aria-label={`Явка по дням: ${points.map((p) => `${dayLabel(p.day)} — ${p.value === null ? 'нет графика' : `${p.value} %`}`).join(', ')}`}>
        {[0, 25, 50, 75, 100].map((tick) => (
          <g key={tick}>
            <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="hm-rhythm__grid" />
            <text x={left - 8} y={y(tick) + 4} textAnchor="end" className="hm-rhythm__axis">{tick}</text>
          </g>
        ))}
        {runs.map((seg, n) => (
          <g key={n}>
            {seg.length > 1 && (
              <path className="hm-rhythm__area"
                    d={`${curve(seg)} L${x(seg[seg.length - 1]!.i)},${y(0)} L${x(seg[0]!.i)},${y(0)} Z`} />
            )}
            <path className="hm-rhythm__line" d={curve(seg)} />
          </g>
        ))}
        {points.map((p, i) => (
          <g key={p.day}>
            {p.value !== null && (
              <circle cx={x(i)} cy={y(p.value)} r={3.5} className="hm-rhythm__dot">
                <title>{`${dayLabel(p.day)}: ${p.attended} из ${p.expected} (${p.value} %)`}</title>
              </circle>
            )}
            {i % labelEvery === 0 || i === points.length - 1 ? (
              <text x={x(i)} y={height - 20} textAnchor="middle" className="hm-rhythm__axis">
                <tspan x={x(i)}>{dayLabel(p.day)}</tspan>
                <tspan x={x(i)} dy={14}>{weekday(p.day)}</tspan>
              </text>
            ) : null}
          </g>
        ))}
      </svg>
    </div>
  );
}

type Event = { id: string; at: string; title: string; note: string; to: string; icon: AppIconName; tone: 'ok' | 'bad' | 'plain' | 'warn' };

/**
 * Лента: новые заявки, справки и обращения из ленты уведомлений HR плюс
 * решения по заявкам. Отбор по офису — по офису события; когда офис
 * выбран, событие без офиса не показывается: о нём нельзя сказать, что
 * оно из этого офиса.
 */
function timeline(feed: api.FeedEvent[], decided: api.QueueItem[], offices: Set<string>, narrowed: boolean): Event[] {
  const out: Event[] = [];
  for (const one of feed) {
    if (narrowed && (!one.office_id || !offices.has(one.office_id))) continue;
    const look: Record<api.FeedType, [AppIconName, Event['tone']]> = {
      absence_request: ['calendar', 'plain'],
      sick_leave: ['doc', 'bad'],
      absence_cancel: ['cross', 'plain'],
      absence_document: ['doc', 'plain'],
      attendance_correction: ['late', 'warn'],
      question: ['chat', 'plain'],
    };
    const [icon, tone] = look[one.type] ?? ['info', 'plain'];
    out.push({
      id: one.id, at: one.created_at, title: one.title, icon, tone, to: one.action_url || '/',
      note: [one.employee_name, one.short_text || one.office_name].filter(Boolean).join(' — '),
    });
  }
  for (const item of decided) {
    const row = item.absence;
    if (!row?.reviewed_at) continue;
    const ok = row.status === 'APPROVED';
    const sick = row.absence_type.code === 'SICK_LEAVE';
    out.push({
      id: `decided:${row.id}`,
      at: row.reviewed_at,
      title: sick ? (ok ? 'Больничный подтверждён' : 'Больничный отклонён') : `${row.absence_type.name}: ${ok ? 'одобрено' : 'отклонено'}`,
      note: [shortName(row.employee.full_name), period(row.first_day, row.last_day)].filter(Boolean).join(' — '),
      to: `/requests/${encodeURIComponent(row.id)}`,
      icon: ok ? 'check' : 'cross',
      tone: ok ? 'ok' : 'plain',
    });
  }
  return out.sort((a, b) => b.at.localeCompare(a.at));
}

/**
 * Обёртка блока. Загрузка, отказ и ошибка — три разных вида, и ни один
 * из них не выглядит как ноль.
 */
function Guard<T>({ block, name, children, className }: {
  block: Block<T>;
  name: string;
  children: (data: T) => ReactNode;
  className?: string;
}) {
  if (block.state === 'ready') return <>{children(block.data)}</>;
  const text = block.state === 'loading'
    ? `Загружаем ${name}…`
    : block.state === 'denied'
      ? `Нет доступа к разделу «${name}».`
      : `Не удалось загрузить ${name}. Данные не показаны — это не ноль.`;
  return <p className={['hm-empty', block.state === 'error' ? 'hm-empty--bad' : '', className ?? ''].filter(Boolean).join(' ')}>{text}</p>;
}

// --- мелочи ---------------------------------------------------------------------

function byKey(cards: api.Card[]): Record<string, number> {
  return Object.fromEntries(cards.map((card) => [card.key, card.value]));
}

/** По графику: пришли, ушли и не пришли. Выходной и отсутствие сюда не входят. */
const expected = (c: Record<string, number>) => (c['IN_OFFICE'] ?? 0) + (c['LEFT'] ?? 0) + (c['NOT_COME'] ?? 0);

/** Отсутствуют: только оформленное и подтверждённое — отпуск, больничный, иное. */
const absent = (c: Record<string, number>) => (c['VACATION'] ?? 0) + (c['SICK_LEAVE'] ?? 0) + (c['OTHER_ABSENCE'] ?? 0);

function plural(n: number, forms: [string, string, string]): string {
  const tail = Math.abs(n) % 100;
  const last = tail % 10;
  if (tail > 10 && tail < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

const count = (n: number, forms: [string, string, string]) => `${n} ${plural(n, forms)}`;
const people = (n: number) => plural(n, ['сотрудник', 'сотрудника', 'сотрудников']);
/** «из 1 сотрудника», «из 5 сотрудников» — после «из» родительный падеж. */
const genitive = (n: number) => plural(n, ['сотрудника', 'сотрудников', 'сотрудников']);

function dayLabel(iso: string): string {
  const [, m, d] = iso.split('-');
  return `${d}.${m}`;
}

function weekday(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  return WEEKDAYS[new Date(Date.UTC(y!, m! - 1, d!)).getUTCDay()]!;
}

function period(first: string | null, last: string | null): string {
  if (!first) return '';
  const f = (iso: string) => iso.split('-').reverse().join('.');
  return last && last !== first ? `${f(first)} – ${f(last)}` : `с ${f(first)}`;
}

/** «10:24», «Вчера, 18:32», «22 сен, 10:00». */
function moment(iso: string): string {
  const date = new Date(iso);
  const time = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const key = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const now = new Date();
  if (key(date) === key(now)) return time;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key(date) === key(yesterday)) return `Вчера, ${time}`;
  return `${date.getDate()} ${MONTHS_SHORT[date.getMonth()]}, ${time}`;
}

/** «Каримов Нодир Азизович» -> «Каримов Н.»: в строке ленты ФИО целиком не нужно. */
function shortName(full: string): string {
  const [last, first] = full.split(/\s+/);
  if (!last) return full;
  return first ? `${last} ${first[0]}.` : last;
}
