/**
 * Посещаемость: сводка за день, состав смены и журнал отметок.
 *
 * Ни одна величина здесь не считается заново. Присутствие, время в
 * офисе, опоздания, отсутствия и календарные исключения считает сервер;
 * повторить эти правила в браузере значило бы завести второе место, где
 * та же цифра получается по другим правилам, — и однажды они разойдутся.
 *
 * Чего страница НЕ делает, намеренно:
 * — не называет «нет отметки» прогулом: причин может быть много;
 * — не считает опозданием отсутствие графика: сравнивать не с чем;
 * — не выдаёт разницу между первым входом и последним выходом за время
 *   в офисе — посещений за день может быть несколько;
 * — не называет промежуток между посещениями обедом.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { DayBar, atClock } from '../components/DayBar';
import { DayCard } from '../components/DayCard';
import {
  formatTime, longDate, today, useBlock, type Block,
} from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import { clock, clockOnDay } from '../features/time/zone';

/** Состояния состава смены. Взаимоисключающими они не являются. */
const STATE_TITLE: Record<string, string> = {
  IN_OFFICE: 'В офисе',
  LEFT: 'Ушёл',
  NOT_COME: 'Нет отметки',
  VACATION: 'Отпуск',
  SICK_LEAVE: 'Больничный',
  OTHER_ABSENCE: 'Отсутствие',
  DAY_OFF: 'Выходной по графику',
  NO_SCHEDULE: 'Без графика',
};

const PAGE = 10;

export function AttendancePage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);

  const [params, setParams] = useSearchParams();
  const day = params.get('date') ?? today();
  const tab = params.get('tab') === 'log' ? 'log' : 'day';
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const state = params.get('state') ?? '';
  // Признак строки, а не состояние сервера: опоздавший может быть
  // и в офисе, и уже ушедшим, поэтому «Опоздали» — отдельная ось.
  const flag = params.get('flag') === 'late' || params.get('flag') === 'open'
    ? (params.get('flag') as 'late' | 'open')
    : '';
  const page = Number(params.get('page') ?? '1');
  const picked = params.get('employee') ?? '';

  const [draft, setDraft] = useState(search);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [attempt, setAttempt] = useState(0);
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
    () => ({
      date: day,
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
    }),
    [day, region, office],
  );
  // Панель и список по умолчанию смотрят на ПОЛНЫЙ состав дня: доля
  // в офисе и динамика приходов не должны меняться от того, какой
  // фильтр выбран в таблице.
  const wide = `${day}|${region}|${office}|${attempt}`;
  const key = `${wide}|${state}|${search}`;
  const narrowed = Boolean(state || search);

  // Пять показателей берутся у дашборда: он считает их по ВСЕМУ составу,
  // а не по строкам, которые поместились в ответ.
  const [cards] = useBlock(
    (signal) =>
      api.dashboard(scope, signal).then((body) => {
        setUpdated(new Date());
        return body;
      }),
    key,
  );

  const [base] = useBlock(
    (signal) => api.presenceDay(scope, signal),
    wide,
    tab === 'day',
  );

  // Второй запрос — только когда отбор действительно сужает набор.
  // Без фильтра он повторял бы первый.
  const [narrow] = useBlock(
    (signal) =>
      api.presenceDay(
        { ...scope, ...(state ? { state } : {}), ...(search ? { search } : {}) },
        signal,
      ),
    key,
    tab === 'day' && narrowed,
  );

  const shift = narrowed ? narrow : base;

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items,
        offices: o.items,
      })),
    'directory',
  );

  const offices = useMemo(() => {
    if (directory.state !== 'ready') return [];
    const all = directory.data.offices.filter((o) => o.status === 'ACTIVE');
    return region ? all.filter((o) => o.region_id === region) : all;
  }, [directory, region]);

  const found = shift.state === 'ready' ? shift.data.items : [];
  // Опоздание и незакрытая сессия уже лежат в строке: спрашивать их у
  // сервера отдельно значило бы сходить за тем, что пришло.
  const rows = flag === 'late'
    ? found.filter((row) => (row.late_minutes ?? 0) > 0)
    : flag === 'open'
      ? found.filter((row) => row.open_session_id !== null)
      : found;
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const slice = rows.slice((page - 1) * PAGE, page * PAGE);
  const current = rows.find((row) => row.employee_id === picked) ?? null;
  const dirty = Boolean(search || region || office || state || flag);
  const counts = cards.state === 'ready'
    ? Object.fromEntries(cards.data.cards.map((c) => [c.key, c.value]))
    : {};
  const past = day < today();

  // Чипы, плитки панели и строки «Требует внимания» — один и тот же
  // отбор, показанный в трёх местах. Нажатие в любом из них ставит то
  // же самое, и подсветка совпадает без отдельного состояния.
  const chosen = pickedQuick(state, flag);
  const choose = (item: Quick) =>
    patch(
      chosen === item.id && item.id !== 'all'
        ? { state: null, flag: null }
        : { state: item.state ?? null, flag: item.flag ?? null },
    );

  return (
    <AppShell breadcrumb="Посещаемость" section="attendance">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Посещаемость</h1>
          <p className="head__sub">
            {longDate(day)} <span className="dot">·</span> По данным отметок
          </p>
        </div>
        <div className="head__filters">
          <div className="filters">
            <label className="pick pick--date">
              <AppIcon name="calendar" size={16} />
              <input type="date" value={day} aria-label="Дата"
                     onChange={(event) => patch({ date: event.target.value || today() })} />
            </label>
            {can('reports.export') && (
              <button type="button" className="btn" disabled
                      title="Выгрузка появится следующим этапом">
                <AppIcon name="report" size={16} />
                Экспорт
              </button>
            )}
            <button type="button" className="pick pick--icon" aria-label="Обновить"
                    onClick={() => setAttempt((n) => n + 1)}>
              <AppIcon name="refresh" size={16} />
            </button>
          </div>
          <p className="head__updated">
            {updated ? `Обновлено в ${formatTime(updated)}` : 'Загружаем…'}
          </p>
        </div>
      </header>

      <div className="tabs tabs--bare" role="tablist">
        {[
          { key: 'day', title: 'За день' },
          { key: 'log', title: 'Журнал отметок' },
        ].map((item) => (
          <button key={item.key} type="button" role="tab" aria-selected={item.key === tab}
                  className={item.key === tab ? 'tab tab--on' : 'tab'}
                  onClick={() => patch({ tab: item.key === 'day' ? null : item.key })}>
            {item.title}
          </button>
        ))}
      </div>

      {tab === 'day' ? (
        <>
          <Section block={cards} name="показатели">
            {(data) => (
              <Today
                counts={counts}
                past={past}
                rows={base.state === 'ready' ? base.data.items : []}
                zone={base.state === 'ready' ? base.data.timezone : ''}
                chosen={chosen}
                onPick={choose}
                date={data.date}
              />
            )}
          </Section>

          <div className="queue-grid">
            <section className="sheet">
              <div className="toolbar toolbar--day">
                {/* Быстрые фильтры — те же состояния, что и в списке
                    справа: один способ сузить выборку, а не два. */}
                <div className="chips" role="group" aria-label="Быстрый отбор">
                  {QUICK.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={`chip${chosen === item.id ? ' chip--on' : ''}`}
                      aria-pressed={chosen === item.id}
                      onClick={() => choose(item)}
                    >
                      {item.title}
                      <span className="chip__count">{item.count(counts)}</span>
                    </button>
                  ))}
                </div>
                <label className="find find--wide">
                  <AppIcon name="search" size={16} />
                  <input type="search" value={draft} placeholder="Поиск сотрудника"
                         aria-label="Поиск сотрудника"
                         onChange={(event) => setDraft(event.target.value)} />
                </label>
                <Picker label="Регион" value={region} empty="Все регионы"
                        options={directory.state === 'ready' ? directory.data.regions : []}
                        onChange={(value) => patch({ region_id: value || null, office_id: null })} />
                <Picker label="Офис" value={office} empty="Все офисы" options={offices}
                        onChange={(value) => patch({ office_id: value || null })} />
                <label className="pick">
                  <span className="visually-hidden">Статус</span>
                  <select value={state}
                          onChange={(event) =>
                            patch({ state: event.target.value || null, flag: null })}>
                    <option value="">Все статусы</option>
                    {Object.entries(STATE_TITLE).map(([code, title]) => (
                      <option key={code} value={code}>{title}</option>
                    ))}
                  </select>
                </label>
                {dirty && (
                  <button type="button" className="btn" onClick={() =>
                    patch({ search: null, region_id: null, office_id: null,
                            state: null, flag: null })}>
                    Сбросить
                  </button>
                )}
              </div>

              <Section block={shift} name="состав смены">
                {(data) =>
                  rows.length === 0 ? (
                    <p className="empty">
                      {dirty ? 'По этим условиям никого нет.' : 'На выбранный день отметок нет.'}
                    </p>
                  ) : (
                    <>
                      {data.truncated && (
                        <p className="empty empty--bad">
                          Показаны не все: состав больше, чем помещается в один ответ.
                          Сузьте фильтры — иначе список неполон.
                        </p>
                      )}
                      <div className="scroller">
                        <table className="people">
                          <thead>
                            <tr>
                              <th>Сотрудник</th>
                              <th>Офис / график</th>
                              <th className="people__day">Рабочий день</th>
                              <th>В офисе</th>
                              <th>Статус</th>
                            </tr>
                          </thead>
                          <tbody>
                            {slice.map((row) => (
                              <tr key={row.employee_id} tabIndex={0}
                                  className={row.employee_id === picked ? 'is-picked' : undefined}
                                  onClick={() => patch({ employee: row.employee_id }, true)}
                                  onKeyDown={(event) =>
                                    event.key === 'Enter' && patch({ employee: row.employee_id }, true)}>
                                <td>
                                  <span className="who">
                                    <span className="avatar">{initials(row.full_name)}</span>
                                    <span className="who__text">
                                      <span className="who__name">{row.full_name}</span>
                                      <span className="who__id">{row.employee_number ?? '—'}</span>
                                    </span>
                                  </span>
                                </td>
                                <td>
                                  <span className="two">
                                    <span className="two__first">{row.office_name ?? '—'}</span>
                                    <span className="two__second">
                                      {row.scheduled_start && row.scheduled_end
                                        ? `${row.scheduled_start.slice(0, 5)} – ${row.scheduled_end.slice(0, 5)}`
                                        : 'График не задан'}
                                    </span>
                                  </span>
                                </td>
                                <td className="people__day">
                                  <DayBar row={row} zone={data.timezone} />
                                </td>
                                <td className="num">{row.seconds ? span(row.seconds) : '—'}</td>
                                <td>
                                  <span className="two">
                                    <span className="state">
                                      <i className="state__dot" />
                                      {STATE_TITLE[row.state] ?? row.state}
                                    </span>
                                    {row.late_minutes !== null && row.late_minutes > 0 && (
                                      <span className="two__second">
                                        Позже на {row.late_minutes} мин
                                      </span>
                                    )}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )
                }
              </Section>

              <div className="pager">
                <p className="pager__note">
                  {shift.state === 'ready'
                    ? `Показано ${slice.length} из ${rows.length}`
                    : ''}
                </p>
                <div className="pager__tools">
                  <button type="button" className="btn" disabled={page <= 1}
                          onClick={() => patch({ page: String(page - 1) }, true)}>
                    Назад
                  </button>
                  <button type="button" className="btn btn--dark" disabled={page >= pages}
                          onClick={() => patch({ page: String(page + 1) }, true)}>
                    Далее
                  </button>
                </div>
              </div>
              <p className="sheet__hint">Нажмите на сотрудника, чтобы увидеть отметки.</p>
            </section>

            <div className="att-side">
              <Attention
                counts={counts}
                chosen={chosen}
                onPick={choose}
              />

              {current ? (
                <DayCard
                row={current}
                day={day}
                timezone={shift.state === 'ready' ? shift.data.timezone : ''}
                canAdd={can('attendance.manual')}
                onClose={() => patch({ employee: null }, true)}
                onChanged={() => setAttempt((n) => n + 1)}
                />
              ) : (
                <section className="panel">
                  <p className="empty">Выберите сотрудника, чтобы увидеть его день.</p>
                </section>
              )}
            </div>
          </div>
        </>
      ) : (
        <Journal day={day} region={region} office={office} />
      )}
    </AppShell>
  );
}

/**
 * Быстрый отбор над таблицей.
 *
 * Значения — настоящие состояния сервера, а не выдуманные ярлыки:
 * нажатие ставит тот же `state`, который принимает `/attendance/presence`.
 * «Все» — пустое значение, то есть снятый фильтр.
 */
type Quick = {
  /** Опознавательный знак отбора: по нему совпадает подсветка. */
  id: string;
  title: string;
  /** Состояние присутствия. Его понимает сервер. */
  state?: string;
  /**
   * Признак строки. Состоянием он быть не может: опоздавший бывает и
   * в офисе, и уже ушедшим, а сессию оставляют открытой в любом из них.
   * Считается по уже полученным строкам.
   */
  flag?: 'late' | 'open';
  icon?: 'alert' | 'late' | 'clock' | 'logout';
  count: (counts: Record<string, number>) => number;
};

const QUICK: Quick[] = [
  { id: 'all', title: 'Все', count: (c) => c['should_work_today'] ?? 0 },
  { id: 'now', title: 'Сейчас', state: 'IN_OFFICE', count: (c) => c['in_office'] ?? 0 },
  { id: 'none', title: 'Нет отметки', state: 'NOT_COME', count: (c) => c['not_come'] ?? 0 },
  { id: 'late', title: 'Опоздали', flag: 'late', count: (c) => c['late'] ?? 0 },
];

/**
 * Четыре числа панели. Каждое — кнопка: она сужает таблицу, а не просто
 * повторяет цифру.
 */
const METRICS: Quick[] = [
  { id: 'left', title: 'Уже ушли', state: 'LEFT', icon: 'logout',
    count: (c) => c['left'] ?? 0 },
  { id: 'none', title: 'Нет отметки', state: 'NOT_COME', icon: 'alert',
    count: (c) => c['not_come'] ?? 0 },
  { id: 'late', title: 'Опоздали', flag: 'late', icon: 'late',
    count: (c) => c['late'] ?? 0 },
  { id: 'open', title: 'Незакрытые', flag: 'open', icon: 'clock',
    count: (c) => c['open_sessions'] ?? 0 },
];

/**
 * Строки блока «Требует внимания». Каждая — тот же отбор таблицы.
 *
 * «Вне геозоны» в макете есть, а здесь нет: признак живёт на отметке
 * (inside_geofence), а не на строке состава, и ни одно число дня его
 * не считает. Строка с нулём утверждала бы, что таких нет, — а это
 * неизвестно.
 */
const ATTENTION: Quick[] = [
  { id: 'none', title: 'Нет отметки', state: 'NOT_COME', icon: 'alert',
    count: (c) => c['not_come'] ?? 0 },
  { id: 'late', title: 'Опоздали', flag: 'late', icon: 'late',
    count: (c) => c['late'] ?? 0 },
  { id: 'open', title: 'Незакрытые посещения', flag: 'open', icon: 'clock',
    count: (c) => c['open_sessions'] ?? 0 },
];

/**
 * Какой из быстрых отборов сейчас включён.
 *
 * Ответ один на все три места: признак важнее состояния, потому что
 * «опоздали» и «незакрытые» ставятся вместе со снятым состоянием.
 */
function pickedQuick(state: string, flag: string): string {
  if (flag) return QUICK.concat(METRICS, ATTENTION).find((one) => one.flag === flag)?.id ?? '';
  if (!state) return 'all';
  return QUICK.concat(METRICS, ATTENTION).find((one) => one.state === state)?.id ?? '';
}

/**
 * Панель «Сегодня»: доля в офисе, приходы по времени и четыре числа.
 *
 * Заменяет пять отдельных карточек. Те занимали треть экрана и отвечали
 * на один вопрос — сколько человек где; здесь тот же ответ, но место
 * остаётся таблице, ради которой на страницу и заходят.
 */
function Today({
  counts,
  past,
  rows,
  zone,
  chosen,
  onPick,
  date,
}: {
  counts: Record<string, number>;
  past: boolean;
  rows: api.PresenceRow[];
  zone: string;
  chosen: string;
  onPick: (item: Quick) => void;
  date: string;
}) {
  const expected = counts['should_work_today'] ?? 0;
  // У прошедшего дня «сейчас в офисе» равно нулю по определению:
  // спрашивают у него не это, а сколько человек пришло.
  const here = past ? (counts['came'] ?? 0) : (counts['in_office'] ?? 0);
  // Доля считается от тех, кого ждали, а не от всей организации:
  // выходной у половины компании иначе выглядел бы провалом.
  const share = expected > 0 ? (here / expected) * 100 : 0;

  return (
    <section className="panel today">
      <h2 className="today__title">{past ? 'Итоги дня' : 'Сегодня'}</h2>

      <div className="today__body">
        <div className="today__share">
          <Donut share={share} />
          <p className="today__sum">
            <strong>{here} из {expected}</strong>
            <span>{past ? 'пришли на работу' : 'сейчас в офисе'}</span>
          </p>
        </div>

        <Arrivals rows={rows} zone={zone} past={past} />

        <div className="today__metrics">
          {METRICS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`today__metric${chosen === item.id ? ' today__metric--on' : ''}`}
              onClick={() => onPick(item)}
              aria-pressed={chosen === item.id}
            >
              <AppIcon name={item.icon ?? 'alert'} size={18} />
              <span className="today__metric-title">{item.title}</span>
              <strong className="today__metric-value">{item.count(counts)}</strong>
            </button>
          ))}
        </div>
      </div>
      <span className="visually-hidden">{date}</span>
    </section>
  );
}

/** Кольцо доли. Дугой, а не заливкой: так видна и сотая часть. */
function Donut({ share }: { share: number }) {
  const length = 2 * Math.PI * 26;
  const filled = (Math.min(Math.max(share, 0), 100) / 100) * length;
  return (
    <svg className="donut" viewBox="0 0 60 60" width={72} height={72}
         role="img" aria-label={`В офисе ${share.toFixed(1)} процента`}>
      <circle className="donut__track" cx="30" cy="30" r="26" />
      <circle className="donut__fill" cx="30" cy="30" r="26"
              strokeDasharray={`${filled} ${length}`} />
      <text className="donut__text" x="30" y="30">
        {share.toFixed(1).replace('.', ',')}%
      </text>
    </svg>
  );
}

/**
 * Динамика приходов: во сколько сегодня приходили.
 *
 * Берётся ПЕРВЫЙ вход каждого человека. Все входы подряд сместили бы
 * картину к обеду: возвращение с обеда — это тоже вход.
 */
function Arrivals({ rows, zone, past }: {
  rows: api.PresenceRow[]; zone: string; past: boolean;
}) {
  const step = 15;
  const title = past ? 'Динамика приходов' : 'Динамика приходов сегодня';
  const values = rows
    .map((row) => minutesOf(row.first_entry_at, zone))
    .filter((one): one is number => one !== null);

  if (values.length === 0) {
    return (
      <div className="today__chart">
        <p className="today__chart-title">{title}</p>
        <p className="muted">{past ? 'Приходов в этот день нет.' : 'Приходов пока нет.'}</p>
      </div>
    );
  }

  const low = Math.floor(Math.min(...values) / 60) * 60;
  const high = Math.ceil(Math.max(...values) / 60) * 60;
  const buckets: { from: number; count: number }[] = [];
  for (let from = low; from <= high; from += step) {
    buckets.push({
      from,
      count: values.filter((one) => one >= from && one < from + step).length,
    });
  }
  const top = Math.max(1, ...buckets.map((one) => one.count));

  return (
    <div className="today__chart">
      <p className="today__chart-title">{title}</p>
      <div className="today__bars">
        {buckets.map((one) => (
          <span
            key={one.from}
            className="today__bar"
            style={{ height: `${Math.max((one.count / top) * 100, one.count ? 8 : 0)}%` }}
            title={`${atClock(one.from)} – ${atClock(one.from + step)} · ${one.count}`}
          />
        ))}
      </div>
      <div className="today__axis">
        <span>{atClock(low)}</span>
        <span>{atClock(high)}</span>
      </div>
    </div>
  );
}

/** Минуты от полуночи в поясе офиса. */
function minutesOf(at: string | null, zone: string): number | null {
  if (!at) return null;
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return null;
  const text = new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    ...(zone ? { timeZone: zone } : {}),
  }).format(date);
  const [h, m] = text.split(':').map((part) => Number.parseInt(part, 10));
  if (Number.isNaN(h as number) || Number.isNaN(m as number)) return null;
  return (h as number) * 60 + (m as number);
}

/** «Требует внимания»: те же состояния, но списком и с переходом. */
function Attention({
  counts,
  chosen,
  onPick,
}: {
  counts: Record<string, number>;
  chosen: string;
  onPick: (item: Quick) => void;
}) {
  const total = ATTENTION.reduce((sum, one) => sum + one.count(counts), 0);
  return (
    <section className="panel attention">
      <h2 className="attention__title">
        Требует внимания
        <span className="attention__total">{total}</span>
      </h2>
      <ul className="attention__rows">
        {ATTENTION.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              className={`attention__row${chosen === item.id ? ' attention__row--on' : ''}`}
              aria-pressed={chosen === item.id}
              onClick={() => onPick(item)}
            >
              <AppIcon name={item.icon ?? 'alert'} size={18} />
              <span className="attention__name">{item.title}</span>
              <span className="attention__count">{item.count(counts)}</span>
              <AppIcon name="next" size={16} />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

// --- журнал ----------------------------------------------------------------

function Journal({ day, region, office }: { day: string; region: string; office: string }) {
  const [cursor, setCursor] = useState('');
  const [direction, setDirection] = useState('');
  const [onlyAccepted, setOnlyAccepted] = useState(true);

  const [log] = useBlock(
    (signal) =>
      api.events(
        {
          date_from: day,
          date_to: day,
          limit: '20',
          ...(region ? { region_id: region } : {}),
          ...(office ? { office_id: office } : {}),
          ...(direction ? { event_type: direction } : {}),
          // Успешные отметки и отклонённые попытки — разные вещи,
          // и складывать их в один список нельзя.
          ...(onlyAccepted ? { verification_status: 'ACCEPTED' } : {}),
        },
        signal,
      ),
    `log|${day}|${region}|${office}|${direction}|${onlyAccepted}|${cursor}`,
  );

  return (
    <section className="sheet">
      <div className="toolbar">
        <label className="pick">
          <span className="visually-hidden">Направление</span>
          <select value={direction} onChange={(event) => { setDirection(event.target.value); setCursor(''); }}>
            <option value="">Вход и выход</option>
            <option value="ENTRY">Только входы</option>
            <option value="EXIT">Только выходы</option>
          </select>
        </label>
        <label className="remember">
          <input type="checkbox" checked={onlyAccepted}
                 onChange={(event) => { setOnlyAccepted(event.target.checked); setCursor(''); }} />
          <span>Только успешные отметки</span>
        </label>
      </div>

      <Section block={log} name="журнал">
        {(data) =>
          data.items.length === 0 ? (
            <p className="empty">За выбранный день отметок нет.</p>
          ) : (
            <div className="scroller">
              <table className="people">
                <thead>
                  <tr>
                    <th>Время</th>
                    <th>Сотрудник</th>
                    <th>Офис и точка</th>
                    <th>Направление</th>
                    <th>Источник</th>
                    <th>Состояние</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((event) => (
                    <tr key={event.id}>
                      <td className="num">{event.occurred_at.slice(11, 16)}</td>
                      <td>{event.employee_id.slice(0, 8)}</td>
                      <td>
                        <span className="two">
                          <span className="two__first">{event.office_name ?? '—'}</span>
                          {event.qr_point_name && (
                            <span className="two__second">{event.qr_point_name}</span>
                          )}
                        </span>
                      </td>
                      <td>{event.event_type === 'ENTRY' ? 'Вход' : 'Выход'}</td>
                      <td>{event.source}</td>
                      <td>{event.verification_status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Section>

      <div className="pager">
        <p className="pager__note" />
        <div className="pager__tools">
          <button type="button" className="btn" disabled={!cursor} onClick={() => setCursor('')}>
            Назад
          </button>
          <button type="button" className="btn btn--dark"
                  disabled={log.state !== 'ready' || !log.data.has_more}
                  onClick={() => log.state === 'ready' && setCursor(log.data.next_cursor ?? '')}>
            Далее
          </button>
        </div>
      </div>
    </section>
  );
}

// --- мелочи ----------------------------------------------------------------

// Формат времени общий для всей CRM и живёт в `features/time/zone`.
// Здесь он переэкспортируется, потому что карточка дня берёт его
// отсюда: два разных способа показать одно и то же время — это ровно
// та ошибка, из-за которой на странице стоял UTC под подписью пояса.
export { clock, clockOnDay };


export function span(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours ? `${hours} ч ${String(minutes).padStart(2, '0')} м` : `${minutes} м`;
}

function Picker({ label, value, empty, options, onChange }: {
  label: string; value: string; empty: string;
  options: { id: string; name: string }[]; onChange: (value: string) => void;
}) {
  return (
    <label className="pick">
      <span className="visually-hidden">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{empty}</option>
        {options.map((option) => (
          <option key={option.id} value={option.id}>{option.name}</option>
        ))}
      </select>
    </label>
  );
}

function Section<T>({ block, name, children }: {
  block: Block<T>; name: string; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к разделу «{name}».</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить {name}. Это ошибка запроса, а не «данных нет».
      </p>
    );
  }
  return <>{children(block.data)}</>;
}
