/**
 * «Посещаемость»: один экран, четыре режима — за день, журнал отметок,
 * неделя и период.
 *
 * Лист, шапка, информационная полоса, вкладки и место под содержимое —
 * постоянные: при смене режима они не пересоздаются и не меняют высоту.
 * Меняется только то, что внутри, и линия под вкладкой переезжает.
 * Каждый режим — свой набор данных; грузится только выбранный.
 *
 * Ни одна величина здесь не считается по своим правилам: присутствие,
 * опоздания, рабочее время и отсутствия считает сервер. Отсутствием
 * считается только подтверждённое — неподтверждённый больничный им не
 * является. «Нет отметки» — не прогул, а вопрос, и день, который ещё не
 * наступил, не может быть «без отметки».
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { Dropdown } from '../components/AppSelect';
import { DatePicker } from '../components/DatePicker';
import { DayCard } from '../components/DayCard';
import { SlideTabs } from '../components/SlideTabs';
import { longDate, today, useBlock, type Block } from '../features/dashboard/data';
import { clock, clockOnDay } from '../features/time/zone';
import '../styles/attendance.css';

type Mode = 'day' | 'log' | 'week' | 'period';

const MODES: { key: Mode; title: string }[] = [
  { key: 'day', title: 'За день' },
  { key: 'log', title: 'Журнал отметок' },
  { key: 'week', title: 'Неделя' },
  { key: 'period', title: 'Период' },
];

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const WEEKDAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

/** Режим из адреса. Старые ссылки (`tab=log`, `period=week|month|range`) открывают свой режим. */
function modeOf(params: URLSearchParams): Mode {
  const tab = params.get('tab');
  if (tab === 'log' || tab === 'week' || tab === 'period' || tab === 'day') return tab;
  const period = params.get('period');
  if (period === 'week') return 'week';
  if (period === 'month' || period === 'range') return 'period';
  return 'day';
}

type Tone = 'ok' | 'warn' | 'bad' | 'violet' | 'blue' | 'grey' | 'idle';

/** Состояние строки словами и цветом — коротко, как говорят в отделе кадров. */
function stateOf(row: api.PresenceRow, past: boolean): { title: string; tone: Tone } {
  const late = row.late_minutes ?? 0;
  switch (row.state) {
    case 'IN_OFFICE':
      return late > 0 ? { title: `Опоздал на ${late} мин`, tone: 'warn' } : { title: past ? 'Не отметил уход' : 'В офисе', tone: past ? 'warn' : 'ok' };
    case 'LEFT':
      return late > 0 ? { title: `Опоздал на ${late} мин`, tone: 'warn' } : { title: 'Смена завершена', tone: 'idle' };
    case 'LATE':
      // Предупредил, что задерживается: это не то же самое, что пропал.
      return { title: 'Предупредил об опоздании', tone: 'warn' };
    case 'NOT_COME':
      return row.notice_kind === 'ABSENT' ? { title: 'Предупредил: не придёт', tone: 'warn' } : { title: 'Нет отметки', tone: 'bad' };
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
      return { title: 'Неизвестно', tone: 'grey' };
  }
}

/**
 * Требует внимания HR. Одно правило на чип, счётчик и подсветку строки:
 * нет отметки, опоздание, предупреждение, противоречивые отметки, отметка
 * вне геозоны и незакрытая смена прошедшего дня.
 */
function needsAttention(row: api.PresenceRow, past: boolean): boolean {
  if (row.state === 'NOT_COME' || row.state === 'LATE') return true;
  if ((row.late_minutes ?? 0) > 0) return true;
  if (row.conflicting_marks || row.outside_geofence) return true;
  return past && row.open_session_id !== null;
}

type Ctx = {
  params: URLSearchParams;
  patch: (changes: Record<string, string | null>) => void;
  attempt: number;
  offices: api.Office[];
  departments: { id: string; name: string }[];
  order: (span: { from: string; to: string }) => void;
  openDay: (row: api.PresenceRow, day: string, zone: string) => void;
};

type View = {
  title: string;
  sub: ReactNode;
  tools: ReactNode;
  strip: ReactNode;
  body: ReactNode;
};

export function AttendancePage() {
  const [params, setParams] = useSearchParams();
  const mode = modeOf(params);
  const [attempt, setAttempt] = useState(0);

  const patch = useCallback(
    (changes: Record<string, string | null>) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const [directory] = useBlock(
    (signal) =>
      Promise.all([
        api.offices(signal),
        api.departmentsPage({ limit: '200', status: 'ACTIVE' }, signal),
      ]).then(([offices, departments]) => ({
        offices: offices.items.filter((one) => one.status === 'ACTIVE'),
        departments: departments.items.map((one) => ({ id: one.id, name: one.name })),
      })),
    'attendance-directory',
  );

  // Выгрузка — в общую очередь отчётов; файл появится в «Отчётах».
  const [ordered, setOrdered] = useState<string | null>(null);
  useEffect(() => {
    if (!ordered) return;
    const timer = window.setTimeout(() => setOrdered(null), 6000);
    return () => window.clearTimeout(timer);
  }, [ordered]);
  const office = params.get('office_id') ?? '';
  const region = params.get('region_id') ?? '';
  const department = params.get('department_id') ?? '';
  const order = useCallback((span: { from: string; to: string }) => {
    setOrdered(null);
    api.orderExport({
      kind: 'attendance', fmt: 'xlsx', date_from: span.from, date_to: span.to,
      ...(office ? { office_id: office } : region ? { region_id: region } : {}),
      ...(department ? { department_id: department } : {}),
    })
      .then(() => setOrdered('Выгрузка поставлена в очередь'))
      .catch((error) => setOrdered(messageFor(error)));
  }, [office, region, department]);

  // Карточка дня сотрудника с ручной отметкой — выдвижной панелью.
  const [opened, setOpened] = useState<{ row: api.PresenceRow; day: string; zone: string } | null>(null);

  const ctx: Ctx = {
    params,
    patch,
    attempt,
    offices: directory.state === 'ready' ? directory.data.offices : [],
    departments: directory.state === 'ready' ? directory.data.departments : [],
    order,
    openDay: (row, day, zone) => setOpened({ row, day, zone }),
  };

  const views: Record<Mode, View> = {
    day: useDayMode(mode === 'day', ctx),
    log: useLogMode(mode === 'log', ctx),
    week: useWeekMode(mode === 'week', ctx),
    period: usePeriodMode(mode === 'period', ctx),
  };
  const view = views[mode];

  return (
    <AppShell breadcrumb="Посещаемость" section="attendance">
      <div className="at">
        <section className="at-sheet">
          <header className="at-head">
            <div className="at-head__text">
              <h1 className="at-head__title">{view.title}</h1>
              <p className="at-head__sub">{view.sub}</p>
            </div>
            <div className="at-head__tools">
              {view.tools}
              <button type="button" className="at-icon" aria-label="Обновить" onClick={() => setAttempt((n) => n + 1)}>
                <AppIcon name="refresh" size={18} />
              </button>
            </div>
            {ordered && (
              <p className="at-toast" role="status">
                {ordered} — <Link to="/reports">файл появится в отчётах</Link>
              </p>
            )}
          </header>

          <div className="at-strip">{view.strip}</div>

          <SlideTabs
            label="Режим посещаемости"
            value={mode}
            items={MODES}
            onPick={(key) => patch({ tab: key === 'day' ? null : key, period: null, page: null, search: null, quick: null, kind: null, source: null, who: null, state: null })}
            classes={{ list: 'at-tabs', tab: 'at-tab', on: 'at-tab--on', ink: 'at-tabs__ink' }}
          />

          <div className="at-place">
            <div key={mode} className="at-pane" role="tabpanel" aria-label={MODES.find((one) => one.key === mode)?.title}>
              {view.body}
            </div>
          </div>
        </section>
      </div>

      {opened && (
        <div className="at-drawer" role="presentation">
          <button type="button" className="at-drawer__scrim" aria-label="Закрыть" onClick={() => setOpened(null)} />
          <div className="at-drawer__panel">
            <DayCard row={opened.row} day={opened.day} timezone={opened.zone} canAdd
                     onClose={() => setOpened(null)}
                     onChanged={() => { setOpened(null); setAttempt((n) => n + 1); }} />
          </div>
        </div>
      )}
    </AppShell>
  );
}

// --- За день ------------------------------------------------------------------------

const DAY_PAGE = 12;

function useDayMode(active: boolean, ctx: Ctx): View {
  const { params, patch, attempt } = ctx;
  const day = params.get('date') ?? today();
  const office = params.get('office_id') ?? '';
  const region = params.get('region_id') ?? '';
  const department = params.get('department_id') ?? '';
  const state = params.get('state') ?? '';
  const chosenQuick = params.get('quick');
  const search = params.get('search') ?? '';
  const page = Math.max(1, Number(params.get('page') ?? '1'));
  const picked = params.get('employee') ?? '';
  const past = day < today();

  const scope = useMemo(() => ({
    date: day,
    ...(office ? { office_id: office } : region ? { region_id: region } : {}),
    ...(department ? { department_id: department } : {}),
  }), [day, office, region, department]);
  const key = `${JSON.stringify(scope)}|${attempt}`;

  const [cards] = useBlock((signal) => api.dashboard(scope, signal), key, active);
  // Сдвиг явки к прошлому дню — посчитан сервером одним правилом.
  const [shift] = useBlock(
    (signal) => api.analyticsOverview({
      date_from: day, date_to: day,
      ...(office ? { office_id: office } : region ? { region_id: region } : {}),
      ...(department ? { department_id: department } : {}),
      people_limit: '1',
    }, signal).then((body) => body.summary.difference_points).catch(() => null),
    key,
    active,
  );
  const [roster] = useBlock((signal) => api.presenceDay(scope, signal), key, active);

  const [draft, setDraft] = useState(search);
  useEffect(() => setDraft(search), [search]);
  useEffect(() => {
    if (draft === search) return;
    const timer = window.setTimeout(() => patch({ search: draft || null, page: null }), 300);
    return () => window.clearTimeout(timer);
  }, [draft, search, patch]);

  const card = cards.state === 'ready' ? Object.fromEntries(cards.data.cards.map((c) => [c.key, c.value])) : {};
  const rows = roster.state === 'ready' ? roster.data.items : [];
  // Человек, пришедший по ссылке из его карточки, открывается сам.
  useEffect(() => {
    if (!active || !picked || roster.state !== 'ready') return;
    const row = roster.data.items.find((one) => one.employee_id === picked);
    if (row) ctx.openDay(row, day, roster.data.timezone);
    patch({ employee: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, picked, roster]);

  const attention = rows.filter((row) => needsAttention(row, past));
  // По умолчанию — «Требуют внимания», но пустой таблицы вместо людей
  // человек видеть не должен: если внимания никто не требует, — все.
  const quick = chosenQuick ?? (roster.state === 'ready' && attention.length === 0 ? 'all' : 'attention');
  const inOffice = rows.filter((row) => row.state === 'IN_OFFICE');
  const needle = search.trim().toLowerCase();
  const shown = (quick === 'attention' ? attention : quick === 'office' ? inOffice : rows)
    .filter((row) => !state || row.state === state)
    .filter((row) => !needle || row.full_name.toLowerCase().includes(needle));
  const pages = Math.max(1, Math.ceil(shown.length / DAY_PAGE));
  const slice = shown.slice((page - 1) * DAY_PAGE, page * DAY_PAGE);

  const expected = card['should_work_today'] ?? 0;
  const came = card['came'] ?? 0;
  const share = expected > 0 ? Math.round((came / expected) * 100) : null;
  const diff = shift.state === 'ready' ? shift.data : null;

  return {
    title: 'Посещаемость',
    sub: <>{longDate(day)} · По данным отметок</>,
    tools: (
      <>
        <DatePicker label="Дата" value={day} now={today()} allowEmpty
                    onChange={(value) => patch({ date: value || null, page: null })} />
        <Dropdown label="Офис" empty="Все офисы" value={office}
                  options={ctx.offices.map((one) => ({ id: one.id, name: one.name }))}
                  onChange={(value) => patch({ office_id: value || null, region_id: null, page: null })} />
        <button type="button" className="at-btn" onClick={() => ctx.order({ from: day, to: day })}>
          <AppIcon name="download" size={18} /> Экспорт
        </button>
      </>
    ),
    strip: (
      <Guard block={cards} name="показатели дня" strip>
        {() => (
          <div className="at-facts">
            <div className="at-fact at-fact--main">
              <span className="at-fact__label">{past ? 'Явка за день' : 'Явка сегодня'}</span>
              <span className="at-fact__line">
                <b className="at-fact__big">{share === null ? '—' : `${share}%`}</b>
                <span>{expected > 0 ? `${came} из ${expected} по графику` : 'По графику никого не ждали'}</span>
              </span>
              <span className="at-meter"><i style={{ width: `${share ?? 0}%` }} /></span>
              {diff !== null && (
                <small className={diff >= 0 ? 'at-delta at-delta--up' : 'at-delta at-delta--down'}>
                  {diff >= 0 ? '+' : '−'}{Math.abs(diff).toLocaleString('ru-RU')} п.п. к вчера
                </small>
              )}
            </div>
            <div className="at-fact">
              <span className="at-fact__label">{past ? 'Статус дня' : 'Статус на сейчас'}</span>
              <ul className="at-counts">
                <li><i className="at-dot at-dot--ok" />{past ? 'Отметились' : 'В офисе'}<b>{past ? came : card['in_office'] ?? 0}</b></li>
                <li><i className="at-dot at-dot--warn" />Опоздали<b>{card['late'] ?? 0}</b></li>
                <li><i className="at-dot at-dot--bad" />Без отметки<b>{card['not_come'] ?? 0}</b></li>
              </ul>
            </div>
            <div className="at-fact">
              <span className="at-fact__label">{past ? 'Отсутствовали' : 'Сегодня отсутствуют'}</span>
              <ul className="at-counts">
                <li><i className="at-dot at-dot--violet" />В отпуске<b>{card['vacation'] ?? 0}</b></li>
                <li><i className="at-dot at-dot--blue" />На больничном<b>{card['sick_leave'] ?? 0}</b></li>
                <li><i className="at-dot at-dot--idle" />Завершили смену<b>{card['left'] ?? 0}</b></li>
              </ul>
              <small className="at-fact__note">Только подтверждённые отсутствия</small>
            </div>
          </div>
        )}
      </Guard>
    ),
    body: (
      <>
        <div className="at-filters">
          <div className="at-chips" role="group" aria-label="Отбор сотрудников">
            {([
              ['attention', 'Требуют внимания', attention.length],
              ['all', 'Все сотрудники', rows.length],
              ['office', past ? 'Были в офисе' : 'Сейчас в офисе', inOffice.length],
            ] as const).map(([keyName, title, count]) => (
              <button key={keyName} type="button" aria-pressed={quick === keyName}
                      className={quick === keyName ? 'at-chip at-chip--on' : 'at-chip'}
                      onClick={() => patch({ quick: keyName, page: null })}>
                {title}<span className="at-chip__n">{roster.state === 'ready' ? count : '·'}</span>
              </button>
            ))}
            {state && (
              <button type="button" className="at-chip at-chip--on" onClick={() => patch({ state: null })}
                      aria-label="Снять отбор по статусу">
                {STATE_TITLE[state] ?? state} <AppIcon name="close" size={16} />
              </button>
            )}
          </div>
          <label className="at-find">
            <AppIcon name="search" size={16} />
            <input type="search" value={draft} placeholder="Найти сотрудника" aria-label="Найти сотрудника"
                   onChange={(event) => setDraft(event.target.value)} />
          </label>
          <Dropdown label="Отдел" empty="Все отделы" value={department} options={ctx.departments}
                    onChange={(value) => patch({ department_id: value || null, page: null })} />
        </div>

        <div className="at-table at-table--day" role="table" aria-label="Сотрудники за день">
          <div className="at-table__head" role="row">
            <span role="columnheader">Сотрудник</span>
            <span role="columnheader">Офис / отдел</span>
            <span role="columnheader">График</span>
            <span role="columnheader">Приход</span>
            <span role="columnheader">Уход</span>
            <span role="columnheader">Рабочее время</span>
            <span role="columnheader">Статус</span>
            <span role="columnheader" className="at-right">Действие</span>
          </div>
          <div className="at-table__body">
            <Guard block={roster} name="сотрудников">
              {(data) => (
                <>
                  {data.truncated && <p className="at-warn">Показаны не все сотрудники: состав больше одного ответа. Сузьте фильтры.</p>}
                  {slice.length === 0 ? (
                    <p className="at-empty">
                      {quick === 'attention' && !needle && !state
                        ? 'Сейчас никто не требует внимания.'
                        : 'По этим условиям никого нет.'}
                    </p>
                  ) : slice.map((row) => {
                    const look = stateOf(row, past);
                    const hot = needsAttention(row, past);
                    return (
                      <div key={row.employee_id} role="row"
                           className={`at-row${hot ? (row.state === 'NOT_COME' ? ' at-row--bad' : ' at-row--warn') : ''}`}>
                        <span role="cell" className="at-who">
                          <span className="at-face" aria-hidden="true">{initials(row.full_name)}</span>
                          <b>{row.full_name}</b>
                        </span>
                        <span role="cell" className="at-muted">
                          {[row.office_name, row.department_name].filter(Boolean).join(' · ') || '—'}
                        </span>
                        <span role="cell" className="at-muted">
                          {row.scheduled_start && row.scheduled_end ? `${row.scheduled_start}–${row.scheduled_end}` : 'Не задан'}
                        </span>
                        <span role="cell" className={(row.late_minutes ?? 0) > 0 ? 'at-num at-num--warn' : 'at-num'}>
                          {clockOnDay(row.first_entry_at, data.timezone, day)}
                        </span>
                        <span role="cell" className="at-num">{clockOnDay(row.last_exit_at, data.timezone, day)}</span>
                        <span role="cell" className="at-num">{row.seconds > 0 ? duration(row.seconds) : '—'}</span>
                        <span role="cell" className={`at-state at-state--${look.tone}`}>
                          <i className={`at-dot at-dot--${look.tone}`} />
                          <span className="at-state__text">
                            {look.title}
                            {/* Что человек сам написал — рядом со статусом, а
                                не только в базе: кадровик решает, звонить ли. */}
                            {row.notice_comment && <small>{row.notice_comment}</small>}
                          </span>
                        </span>
                        <span role="cell" className="at-right">
                          <button type="button" className="at-link" onClick={() => ctx.openDay(row, day, data.timezone)}>
                            {hot ? 'Исправить' : 'Просмотр'}
                          </button>
                        </span>
                      </div>
                    );
                  })}
                </>
              )}
            </Guard>
          </div>
        </div>

        <Footer
          text={roster.state === 'ready' ? shownLine(page, DAY_PAGE, shown.length, 'сотрудник') : ''}
          page={page} pages={pages} onGo={(next) => patch({ page: next === 1 ? null : String(next) })}
        />
      </>
    ),
  };
}

const STATE_TITLE: Record<string, string> = {
  IN_OFFICE: 'В офисе', LEFT: 'Ушли', LATE: 'Предупредили об опоздании', NOT_COME: 'Нет отметки',
  VACATION: 'В отпуске', SICK_LEAVE: 'На больничном', OTHER_ABSENCE: 'Отсутствуют',
  DAY_OFF: 'Выходной', NO_SCHEDULE: 'Без графика',
};

// --- Журнал отметок -----------------------------------------------------------------

const LOG_PAGE = 14;
/** Потолок журнала за день: больше — сообщаем, что показаны не все. */
const LOG_CAP = 3000;

const SOURCE_TITLE: Record<string, string> = { QR: 'QR-код', MANUAL: 'Вручную HR', IMPORT: 'Импорт' };

function useLogMode(active: boolean, ctx: Ctx): View {
  const { params, patch, attempt } = ctx;
  const day = params.get('date') ?? today();
  const office = params.get('office_id') ?? '';
  const kind = params.get('kind') ?? '';
  const source = params.get('source') ?? '';
  const search = params.get('search') ?? '';
  const page = Math.max(1, Number(params.get('page') ?? '1'));
  const key = `${day}|${office}|${attempt}`;

  // Весь день целиком, страницами по 200: счётчики, активность по часам и
  // поиск считаются по всему дню, а не по первой странице.
  const [log] = useBlock(async (signal) => {
    const items: api.EventRow[] = [];
    let cursor = '';
    let more = true;
    while (more && items.length < LOG_CAP) {
      const pageData = await api.events({
        date_from: day, date_to: day, limit: '200',
        ...(office ? { office_id: office } : {}),
        ...(cursor ? { cursor } : {}),
      }, signal);
      items.push(...pageData.items);
      more = pageData.has_more && Boolean(pageData.next_cursor);
      cursor = pageData.next_cursor ?? '';
    }
    return { items, capped: more };
  }, key, active);
  // Состав дня — чтобы назвать вход опозданием так же, как его назвал сервер.
  const [roster] = useBlock(
    (signal) => api.presenceDay({ date: day, ...(office ? { office_id: office } : {}) }, signal),
    key, active,
  );

  const [draft, setDraft] = useState(search);
  useEffect(() => setDraft(search), [search]);
  useEffect(() => {
    if (draft === search) return;
    const timer = window.setTimeout(() => patch({ search: draft || null, page: null }), 300);
    return () => window.clearTimeout(timer);
  }, [draft, search, patch]);

  const all = log.state === 'ready' ? [...log.data.items].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at)) : [];
  const zone = roster.state === 'ready' ? roster.data.timezone : '';
  const byEmployee = new Map((roster.state === 'ready' ? roster.data.items : []).map((row) => [row.employee_id, row]));
  const review = (one: api.EventRow) => one.verification_status !== 'ACCEPTED' || one.inside_geofence === false;
  const lateOf = (one: api.EventRow) => {
    const row = byEmployee.get(one.employee?.id ?? one.employee_id);
    return one.event_type === 'ENTRY' && row && (row.late_minutes ?? 0) > 0 && row.first_entry_at === one.occurred_at
      ? row.late_minutes ?? 0 : 0;
  };
  const needle = search.trim().toLowerCase();
  const shown = all
    .filter((one) => !kind || (kind === 'MANUAL' ? one.source === 'MANUAL' : one.event_type === kind))
    .filter((one) => !source || one.source === source)
    .filter((one) => !needle || (one.employee?.full_name ?? '').toLowerCase().includes(needle));
  const pages = Math.max(1, Math.ceil(shown.length / LOG_PAGE));
  const slice = shown.slice((page - 1) * LOG_PAGE, page * LOG_PAGE);

  const hours = Array.from({ length: 13 }, (_, i) => 8 + i);
  const perHour = hours.map((hour) => all.filter((one) => hourIn(one.occurred_at, zone) === hour).length);
  const peak = Math.max(1, ...perHour);
  const isToday = day === today();

  return {
    title: 'Журнал отметок',
    sub: <>События входа, выхода и ручных исправлений · {longDate(day)}</>,
    tools: (
      <>
        <DatePicker label="Дата" value={day} now={today()} allowEmpty
                    onChange={(value) => patch({ date: value || null, page: null })} />
        <button type="button" className="at-btn" onClick={() => ctx.order({ from: day, to: day })}>
          <AppIcon name="download" size={18} /> Экспорт
        </button>
      </>
    ),
    strip: (
      <Guard block={log} name="журнал" strip>
        {() => (
          <div className="at-facts at-facts--three">
            <Big icon="doc" value={all.length} title={plural(all.length, ['событие', 'события', 'событий'])} note={isToday ? 'за сегодня' : 'за день'} />
            <Big icon="alert" tone="warn" value={all.filter(review).length} title="требуют проверки" note="отклонены или вне геозоны" />
            <Big icon="pencil" value={all.filter((one) => one.source === 'MANUAL').length}
                 title={plural(all.filter((one) => one.source === 'MANUAL').length, ['исправление HR', 'исправления HR', 'исправлений HR'])}
                 note="внесены вручную" />
          </div>
        )}
      </Guard>
    ),
    body: (
      <>
        <div className="at-filters">
          <div className="at-chips" role="group" aria-label="Вид события">
            {([['', 'Все события', 'blue'], ['ENTRY', 'Входы', 'ok'], ['EXIT', 'Выходы', 'blue'], ['MANUAL', 'Ручные исправления', 'violet']] as const).map(([value, title, tone]) => (
              <button key={value} type="button" aria-pressed={kind === value}
                      className={kind === value ? 'at-chip at-chip--on' : 'at-chip'}
                      onClick={() => patch({ kind: value || null, page: null })}>
                {value && <i className={`at-dot at-dot--${tone}`} />}{title}
              </button>
            ))}
          </div>
          <label className="at-find">
            <AppIcon name="search" size={16} />
            <input type="search" value={draft} placeholder="Найти сотрудника" aria-label="Найти сотрудника"
                   onChange={(event) => setDraft(event.target.value)} />
          </label>
          <Dropdown label="Офис" empty="Все офисы" value={office}
                    options={ctx.offices.map((one) => ({ id: one.id, name: one.name }))}
                    onChange={(value) => patch({ office_id: value || null, page: null })} />
          <Dropdown label="Источник" empty="Все источники" value={source}
                    options={Object.entries(SOURCE_TITLE).map(([id, name]) => ({ id, name }))}
                    onChange={(value) => patch({ source: value || null, page: null })} />
        </div>

        <div className="at-hours" aria-label={`Активность по часам: ${hours.map((h, i) => `${h}:00 — ${perHour[i]}`).join(', ')}`}>
          <span className="at-hours__title">Активность по часам</span>
          <div className="at-hours__bars">
            {hours.map((hour, i) => (
              <span key={hour} className="at-hours__col">
                <i style={{ height: `${Math.round(((perHour[i] ?? 0) / peak) * 100)}%` }} title={`${hour}:00 — ${perHour[i]}`} />
                <small>{String(hour).padStart(2, '0')}:00</small>
              </span>
            ))}
          </div>
        </div>

        <div className="at-table at-table--log" role="table" aria-label="Журнал отметок">
          <div className="at-table__head" role="row">
            <span role="columnheader">Время</span>
            <span role="columnheader">Сотрудник</span>
            <span role="columnheader">Событие</span>
            <span role="columnheader">Точка / офис</span>
            <span role="columnheader">Источник</span>
            <span role="columnheader">Результат</span>
            <span role="columnheader" className="at-right">Действие</span>
          </div>
          <div className="at-table__body">
            <Guard block={log} name="журнал">
              {(data) => (
                <>
                  {data.capped && <p className="at-warn">Показаны первые {LOG_CAP} событий дня. Сузьте отбор по офису.</p>}
                  {slice.length === 0 ? (
                    <p className="at-empty">{all.length === 0 ? 'За этот день отметок нет.' : 'По этим условиям событий нет.'}</p>
                  ) : slice.map((one) => {
                    const late = lateOf(one);
                    const bad = review(one);
                    const manual = one.source === 'MANUAL';
                    const row = byEmployee.get(one.employee?.id ?? one.employee_id);
                    return (
                      <div key={one.id} role="row" className={bad || late > 0 ? 'at-row at-row--warn' : 'at-row'}>
                        <span role="cell" className="at-num">{clock(one.occurred_at, zone)}</span>
                        <span role="cell" className="at-who">
                          <span className="at-face" aria-hidden="true">{initials(one.employee?.full_name ?? '')}</span>
                          <b>{one.employee?.full_name ?? 'Сотрудник'}</b>
                        </span>
                        <span role="cell" className="at-event">
                          <AppIcon name={manual ? 'pencil' : one.event_type === 'ENTRY' ? 'next' : 'back'} size={16} />
                          {manual ? `Ручное исправление · ${one.event_type === 'ENTRY' ? 'вход' : 'выход'}`
                            : one.event_type === 'ENTRY' ? (late > 0 ? 'Вход с опозданием' : 'Вход') : 'Выход'}
                        </span>
                        <span role="cell" className="at-muted">
                          {[one.office_name, one.qr_point_name].filter(Boolean).join(' · ') || '—'}
                        </span>
                        <span role="cell" className="at-muted">
                          {manual ? `HR${one.author_name ? ` (${shortName(one.author_name)})` : ''}` : SOURCE_TITLE[one.source] ?? 'Другой источник'}
                        </span>
                        <span role="cell" className={`at-state at-state--${bad ? 'bad' : late > 0 ? 'warn' : 'ok'}`}>
                          <i className={`at-dot at-dot--${bad ? 'bad' : late > 0 ? 'warn' : 'ok'}`} />
                          {bad ? (one.verification_status !== 'ACCEPTED' ? 'Отклонено' : 'Вне геозоны')
                            : late > 0 ? `Опоздание на ${late} мин` : manual ? 'Исправлено' : 'Принято'}
                        </span>
                        <span role="cell" className="at-right">
                          {row ? (
                            <button type="button" className="at-link" onClick={() => ctx.openDay(row, day, zone)}>День</button>
                          ) : <span className="at-muted">—</span>}
                        </span>
                      </div>
                    );
                  })}
                </>
              )}
            </Guard>
          </div>
        </div>

        <Footer text={log.state === 'ready' ? shownLine(page, LOG_PAGE, shown.length, 'событие') : ''}
                page={page} pages={pages} onGo={(next) => patch({ page: next === 1 ? null : String(next) })} />
      </>
    ),
  };
}

// --- Неделя -------------------------------------------------------------------------

const WEEK_PAGE = 10;

function useWeekMode(active: boolean, ctx: Ctx): View {
  const { params, patch, attempt } = ctx;
  const anchor = params.get('date') ?? today();
  const office = params.get('office_id') ?? '';
  const region = params.get('region_id') ?? '';
  const department = params.get('department_id') ?? '';
  const page = Math.max(1, Number(params.get('page') ?? '1'));
  const monday = weekStart(anchor);
  const days = Array.from({ length: 7 }, (_, i) => addDays(monday, i));
  const sunday = days[6]!;
  const place = {
    ...(office ? { office_id: office } : region ? { region_id: region } : {}),
    ...(department ? { department_id: department } : {}),
  };
  const key = `${monday}|${office}|${region}|${department}|${attempt}`;
  const now = today();

  // Ячейки — состояния, которые сервер отдал по каждому дню недели.
  const [grid] = useBlock(
    (signal) => Promise.all(days.map((d) => (d > now ? Promise.resolve(null) : api.presenceDay({ date: d, ...place }, signal)))),
    key, active,
  );
  // Полоса и «Итого» — сводка сервера за неделю, со сравнением с прошлой.
  const [summary] = useBlock(
    (signal) => api.analyticsOverview({ date_from: monday, date_to: sunday, ...place, people_limit: '2000' }, signal),
    key, active,
  );

  const pagesData = grid.state === 'ready' ? grid.data : [];
  const people = new Map<string, { id: string; name: string; office: string | null; schedule: string | null }>();
  for (const dayData of pagesData) {
    for (const row of dayData?.items ?? []) {
      if (!people.has(row.employee_id)) {
        people.set(row.employee_id, {
          id: row.employee_id, name: row.full_name, office: row.office_name,
          schedule: row.scheduled_start && row.scheduled_end ? `${row.scheduled_start}–${row.scheduled_end}` : null,
        });
      } else if (!people.get(row.employee_id)!.schedule && row.scheduled_start && row.scheduled_end) {
        people.get(row.employee_id)!.schedule = `${row.scheduled_start}–${row.scheduled_end}`;
      }
    }
  }
  const list = [...people.values()].sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  const pages = Math.max(1, Math.ceil(list.length / WEEK_PAGE));
  const slice = list.slice((page - 1) * WEEK_PAGE, page * WEEK_PAGE);
  const totals = new Map((summary.state === 'ready' ? summary.data.employees : []).map((one) => [one.id, one]));
  const zone = pagesData.find((one) => one)?.timezone ?? '';

  const perDay = summary.state === 'ready' ? summary.data.days : [];

  return {
    title: 'Посещаемость',
    sub: <>{rangeTitle(monday, sunday)} · Недельная сводка</>,
    tools: (
      <>
        <span className="at-stepper">
          <button type="button" aria-label="Прошлая неделя" onClick={() => patch({ date: addDays(monday, -7), page: null })}>
            <AppIcon name="back" size={16} />
          </button>
          <span>{rangeTitle(monday, sunday)}</span>
          <button type="button" aria-label="Следующая неделя" disabled={addDays(monday, 7) > now}
                  onClick={() => patch({ date: addDays(monday, 7), page: null })}>
            <AppIcon name="next" size={16} />
          </button>
        </span>
        <button type="button" className="at-btn" onClick={() => ctx.order({ from: monday, to: sunday })}>
          <AppIcon name="download" size={18} /> Экспорт
        </button>
      </>
    ),
    strip: (
      <Guard block={summary} name="сводку недели" strip>
        {(data) => (
          <div className="at-facts at-facts--four">
            <Share label="Явка за неделю" share={data.summary.attendance} diff={data.summary.difference_points} against="к прошлой неделе" />
            <Big icon="clock" tone="warn" value={data.summary.late.numerator} title={plural(data.summary.late.numerator, ['опоздание', 'опоздания', 'опозданий'])} note="после допуска" />
            <Big icon="alert" tone="bad" value={data.summary.missed_days} title={`${plural(data.summary.missed_days, ['день', 'дня', 'дней'])} без отметки`} note="по графику, но без входа" />
            <Big icon="calendar" value={data.summary.vacation_days + data.summary.sick_leave_days + data.summary.other_absence_days}
                 title="дней отсутствий" note={`${data.summary.vacation_days} отпуск · ${data.summary.sick_leave_days} больничный`} />
          </div>
        )}
      </Guard>
    ),
    body: (
      <>
        <div className="at-daysline" aria-label="Явка по дням">
          <span className="at-daysline__title">Явка по дням</span>
          {days.map((d) => {
            const row = perDay.find((one) => one.day === d);
            const value = row?.percent ?? null;
            return (
              <span key={d} className="at-daysline__cell">
                <small>{weekdayOf(d)}</small>
                <b>{d > now ? '—' : value === null ? '—' : `${Math.round(value)}%`}</b>
                <span className="at-meter at-meter--thin"><i style={{ width: `${value ?? 0}%` }} /></span>
              </span>
            );
          })}
        </div>

        <div className="at-table at-table--week" role="table" aria-label="Неделя по сотрудникам">
          <div className="at-table__head" role="row">
            <span role="columnheader">Сотрудник</span>
            <span role="columnheader">Офис / график</span>
            {days.map((d) => (
              <span key={d} role="columnheader" className={isWeekend(d) ? 'at-center at-weekend-head' : 'at-center'}>
                {weekdayOf(d)} {Number(d.slice(8))}
              </span>
            ))}
            <span role="columnheader" className="at-center">Итого</span>
          </div>
          <div className="at-table__body">
            <Guard block={grid} name="неделю">
              {(data) => (
                <>
                  {data.some((one) => one?.truncated) && <p className="at-warn">Показаны не все сотрудники: состав больше одного ответа. Сузьте фильтры.</p>}
                  {slice.length === 0 ? <p className="at-empty">За эту неделю по выбранным условиям никого нет.</p> : slice.map((person) => {
                    const total = totals.get(person.id);
                    return (
                      <div key={person.id} role="row" className="at-row">
                        <span role="cell" className="at-who">
                          <span className="at-face" aria-hidden="true">{initials(person.name)}</span>
                          <b>{person.name}</b>
                        </span>
                        <span role="cell" className="at-muted at-two">
                          <span>{person.office ?? '—'}</span>
                          <small>{person.schedule ?? 'Без графика'}</small>
                        </span>
                        {days.map((d, i) => {
                          const row = data[i]?.items.find((one) => one.employee_id === person.id);
                          const cell = weekCell(row, d > now, zone, d);
                          return (
                            <span key={d} role="cell" className={`at-cell at-cell--${cell.tone}`} title={cell.hint}>
                              {cell.text}
                            </span>
                          );
                        })}
                        <span role="cell" className="at-total">
                          <b>{total?.attendance.percent === null || !total ? '—' : `${Math.round(total.attendance.percent)}%`}</b>
                          <small>{total?.seconds ? duration(total.seconds) : '—'}</small>
                        </span>
                      </div>
                    );
                  })}
                </>
              )}
            </Guard>
          </div>
        </div>

        <Footer text={grid.state === 'ready' ? shownLine(page, WEEK_PAGE, list.length, 'сотрудник') : ''}
                page={page} pages={pages} onGo={(next) => patch({ page: next === 1 ? null : String(next) })} />
      </>
    ),
  };
}

/** Ячейка недели: время, опоздание, отсутствие или прочерк. */
function weekCell(row: api.PresenceRow | undefined, future: boolean, zone: string, day: string): { text: string; tone: string; hint: string } {
  if (future) return { text: '', tone: 'future', hint: 'День ещё не наступил' };
  if (!row) return { text: '—', tone: 'off', hint: 'Не в составе на этот день' };
  const late = row.late_minutes ?? 0;
  switch (row.state) {
    case 'IN_OFFICE':
    case 'LEFT': {
      const span = `${clockOnDay(row.first_entry_at, zone, day)}–${row.last_exit_at ? clockOnDay(row.last_exit_at, zone, day) : '…'}`;
      return late > 0 ? { text: `Опозд. ${late} мин`, tone: 'warn', hint: span } : { text: span, tone: 'ok', hint: duration(row.seconds) };
    }
    case 'LATE':
      return { text: 'Предупредил', tone: 'warn', hint: 'Предупредил об опоздании' };
    case 'NOT_COME':
      return { text: 'Нет отметки', tone: 'bad', hint: row.notice_kind === 'ABSENT' ? 'Предупредил, что не придёт' : 'По графику, но без входа' };
    case 'VACATION':
      return { text: 'Отпуск', tone: 'violet', hint: 'Подтверждённый отпуск' };
    case 'SICK_LEAVE':
      return { text: 'Бол.', tone: 'blue', hint: 'Подтверждённый больничный' };
    case 'OTHER_ABSENCE':
      return { text: 'Отсутств.', tone: 'violet', hint: row.absence_name ?? 'Подтверждённое отсутствие' };
    case 'DAY_OFF':
      return { text: '—', tone: 'off', hint: 'Выходной' };
    default:
      return { text: '—', tone: 'off', hint: 'Без графика' };
  }
}

// --- Период -------------------------------------------------------------------------

const PERIOD_PAGE = 10;

function usePeriodMode(active: boolean, ctx: Ctx): View {
  const { params, patch, attempt } = ctx;
  const now = today();
  const period = params.get('period');
  const anchor = params.get('date') ?? now;
  const monthStart = `${anchor.slice(0, 8)}01`;
  const from = params.get('from') ?? (period === 'month' || !params.get('from') ? monthStart : anchor);
  const to = params.get('to') ?? (period === 'month' ? monthEnd(anchor) : anchor);
  const office = params.get('office_id') ?? '';
  const region = params.get('region_id') ?? '';
  const department = params.get('department_id') ?? '';
  const view = params.get('who') === 'attention' ? 'attention' : 'all';
  const search = params.get('search') ?? '';
  const page = Math.max(1, Number(params.get('page') ?? '1'));
  const place = {
    ...(office ? { office_id: office } : region ? { region_id: region } : {}),
    ...(department ? { department_id: department } : {}),
  };
  const key = `${from}|${to}|${office}|${region}|${department}|${attempt}`;

  const [overview] = useBlock(
    (signal) => api.analyticsOverview({ date_from: from, date_to: to, ...place, people_limit: '2000' }, signal),
    key, active,
  );

  const [draft, setDraft] = useState(search);
  useEffect(() => setDraft(search), [search]);
  useEffect(() => {
    if (draft === search) return;
    const timer = window.setTimeout(() => patch({ search: draft || null, page: null }), 300);
    return () => window.clearTimeout(timer);
  }, [draft, search, patch]);

  const people = overview.state === 'ready' ? overview.data.employees : [];
  const troubled = (one: api.OverviewPerson) => one.missed_days > 0 || one.late_days > 0;
  const needle = search.trim().toLowerCase();
  const shown = people
    .filter((one) => view === 'all' || troubled(one))
    .filter((one) => !needle || one.name.toLowerCase().includes(needle))
    .sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  const pages = Math.max(1, Math.ceil(shown.length / PERIOD_PAGE));
  const slice = shown.slice((page - 1) * PERIOD_PAGE, page * PERIOD_PAGE);

  return {
    title: 'Посещаемость',
    sub: <>{rangeTitle(from, to)} · Сводка за период</>,
    tools: (
      <>
        <span className="at-range">
          <DatePicker label="Начало периода" value={from} now={now} max={to}
                      onChange={(value) => patch({ from: value || null, period: null, page: null })} />
          <span aria-hidden="true">—</span>
          <DatePicker label="Конец периода" value={to} now={now} min={from} max={now}
                      onChange={(value) => patch({ to: value || null, period: null, page: null })} />
        </span>
        <Dropdown label="Офис" empty="Все офисы" value={office}
                  options={ctx.offices.map((one) => ({ id: one.id, name: one.name }))}
                  onChange={(value) => patch({ office_id: value || null, region_id: null, page: null })} />
        <Dropdown label="Отдел" empty="Все отделы" value={department} options={ctx.departments}
                  onChange={(value) => patch({ department_id: value || null, page: null })} />
        <button type="button" className="at-btn" onClick={() => ctx.order({ from, to })}>
          <AppIcon name="download" size={18} /> Экспорт
        </button>
      </>
    ),
    strip: (
      <Guard block={overview} name="сводку периода" strip>
        {(data) => {
          const s = data.summary;
          const absences = s.vacation_days + s.sick_leave_days + s.other_absence_days;
          return (
            <div className="at-facts at-facts--four">
              <Share label="Явка за период" share={s.attendance} diff={s.difference_points} against="к прошлому периоду" />
              <Big icon="clock" value={s.average_seconds === null ? '—' : duration(s.average_seconds)} title="" note="среднее время в день" />
              <Big icon="alert" tone="warn" value={s.late.numerator} title={plural(s.late.numerator, ['опоздание', 'опоздания', 'опозданий'])} note="за весь период" />
              <Big icon="calendar" value={absences} title="дней отсутствий"
                   note={`${s.vacation_days} отпуск · ${s.sick_leave_days} больничный`} />
            </div>
          );
        }}
      </Guard>
    ),
    body: (
      <div className="at-split">
        <section className="at-split__main" aria-label="Сотрудники за период">
          <div className="at-filters">
            <h2 className="at-part-title">Сотрудники за период</h2>
            <label className="at-find">
              <AppIcon name="search" size={16} />
              <input type="search" value={draft} placeholder="Найти сотрудника" aria-label="Найти сотрудника"
                     onChange={(event) => setDraft(event.target.value)} />
            </label>
            <div className="at-chips" role="group" aria-label="Отбор сотрудников">
              {([['all', 'Все сотрудники', people.length], ['attention', 'Требуют внимания', people.filter(troubled).length]] as const).map(([value, title, count]) => (
                <button key={value} type="button" aria-pressed={view === value}
                        className={view === value ? 'at-chip at-chip--on' : 'at-chip'}
                        onClick={() => patch({ who: value === 'all' ? null : value, page: null })}>
                  {title}<span className="at-chip__n">{overview.state === 'ready' ? count : '·'}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="at-table at-table--period" role="table" aria-label="Сотрудники за период">
            <div className="at-table__head" role="row">
              <span role="columnheader">Сотрудник</span>
              <span role="columnheader" className="at-center">Рабочих дней</span>
              <span role="columnheader" className="at-center">Присутствовал</span>
              <span role="columnheader">Явка</span>
              <span role="columnheader" className="at-center">Отработано</span>
              <span role="columnheader" className="at-center">Опоздания</span>
              <span role="columnheader" className="at-center">Нет отметки</span>
              <span role="columnheader" className="at-center">Отсутствия</span>
              <span role="columnheader" />
            </div>
            <div className="at-table__body">
              <Guard block={overview} name="сотрудников">
                {() => slice.length === 0 ? <p className="at-empty">{people.length === 0 ? 'За период по графику никого не ждали.' : 'По этим условиям никого нет.'}</p> : slice.map((one) => {
                  const percent = one.attendance.percent;
                  const absent = (one.vacation_days ?? 0) + (one.sick_days ?? 0) + (one.other_days ?? 0);
                  return (
                    <Link key={one.id} role="row" className="at-row" to={`/employees/${one.id}?tab=attendance`}>
                      <span role="cell" className="at-who">
                        <span className="at-face" aria-hidden="true">{initials(one.name)}</span>
                        <b>{one.name}</b>
                      </span>
                      <span role="cell" className="at-center at-num">{one.attendance.denominator}</span>
                      <span role="cell" className="at-center at-num">{one.attendance.numerator}</span>
                      <span role="cell" className="at-share">
                        <span className="at-meter at-meter--thin"><i style={{ width: `${percent ?? 0}%` }} /></span>
                        <b>{percent === null ? '—' : `${Math.round(percent)}%`}</b>
                      </span>
                      <span role="cell" className="at-center at-num">{one.seconds ? duration(one.seconds) : '—'}</span>
                      <span role="cell" className={one.late_days > 0 ? 'at-center at-num at-num--warn' : 'at-center at-num'}>{one.late_days}</span>
                      <span role="cell" className={one.missed_days > 0 ? 'at-center at-num at-num--bad' : 'at-center at-num'}>{one.missed_days}</span>
                      <span role="cell" className="at-center at-num" title={absent ? `${one.vacation_days ?? 0} отпуск · ${one.sick_days ?? 0} больничный` : undefined}>{absent}</span>
                      <AppIcon name="next" size={16} className="at-go" />
                    </Link>
                  );
                })}
              </Guard>
            </div>
          </div>
          <Footer text={overview.state === 'ready' ? shownLine(page, PERIOD_PAGE, shown.length, 'сотрудник') : ''}
                  page={page} pages={pages} onGo={(next) => patch({ page: next === 1 ? null : String(next) })} />
        </section>

        <aside className="at-split__side" aria-label="Аналитика периода">
          <Guard block={overview} name="аналитику">
            {(data) => (
              <>
                <section className="at-side-part">
                  <h2 className="at-part-title">
                    Динамика явки
                    <b>{data.summary.attendance.percent === null ? '—' : `${Math.round(data.summary.attendance.percent)}%`}</b>
                  </h2>
                  <MiniLine days={data.days} />
                </section>
                <section className="at-side-part">
                  <h2 className="at-part-title">Причины отсутствий</h2>
                  <Bars rows={[
                    ['Отпуск', data.summary.vacation_days, 'violet'],
                    ['Больничный', data.summary.sick_leave_days, 'blue'],
                    ['Прочее', data.summary.other_absence_days, 'grey'],
                  ]} />
                </section>
                <section className="at-side-part">
                  <h2 className="at-part-title">Требуют внимания</h2>
                  <AttentionList people={data.employees} onOpen={(employee, day) => patch({ tab: null, date: day, employee, from: null, to: null, period: null, page: null })} />
                </section>
              </>
            )}
          </Guard>
        </aside>
      </div>
    ),
  };
}

function MiniLine({ days }: { days: api.OverviewDay[] }) {
  // Нерабочие дни (без графика) линию не рвут — их просто нет на ней.
  const points = days.filter((one) => !one.future && one.percent !== null);
  if (points.length === 0) {
    return <p className="at-empty at-empty--small">За период по графику никого не ждали.</p>;
  }
  const width = 280;
  const height = 90;
  const x = (i: number) => (points.length > 1 ? (i / (points.length - 1)) * (width - 8) + 4 : width / 2);
  // Шкала — от чуть ниже минимума до 100: колебания явки видны, а не
  // прижаты к верхнему краю.
  const floor = Math.max(0, Math.floor((Math.min(...points.map((one) => one.percent ?? 0)) - 10) / 10) * 10);
  const y = (v: number) => height - 6 - ((v - floor) / (100 - floor || 1)) * (height - 14);
  const d = points.map((one, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(one.percent ?? 0).toFixed(1)}`).join(' ');
  return (
    <div className="at-mini">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img"
           aria-label={`Явка по дням: ${points.map((one) => `${one.day.slice(8)}.${one.day.slice(5, 7)} — ${Math.round(one.percent ?? 0)}%`).join(', ')}`}>
        {[0.25, 0.5, 0.75].map((k) => {
          const v = floor + (100 - floor) * k;
          return <line key={k} x1={0} x2={width} y1={y(v)} y2={y(v)} className="at-mini__grid" />;
        })}
        <path d={d} className="at-mini__line" />
      </svg>
      <div className="at-mini__axis">
        <span>{points[0] ? `${Number(points[0].day.slice(8))} ${MONTHS[Number(points[0].day.slice(5, 7)) - 1]}` : ''}</span>
        <span>{points.length ? `${Number(points[points.length - 1]!.day.slice(8))} ${MONTHS[Number(points[points.length - 1]!.day.slice(5, 7)) - 1]}` : ''}</span>
      </div>
    </div>
  );
}

function Bars({ rows }: { rows: [string, number, string][] }) {
  const top = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <ul className="at-bars">
      {rows.map(([title, n, tone]) => (
        <li key={title}>
          <span>{title}</span>
          <span className="at-bars__track"><i className={`at-bars__fill at-bars__fill--${tone}`} style={{ width: `${(n / top) * 100}%` }} /></span>
          <b>{n}</b>
        </li>
      ))}
    </ul>
  );
}

function AttentionList({ people, onOpen }: { people: api.OverviewPerson[]; onOpen: (employee: string, day: string) => void }) {
  const items = people.flatMap((one) => [
    ...(one.missed_dates ?? []).map((day) => ({ id: one.id, name: one.name, day, text: 'Нет отметки', tone: 'bad' })),
    ...(one.late_dates ?? []).map((late) => ({ id: one.id, name: one.name, day: late.day, text: `Опоздание ${late.minutes} мин`, tone: 'warn' })),
  ]).sort((a, b) => b.day.localeCompare(a.day)).slice(0, 5);
  if (items.length === 0) return <p className="at-empty at-empty--small">За период никто не требует внимания.</p>;
  return (
    <ul className="at-attention">
      {items.map((one) => (
        <li key={`${one.id}-${one.day}-${one.text}`}>
          <button type="button" onClick={() => onOpen(one.id, one.day)}>
            <span className="at-face at-face--sm" aria-hidden="true">{initials(one.name)}</span>
            <span className="at-attention__text">
              <b>{one.name}</b>
              <small className={`at-state--${one.tone}`}><i className={`at-dot at-dot--${one.tone}`} />{one.text}</small>
            </span>
            <time dateTime={one.day}>{Number(one.day.slice(8))} {MONTHS[Number(one.day.slice(5, 7)) - 1]}</time>
          </button>
        </li>
      ))}
    </ul>
  );
}

// --- общие части ----------------------------------------------------------------------

function Big({ icon, value, title, note, tone }: {
  icon: AppIconName;
  value: number | string;
  title: string;
  note: string;
  tone?: 'warn' | 'bad';
}) {
  const hot = tone && typeof value === 'number' && value > 0;
  return (
    <div className="at-fact at-fact--big">
      <span className={hot ? `at-fact__icon at-fact__icon--${tone}` : 'at-fact__icon'} aria-hidden="true">
        <AppIcon name={icon} size={20} />
      </span>
      <span className="at-fact__stack">
        <span className="at-fact__line">
          <b className={hot ? `at-fact__big at-fact__big--${tone}` : 'at-fact__big'}>{value}</b>
          {title && <span className="at-fact__title">{title}</span>}
        </span>
        <small className="at-fact__note">{note}</small>
      </span>
    </div>
  );
}

function Share({ label, share, diff, against }: { label: string; share: api.Share; diff: number | null; against: string }) {
  return (
    <div className="at-fact at-fact--main">
      <span className="at-fact__label">{label}</span>
      <span className="at-fact__line">
        <b className="at-fact__big">{share.percent === null ? '—' : `${Math.round(share.percent)}%`}</b>
        <span>{share.denominator > 0 ? `${share.numerator} из ${share.denominator} дней` : 'Рабочих дней не было'}</span>
      </span>
      <span className="at-meter"><i style={{ width: `${share.percent ?? 0}%` }} /></span>
      {diff !== null && (
        <small className={diff >= 0 ? 'at-delta at-delta--up' : 'at-delta at-delta--down'}>
          {diff >= 0 ? '+' : '−'}{Math.abs(diff).toLocaleString('ru-RU')} п.п. {against}
        </small>
      )}
    </div>
  );
}

function Footer({ text, page, pages, onGo }: { text: string; page: number; pages: number; onGo: (next: number) => void }) {
  const numbers = Array.from({ length: pages }, (_, i) => i + 1)
    .filter((n) => n === 1 || n === pages || Math.abs(n - page) <= 2);
  return (
    <footer className="at-foot">
      <span>{text}</span>
      {pages > 1 && (
        <nav className="at-pages" aria-label="Страницы">
          <button type="button" aria-label="Назад" disabled={page <= 1} onClick={() => onGo(page - 1)}><AppIcon name="back" size={16} /></button>
          {numbers.map((n, i) => (
            <span key={n} className="at-pages__group">
              {i > 0 && n - (numbers[i - 1] ?? n) > 1 && <span className="at-pages__gap">…</span>}
              <button type="button" aria-current={n === page ? 'page' : undefined} className={n === page ? 'at-pages__on' : undefined}
                      onClick={() => onGo(n)}>{n}</button>
            </span>
          ))}
          <button type="button" aria-label="Вперёд" disabled={page >= pages} onClick={() => onGo(page + 1)}><AppIcon name="next" size={16} /></button>
        </nav>
      )}
    </footer>
  );
}

/**
 * Обёртка блока: загрузка — заготовкой строк, ошибка и отказ — словами.
 * Ни одно из состояний не выглядит как ноль.
 */
function Guard<T>({ block, name, children, strip }: {
  block: Block<T>;
  name: string;
  children: (data: T) => ReactNode;
  strip?: boolean;
}) {
  if (block.state === 'ready') return <>{children(block.data)}</>;
  if (block.state === 'loading') {
    return strip ? (
      <div className="at-facts at-facts--ghost" aria-label={`Загружаем ${name}`}>
        {[0, 1, 2].map((one) => <span key={one} className="at-ghost at-ghost--block" />)}
      </div>
    ) : (
      <div className="at-skeleton" aria-label={`Загружаем ${name}`}>
        {[0, 1, 2, 3, 4, 5].map((one) => (
          <span key={one} className="at-skeleton__row">
            <span className="at-ghost at-ghost--round" />
            <span className="at-ghost" />
            <span className="at-ghost at-ghost--short" />
          </span>
        ))}
      </div>
    );
  }
  const text = block.state === 'denied'
    ? `Нет доступа: ${name}.`
    : `Не удалось загрузить ${name}. Данные не показаны — это не ноль.`;
  return <p className={strip ? 'at-empty at-empty--strip' : 'at-empty at-empty--bad'}>{text}</p>;
}

// --- мелочи ---------------------------------------------------------------------------

/** «7 ч 42 мин»: длительность в карточке дня и в таблицах. */
export function span(seconds: number): string {
  return duration(seconds);
}

function duration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h} ч ${String(m).padStart(2, '0')} мин` : `${m} мин`;
}

function plural(n: number, forms: [string, string, string]): string {
  const tail = Math.abs(n) % 100;
  const last = tail % 10;
  if (tail > 10 && tail < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

/** «Показано 1–12 из 48 сотрудников». */
function shownLine(page: number, size: number, total: number, word: 'сотрудник' | 'событие'): string {
  if (total === 0) return '';
  const first = (page - 1) * size + 1;
  const last = Math.min(page * size, total);
  const forms: [string, string, string] = word === 'сотрудник'
    ? ['сотрудника', 'сотрудников', 'сотрудников']
    : ['события', 'событий', 'событий'];
  return `Показано ${first}–${last} из ${total} ${forms[total % 10 === 1 && total % 100 !== 11 ? 0 : 1]}`;
}

function shortName(full: string): string {
  const [last, first] = full.split(/\s+/);
  if (!last) return full;
  return first ? `${last} ${first[0]}.` : last;
}

function parseDay(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
}

function isoOf(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function addDays(iso: string, days: number): string {
  const date = parseDay(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return isoOf(date);
}

/** Неделя начинается с понедельника: рабочая неделя — понедельник–пятница. */
function weekStart(iso: string): string {
  const weekday = parseDay(iso).getUTCDay();
  return addDays(iso, -((weekday + 6) % 7));
}

function monthEnd(iso: string): string {
  const date = parseDay(`${iso.slice(0, 8)}01`);
  date.setUTCMonth(date.getUTCMonth() + 1);
  date.setUTCDate(0);
  const last = isoOf(date);
  return last > today() ? today() : last;
}

function weekdayOf(iso: string): string {
  return WEEKDAYS[parseDay(iso).getUTCDay()]!;
}

function isWeekend(iso: string): boolean {
  const weekday = parseDay(iso).getUTCDay();
  return weekday === 0 || weekday === 6;
}

/** «21–27 сентября 2026», «28 сентября – 4 октября 2026». */
function rangeTitle(from: string, to: string): string {
  const a = parseDay(from);
  const b = parseDay(to);
  if (from === to) return `${a.getUTCDate()} ${MONTHS[a.getUTCMonth()]} ${a.getUTCFullYear()}`;
  if (a.getUTCMonth() === b.getUTCMonth() && a.getUTCFullYear() === b.getUTCFullYear()) {
    return `${a.getUTCDate()}–${b.getUTCDate()} ${MONTHS[b.getUTCMonth()]} ${b.getUTCFullYear()}`;
  }
  return `${a.getUTCDate()} ${MONTHS[a.getUTCMonth()]} – ${b.getUTCDate()} ${MONTHS[b.getUTCMonth()]} ${b.getUTCFullYear()}`;
}

/** Час события в поясе офиса — активность по часам. */
function hourIn(at: string, zone: string): number {
  const text = clock(at, zone);
  return Number(text.slice(0, 2));
}
