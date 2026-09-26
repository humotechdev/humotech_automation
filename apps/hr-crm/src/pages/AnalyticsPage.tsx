/**
 * «Аналитика»: один лист, семь вкладок — обзор, посещаемость, отсутствия
 * и заявки, команда, стажировки, опросы, ознакомления.
 *
 * Лист, шапка, строка фильтров, вкладки и полоса показателей —
 * постоянные: при смене вкладки они не пересоздаются и не меняют высоту.
 * Меняется содержимое, линия под вкладкой переезжает. Каждая вкладка
 * грузит только своё; пока идёт загрузка, серые заглушки стоят только в
 * месте данных.
 *
 * Ни одна величина не придумана. Явку, опоздания, время и отсутствия
 * считает сервер; численность, приём, уход и переводы — тоже он, по
 * датам карточек и журналу. Выводы собираются только из настоящих
 * разниц: нет разницы — нет и фразы про рост.
 *
 * Отсутствием считается только подтверждённое. «Нет отметки» — не
 * прогул, а вопрос к дню.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { Dropdown } from '../components/AppSelect';
import { DatePicker } from '../components/DatePicker';
import { SlideTabs } from '../components/SlideTabs';
import { shift, today, useBlock, type Block } from '../features/dashboard/data';
import { Bars, LineChart, MonthCalendar, dayLong, dayShort, type BarRow, type CalendarKind, type LinePoint } from '../features/analytics/charts';
import { kindOf, statusOf } from '../features/requests/model';
import { AUDIENCE } from '../features/surveys/model';
import '../styles/analytics.css';

type Tab = 'overview' | 'attendance' | 'absences' | 'team' | 'probation' | 'surveys' | 'onboarding';

const TABS: { key: Tab; title: string }[] = [
  { key: 'overview', title: 'Обзор' },
  { key: 'attendance', title: 'Посещаемость' },
  { key: 'absences', title: 'Отсутствия и заявки' },
  { key: 'team', title: 'Команда' },
  { key: 'probation', title: 'Стажировки' },
  { key: 'surveys', title: 'Опросы' },
  { key: 'onboarding', title: 'Ознакомления' },
];

type By = 'offices' | 'regions' | 'departments' | 'positions' | 'heads' | 'previous';

const BY: { id: By; name: string; title: string }[] = [
  { id: 'regions', name: 'По регионам', title: 'Явка по регионам' },
  { id: 'departments', name: 'По отделам', title: 'Явка по отделам' },
  { id: 'positions', name: 'По должностям', title: 'Явка по должностям' },
  { id: 'heads', name: 'По руководителям', title: 'Явка по руководителям' },
  { id: 'previous', name: 'С прошлым периодом', title: 'Этот период и прошлый' },
];

/** Какой вид выгрузки у вкладки. У опросов и ознакомлений своего вида нет. */
const EXPORTS: Record<Tab, api.ReportKindKey | null> = {
  overview: 'attendance',
  attendance: 'attendance',
  absences: 'absences',
  team: 'employees',
  probation: 'employees',
  surveys: null,
  onboarding: null,
};

const OPEN = 'SUBMITTED,IN_REVIEW';

function tabOf(params: URLSearchParams): Tab {
  const raw = params.get('tab');
  return TABS.some((one) => one.key === raw) ? (raw as Tab) : 'overview';
}

// --- общие мелочи -------------------------------------------------------------

type Scope = {
  from: string;
  to: string;
  /** Прошлый период той же длины, вплотную перед выбранным. */
  prevFrom: string;
  prevTo: string;
  region: string;
  office: string;
  department: string;
  /** Для запросов: офис важнее региона, отдел сужает состав. */
  place: { region_id?: string; office_id?: string };
  narrow: { department_id?: string };
  key: string;
};

type Ctx = {
  scope: Scope;
  compare: boolean;
  by: By;
  attempt: number;
  offices: api.Office[];
  regions: api.Region[];
  departments: { id: string; name: string }[];
};

type View = {
  sub: string;
  kpis: ReactNode;
  body: ReactNode;
};

/** Ключ повтора заказа: повторное нажатие после сбоя не заказывает второй файл. */
function requestKey(): string {
  const source = globalThis.crypto;
  if (source && typeof source.randomUUID === 'function') return source.randomUUID();
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

function days(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000) + 1;
}

function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—';
  const minutes = Math.round(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h ? `${h} ч ${String(m).padStart(2, '0')} мин` : `${m} мин`;
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${Math.round(value)}%`;
}

function share(n: number, total: number): number | null {
  return total ? (n * 100) / total : null;
}

function joined<T extends unknown[]>(...blocks: { [K in keyof T]: Block<T[K]> }): Block<T> {
  const list = blocks as Block<unknown>[];
  if (list.some((b) => b.state === 'denied')) return { state: 'denied' };
  const failed = list.find((b) => b.state === 'error');
  if (failed) return failed as Block<T>;
  if (list.some((b) => b.state === 'loading')) return { state: 'loading' };
  return { state: 'ready', data: list.map((b) => (b as { data: unknown }).data) as T };
}

/** Все страницы списка, но не больше `cap` строк: аналитике хватает. */
async function pages<T>(load: (cursor: string | undefined) => Promise<api.Cursored<T>>, cap = 1000): Promise<T[]> {
  const rows: T[] = [];
  let cursor: string | undefined;
  do {
    const page = await load(cursor);
    rows.push(...page.items);
    cursor = page.has_more && page.next_cursor ? page.next_cursor : undefined;
  } while (cursor && rows.length < cap);
  return rows;
}

type Tone = 'blue' | 'green' | 'amber' | 'red' | 'violet' | 'sky' | 'grey';

/** Разница с прошлым периодом: стрелка, число и цвет «лучше/хуже». */
function Delta({ value, unit, good, text }: { value: number | null | undefined; unit: 'pp' | 'n' | 'min' | 'day' | 'pct'; good: 'up' | 'down' | 'none'; text?: string }) {
  if (value === null || value === undefined) {
    return <span className="ax-delta ax-delta--flat">— <em>{text ?? 'нет данных за прошлый период'}</em></span>;
  }
  const rounded = unit === 'pp' ? Math.round(value * 10) / 10 : Math.round(value);
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '';
  const size = Math.abs(rounded);
  const shown = unit === 'pp' ? `${sign}${String(size).replace('.', ',')} п.п.`
    : unit === 'min' ? `${sign}${size} мин`
      : unit === 'day' ? `${sign}${size} ${plural(size, 'день', 'дня', 'дней')}`
        : unit === 'pct' ? `${sign}${size}%`
          : `${sign}${size}`;
  const tone = rounded === 0 || good === 'none' ? 'flat' : (rounded > 0) === (good === 'up') ? 'good' : 'bad';
  return (
    <span className={`ax-delta ax-delta--${tone}`}>
      {rounded !== 0 && <AppIcon name={rounded > 0 ? 'trend-up' : 'trend-down'} size={16} />}
      {rounded === 0 ? '0' : shown} <em>{text ?? 'к предыдущему периоду'}</em>
    </span>
  );
}

type KpiProps = { icon: AppIconName; tone: Tone; value: ReactNode; label: ReactNode; foot?: ReactNode };

function Kpi({ icon, tone, value, label, foot }: KpiProps) {
  return (
    <div className="ax-kpi">
      <span className={`ax-kpi__icon ax-kpi__icon--${tone}`}><AppIcon name={icon} size={20} /></span>
      <div className="ax-kpi__text">
        <b className="ax-kpi__value">{value}</b>
        <span className="ax-kpi__label">{label}</span>
        {foot && <span className="ax-kpi__foot">{foot}</span>}
      </div>
    </div>
  );
}

/** Полоса из четырёх показателей. Пока грузится — заглушки той же высоты. */
function Kpis<T>({ block, children }: { block: Block<T>; children: (data: T) => KpiProps[] }) {
  if (block.state !== 'ready') {
    return (
      <div className="ax-kpis" aria-busy={block.state === 'loading'}>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="ax-kpi">
            <span className="ax-ghost ax-ghost--icon" />
            <div className="ax-kpi__text">
              {block.state === 'loading'
                ? <><span className="ax-ghost ax-ghost--value" /><span className="ax-ghost" /></>
                : <span className="ax-kpi__label">{i === 0 ? failure(block) : ''}</span>}
            </div>
          </div>
        ))}
      </div>
    );
  }
  return <div className="ax-kpis">{children(block.data).map((one, i) => <Kpi key={i} {...one} />)}</div>;
}

function failure(block: Block<unknown>): string {
  if (block.state === 'denied') return 'Нет прав на этот раздел';
  if (block.state === 'error') return 'Не удалось загрузить данные';
  return '';
}

/** Место данных: заглушки при загрузке, причина при ошибке. */
function Guard<T>({ block, rows = 4, children }: { block: Block<T>; rows?: number; children: (data: T) => ReactNode }) {
  if (block.state === 'ready') return <>{children(block.data)}</>;
  if (block.state === 'loading') {
    return (
      <div className="ax-skeleton" aria-busy="true" aria-label="Загрузка">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="ax-skeleton__row"><span className="ax-ghost" /><span className="ax-ghost ax-ghost--short" /></div>
        ))}
      </div>
    );
  }
  return <p className="ax-empty ax-empty--error" role="alert">{failure(block)}</p>;
}

function Section({ title, sub, action, children, className }: { title: string; sub?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`ax-sec${className ? ` ${className}` : ''}`} aria-label={title}>
      <header className="ax-sec__head">
        <div>
          <h2 className="ax-sec__title">{title}</h2>
          {sub && <p className="ax-sec__sub">{sub}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}

function Conclusion({ title = 'Ключевой вывод', children }: { title?: string; children: ReactNode }) {
  return (
    <div className="ax-note">
      <span className="ax-note__icon"><AppIcon name="bulb" size={20} /></span>
      <b>{title}</b>
      <span className="ax-note__text">{children}</span>
    </div>
  );
}

function Person({ name }: { name: string }) {
  return (
    <span className="ax-person">
      <span className="ax-person__avatar" aria-hidden="true">{initials(name)}</span>
      <span className="ax-person__name">{name}</span>
    </span>
  );
}

function Status({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`ax-status ax-status--${tone}`}><i aria-hidden="true" />{children}</span>;
}

function Progress({ value }: { value: number | null }) {
  return (
    <span className="ax-progress">
      <span className="ax-progress__text">{pct(value)}</span>
      <span className="ax-progress__track"><span style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} /></span>
    </span>
  );
}

// --- страница -------------------------------------------------------------------

export function AnalyticsPage() {
  const [params, setParams] = useSearchParams();
  const tab = tabOf(params);
  const [attempt, setAttempt] = useState(0);

  const patch = useCallback((changes: Record<string, string | null>) => {
    setParams((was) => {
      const next = new URLSearchParams(was);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    }, { replace: true });
  }, [setParams]);

  const now = today();
  const to = params.get('to') ?? now;
  const from = params.get('from') ?? shift(to, -29);
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const department = params.get('department_id') ?? '';
  const compare = params.get('compare') === '1';
  const by = (BY.some((one) => one.id === params.get('by')) ? params.get('by') : 'offices') as By;

  const scope = useMemo<Scope>(() => {
    const length = days(from, to);
    const prevTo = shift(from, -1);
    return {
      from, to, prevFrom: shift(prevTo, -(length - 1)), prevTo, region, office, department,
      place: office ? { office_id: office } : region ? { region_id: region } : {},
      narrow: department ? { department_id: department } : {},
      key: `${from}|${to}|${region}|${office}|${department}|${attempt}`,
    };
  }, [from, to, region, office, department, attempt]);

  const [directory] = useBlock(
    (signal) => Promise.all([
      api.regions(signal),
      api.offices(signal),
      api.departmentsPage({ limit: '200', status: 'ACTIVE' }, signal),
    ]).then(([regions, offices, departments]) => ({
      regions: regions.items.filter((one) => one.status === 'ACTIVE'),
      offices: offices.items.filter((one) => one.status === 'ACTIVE'),
      departments: departments.items.map((one) => ({ id: one.id, name: one.name })),
    })),
    'analytics-directory',
  );
  const lists = directory.state === 'ready' ? directory.data : { regions: [], offices: [], departments: [] };

  const ctx: Ctx = { scope, compare, by, attempt, ...lists };
  const views: Record<Tab, View> = {
    overview: useOverviewTab(tab === 'overview', ctx),
    attendance: useAttendanceTab(tab === 'attendance', ctx),
    absences: useAbsencesTab(tab === 'absences', ctx),
    team: useTeamTab(tab === 'team', ctx),
    probation: useProbationTab(tab === 'probation', ctx),
    surveys: useSurveysTab(tab === 'surveys', ctx),
    onboarding: useOnboardingTab(tab === 'onboarding', ctx),
  };
  const view = views[tab];

  // Выгрузка — в общую очередь отчётов; файл появится в «Отчётах».
  const [ordered, setOrdered] = useState<string | null>(null);
  useEffect(() => {
    if (!ordered) return;
    const timer = window.setTimeout(() => setOrdered(null), 6000);
    return () => window.clearTimeout(timer);
  }, [ordered]);
  const kind = EXPORTS[tab];
  // Заказ идёт через конструктор отчётов: простая выгрузка не знает
  // отдела, и файл разошёлся бы с экраном, отфильтрованным по отделу.
  const order = () => {
    if (!kind) return;
    setOrdered(null);
    api.reportCatalog()
      .then((catalog) => {
        const fields = catalog.kinds.find((one) => one.key === kind)?.fields.filter((one) => one.default).map((one) => one.key) ?? [];
        return api.orderExport({
          kind, fmt: 'xlsx', builder: true, client_request_id: requestKey(), period: 'custom',
          date_from: from, date_to: to, region_id: office ? null : region || null,
          office_ids: office ? [office] : [], department_ids: department ? [department] : [],
          employee_id: null, include_inactive: false, fields, name: null,
        });
      })
      .then(() => setOrdered('Выгрузка поставлена в очередь'))
      .catch((error) => setOrdered(messageFor(error)));
  };

  const officesHere = region ? lists.offices.filter((one) => one.region_id === region) : lists.offices;

  return (
    <AppShell breadcrumb="Аналитика" section="analytics">
      <div className="ax">
        <section className="ax-sheet">
          <header className="ax-head">
            <h1 className="ax-head__title">Аналитика</h1>
            <p className="ax-head__sub">{view.sub}</p>
            {ordered && (
              <p className="ax-toast" role="status">
                {ordered} — <Link to="/reports">файл появится в отчётах</Link>
              </p>
            )}
          </header>

          <div className="ax-filters">
            <span className="ax-range">
              <DatePicker label="Начало периода" value={from} now={now} max={to}
                          onChange={(value) => patch({ from: value || null })} />
              <span aria-hidden="true">—</span>
              <DatePicker label="Конец периода" value={to} now={now} min={from} max={now}
                          onChange={(value) => patch({ to: value || null })} />
            </span>
            <Dropdown label="Регион" empty="Все регионы" value={region}
                      options={lists.regions.map((one) => ({ id: one.id, name: one.name }))}
                      onChange={(value) => patch({ region_id: value || null, office_id: null })} />
            <Dropdown label="Офис" empty="Все офисы" value={office}
                      options={officesHere.map((one) => ({ id: one.id, name: one.name }))}
                      onChange={(value) => patch({ office_id: value || null })} />
            <Dropdown label="Отдел" empty="Все отделы" value={department} options={lists.departments}
                      onChange={(value) => patch({ department_id: value || null })} />
            <button type="button" role="switch" aria-checked={compare} className="ax-switch"
                    onClick={() => patch({ compare: compare ? null : '1' })}>
              <span className="ax-switch__track" aria-hidden="true"><span /></span>
              Сравнить с предыдущим периодом
            </button>
            <span className="ax-by">
              <Dropdown label="Сравнить по" empty="По офисам" value={by === 'offices' ? '' : by}
                        options={BY.map(({ id, name }) => ({ id, name }))}
                        onChange={(value) => patch({ by: value || null })} />
            </span>
            <span className="ax-filters__end">
              <button type="button" className="ax-icon" aria-label="Обновить" onClick={() => setAttempt((n) => n + 1)}>
                <AppIcon name="refresh" size={18} />
              </button>
              <button type="button" className="ax-export" onClick={order} disabled={!kind}
                      title={kind ? 'Выгрузить в Excel' : 'У этой вкладки нет своей выгрузки: файл рассылки или ознакомления выгружается в его разделе'}>
                <AppIcon name="download" size={18} /> Экспорт
              </button>
            </span>
          </div>

          <SlideTabs
            label="Разделы аналитики"
            value={tab}
            items={TABS}
            onPick={(key) => patch({ tab: key === 'overview' ? null : key })}
            classes={{ list: 'ax-tabs', tab: 'ax-tab', on: 'ax-tab--on', ink: 'ax-tabs__ink' }}
          />

          <div className="ax-place">
            <div key={tab} className="ax-pane" role="tabpanel" aria-label={TABS.find((one) => one.key === tab)?.title}>
              {view.kpis}
              <div className="ax-body">{view.body}</div>
            </div>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

// --- данные, общие для нескольких вкладок ---------------------------------------

/** Сводка периода и такая же сводка прошлого — для разниц, которых нет в первой. */
function useOverviewPair(active: boolean, { scope }: Ctx, limit = '300') {
  const query = { date_from: scope.from, date_to: scope.to, ...scope.place, ...scope.narrow };
  const [now] = useBlock((signal) => api.analyticsOverview({ ...query, people_limit: limit }, signal), `ov|${scope.key}|${limit}`, active);
  const [was] = useBlock(
    (signal) => api.analyticsOverview({ ...query, date_from: scope.prevFrom, date_to: scope.prevTo, people_limit: '1' }, signal),
    `ov-prev|${scope.key}`,
    active,
  );
  return [now, was] as const;
}

function useTeam(active: boolean, { scope }: Ctx) {
  const [team] = useBlock(
    (signal) => api.analyticsTeam({ date_from: scope.from, date_to: scope.to, ...scope.place, ...scope.narrow }, signal),
    `team|${scope.key}`,
    active,
  );
  return team;
}

function useProbation(active: boolean, { scope }: Ctx) {
  const [probation] = useBlock(
    (signal) => api.analyticsProbation({ date_from: scope.from, date_to: scope.to, ...scope.place, ...scope.narrow }, signal),
    `probation|${scope.key}`,
    active,
  );
  return probation;
}

function officeName(ctx: Ctx, id: string | null | undefined): string {
  return ctx.offices.find((one) => one.id === id)?.name ?? '—';
}

function absencesOf(summary: api.Overview['summary']): number {
  return summary.vacation_days + summary.sick_leave_days + summary.other_absence_days + (summary.trip_days ?? 0);
}

function attendancePoints(data: api.Overview): LinePoint[] {
  return data.days.map((day, i) => ({
    day: day.day,
    value: day.future ? null : day.percent,
    previous: data.previous_days[i]?.percent ?? null,
  }));
}

// --- Обзор ------------------------------------------------------------------------

type Attention = { key: string; icon: AppIconName; tone: Tone; title: string; who: string; date: string; badge: string; rank: number; to: string };

/** Ход ознакомления — общий для обзора, команды и стажировок: один запрос на всех. */
function useOnboardingRows(active: boolean, { scope }: Ctx) {
  const [rows] = useBlock(
    (signal) => pages((cursor) => api.onboardingProgress({ limit: '200', ...(scope.office ? { office_id: scope.office } : {}), ...(cursor ? { cursor } : {}) }, signal), 1000)
      .catch(() => [] as api.OnboardingRow[]),
    `onb-rows|${scope.key}`,
    active,
  );
  return rows;
}

function useOverviewTab(active: boolean, ctx: Ctx): View {
  const { scope } = ctx;
  const [overview, previous] = useOverviewPair(active, ctx);
  const team = useTeam(active, ctx);
  const probation = useProbation(active, ctx);
  const onboarding = useOnboardingRows(active, ctx);
  const [open] = useBlock(
    (signal) => pages((cursor) => api.queue({ status: OPEN, limit: '200', ...scope.place, ...(cursor ? { cursor } : {}) }, signal), 400),
    `open|${scope.key}`,
    active,
  );
  // Подтверждённые отсутствия периода — для событий «заявка одобрена» и «вышел из отпуска».
  const [approved] = useBlock(
    (signal) => pages((cursor) => api.queue({ kind: 'absence', status: 'APPROVED', date_from: scope.from, date_to: scope.to, limit: '200', ...scope.place, ...(cursor ? { cursor } : {}) }, signal), 400),
    `approved|${scope.key}`,
    active,
  );

  const all = joined(overview, previous, team);

  // Одно правило «требует внимания» на список и на показатель над ним.
  const attention = joined(overview, open, probation, onboarding);
  const items: Attention[] = attention.state === 'ready' ? (() => {
    const [ov, queue, trial, rows] = attention.data;
    const list: Attention[] = [];
    for (const person of ov.employees) {
      if (person.missed_days <= 0) continue;
      const last = person.missed_dates?.[person.missed_dates.length - 1] ?? scope.to;
      list.push({
        key: `m-${person.id}`, icon: 'alert', tone: 'red', title: 'Нет отметки о явке',
        who: `${person.name} · ${officeName(ctx, person.office_id)}`, date: last,
        badge: `${person.missed_days} ${plural(person.missed_days, 'день', 'дня', 'дней')}`, rank: 0,
        to: `/attendance?date=${last}&employee=${person.id}`,
      });
    }
    for (const item of queue) {
      const submitted = item.absence?.submitted_at ?? item.correction?.submitted_at ?? item.created_at;
      const waited = days(submitted.slice(0, 10), today()) - 1;
      if (waited < 3) continue;
      const who = item.absence?.employee.full_name ?? item.correction?.employee?.full_name ?? '—';
      list.push({
        key: `q-${item.id}`, icon: 'doc', tone: 'amber', title: `Заявка ждёт решения: ${kindOf(item).title.toLowerCase()}`,
        who, date: submitted.slice(0, 10), badge: `${waited} ${plural(waited, 'день', 'дня', 'дней')} ждёт`, rank: 1,
        to: `/requests/${item.id}`,
      });
    }
    for (const person of trial.trainees) {
      if (person.days_left === null || person.days_left > trial.due_days) continue;
      list.push({
        key: `p-${person.id}`, icon: 'grad', tone: person.days_left < 0 ? 'red' : 'amber',
        title: person.days_left < 0 ? 'Стажировка закончилась, решения нет' : `Стажировка заканчивается через ${person.days_left} ${plural(person.days_left, 'день', 'дня', 'дней')}`,
        who: `${person.name} · ${person.department ?? person.office ?? '—'}`, date: person.probation_to ?? scope.to,
        badge: person.days_left < 0 ? 'Срок прошёл' : person.days_left === 0 ? 'Сегодня' : `Через ${person.days_left} ${plural(person.days_left, 'день', 'дня', 'дней')}`,
        rank: person.days_left < 0 ? 0 : 1, to: `/employees/${person.id}`,
      });
    }
    for (const row of rows) {
      if (row.status !== 'BLOCKED_BY_DECLINED_POLICY') continue;
      list.push({
        key: `o-${row.employee_id}`, icon: 'doc', tone: 'red', title: 'Документ не подтверждён',
        who: `${row.full_name} · ${row.department_name ?? row.office_name ?? '—'}`, date: (row.invited_at ?? `${scope.to}T`).slice(0, 10),
        badge: 'Не пройден', rank: 0, to: `/employees/${row.employee_id}`,
      });
    }
    return list.sort((a, b) => a.rank - b.rank || b.date.localeCompare(a.date));
  })() : [];

  const kpis = (
    <Kpis block={joined(all, attention)}>
      {([[ov, was, people]]) => [
        {
          icon: 'users', tone: 'blue', value: people.summary.headcount,
          label: `${plural(people.summary.headcount, 'активный сотрудник', 'активных сотрудника', 'активных сотрудников')}`,
          foot: <Delta value={people.summary.headcount - people.summary.headcount_start} unit="n" good="up" text="за период" />,
        },
        {
          icon: 'clock', tone: 'blue', value: pct(ov.summary.attendance.percent), label: 'явка',
          foot: <Delta value={ov.summary.difference_points} unit="pp" good="up" />,
        },
        {
          icon: 'calendar', tone: 'amber', value: absencesOf(ov.summary),
          label: `${plural(absencesOf(ov.summary), 'день', 'дня', 'дней')} подтверждённых отсутствий`,
          foot: <Delta value={absencesOf(ov.summary) - absencesOf(was.summary)} unit="n" good="none" text={`${ov.summary.vacation_days} отпуск · ${ov.summary.sick_leave_days} больничный`} />,
        },
        {
          icon: 'warning', tone: items.length ? 'red' : 'green', value: items.length, label: 'требуют внимания',
          foot: <span className="ax-muted">список — ниже, под графиком</span>,
        },
      ]}
    </Kpis>
  );

  const body = (
    <div className="ax-grid ax-grid--side">
      <div className="ax-col">
        <Section title="Что происходит сейчас" sub="Явка по дням за выбранный период"
                 action={ctx.compare ? <Legend /> : undefined}>
          <Guard block={overview} rows={5}>
            {(data) => <LineChart points={attendancePoints(data)} height={176} max={100} format={(v) => `${Math.round(v)}%`} label="Явка по дням" compare={ctx.compare} />}
          </Guard>
        </Section>
        <Section title="Требует внимания" className="ax-sec--line"
                 action={items.length > 5 ? <span className="ax-muted">ещё {items.length - 5}</span> : undefined}>
          <Guard block={attention} rows={4}>
            {() => items.length ? (
              <ul className="ax-attn">
                {items.slice(0, 5).map((item) => (
                  <li key={item.key} className="ax-attn__row">
                    <span className={`ax-attn__icon ax-tone--${item.tone}`}><AppIcon name={item.icon} size={20} /></span>
                    <span className="ax-attn__text"><Link to={item.to}>{item.title}</Link><small>{item.who}</small></span>
                    <span className="ax-attn__date">{dayLong(item.date)}</span>
                    <span className={`ax-attn__badge ax-tone--${item.tone}`}>{item.badge}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="ax-empty">Всё спокойно: пропусков, зависших заявок, стажировок на исходе и неподтверждённых документов нет.</p>}
          </Guard>
        </Section>
      </div>
      <div className="ax-col ax-col--side">
        <Section title="Ключевые события" sub="Кадровые изменения и отсутствия периода"
                 action={<Link className="ax-link" to="/analytics?tab=team">Все события <AppIcon name="arrow" size={16} /></Link>}>
          <Guard block={joined(team, approved)} rows={5}>
            {([data, absences]) => <Timeline team={data} absences={absences} />}
          </Guard>
        </Section>
      </div>
      <div className="ax-foot">
        <Guard block={team} rows={1}>
          {(data) => (
            <div className="ax-strip">
              <div className="ax-strip__title"><b>Изменения в команде</b><span>Сравнение с предыдущим периодом</span></div>
              <StripItem icon="user-plus" tone="green" value={data.summary.hired} label={plural(data.summary.hired, 'новый сотрудник', 'новых сотрудника', 'новых сотрудников')}
                         delta={<Delta value={data.summary.hired - data.summary.previous_hired} unit="n" good="up" text="" />} />
              <StripItem icon="user-ok" tone="blue" value={data.summary.promoted} label="принято в штат после стажировки"
                         delta={<Delta value={data.summary.promoted - data.summary.previous_promoted} unit="n" good="up" text="" />} />
              <StripItem icon="user-x" tone="red" value={data.summary.left} label={plural(data.summary.left, 'увольнение', 'увольнения', 'увольнений')}
                         delta={<Delta value={data.summary.left - data.summary.previous_left} unit="n" good="down" text="" />} />
              <StripItem icon="transfer" tone="blue" value={data.transfers.length} label={plural(data.transfers.length, 'перевод', 'перевода', 'переводов')} />
            </div>
          )}
        </Guard>
      </div>
    </div>
  );

  return { sub: 'Картина команды за выбранный период', kpis, body };
}

function Legend() {
  return <span className="ax-legend"><i className="ax-legend__now" />Текущий период<i className="ax-legend__was" />Предыдущий период</span>;
}

function StripItem({ icon, tone, value, label, delta }: { icon: AppIconName; tone: Tone; value: number; label: string; delta?: ReactNode }) {
  return (
    <div className="ax-strip__item">
      <span className={`ax-strip__icon ax-tone--${tone}`}><AppIcon name={icon} size={20} /></span>
      <span><b>{value}</b><small>{label}</small>{delta}</span>
    </div>
  );
}

function Timeline({ team, absences }: { team: api.TeamReport; absences: api.QueueItem[] }) {
  const now = today();
  const events = [
    ...team.hires.map((one) => ({ key: `h-${one.id}`, date: one.hire_date, tone: 'blue' as Tone, title: 'Новый сотрудник', text: one.name, place: [one.department, one.office].filter(Boolean).join(' · ') })),
    ...team.departures.map((one) => ({ key: `d-${one.id}`, date: one.termination_date, tone: 'red' as Tone, title: 'Увольнение', text: one.name, place: one.reason ?? '' })),
    ...team.transfers.map((one) => ({ key: `t-${one.id}-${one.date}`, date: one.date, tone: 'blue' as Tone, title: one.from_office !== one.to_office ? 'Перевод в другой офис' : 'Перевод в другой отдел', text: one.name, place: one.from_office !== one.to_office ? `${one.from_office} → ${one.to_office}` : `${one.from_department ?? 'Без отдела'} → ${one.to_department ?? 'Без отдела'}` })),
    ...absences.flatMap((item) => {
      const a = item.absence;
      if (!a) return [];
      const out = [];
      if (a.reviewed_at) {
        out.push({ key: `a-${item.id}`, date: a.reviewed_at.slice(0, 10), tone: 'amber' as Tone, title: 'Заявка одобрена', text: `${a.absence_type.name} · ${a.employee.full_name}`, place: a.first_day && a.last_day ? `${dayShort(a.first_day)} – ${dayShort(a.last_day)}` : '' });
      }
      // Вернулся — день после последнего дня отпуска, если он уже прошёл.
      if (a.absence_type.code === 'ANNUAL_LEAVE' && a.last_day && shift(a.last_day, 1) <= now) {
        out.push({ key: `r-${item.id}`, date: shift(a.last_day, 1), tone: 'green' as Tone, title: 'Выход из отпуска', text: a.employee.full_name, place: '' });
      }
      return out;
    }),
  ].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 5);
  if (!events.length) return <p className="ax-empty">За период кадровых изменений и отсутствий не было.</p>;
  return (
    <ol className="ax-timeline">
      {events.map((one) => (
        <li key={one.key} className="ax-timeline__row">
          <i className={`ax-timeline__dot ax-dot--${one.tone}`} aria-hidden="true" />
          <span className="ax-timeline__date">{dayLong(one.date)}</span>
          <span className="ax-timeline__text"><b>{one.title}</b><small>{one.text}</small>{one.place && <small>{one.place}</small>}</span>
        </li>
      ))}
    </ol>
  );
}

// --- Посещаемость ---------------------------------------------------------------

/** Состояние на сейчас словами: как в разделе «Посещаемость». */
function nowState(row: api.PresenceRow | undefined): { title: string; tone: Tone } {
  if (!row) return { title: '—', tone: 'grey' };
  const late = row.late_minutes ?? 0;
  switch (row.state) {
    case 'IN_OFFICE': return late > 0 ? { title: `Опоздание ${late} мин`, tone: 'amber' } : { title: 'В офисе', tone: 'green' };
    case 'LEFT': return { title: 'Смена завершена', tone: 'grey' };
    case 'LATE': return { title: 'Предупреждение об опоздании', tone: 'amber' };
    case 'NOT_COME': return row.notice_kind === 'ABSENT' ? { title: 'Предупреждение: не придёт', tone: 'amber' } : { title: 'Нет отметки', tone: 'red' };
    case 'VACATION': return { title: 'В отпуске', tone: 'violet' };
    case 'SICK_LEAVE': return { title: 'На больничном', tone: 'sky' };
    case 'OTHER_ABSENCE': return { title: row.absence_name ?? 'Отсутствует', tone: 'violet' };
    case 'DAY_OFF': return { title: 'Выходной', tone: 'grey' };
    case 'NO_SCHEDULE': return { title: 'Без графика', tone: 'grey' };
    default: return { title: '—', tone: 'grey' };
  }
}

/** Почему человек требует внимания сейчас. Пусто — не требует. */
function reasonNow(row: api.PresenceRow): { title: string; tone: Tone } | null {
  if (row.state === 'NOT_COME' && row.notice_kind !== 'ABSENT') return { title: 'Нет отметки', tone: 'red' };
  if (row.outside_geofence) return { title: 'Отметка вне геозоны', tone: 'red' };
  if (row.conflicting_marks) return { title: 'Противоречивые отметки', tone: 'amber' };
  if ((row.late_minutes ?? 0) > 0) return { title: `Опоздание ${row.late_minutes} мин`, tone: 'amber' };
  if (row.state === 'LATE') return { title: 'Предупреждение об опоздании', tone: 'amber' };
  return null;
}

function useAttendanceTab(active: boolean, ctx: Ctx): View {
  const { scope } = ctx;
  const [overview, previous] = useOverviewPair(active, ctx);
  const both = joined(overview, previous);
  const [presence] = useBlock(
    (signal) => api.presenceDay({ date: today(), ...scope.place, ...scope.narrow }, signal),
    `presence|${scope.key}`,
    active,
  );

  const kpis = (
    <Kpis block={both}>
      {([ov, was]) => {
        const s = ov.summary;
        const p = was.summary;
        return [
          { icon: 'users', tone: 'blue', value: pct(s.attendance.percent), label: 'явка', foot: <Delta value={s.difference_points} unit="pp" good="up" /> },
          {
            icon: 'clock', tone: 'blue', value: duration(s.average_seconds), label: 'среднее рабочее время в день',
            foot: <Delta value={s.average_seconds !== null && p.average_seconds !== null ? (s.average_seconds - p.average_seconds) / 60 : null} unit="min" good="up" />,
          },
          {
            icon: 'check', tone: 'green', value: pct(s.on_time.percent), label: 'пришли вовремя',
            foot: <Delta value={s.on_time.percent !== null && p.on_time.percent !== null ? s.on_time.percent - p.on_time.percent : null} unit="pp" good="up" />,
          },
          { icon: 'late', tone: 'amber', value: s.late.numerator, label: plural(s.late.numerator, 'опоздание', 'опоздания', 'опозданий'), foot: <Delta value={s.late.numerator - p.late.numerator} unit="n" good="down" /> },
        ];
      }}
    </Kpis>
  );

  const rankTitle = ctx.by === 'offices' ? 'Сравнение по офисам' : BY.find((one) => one.id === ctx.by)?.title.replace('Явка по', 'Сравнение по') ?? '';

  const body = (
    <div className="ax-grid ax-grid--side">
      <div className="ax-col">
        <Section title="Явка по дням" sub="Доля пришедших от ожидаемых по графику"
                 action={ctx.compare ? <Legend /> : undefined}>
          <Guard block={overview} rows={5}>
            {(data) => <LineChart points={attendancePoints(data)} height={150} max={100} format={(v) => `${Math.round(v)}%`} label="Явка по дням" compare={ctx.compare} />}
          </Guard>
        </Section>
        <Section title="Сотрудники" sub="Худшая явка сверху · статус — на сейчас" className="ax-sec--line"
                 action={<Link className="ax-link" to={`/attendance?tab=period&from=${scope.from}&to=${scope.to}`}>Все сотрудники <AppIcon name="arrow" size={16} /></Link>}>
          <Guard block={joined(overview, presence)} rows={5}>
            {([data, day]) => {
              const rows = data.employees.slice(0, 5);
              if (!rows.length) return <p className="ax-empty">За период никого не ждали по графику.</p>;
              return (
                <table className="ax-table" aria-label="Сотрудники">
                  <thead><tr><th>Сотрудник</th><th>Офис</th><th>По графику</th><th>Отработано</th><th>Опоздания</th><th>Нет отметок</th><th>Сейчас</th></tr></thead>
                  <tbody>
                    {rows.map((one) => {
                      const state = nowState(day.items.find((r) => r.employee_id === one.id));
                      return (
                        <tr key={one.id}>
                          <td><Link to={`/employees/${one.id}`}>{one.name}</Link></td>
                          <td>{officeName(ctx, one.office_id)}</td>
                          <td>{one.attendance.denominator} {plural(one.attendance.denominator, 'день', 'дня', 'дней')}</td>
                          <td>{duration(one.seconds ?? null)}</td>
                          <td className={one.late_days ? 'ax-amber' : ''}>{one.late_days}</td>
                          <td className={one.missed_days ? 'ax-red' : ''}>{one.missed_days}</td>
                          <td><Status tone={state.tone}>{state.title}</Status></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              );
            }}
          </Guard>
        </Section>
      </div>
      <div className="ax-col ax-col--side">
        <Section title="Сейчас требуют внимания" sub="Сегодня: нет отметки, опоздания, геозона, противоречия">
          <Guard block={joined(presence, overview)} rows={5}>
            {([day, ov]) => {
              const flagged = day.items.map((row) => ({ row, why: reasonNow(row) })).filter((one): one is { row: api.PresenceRow; why: { title: string; tone: Tone } } => one.why !== null);
              const count = (test: (row: api.PresenceRow) => boolean) => day.items.filter(test).length;
              return (
                <>
                  <ul className="ax-counts">
                    <li><b className="ax-red">{count((r) => r.state === 'NOT_COME' && r.notice_kind !== 'ABSENT')}</b>нет отметки</li>
                    <li><b className="ax-amber">{count((r) => (r.late_minutes ?? 0) > 0 || r.state === 'LATE')}</b>опоздали</li>
                    <li><b className="ax-amber">{ov.summary.open_sessions}</b>незакрытых смен</li>
                    <li><b className="ax-red">{count((r) => r.outside_geofence)}</b>вне геозоны</li>
                  </ul>
                  {flagged.length ? (
                    <ul className="ax-people">
                      {flagged.slice(0, 5).map(({ row, why }) => (
                        <li key={row.employee_id}>
                          <Link to={`/attendance?date=${today()}&employee=${row.employee_id}`}>{row.full_name}</Link>
                          <small>{row.office_name ?? '—'}</small>
                          <Status tone={why.tone}>{why.title}</Status>
                        </li>
                      ))}
                    </ul>
                  ) : <p className="ax-empty">Сейчас всё спокойно.</p>}
                </>
              );
            }}
          </Guard>
        </Section>
      </div>
      <div className="ax-foot">
        <Section title={rankTitle} sub={ctx.by === 'previous' ? 'Выбранный период против прошлого той же длины' : 'Явка за период: доля пришедших от ожидаемых'}>
          <Guard block={both} rows={2}>
            {([ov, was]) => <Ranking ov={ov} was={was} by={ctx.by} compare={ctx.compare} />}
          </Guard>
        </Section>
      </div>
    </div>
  );

  return { sub: 'Посещаемость и рабочее время: явка, опоздания и время на месте', kpis, body };
}

function Ranking({ ov, was, by, compare }: { ov: api.Overview; was: api.Overview; by: By; compare: boolean }) {
  if (by === 'previous') {
    const rows: [string, string, string][] = [
      ['Явка', pct(ov.summary.attendance.percent), pct(was.summary.attendance.percent)],
      ['Вовремя', pct(ov.summary.on_time.percent), pct(was.summary.on_time.percent)],
      ['Опозданий', String(ov.summary.late.numerator), String(was.summary.late.numerator)],
      ['Дней без отметки', String(ov.summary.missed_days), String(was.summary.missed_days)],
      ['Среднее время', duration(ov.summary.average_seconds), duration(was.summary.average_seconds)],
      ['Дней отсутствий', String(absencesOf(ov.summary)), String(absencesOf(was.summary))],
    ];
    return (
      <table className="ax-table ax-table--tight ax-table--cols" aria-label="Сравнение с прошлым периодом">
        <thead><tr><th>Период</th>{rows.map(([name]) => <th key={name}>{name}</th>)}</tr></thead>
        <tbody>
          <tr><td>Выбранный</td>{rows.map(([name, a]) => <td key={name}><b>{a}</b></td>)}</tr>
          <tr><td>Предыдущий</td>{rows.map(([name, , b]) => <td key={name}>{b}</td>)}</tr>
        </tbody>
      </table>
    );
  }
  const source: { id: string; name: string; attendance: api.Share; difference_points: number | null }[] =
    by === 'offices' ? ov.offices : by === 'regions' ? ov.regions : (ov[by] ?? []);
  const rows: BarRow[] = source
    .filter((one) => one.attendance.denominator > 0)
    .slice(0, 6)
    .map((one) => ({
      key: one.id,
      name: one.name,
      value: one.attendance.percent,
      text: pct(one.attendance.percent),
      tone: (one.attendance.percent ?? 0) < 60 ? 'red' : (one.attendance.percent ?? 0) < 80 ? 'amber' : 'blue',
      ...(compare ? { note: <Delta value={one.difference_points} unit="pp" good="up" text="" /> } : {}),
    }));
  return <div className="ax-bars--wide"><Bars rows={rows} label="Рейтинг явки" empty="За период никого не ждали по графику" /></div>;
}

// --- Отсутствия и заявки ----------------------------------------------------------

/** Кто сейчас держит заявку: бумагу несёт сотрудник, решение — за HR. */
function waitsFor(item: api.QueueItem): string {
  const stage = item.absence?.stage;
  if (stage === 'WAITING_DOCUMENTS' || stage === 'NEEDS_FIX') return 'Сотрудник';
  return 'HR';
}

const LOAD: { key: string; title: string; tone: BarRow['tone'] }[] = [
  { key: 'WAITING_DOCUMENTS', title: 'Ждут документы', tone: 'amber' },
  { key: 'HR_REVIEW', title: 'На проверке HR', tone: 'blue' },
  { key: 'NEEDS_FIX', title: 'Нужны исправления', tone: 'red' },
  { key: 'PENDING', title: 'Ожидают согласования', tone: 'blue' },
];

function overlaps(a: { first_day: string | null; last_day: string | null }, b: { first_day: string | null; last_day: string | null }): boolean {
  if (!a.first_day || !a.last_day || !b.first_day || !b.last_day) return false;
  return a.first_day <= b.last_day && b.first_day <= a.last_day;
}

function useAbsencesTab(active: boolean, ctx: Ctx): View {
  const { scope } = ctx;
  const [overview, previous] = useOverviewPair(active, ctx, '1');
  const [requests] = useBlock(
    (signal) => pages((cursor) => api.queue({ kind: 'absence', date_from: scope.from, date_to: scope.to, limit: '200', ...scope.place, ...(cursor ? { cursor } : {}) }, signal), 600),
    `abs|${scope.key}`,
    active,
  );
  const [open] = useBlock(
    (signal) => pages((cursor) => api.queue({ kind: 'absence', status: OPEN, limit: '200', ...scope.place, ...(cursor ? { cursor } : {}) }, signal), 400),
    `abs-open|${scope.key}`,
    active,
  );
  const [month, setMonth] = useState(scope.to.slice(0, 7));
  useEffect(() => setMonth(scope.to.slice(0, 7)), [scope.to]);

  const both = joined(overview, previous, open);

  const kpis = (
    <Kpis block={both}>
      {([ov, was, waiting]) => [
        {
          icon: 'calendar', tone: 'blue', value: absencesOf(ov.summary),
          label: `${plural(absencesOf(ov.summary), 'день', 'дня', 'дней')} подтверждённых отсутствий`,
          foot: <Delta value={absencesOf(ov.summary) - absencesOf(was.summary)} unit="n" good="none" />,
        },
        { icon: 'bag', tone: 'violet', value: ov.summary.vacation_days, label: `${plural(ov.summary.vacation_days, 'день', 'дня', 'дней')} отпуска`, foot: <Delta value={ov.summary.vacation_days - was.summary.vacation_days} unit="n" good="none" /> },
        { icon: 'doc', tone: 'sky', value: ov.summary.sick_leave_days, label: `${plural(ov.summary.sick_leave_days, 'день', 'дня', 'дней')} больничного`, foot: <Delta value={ov.summary.sick_leave_days - was.summary.sick_leave_days} unit="n" good="down" /> },
        { icon: 'warning', tone: 'amber', value: waiting.length, label: `${plural(waiting.length, 'заявка требует', 'заявки требуют', 'заявок требуют')} решения`, foot: <span className="ax-muted">сейчас, по всем датам</span> },
      ]}
    </Kpis>
  );

  const months = useMemo(() => {
    const list: string[] = [];
    let at = `${scope.from.slice(0, 7)}-01`;
    while (at.slice(0, 7) <= scope.to.slice(0, 7)) {
      list.push(at.slice(0, 7));
      const [y, m] = at.split('-').map(Number) as [number, number];
      at = m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, '0')}-01`;
    }
    return list;
  }, [scope.from, scope.to]);
  const monthIndex = Math.max(0, months.indexOf(month));
  const MONTH_NAMES = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

  const body = (
    <div className="ax-grid ax-grid--side">
      <div className="ax-col">
        <Section title="Календарь отсутствий" sub={<span className="ax-keys"><i className="ax-key--vacation" />отпуск<i className="ax-key--sick" />больничный<i className="ax-key--trip" />командировка<i className="ax-key--other" />другое · число — сколько человек</span>}
                 action={(
                   <span className="ax-month">
                     <button type="button" className="ax-month__btn" aria-label="Предыдущий месяц" disabled={monthIndex <= 0}
                             onClick={() => setMonth(months[monthIndex - 1] ?? month)}><AppIcon name="back" size={16} /></button>
                     <b>{MONTH_NAMES[Number(month.slice(5, 7)) - 1]} {month.slice(0, 4)}</b>
                     <button type="button" className="ax-month__btn" aria-label="Следующий месяц" disabled={monthIndex >= months.length - 1}
                             onClick={() => setMonth(months[monthIndex + 1] ?? month)}><AppIcon name="next" size={16} /></button>
                   </span>
                 )}>
          <Guard block={overview} rows={5}>
            {(data) => {
              const cells = Object.fromEntries(data.days.map((d) => {
                const parts: [CalendarKind, number][] = [['vacation', d.vacation], ['sick', d.sick_leave], ['trip', d.trip ?? 0], ['other', d.other_absence]];
                const top = parts.reduce((best, one) => (one[1] > best[1] ? one : best));
                return [d.day, { total: parts.reduce((sum, one) => sum + one[1], 0), kind: top[0] }];
              }));
              return <MonthCalendar month={`${month}-01`} cells={cells} inside={(day) => day >= scope.from && day <= scope.to} label="Календарь отсутствий" />;
            }}
          </Guard>
        </Section>
        <Section title="Заявки в работе" sub="Ждут решения или действий сотрудника" className="ax-sec--line"
                 action={<Link className="ax-link" to="/requests">Все заявки <AppIcon name="arrow" size={16} /></Link>}>
          <Guard block={open} rows={4}>
            {(items) => items.length ? (
              <table className="ax-table" aria-label="Заявки в работе">
                <thead><tr><th>Сотрудник</th><th>Тип</th><th>Период</th><th>Статус</th><th>Ждёт действия</th></tr></thead>
                <tbody>
                  {items.slice(0, 4).map((item) => {
                    const status = statusOf(item);
                    const a = item.absence;
                    const who = waitsFor(item);
                    return (
                      <tr key={item.id}>
                        <td><Link to={`/requests/${item.id}`}>{a?.employee.full_name ?? '—'}</Link></td>
                        <td>{kindOf(item).title}</td>
                        <td>{a?.first_day ? `${dayShort(a.first_day)}${a.last_day && a.last_day !== a.first_day ? ` – ${dayShort(a.last_day)}` : ''}` : 'даты не указаны'}</td>
                        <td><Status tone={status.tone === 'red' ? 'red' : status.tone === 'grey' ? 'grey' : 'amber'}>{status.title}</Status></td>
                        <td className={who === 'HR' ? 'ax-blue' : 'ax-muted'}>{who}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : <p className="ax-empty">Заявок, ждущих решения, нет.</p>}
          </Guard>
        </Section>
      </div>
      <div className="ax-col ax-col--side">
        <Section title="Нагрузка HR" sub="Заявки в работе по этапам">
          <Guard block={open} rows={4}>
            {(items) => {
              const total = Math.max(1, items.length);
              return (
                <Bars label="Нагрузка HR" rows={LOAD.map((one) => {
                  const n = items.filter((item) => (item.absence?.stage ?? 'PENDING') === one.key || (one.key === 'PENDING' && !LOAD.some((l) => l.key === item.absence?.stage))).length;
                  return { key: one.key, name: one.title, value: (n * 100) / total, text: String(n), ...(one.tone ? { tone: one.tone } : {}) };
                })} />
              );
            }}
          </Guard>
        </Section>
        <Section title="Пересечения" sub="Заявка в работе на дни, когда человек уже отсутствует" className="ax-sec--line">
          <Guard block={joined(open, requests)} rows={3}>
            {([waiting, all]) => {
              const approved = all.filter((one) => one.absence?.status === 'APPROVED');
              const found = waiting.flatMap((item) => {
                const a = item.absence;
                if (!a) return [];
                const hit = approved.find((one) => one.id !== item.id && one.absence?.employee.id === a.employee.id && overlaps(one.absence!, a));
                return hit ? [{ item, hit }] : [];
              });
              if (!found.length) return <p className="ax-empty">Пересечений нет: заявки в работе не накладываются на подтверждённые отсутствия.</p>;
              return (
                <ul className="ax-people">
                  {found.slice(0, 4).map(({ item, hit }) => (
                    <li key={item.id}>
                      <Link to={`/requests/${item.id}`}>{item.absence!.employee.full_name}</Link>
                      <small>{kindOf(item).title} накладывается на «{hit.absence!.absence_type.name}» {dayShort(hit.absence!.first_day!)} – {dayShort(hit.absence!.last_day!)}</small>
                      <Status tone="red">Пересечение</Status>
                    </li>
                  ))}
                </ul>
              );
            }}
          </Guard>
        </Section>
      </div>
    </div>
  );

  return { sub: 'Отсутствия и заявки: только подтверждённые отсутствия и заявки в работе', kpis, body };
}

// --- Команда -----------------------------------------------------------------------

function useTeamTab(active: boolean, ctx: Ctx): View {
  const team = useTeam(active, ctx);
  const progress = useOnboardingRows(active, ctx);

  const kpis = (
    <Kpis block={team}>
      {(data) => {
        const s = data.summary;
        return [
          { icon: 'users', tone: 'blue', value: s.headcount, label: `${plural(s.headcount, 'сотрудник', 'сотрудника', 'сотрудников')} на конец периода`, foot: <Delta value={s.headcount - s.headcount_start} unit="n" good="up" text="за период" /> },
          { icon: 'user-plus', tone: 'green', value: s.hired, label: plural(s.hired, 'новый сотрудник', 'новых сотрудника', 'новых сотрудников'), foot: <Delta value={s.hired - s.previous_hired} unit="n" good="up" /> },
          { icon: 'team', tone: 'blue', value: s.promoted, label: 'приняты после стажировки', foot: <Delta value={s.promoted - s.previous_promoted} unit="n" good="up" /> },
          { icon: 'user-x', tone: 'red', value: s.left, label: plural(s.left, 'увольнение', 'увольнения', 'увольнений'), foot: <Delta value={s.left - s.previous_left} unit="n" good="down" /> },
        ];
      }}
    </Kpis>
  );

  const body = (
    <div className="ax-grid ax-grid--side">
      <div className="ax-col">
        <Section title="Динамика численности" sub="Сколько человек числилось на каждый день периода">
          <Guard block={team} rows={4}>
            {(data) => data.series.length
              ? <LineChart points={data.series.map((one) => ({ day: one.day, value: one.headcount }))} height={132} format={(v) => String(v)} label="Численность по дням" />
              : <p className="ax-empty">Период ещё не наступил.</p>}
          </Guard>
        </Section>
        <Section title="Изменения по отделам" sub="Кто пришёл и ушёл за период" className="ax-sec--line">
          <Guard block={team} rows={3}>
            {(data) => data.by_department.length ? (
              <table className="ax-table ax-table--tight" aria-label="Изменения по отделам">
                <thead><tr><th>Отдел</th><th>Пришли</th><th>Ушли</th><th>Итог</th></tr></thead>
                <tbody>
                  {data.by_department.slice(0, 3).map((one) => (
                    <tr key={one.id ?? one.name}>
                      <td>{one.name}</td>
                      <td className={one.hired ? 'ax-green' : ''}>{one.hired ? `+${one.hired}` : 0}</td>
                      <td className={one.left ? 'ax-red' : ''}>{one.left ? `−${one.left}` : 0}</td>
                      <td><b>{one.difference > 0 ? `+${one.difference}` : one.difference < 0 ? `−${-one.difference}` : '0'}</b></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="ax-empty">Приёма и увольнений за период не было.</p>}
          </Guard>
        </Section>
        <Section title="Новые сотрудники" sub="Вышли в выбранном периоде · адаптация — ход ознакомления" className="ax-sec--line">
          <Guard block={joined(team, progress)} rows={3}>
            {([data, rows]) => data.hires.length ? (
              <table className="ax-table" aria-label="Новые сотрудники">
                <thead><tr><th>Сотрудник</th><th>Отдел</th><th>Офис</th><th>Дата выхода</th><th>Адаптация</th></tr></thead>
                <tbody>
                  {data.hires.slice(0, 3).map((one) => {
                    const row = rows.find((r) => r.employee_id === one.id);
                    const total = row ? row.sections_total + row.policies_total : 0;
                    const done = row ? row.sections_done + row.policies_done : 0;
                    return (
                      <tr key={one.id}>
                        <td><Link to={`/employees/${one.id}`}><Person name={one.name} /></Link></td>
                        <td>{one.department ?? '—'}</td>
                        <td>{one.office ?? '—'}</td>
                        <td>{dayLong(one.hire_date)}</td>
                        <td>{row ? <span className="ax-inline">{done}/{total}<Progress value={share(done, total)} /></span> : <span className="ax-muted">не приглашён в ознакомление</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : <p className="ax-empty">За период новых сотрудников нет.</p>}
          </Guard>
        </Section>
      </div>
      <div className="ax-col ax-col--side">
        <Section title="Состав по отделам" sub="Численность на конец периода"
                 action={<Link className="ax-link" to="/employees">Все сотрудники <AppIcon name="arrow" size={16} /></Link>}>
          <Guard block={team} rows={4}>
            {(data) => <Composition rows={data.departments} compare={ctx.compare} />}
          </Guard>
        </Section>
        <Section title="Распределение по офисам" className="ax-sec--line">
          <Guard block={team} rows={3}>
            {(data) => <Composition rows={data.offices} compare={ctx.compare} />}
          </Guard>
        </Section>
        <Section title="Переводы" sub="Смена отдела или офиса" className="ax-sec--line">
          <Guard block={team} rows={2}>
            {(data) => data.transfers.length ? (
              <ul className="ax-people">
                {data.transfers.slice(0, 3).map((one) => (
                  <li key={`${one.id}-${one.date}`}>
                    <Link to={`/employees/${one.id}`}>{one.name}</Link>
                    <small>{one.from_office !== one.to_office ? `${one.from_office} → ${one.to_office}` : `${one.from_department ?? 'Без отдела'} → ${one.to_department ?? 'Без отдела'}`}</small>
                    <span className="ax-muted">{dayShort(one.date)}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="ax-empty">Переводов за период не было.</p>}
          </Guard>
          <Guard block={team} rows={1}>
            {(data) => <p className="ax-said"><AppIcon name="bulb" size={18} />{teamConclusion(data)}</p>}
          </Guard>
        </Section>
      </div>
      <div className="ax-foot">
        <Guard block={team} rows={1}>
          {(data) => (
            <div className="ax-strip">
              <div className="ax-strip__title"><b>Кадровые изменения</b><span>За выбранный период</span></div>
              <StripItem icon="user-plus" tone="green" value={data.summary.hired} label="найм" />
              <StripItem icon="transfer" tone="blue" value={data.transfers.length} label={plural(data.transfers.length, 'перевод', 'перевода', 'переводов')} />
              <StripItem icon="grad" tone="blue" value={data.summary.promoted + data.summary.probation_failed} label="завершили стажировку" />
              <StripItem icon="user-x" tone="red" value={data.summary.left} label={plural(data.summary.left, 'увольнение', 'увольнения', 'увольнений')} />
            </div>
          )}
        </Guard>
      </div>
    </div>
  );

  return { sub: 'Команда и кадровые изменения: численность, состав и новые сотрудники', kpis, body };
}

function Composition({ rows, compare }: { rows: { id: string | null; name: string; headcount: number; previous_headcount: number }[]; compare: boolean }) {
  const top = Math.max(1, ...rows.map((one) => one.headcount));
  const total = rows.reduce((sum, one) => sum + one.headcount, 0);
  return (
    <Bars label="Состав" empty="Сотрудников нет" rows={rows.slice(0, 4).map((one) => ({
      key: one.id ?? one.name, name: one.name, value: (one.headcount * 100) / top, text: String(one.headcount),
      note: compare ? <Delta value={one.headcount - one.previous_headcount} unit="n" good="none" text="" /> : <span className="ax-muted">{pct(share(one.headcount, total))}</span>,
    }))} />
  );
}

/** Короткий вывод — только из настоящей разницы. */
function teamConclusion(data: api.TeamReport): string {
  const grown = [...data.departments].map((one) => ({ ...one, diff: one.headcount - one.previous_headcount }))
    .sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff))[0];
  if (grown && grown.diff !== 0) {
    const n = Math.abs(grown.diff);
    return `Отдел ${grown.name} ${grown.diff > 0 ? 'вырос' : 'уменьшился'} на ${n} ${plural(n, 'сотрудника', 'сотрудника', 'сотрудников')} за период.`;
  }
  const diff = data.summary.headcount - data.summary.headcount_start;
  return diff ? `Численность ${diff > 0 ? 'выросла' : 'снизилась'} на ${Math.abs(diff)} за период.` : 'Численность за период не изменилась.';
}

// --- Стажировки -------------------------------------------------------------------

function useProbationTab(active: boolean, ctx: Ctx): View {
  const probation = useProbation(active, ctx);
  const progress = useOnboardingRows(active, ctx);

  const kpis = (
    <Kpis block={probation}>
      {(data) => {
        const s = data.summary;
        return [
          { icon: 'users', tone: 'blue', value: s.active, label: `${plural(s.active, 'активный стажёр', 'активных стажёра', 'активных стажёров')}`, foot: <span className="ax-muted">начали в периоде: {s.started}</span> },
          { icon: 'clock', tone: 'amber', value: s.due, label: `${plural(s.due, 'решение', 'решения', 'решений')} в ближайшие ${data.due_days} дней`, foot: s.overdue ? <span className="ax-red">срок уже прошёл: {s.overdue}</span> : <span className="ax-muted">просроченных решений нет</span> },
          { icon: 'user-plus', tone: 'green', value: s.promoted, label: 'приняты в штат', foot: <Delta value={s.promoted - s.previous_promoted} unit="n" good="up" /> },
          { icon: 'user-x', tone: 'red', value: s.failed, label: 'завершили без найма', foot: <Delta value={s.failed - s.previous_failed} unit="n" good="down" /> },
        ];
      }}
    </Kpis>
  );

  const body = (
    <div className="ax-grid ax-grid--side">
      <div className="ax-col">
        <Section title="Стажёры сейчас" sub="Ближайшее решение сверху · адаптация — ход ознакомления в боте">
          <Guard block={joined(probation, progress)} rows={6}>
            {([data, rows]) => data.trainees.length ? (
              <table className="ax-table" aria-label="Стажёры сейчас">
                <thead><tr><th>Сотрудник</th><th>Отдел</th><th>Стажировка</th><th>Осталось</th><th>Наставник</th><th>Адаптация</th><th>Решение</th></tr></thead>
                <tbody>
                  {data.trainees.slice(0, 8).map((one) => {
                    const row = rows.find((r) => r.employee_id === one.id);
                    const total = row ? row.sections_total + row.policies_total : 0;
                    const done = row ? row.sections_done + row.policies_done : 0;
                    const left = one.days_left;
                    return (
                      <tr key={one.id}>
                        <td><Link to={`/employees/${one.id}`}>{one.name}</Link></td>
                        <td>{one.department ?? '—'}</td>
                        <td>{dayShort(one.probation_from)} – {one.probation_to ? dayShort(one.probation_to) : 'без даты'}</td>
                        <td className={left === null ? '' : left < 0 ? 'ax-red' : left <= data.due_days ? 'ax-amber' : 'ax-blue'}>
                          {left === null ? '—' : left < 0 ? `просрочено ${-left} дн.` : `${left} ${plural(left, 'день', 'дня', 'дней')}`}
                        </td>
                        <td>{one.mentor ? one.mentor.name : <span className="ax-muted">не назначен</span>}</td>
                        <td>{row ? <Progress value={share(done, total)} /> : <span className="ax-muted">не приглашён</span>}</td>
                        <td>{left === null ? <Status tone="grey">Срок не указан</Status> : left <= data.due_days ? <Status tone={left < 0 ? 'red' : 'amber'}>Ожидает решения</Status> : <Status tone="green">Идёт по плану</Status>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : <p className="ax-empty">Сейчас стажёров нет.</p>}
          </Guard>
        </Section>
      </div>
      <div className="ax-col ax-col--side">
        <Section title="Ближайшие решения" sub={`Стажировки, которые заканчиваются в ближайшие дни`}>
          <Guard block={probation} rows={3}>
            {(data) => {
              const soon = data.trainees.filter((one) => one.days_left !== null && one.days_left <= data.due_days).slice(0, 3);
              if (!soon.length) return <p className="ax-empty">В ближайшие {data.due_days} дней решений не нужно.</p>;
              return (
                <ul className="ax-decisions">
                  {soon.map((one) => (
                    <li key={one.id}>
                      <i className={`ax-dot--${one.days_left! < 0 ? 'red' : 'amber'}`} aria-hidden="true" />
                      <span className="ax-decisions__date">{dayLong(one.probation_to!)}</span>
                      <span><Link to={`/employees/${one.id}`}>{one.name}</Link><small>{one.department ?? one.office ?? '—'} · решить: в штат или завершить</small></span>
                      <span className={`ax-chip ax-chip--${one.days_left! < 0 ? 'red' : 'amber'}`}>{one.days_left! < 0 ? 'Срок прошёл' : 'Ожидает решения'}</span>
                    </li>
                  ))}
                </ul>
              );
            }}
          </Guard>
        </Section>
        <Section title="Путь адаптации" sub="Стажёры за выбранный период" className="ax-sec--line">
          <Guard block={probation} rows={4}>
            {(data) => {
              const steps = [
                { key: 'started', name: 'Начали стажировку', value: data.summary.started },
                { key: 'active', name: 'Сейчас на стажировке', value: data.summary.active },
                { key: 'due', name: `Решение в ${data.due_days} дней`, value: data.summary.due },
                { key: 'promoted', name: 'Приняты в штат', value: data.summary.promoted },
                { key: 'failed', name: 'Без найма', value: data.summary.failed },
              ];
              const top = Math.max(1, ...steps.map((one) => one.value));
              return <Bars label="Путь адаптации" rows={steps.map((one) => ({ key: one.key, name: one.name, value: (one.value * 100) / top, text: String(one.value), tone: one.key === 'failed' ? 'red' : one.key === 'promoted' ? 'green' : one.key === 'due' ? 'amber' : 'blue' }))} />;
            }}
          </Guard>
        </Section>
      </div>
      <div className="ax-foot">
        <Guard block={probation} rows={1}>
          {(data) => (
            <div className="ax-strip">
              <div className="ax-strip__title"><b>Итоги стажировок за период</b><span>Решения, принятые в выбранном периоде</span></div>
              <StripItem icon="user-plus" tone="green" value={data.summary.promoted} label="приняты в штат" delta={<Delta value={data.summary.promoted - data.summary.previous_promoted} unit="n" good="up" text="" />} />
              <StripItem icon="user-x" tone="red" value={data.summary.failed} label="завершили без найма" delta={<Delta value={data.summary.failed - data.summary.previous_failed} unit="n" good="down" text="" />} />
              <StripItem icon="team" tone="blue" value={data.summary.active} label="продолжают стажировку" />
              <div className="ax-strip__insight">
                <span className="ax-tone--blue"><AppIcon name="chart" size={20} /></span>
                <span><b>Главный вывод</b><small>{probationConclusion(data)}</small></span>
              </div>
            </div>
          )}
        </Guard>
      </div>
    </div>
  );

  return { sub: 'Стажировки и адаптация: сроки решений и итоги испытательного срока', kpis, body };
}

function probationConclusion(data: api.ProbationReport): string {
  const s = data.summary;
  if (s.conversion_percent === null) {
    return s.due ? `Решений за период не было; в ближайшие ${data.due_days} дней их нужно принять по ${s.due} ${plural(s.due, 'стажёру', 'стажёрам', 'стажёрам')}.` : 'За период решений по стажировкам не было.';
  }
  const was = s.previous_conversion_percent;
  const trend = was === null ? '' : s.conversion_percent > was ? `, выше прошлого периода (${pct(was)})` : s.conversion_percent < was ? `, ниже прошлого периода (${pct(was)})` : ', как в прошлом периоде';
  return `Конверсия в найм — ${pct(s.conversion_percent)} среди завершивших${trend}.`;
}

// --- Опросы -----------------------------------------------------------------------

type SurveyData = {
  now: api.SurveyCampaign[];
  before: api.SurveyCampaign[];
  recipients: Record<string, api.SurveyRecipient[]>;
};

const SURVEY_STATUS: Record<string, { title: string; tone: Tone }> = {
  ACTIVE: { title: 'Идёт сбор', tone: 'blue' },
  FINISHED: { title: 'Завершена', tone: 'green' },
  SCHEDULED: { title: 'Запланирована', tone: 'grey' },
  CANCELLED: { title: 'Отменена', tone: 'red' },
  DRAFT: { title: 'Черновик', tone: 'grey' },
};

function useSurveysTab(active: boolean, ctx: Ctx): View {
  const { scope } = ctx;
  const [block, reload] = useBlock(async (signal): Promise<SurveyData> => {
    const all = await pages((cursor) => api.surveyCampaigns({ limit: '100', ...(cursor ? { cursor } : {}) }, signal), 400);
    const inside = (one: api.SurveyCampaign, a: string, b: string) => !!one.sent_at && one.sent_at.slice(0, 10) >= a && one.sent_at.slice(0, 10) <= b;
    const now = all.filter((one) => inside(one, scope.from, scope.to)).sort((a, b) => (b.sent_at ?? '').localeCompare(a.sent_at ?? ''));
    const before = all.filter((one) => inside(one, scope.prevFrom, scope.prevTo));
    const lists = await Promise.all(now.slice(0, 12).map((one) => api.surveyRecipients(one.id, {}, signal).then((r) => [one.id, r.items] as const)));
    return { now, before, recipients: Object.fromEntries(lists) };
  }, `surveys|${scope.key}`, active);

  // Отбор по месту: у получателя есть офис и отдел словами.
  const officeNames = new Set(
    (scope.office ? ctx.offices.filter((one) => one.id === scope.office) : scope.region ? ctx.offices.filter((one) => one.region_id === scope.region) : [])
      .map((one) => one.name),
  );
  const departmentName = ctx.departments.find((one) => one.id === scope.department)?.name ?? null;
  const fits = (one: api.SurveyRecipient) =>
    (!officeNames.size || officeNames.has(one.office_name ?? '')) && (!departmentName || one.department_name === departmentName);

  const [reminded, setReminded] = useState<Record<string, string>>({});
  const remind = (id: string) => {
    setReminded((was) => ({ ...was, [id]: 'Отправляем…' }));
    api.remindSurveyCampaign(id)
      .then(() => { setReminded((was) => ({ ...was, [id]: 'Напоминание отправлено' })); reload(); })
      .catch((error) => setReminded((was) => ({ ...was, [id]: messageFor(error) })));
  };

  const figures = (data: SurveyData) => {
    const people = data.now.flatMap((one) => (data.recipients[one.id] ?? []).filter(fits).map((r) => ({ ...r, campaign: one })));
    const reached = people.filter((one) => one.status !== 'SKIPPED' && one.status !== 'PENDING');
    const done = people.filter((one) => one.status === 'COMPLETED');
    const waiting = people.filter((one) => (one.status === 'SENT' || one.status === 'STARTED') && one.campaign.status === 'ACTIVE');
    return { people, reached, done, waiting };
  };

  const kpis = (
    <Kpis block={block}>
      {(data) => {
        const f = figures(data);
        return [
          { icon: 'megaphone', tone: 'blue', value: data.now.length, label: plural(data.now.length, 'рассылка', 'рассылки', 'рассылок'), foot: <Delta value={data.now.length - data.before.length} unit="n" good="none" /> },
          { icon: 'users', tone: 'blue', value: f.reached.length, label: plural(f.reached.length, 'получатель', 'получателя', 'получателей'), foot: <span className="ax-muted">без пропущенных</span> },
          { icon: 'check', tone: 'green', value: <>{f.done.length}<small> · {pct(share(f.done.length, f.reached.length))}</small></>, label: plural(f.done.length, 'ответ', 'ответа', 'ответов'), foot: <span className="ax-muted">доля ответивших от получивших</span> },
          { icon: 'hourglass', tone: 'amber', value: f.waiting.length, label: 'ждут ответа', foot: <span className="ax-muted">в идущих рассылках</span> },
        ];
      }}
    </Kpis>
  );

  const body = (
    <div className="ax-grid ax-grid--side">
      <div className="ax-col">
        <Section title="Динамика ответов" sub="Доля ответивших от получивших, нарастающим итогом">
          <Guard block={block} rows={5}>
            {(data) => {
              const f = figures(data);
              if (!f.reached.length) return <p className="ax-empty">За период рассылок не было.</p>;
              const points: LinePoint[] = [];
              for (let day = scope.from; day <= scope.to && day <= today(); day = shift(day, 1)) {
                const sent = f.reached.filter((one) => one.sent_at && one.sent_at.slice(0, 10) <= day).length;
                const answered = f.done.filter((one) => one.completed_at && one.completed_at.slice(0, 10) <= day).length;
                points.push({ day, value: sent ? Math.round((answered * 1000) / sent) / 10 : null });
              }
              return <LineChart points={points} height={176} max={100} format={(v) => `${Math.round(v)}%`} label="Доля ответов по дням" />;
            }}
          </Guard>
        </Section>
        <Section title="Рассылки за период" sub="Сколько отправлено и сколько ответили" className="ax-sec--line"
                 action={<Link className="ax-link" to="/surveys/campaigns">Все рассылки <AppIcon name="arrow" size={16} /></Link>}>
          <Guard block={block} rows={4}>
            {(data) => data.now.length ? (
              <table className="ax-table" aria-label="Рассылки за период">
                <thead><tr><th>Опрос</th><th>Аудитория</th><th>Отправлено</th><th>Ответили</th><th>Статус</th><th /></tr></thead>
                <tbody>
                  {data.now.slice(0, 5).map((one) => {
                    const list = (data.recipients[one.id] ?? []).filter(fits);
                    const sent = list.filter((r) => r.status !== 'SKIPPED' && r.status !== 'PENDING').length;
                    const answered = list.filter((r) => r.status === 'COMPLETED').length;
                    const waiting = list.filter((r) => r.status === 'SENT' || r.status === 'STARTED').length;
                    const status = SURVEY_STATUS[one.status] ?? { title: one.status, tone: 'grey' as Tone };
                    return (
                      <tr key={one.id}>
                        <td><Link to={`/surveys/campaigns/${one.id}`}>{one.title}</Link></td>
                        <td className="ax-muted">{AUDIENCE[one.audience_kind]}</td>
                        <td>{sent}</td>
                        <td><span className="ax-inline">{answered}<Progress value={share(answered, sent)} /></span></td>
                        <td><Status tone={status.tone}>{status.title}</Status></td>
                        <td>
                          {one.status === 'ACTIVE' && waiting > 0 && (
                            reminded[one.id]
                              ? <span className="ax-muted">{reminded[one.id]}</span>
                              : <button type="button" className="ax-action" onClick={() => remind(one.id)}><AppIcon name="send" size={16} /> Напомнить</button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : <p className="ax-empty">За период рассылок не было.</p>}
          </Guard>
        </Section>
      </div>
      <div className="ax-col ax-col--side">
        <Section title="Вовлечённость по отделам" sub="Доля ответивших от получивших">
          <Guard block={block} rows={5}>
            {(data) => {
              const per: Record<string, [number, number]> = {};
              for (const one of figures(data).reached) {
                const name = one.department_name ?? 'Без отдела';
                const counts = per[name] ?? [0, 0];
                counts[1] += 1;
                if (one.status === 'COMPLETED') counts[0] += 1;
                per[name] = counts;
              }
              const rows = Object.entries(per).map(([name, [done, total]]) => ({ name, value: share(done, total) }))
                .sort((a, b) => (a.value ?? 0) - (b.value ?? 0)).slice(0, 6);
              return <Bars label="Вовлечённость по отделам" rows={rows.map((one) => ({ key: one.name, name: one.name, value: one.value, text: pct(one.value), tone: (one.value ?? 0) < 60 ? 'amber' : 'blue' }))} empty="Получателей за период нет" />;
            }}
          </Guard>
        </Section>
        <Section title="Нужен контроль" sub="Кто не ответил в идущих рассылках" className="ax-sec--line">
          <Guard block={block} rows={4}>
            {(data) => {
              const per: Record<string, { name: string; n: number }> = {};
              for (const one of figures(data).waiting) {
                const was = per[one.employee_id] ?? { name: one.full_name, n: 0 };
                was.n += 1;
                per[one.employee_id] = was;
              }
              const rows = Object.entries(per).sort((a, b) => b[1].n - a[1].n).slice(0, 5);
              if (!rows.length) return <p className="ax-empty">Все получатели идущих рассылок ответили.</p>;
              return (
                <table className="ax-table ax-table--tight" aria-label="Нужен контроль">
                  <thead><tr><th>Сотрудник</th><th>Рассылок без ответа</th></tr></thead>
                  <tbody>{rows.map(([id, one]) => <tr key={id}><td><Link to={`/employees/${id}`}>{one.name}</Link></td><td>{one.n}</td></tr>)}</tbody>
                </table>
              );
            }}
          </Guard>
        </Section>
      </div>
      <div className="ax-foot">
        <Guard block={block} rows={1}>
          {(data) => (
            <div className="ax-foot-row">
              <Conclusion title="Главный вывод">{surveyConclusion(data, figures(data))}</Conclusion>
              {data.now.some((one) => one.is_anonymous) && (
                <p className="ax-info"><AppIcon name="info" size={18} /> Анонимные опросы показывают только агрегированные результаты, без списка сотрудников.</p>
              )}
            </div>
          )}
        </Guard>
      </div>
    </div>
  );

  return { sub: 'Опросы и вовлечённость: охват, ответы и напоминания', kpis, body };
}

function surveyConclusion(data: SurveyData, f: { reached: api.SurveyRecipient[]; done: api.SurveyRecipient[]; waiting: api.SurveyRecipient[] }): string {
  if (!data.now.length) return 'За период рассылок не было — выводов нет.';
  const rate = share(f.done.length, f.reached.length);
  const per: Record<string, [number, number]> = {};
  for (const one of f.reached) {
    const name = one.department_name ?? 'Без отдела';
    const counts = per[name] ?? [0, 0];
    counts[1] += 1;
    if (one.status === 'COMPLETED') counts[0] += 1;
    per[name] = counts;
  }
  const low = Object.entries(per).filter(([, [, total]]) => total >= 3).sort((a, b) => a[1][0] / a[1][1] - b[1][0] / b[1][1])[0];
  const parts = [`Ответили ${pct(rate)} получивших.`];
  if (low && rate !== null && (low[1][0] * 100) / low[1][1] < rate - 5) parts.push(`Ниже всего отклик в отделе «${low[0]}»: ${pct((low[1][0] * 100) / low[1][1])}.`);
  if (f.waiting.length) parts.push(`Напоминание можно отправить ${f.waiting.length} ${plural(f.waiting.length, 'получателю', 'получателям', 'получателям')}.`);
  return parts.join(' ');
}

// --- Ознакомления --------------------------------------------------------------------

type OnboardingData = {
  rows: api.OnboardingRow[];
  documents: { doc: api.PolicyDocument; pending: api.PolicyPendingRow[] }[];
};

const ONB_STATUS: Record<string, { title: string; tone: Tone }> = {
  NOT_STARTED: { title: 'Не начал', tone: 'amber' },
  IN_PROGRESS: { title: 'Читает разделы', tone: 'blue' },
  INFO_COMPLETED: { title: 'Разделы прочитаны', tone: 'blue' },
  POLICIES_IN_PROGRESS: { title: 'Подтверждает документы', tone: 'blue' },
  UPDATE_REQUIRED: { title: 'Новая редакция', tone: 'amber' },
  BLOCKED_BY_DECLINED_POLICY: { title: 'Отказался подтвердить', tone: 'red' },
  COMPLETED: { title: 'Прошёл', tone: 'green' },
};

/** Что человек ещё не прошёл: разделы о компании или конкретные документы. */
function materialOf(row: api.OnboardingRow, data: OnboardingData): string {
  const docs = data.documents.filter(({ pending }) => pending.some((one) => one.employee_id === row.employee_id)).map(({ doc }) => doc.title);
  if (row.sections_done < row.sections_total) return docs.length ? `Разделы о компании, ${docs.length} ${plural(docs.length, 'документ', 'документа', 'документов')}` : 'Разделы о компании';
  return docs.length ? docs.join(', ') : '—';
}

function useOnboardingTab(active: boolean, ctx: Ctx): View {
  const { scope } = ctx;
  const [block, reload] = useBlock(async (signal): Promise<OnboardingData> => {
    const [rows, docs] = await Promise.all([
      pages((cursor) => api.onboardingProgress({ limit: '200', ...(scope.office ? { office_id: scope.office } : {}), ...(cursor ? { cursor } : {}) }, signal), 1000),
      api.policyDocuments(signal),
    ]);
    const live = docs.items.filter((one) => !one.archived_at && one.current_version?.status === 'PUBLISHED').slice(0, 8);
    const documents = await Promise.all(live.map((doc) => api.policyPending(doc.id, signal).then((p) => ({ doc, pending: p.items }))));
    return { rows, documents };
  }, `onb|${scope.key}`, active);

  const officeNames = new Set((scope.region && !scope.office ? ctx.offices.filter((one) => one.region_id === scope.region) : []).map((one) => one.name));
  const departmentName = ctx.departments.find((one) => one.id === scope.department)?.name ?? null;
  const fits = (one: api.OnboardingRow) =>
    (!officeNames.size || officeNames.has(one.office_name ?? '')) && (!departmentName || one.department_name === departmentName);

  const [sent, setSent] = useState<Record<string, string>>({});
  const remind = (id: string) => {
    setSent((was) => ({ ...was, [id]: 'Отправляем…' }));
    api.remindOnboarding(id)
      .then(() => { setSent((was) => ({ ...was, [id]: 'Напомнили' })); reload(); })
      .catch((error) => setSent((was) => ({ ...was, [id]: messageFor(error) })));
  };

  const kpis = (
    <Kpis block={block}>
      {(data) => {
        const rows = data.rows.filter(fits);
        const done = rows.filter((one) => one.status === 'COMPLETED').length;
        const idle = rows.filter((one) => one.status === 'NOT_STARTED').length;
        const declined = rows.filter((one) => one.status === 'BLOCKED_BY_DECLINED_POLICY').length;
        return [
          { icon: 'users', tone: 'blue', value: rows.length, label: 'назначено', foot: <span className="ax-muted">приглашены пройти ознакомление</span> },
          { icon: 'check', tone: 'green', value: <>{done}<small className="ax-green"> · {pct(share(done, rows.length))}</small></>, label: 'прошли полностью', foot: <span className="ax-muted">прочитали разделы и подтвердили документы</span> },
          { icon: 'clock', tone: 'amber', value: idle, label: 'не начали', foot: <span className="ax-muted">ещё не открыли ознакомление</span> },
          { icon: 'warning', tone: 'red', value: rows.filter((one) => one.overdue).length, label: 'просрочены', foot: <span className="ax-muted">срок прошёл{declined ? ` · отказались подтвердить: ${declined}` : ''}</span> },
        ];
      }}
    </Kpis>
  );

  const body = (
    <div className="ax-grid ax-grid--side">
      <div className="ax-col">
        <Section title="Материалы и прохождение" sub="Кто подтвердил действующую редакцию обязательных документов"
                 action={<Link className="ax-link" to="/onboarding">Раздел ознакомления <AppIcon name="arrow" size={16} /></Link>}>
          <Guard block={block} rows={4}>
            {(data) => {
              const rows = data.rows.filter(fits);
              const ids = new Set(rows.map((one) => one.employee_id));
              if (!data.documents.length) return <p className="ax-empty">Опубликованных документов нет.</p>;
              return (
                <table className="ax-table" aria-label="Материалы и прохождение">
                  <thead><tr><th>Материал</th><th>Версия</th><th>Назначено</th><th>Подтвердили</th><th>Не подтвердили</th><th>Отказались</th><th>Прогресс</th></tr></thead>
                  <tbody>
                    {data.documents.map(({ doc, pending }) => {
                      const mine = pending.filter((one) => ids.has(one.employee_id));
                      const declined = mine.filter((one) => one.declined_at).length;
                      const confirmed = rows.length - mine.length;
                      return (
                        <tr key={doc.id}>
                          <td>{doc.title}</td>
                          <td className="ax-muted">v{doc.current_version?.version}</td>
                          <td>{rows.length}</td>
                          <td>{confirmed}</td>
                          <td>{mine.length - declined}</td>
                          <td className={declined ? 'ax-red' : ''}>{declined}</td>
                          <td><Progress value={share(confirmed, rows.length)} /></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              );
            }}
          </Guard>
        </Section>
        <Section title="Кому нужно напомнить" sub="Ещё не прошли ознакомление — дольше всех ждут сверху" className="ax-sec--line">
          <Guard block={block} rows={4}>
            {(data) => {
              const rows = data.rows.filter(fits).filter((one) => one.status !== 'COMPLETED')
                .sort((a, b) => (a.invited_at ?? '9').localeCompare(b.invited_at ?? '9')).slice(0, 5);
              if (!rows.length) return <p className="ax-empty">Все назначенные прошли ознакомление.</p>;
              return (
                <table className="ax-table" aria-label="Кому нужно напомнить">
                  <thead><tr><th>Сотрудник</th><th>Отдел</th><th>Материал</th><th>Срок</th><th>Статус</th><th>Действие</th></tr></thead>
                  <tbody>
                    {rows.map((one) => {
                      const status = ONB_STATUS[one.status] ?? { title: one.status, tone: 'grey' as Tone };
                      return (
                        <tr key={one.employee_id}>
                          <td><Link to={`/employees/${one.employee_id}`}>{one.full_name}</Link></td>
                          <td>{one.department_name ?? one.office_name ?? '—'}</td>
                          <td className="ax-muted">{materialOf(one, data)}</td>
                          <td className={one.overdue ? 'ax-red' : ''}>{one.due_date ? dayLong(one.due_date) : <span className="ax-muted">не назначен</span>}</td>
                          <td><Status tone={status.tone}>{status.title}</Status></td>
                          <td>
                            {sent[one.employee_id]
                              ? <span className="ax-muted">{sent[one.employee_id]}</span>
                              : <button type="button" className="ax-action" onClick={() => remind(one.employee_id)}><AppIcon name="send" size={16} /> Напомнить</button>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              );
            }}
          </Guard>
        </Section>
      </div>
      <div className="ax-col ax-col--side">
        <Section title="Прохождение по отделам" sub="Доля прошедших ознакомление">
          <Guard block={block} rows={5}>
            {(data) => {
              const per: Record<string, [number, number]> = {};
              for (const one of data.rows.filter(fits)) {
                const name = one.department_name ?? 'Без отдела';
                const counts = per[name] ?? [0, 0];
                counts[1] += 1;
                if (one.status === 'COMPLETED') counts[0] += 1;
                per[name] = counts;
              }
              const rows = Object.entries(per).map(([name, [done, total]]) => ({ name, value: share(done, total) }))
                .sort((a, b) => (b.value ?? 0) - (a.value ?? 0)).slice(0, 6);
              return <Bars label="Прохождение по отделам" rows={rows.map((one) => ({ key: one.name, name: one.name, value: one.value, text: pct(one.value), tone: (one.value ?? 0) < 60 ? 'amber' : 'blue' }))} empty="Назначенных нет" />;
            }}
          </Guard>
        </Section>
        <Section title="Новые редакции" sub="Повторное подтверждение после обновления документа" className="ax-sec--line">
          <Guard block={block} rows={4}>
            {(data) => {
              const ids = new Set(data.rows.filter(fits).map((one) => one.employee_id));
              const rows = data.documents
                .filter(({ doc }) => doc.versions.filter((v) => v.status !== 'DRAFT').length > 1)
                .map(({ doc, pending }) => ({ doc, left: pending.filter((one) => ids.has(one.employee_id)).length }));
              if (!rows.length) return <p className="ax-empty">Обновлённых документов нет — у всех первая редакция.</p>;
              return (
                <table className="ax-table ax-table--tight" aria-label="Новые редакции">
                  <thead><tr><th>Дата</th><th>Документ</th><th>Не подтвердили</th></tr></thead>
                  <tbody>
                    {rows.map(({ doc, left }) => (
                      <tr key={doc.id}>
                        <td>{doc.current_version?.published_at ? dayLong(doc.current_version.published_at.slice(0, 10)) : '—'}</td>
                        <td>{doc.title} <span className="ax-muted">v{doc.current_version?.version}</span></td>
                        <td>{left} {plural(left, 'сотрудник', 'сотрудника', 'сотрудников')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              );
            }}
          </Guard>
        </Section>
      </div>
      <div className="ax-foot">
        <p className="ax-info"><AppIcon name="info" size={18} /> История сохраняется: редакция документа, дата приглашения, подтверждение сотрудника и напоминания.</p>
      </div>
    </div>
  );

  return { sub: 'Ознакомления и охрана труда: подтверждение документов и контроль HR', kpis, body };
}
