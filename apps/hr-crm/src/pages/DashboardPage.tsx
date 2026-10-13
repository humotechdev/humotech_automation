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
import { longDate, shift, today, useBlock, type Block, type Refresh } from '../features/dashboard/data';
import { PAPER_ON_REVIEW, WAITING_PAPER, isLeave, paperOnReview, requestsHref, waitsForPaper } from '../features/requests/filters';
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
  '/api/v1/attendance/presence': { route: '/attendance', accepts: ['date', 'region_id', 'office_id', 'state', 'started'] },
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

  const [cards, , cardsRefresh] = useBlock((signal) => api.dashboard(filters, signal), key);
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

  const [table, , tableRefresh] = useBlock(
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
  const [open, , openRefresh] = useBlock(
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

  const [events, , eventsRefresh] = useBlock(
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

  // Обновление поверх уже показанных данных — только приглушение блока,
  // без пустого места и без счётчиков «с нуля».
  const busy = (block: Block<unknown>, refresh: Refresh) => block.state === 'ready' && refresh.busy;
  const anyBusy = busy(cards, cardsRefresh) || busy(chart, chartRefresh) || busy(table, tableRefresh)
    || busy(open, openRefresh) || busy(events, eventsRefresh);

  const stats = (pending: boolean) => (
    <ul className="hm-today" aria-label="Сегодня">
      <Stat icon="users" value={counts['active_employees']} title="сотрудников" pending={pending}
            note="в штате по выбранным офисам" to={cardLink(cardOf('active_employees'))} />
      <Stat icon="calendar" value={counts['should_work_today']} pending={pending}
            title={isToday ? 'по графику сегодня' : 'было по графику'}
            note={isToday ? 'должны работать сегодня' : 'должны были работать'}
            to={cardLink(cardOf('should_work_today'))} />
      <Stat icon="building" value={counts['in_office']} pending={pending}
            title={isToday ? 'сейчас в офисе' : 'в офисе'}
            note={isToday ? 'на рабочих местах' : 'не отметили уход'}
            to={cardLink(cardOf('in_office'))} />
      <Stat icon="alert" value={counts['not_come']} title="без отметки" warn pending={pending}
            note={isToday ? 'смена уже началась' : 'смена прошла без отметки'}
            to={cardLink(cardOf('not_come'))} />
    </ul>
  );

  const badges: Record<string, number> = {};
  if (open.state === 'ready' && open.data.absences.length + open.data.fixes > 0) {
    badges['requests'] = open.data.absences.length + open.data.fixes;
  }

  return (
    <AppShell breadcrumb="Главная" badges={badges}>
      <div className="hm">
        <section className="hm-sheet">
          {/* --- шапка --- */}
          {/* Секции появляются один раз — при открытии страницы. Смена
              фильтра и новые данные их не пересоздают и не проигрывают
              появление заново. */}
          <header className="hm-head hm-enter">
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
              <button type="button" className="hm-icon" aria-label="Обновить" aria-busy={anyBusy}
                      onClick={() => setAttempt((n) => n + 1)}>
                <AppIcon name="refresh" size={18} />
              </button>
            </div>
          </header>

          {/* --- строка «Сегодня» --- */}
          <div className={zone('hm-band hm-enter', busy(cards, cardsRefresh))} aria-busy={busy(cards, cardsRefresh)}>
            {/* Заглушка — тот же список с настоящими подписями: подписи
                известны заранее, неизвестны только числа. Поэтому высота
                строки при ответе не меняется ни на какой ширине. */}
            <Guard block={cards} name="показатели" className="hm-today hm-today--state" skeleton={stats(true)}>
              {() => stats(false)}
            </Guard>
          </div>

          {/* --- середина: ритм дня и фокус HR --- */}
          <div className="hm-row hm-enter">
            <section className={zone('hm-part', busy(chart, chartRefresh))} aria-label="Ритм дня"
                     aria-busy={busy(chart, chartRefresh)}>
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
              <Guard block={chart} name="явку" skeleton={<div className="hm-sk-chart" />}>
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
              {cards.state === 'loading' && (
                <p className="hm-note" aria-hidden="true"><i className="hm-sk hm-sk--note-line" /></p>
              )}
              {cards.state === 'ready' && (
                <p className="hm-note">
                  <AppIcon name="info" size={16} />
                  {/* Текст — одним элементом: иначе каждое слово и число
                      становятся отдельными частями flex-строки, и на
                      телефоне предложение не переносится, а вылезает вбок. */}
                  <span>
                  {(counts['should_work_today'] ?? 0) === 0
                    ? (isToday ? 'Сегодня по выбранным условиям нет сотрудников по графику.' : 'В этот день по выбранным условиям никто не работал по графику.')
                    : <>По графику <b>{counts['should_work_today']}</b> {people(counts['should_work_today'] ?? 0)}:
                        в офис пришли <b>{counts['came'] ?? 0}</b>, выездную работу подтвердили <b>{counts['field_work'] ?? 0}</b>.</>}
                  </span>
                </p>
              )}
            </section>

            <section className={zone('hm-part', busy(open, openRefresh))} aria-label="Фокус HR"
                     aria-busy={busy(open, openRefresh)}>
              <div className="hm-part__head">
                <h2 className="hm-part__title">Фокус HR</h2>
                <Link className="hm-link" to="/requests?status=open">Все задачи <AppIcon name="next" size={16} /></Link>
              </div>
              <Guard block={open} name="задачи" skeleton={<RowsSkeleton rows={3} className="hm-focus" />}>
                {(data) => {
                  // Число и ссылка — один отбор: правило счёта и параметры
                  // списка лежат рядом, в features/requests/filters.
                  const waitPaper = data.absences.filter(waitsForPaper).length;
                  const review = data.absences.filter(paperOnReview).length;
                  const leave = data.absences.filter(isLeave).length;
                  // «Без отметки» здесь — только у кого смена уже началась:
                  // отдельное число сервера, а не общее «не пришли».
                  const missing = isToday ? counts['not_come_started'] ?? 0 : 0;
                  const fieldPending = counts['field_work_pending'] ?? 0;
                  // Заявки считались по региону и офису страницы — с ними
                  // же и открываются, иначе список шире числа.
                  const all: FocusRow[] = [
                    { icon: 'doc', tone: 'bad', count: waitPaper, title: `${count(waitPaper, ['больничный ждёт', 'больничных ждут', 'больничных ждут'])} справку`,
                      note: 'До подтверждения не попадут в табель',
                      to: requestsHref({ tab: 'sick', ...WAITING_PAPER, ...place }) },
                    { icon: 'check', tone: 'plain', count: review, title: `${count(review, ['справка ждёт', 'справки ждут', 'справок ждут'])} проверки`,
                      note: 'Больничный подтверждается после проверки',
                      to: requestsHref({ tab: 'sick', ...PAPER_ON_REVIEW, ...place }) },
                    { icon: 'calendar', tone: 'plain', count: leave, title: `${count(leave, ['отпуск ждёт', 'отпуска ждут', 'отпусков ждут'])} решения`,
                      note: 'Проверить пересечение периодов', to: requestsHref({ tab: 'leave', status: 'open', ...place }) },
                    { icon: 'late', tone: 'plain', count: data.fixes, title: `${count(data.fixes, ['исправление отметки', 'исправления отметок', 'исправлений отметок'])}`,
                      note: 'Сотрудники просят поправить время', to: requestsHref({ tab: 'fixes', status: 'open', ...place }) },
                    { icon: 'alert', tone: 'warn', count: missing, title: `${count(missing, ['сотрудник', 'сотрудника', 'сотрудников'])} без отметки`,
                      note: 'Смена уже началась', to: cardLink(cardOf('not_come_started')) ?? '/attendance' },
                    { icon: 'calendar', tone: 'warn', count: fieldPending,
                      title: `${count(fieldPending, ['подтверждение выезда', 'подтверждения выезда', 'подтверждений выезда'])} ожидается`,
                      note: 'Сотрудник ещё не ответил в Telegram',
                      to: cardLink(cardOf('field_work_pending')) ?? '/attendance' },
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
          <div className="hm-row hm-row--low hm-enter">
            <section className={zone('hm-part', busy(table, tableRefresh))} aria-label="Офисы сегодня"
                     aria-busy={busy(table, tableRefresh)}>
              <div className="hm-part__head">
                <h2 className="hm-part__title">{isToday ? 'Офисы сегодня' : 'Офисы за день'}</h2>
                <Link className="hm-link" to="/analytics?tab=attendance">Сравнить офисы <AppIcon name="next" size={16} /></Link>
              </div>
              <Guard block={table} name="офисы" skeleton={<TableSkeleton />}>
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

            <section className={zone('hm-part', busy(events, eventsRefresh))} aria-label="Последние события"
                     aria-busy={busy(events, eventsRefresh)}>
              <div className="hm-part__head">
                <h2 className="hm-part__title">Последние события</h2>
              </div>
              <Guard block={events} name="события" skeleton={<RowsSkeleton rows={4} className="hm-events" small />}>
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
          <nav className="hm-links hm-enter" aria-label="Быстрые переходы">
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

function Stat({ icon, value, title, note, to, warn, pending }: {
  icon: AppIconName;
  value: number | undefined;
  title: string;
  note: string;
  to: string | null;
  warn?: boolean;
  /** Число ещё не пришло: на его месте полоса заглушки. */
  pending?: boolean;
}) {
  const hot = !pending && warn && (value ?? 0) > 0;
  const inside = (
    <>
      <span className={hot ? 'hm-stat__icon hm-stat__icon--warn' : 'hm-stat__icon'} aria-hidden="true">
        <AppIcon name={icon} size={20} />
      </span>
      <span className="hm-stat__body">
        <span className="hm-stat__line">
          <b className={hot ? 'hm-stat__value hm-warn' : 'hm-stat__value'}>
            {pending ? <i className="hm-sk hm-sk--value" /> : value ?? '—'}
          </b>
          <span className={hot ? 'hm-stat__title hm-warn' : 'hm-stat__title'}>{title}</span>
        </span>
        <small className="hm-stat__note">{note}</small>
      </span>
    </>
  );
  // Ноль ссылкой не делается: открывать пустой список незачем.
  return (
    <li>
      {!pending && to && (value ?? 0) > 0 ? <Link className="hm-stat hm-stat--go" to={to}>{inside}</Link> : <div className="hm-stat">{inside}</div>}
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
      setWidth(Math.max(240, node.clientWidth));
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
 *
 * Пока данных не было ни разу, на месте блока стоит заглушка по его
 * форме: серые полосы там, где будут числа и строки, без выдуманных
 * значений. Читалке экрана заглушка говорит словами «Загружаем …».
 * Данные, пришедшие на смену заглушке, проявляются (только прозрачность,
 * без сдвига). Данные, бывшие на экране сразу — из памяти вкладки, —
 * и обновления поверх них не проявляются заново.
 */
function Guard<T>({ block, name, children, className, skeleton }: {
  block: Block<T>;
  name: string;
  children: (data: T) => ReactNode;
  className?: string;
  skeleton?: ReactNode;
}) {
  const waited = useRef(block.state === 'loading');
  if (block.state === 'loading') waited.current = true;
  if (block.state === 'ready') {
    return waited.current ? <div className="hm-in">{children(block.data)}</div> : <>{children(block.data)}</>;
  }
  if (block.state === 'loading' && skeleton) {
    return (
      <>
        <span className="visually-hidden" role="status">{`Загружаем ${name}…`}</span>
        <div className="hm-sk-wrap" aria-hidden="true">{skeleton}</div>
      </>
    );
  }
  const text = block.state === 'loading'
    ? `Загружаем ${name}…`
    : block.state === 'denied'
      ? `Нет доступа к разделу «${name}».`
      : `Не удалось загрузить ${name}. Данные не показаны — это не ноль.`;
  return <p className={['hm-empty', block.state === 'error' ? 'hm-empty--bad' : '', className ?? ''].filter(Boolean).join(' ')}>{text}</p>;
}

/** Класс блока с признаком «обновляется поверх показанного». */
const zone = (base: string, refreshing: boolean) => (refreshing ? `${base} hm-busy` : base);

/** Заглушка списка: значок и две строки — как у строк «Фокуса HR» и ленты. */
function RowsSkeleton({ rows, className, small }: { rows: number; className: string; small?: boolean }) {
  return (
    <ul className={`${className} hm-sk-rows`}>
      {Array.from({ length: rows }, (_, n) => (
        <li key={n}>
          <span className={small ? 'hm-sk-row hm-sk-row--small' : 'hm-sk-row'}>
            <i className="hm-sk hm-sk--mark" />
            <span className="hm-sk-row__text">
              <i className="hm-sk hm-sk--line" />
              <i className="hm-sk hm-sk--short" />
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Заглушка таблицы офисов: настоящая шапка и строки той же высоты. */
function TableSkeleton() {
  return (
    <div className="hm-table">
      <div className="hm-table__head">
        <span>Офис</span><span>По графику</span><span>В офисе</span><span>Нет отметки</span><span>Отсутствуют</span>
        <span />
      </div>
      <div className="hm-table__body">
        {[0, 1, 2].map((n) => (
          <div key={n} className="hm-table__row hm-sk-rows">
            <span className="hm-table__name"><i className="hm-sk hm-sk--dot" /><i className="hm-sk hm-sk--name" /></span>
            {[0, 1, 2, 3].map((k) => <span key={k}><i className="hm-sk hm-sk--num" /></span>)}
            <span />
          </div>
        ))}
      </div>
    </div>
  );
}

// --- мелочи ---------------------------------------------------------------------

function byKey(cards: api.Card[]): Record<string, number> {
  return Object.fromEntries(cards.map((card) => [card.key, card.value]));
}

/** По графику: в офисе, на выезде и ещё без подтверждённой отметки. */
const expected = (c: Record<string, number>) =>
  (c['IN_OFFICE'] ?? 0) + (c['LEFT'] ?? 0) + (c['FIELD_WORK'] ?? 0)
  + (c['FIELD_WORK_PENDING'] ?? 0) + (c['NOT_COME'] ?? 0) + (c['LATE'] ?? 0);

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
