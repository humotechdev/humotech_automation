/**
 * Страница «Аналитика»: посещаемость и рабочее время за период.
 *
 * Вёрстка по образцу `ChatGPT Image Sep 15, 2026, 09_42_13 AM.png`,
 * размеры — в `styles/analytics.css`, классы с префиксом `an-`.
 *
 * Все числа — из одного ответа `/analytics/overview`: сводка, дни, офисы,
 * ритм прихода и дни недели считаются сервером по одним правилам и одним
 * фильтрам, и блоки страницы не могут разойтись между собой.
 *
 * Фильтры живут в адресе. Смена фильтра не гасит экран: пока идёт новый
 * запрос, на месте остаются прежние значения, а скелетон показывается
 * только при самой первой загрузке.
 */

import { useCallback, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { AppDateRangePicker } from '../components/DateRangePicker';
import { AppSegmentedControl, Dropdown } from '../components/AppSelect';
import { today as todayIso, useBlock, type Block } from '../features/dashboard/data';
import '../styles/analytics.css';

const PRESETS = [7, 30, 90] as const;
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const WEEKDAY_LONG = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье'];
const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

export function AnalyticsPage() {
  const [params, setParams] = useSearchParams();

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

  // Период: явный диапазон из адреса или быстрый выбор (по умолчанию 30 дней).
  const end = params.get('to') ?? todayIso();
  const presetRaw = Number(params.get('days') ?? '30');
  const preset = params.get('from') ? null : (PRESETS.find((one) => one === presetRaw) ?? 30);
  const start = params.get('from') ?? shift(end, -((preset ?? 30) - 1));
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const compare = params.get('compare') === '1';
  const weekday = params.get('weekday') ?? '';
  const picked = params.get('day') ?? '';

  const query: api.OverviewQuery = useMemo(
    () => ({
      date_from: start,
      date_to: end,
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
      ...(weekday ? { weekday } : {}),
    }),
    [start, end, region, office, weekday],
  );

  const [overview, reload, status] = useBlock(
    (signal) => api.analyticsOverview(query, signal),
    `overview|${start}|${end}|${region}|${office}|${weekday}`,
  );

  // Движение сотрудников. Отдельным запросом, а не полем обзора:
  // единица измерения здесь другая — люди, а не дни, — и держать их в
  // одном ответе значит смешивать два разных вопроса.
  const [movement, reloadMovement] = useBlock(
    (signal) => api.analyticsMovement(
      {
        date_from: start,
        date_to: end,
        ...(office ? { office_id: office } : region ? { region_id: region } : {}),
      },
      signal,
    ),
    `movement|${start}|${end}|${region}|${office}`,
  );

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items.filter((one) => one.status === 'ACTIVE'),
        offices: o.items.filter((one) => one.status === 'ACTIVE'),
      })),
    'analytics-directory',
  );

  const offices = useMemo(() => {
    if (directory.state !== 'ready') return [];
    return region ? directory.data.offices.filter((one) => one.region_id === region) : directory.data.offices;
  }, [directory, region]);

  const data = overview.state === 'ready' ? overview.data : null;

  // Выбранный день: из адреса или последний прошедший рабочий день периода.
  const chosen = data
    ? data.days.find((one) => one.day === picked)
      ?? [...data.days].reverse().find((one) => one.working && !one.future)
      ?? null
    : null;

  const [ordered, setOrdered] = useState<string | null>(null);
  async function order() {
    setOrdered(null);
    try {
      await api.orderExport({
        kind: 'attendance',
        fmt: 'xlsx',
        date_from: start,
        date_to: end,
        ...(office ? { office_id: office } : region ? { region_id: region } : {}),
      });
      setOrdered('Выгрузка поставлена в очередь с текущими фильтрами');
    } catch (error) {
      setOrdered(messageFor(error));
    }
  }

  const lengthDays = Math.round((Date.parse(`${end}T12:00:00Z`) - Date.parse(`${start}T12:00:00Z`)) / 86_400_000) + 1;
  const periodTitle = preset ? `последние ${preset} дней` : `${lengthDays} ${daysWord(lengthDays)}`;

  return (
    <AppShell breadcrumb="Аналитика" section="analytics">
      <div className={status.busy ? 'an an--busy' : 'an'}>
        <header className="an-head">
          <h1 className="an-head__title">Аналитика</h1>
          <p className="an-head__sub">Посещаемость и рабочее время</p>
        </header>

        <div className="an-filters" role="group" aria-label="Фильтры аналитики">
          <AppDateRangePicker className="an-range" label="Период аналитики" now={todayIso()} from={start} to={end}
            onFromChange={(value) => value && patch({ from: value, days: null, day: null })}
            onToChange={(value) => value && patch({ to: value, ...(preset ? { from: start, days: null } : {}), day: null })} />

          <AppSegmentedControl className="an-presets" label="Быстрый выбор периода" value={preset}
            options={PRESETS.map((one) => ({ value: one, label: `${one} дней` }))}
            onChange={(one) => patch({ days: one === 30 ? null : String(one), from: null, to: null, day: null })} />

          <i className="an-filters__rule" />

          <Select label="Регион" empty="Все регионы" value={region}
                  options={directory.state === 'ready' ? directory.data.regions : []}
                  onChange={(value) => patch({ region_id: value || null, office_id: null })} />
          <Select label="Офис" empty="Все офисы" value={office} options={offices}
                  onChange={(value) => patch({ office_id: value || null })} />

          <label className="an-toggle">
            <input type="checkbox" role="switch" checked={compare}
                   onChange={(event) => patch({ compare: event.target.checked ? '1' : null })} />
            <i className="an-toggle__track" aria-hidden="true" />
            Сравнить с предыдущим периодом
          </label>

          <i className="an-filters__rule" />

          <button type="button" className="an-export" onClick={() => void order()}>
            <AppIcon name="download" size={20} />
            Экспорт
          </button>
        </div>

        {ordered && (
          <p className="an-note" role="status">
            {ordered} — <Link to="/reports">файл появится в отчётах</Link>
          </p>
        )}
        {status.failed && data && (
          <p className="an-note an-note--bad" role="status">
            Показаны прежние данные: обновить не удалось.{' '}
            <button type="button" className="an-link" onClick={reload}>Повторить</button>
          </p>
        )}
        {weekday && (
          <p className="an-note">
            Детализация: {WEEKDAY_LONG[Number(weekday) - 1] ?? weekday}.{' '}
            <button type="button" className="an-link" onClick={() => patch({ weekday: null })}>Показать все дни</button>
          </p>
        )}

        <Hero block={overview} compare={compare} periodTitle={periodTitle} onRetry={reload} />

        <div className="an-row an-row--calendar">
          <Calendar block={overview} periodTitle={periodTitle} chosen={chosen?.day ?? ''}
                    onPick={(day) => patch({ day })} onRetry={reload} />
          <DayPanel day={chosen} loading={!data && overview.state === 'loading'}
                    link={chosen ? attendanceLink(chosen.day, region, office) : ''} />
        </div>

        <Movement block={movement} onRetry={reloadMovement} />

        <div className="an-row an-row--bottom">
          <Ranking block={overview} compare={compare} current={office}
                   onPick={(id) => patch({ office_id: id === office ? null : id, day: null })} onRetry={reload} />
          <Rhythm block={overview} onRetry={reload} />
          <Week block={overview} weekday={weekday}
                onPick={(value) => patch({ weekday: value === weekday ? null : value, day: null })} onRetry={reload} />
        </div>
      </div>
    </AppShell>
  );
}

// --- сводка ----------------------------------------------------------------------

function Hero({ block, compare, periodTitle, onRetry }: {
  block: Block<api.Overview>;
  compare: boolean;
  periodTitle: string;
  onRetry: () => void;
}) {
  if (block.state !== 'ready') {
    return (
      <section className="an-hero" aria-label="Общая явка">
        <State block={block} onRetry={onRetry} dark />
      </section>
    );
  }
  const { summary, days, previous_days: previous } = block.data;
  const attendance = summary.attendance;
  const diff = summary.difference_points;

  return (
    <section className="an-hero" aria-label="Общая явка">
      <div className="an-hero__main">
        <p className="an-hero__label"><AppIcon name="users" size={20} />Общая явка</p>
        <p className="an-hero__value">
          <strong>{percent(attendance.percent)}</strong>
          {diff !== null && (
            <span className={diff >= 0 ? 'an-delta an-delta--up' : 'an-delta an-delta--down'}>
              <i aria-hidden="true">{diff >= 0 ? '▲' : '▼'}</i>
              {pointsText(diff)}
            </span>
          )}
        </p>
        <p className="an-hero__note">
          {number(attendance.numerator)} из {number(attendance.denominator)} сотрудника-дней
        </p>
      </div>

      <div className="an-hero__chart">
        <p className="an-hero__chart-title">Динамика общей явки ({periodTitle})</p>
        {attendance.denominator === 0 ? (
          <p className="an-empty an-empty--dark">В периоде нет рабочих дней по графику.</p>
        ) : (
          <TrendChart days={days} previous={previous} compare={compare} />
        )}
      </div>

      <ul className="an-hero__side">
        <Kpi icon="clock" tone="green" title="Вовремя" value={percent(summary.on_time.percent)}
             hint={`${number(summary.on_time.numerator)} из ${number(summary.on_time.denominator)} первых входов`} />
        <Kpi icon="clock" tone="blue" title="Среднее время" value={duration(summary.average_seconds)}
             hint={summary.open_sessions ? `незакрытых посещений: ${summary.open_sessions} — в среднее не входят` : 'по закрытым посещениям'} />
        <Kpi icon="bell" tone="orange" title="Опоздания" value={percent(summary.late.percent)}
             hint={`${number(summary.late.numerator)} опозданий`} />
      </ul>
    </section>
  );
}

function Kpi({ icon, tone, title, value, hint }: {
  icon: AppIconName;
  tone: string;
  title: string;
  value: string;
  hint: string;
}) {
  return (
    <li className={`an-kpi an-kpi--${tone}`} title={hint}>
      <AppIcon name={icon} size={20} />
      <span>{title}</span>
      <strong>{value}</strong>
    </li>
  );
}

const CHART = { w: 700, h: 150, left: 44, right: 26, top: 16, bottom: 26 };

/**
 * Линия явки по дням. Выходные и будущие дни — разрывы, а не нули:
 * ноль утверждал бы, что никто не пришёл. Прошлый период — пунктиром,
 * день к дню по порядку.
 */
function TrendChart({ days, previous, compare }: {
  days: api.OverviewDay[];
  previous: api.Overview['previous_days'];
  compare: boolean;
}) {
  const values = days.map((one) => (one.working && !one.future ? one.percent : null));
  const before = previous.map((one) => one.percent);
  const known = [...values, ...(compare ? before : [])].filter((one): one is number => one !== null);
  const low = Math.max(0, Math.min(50, Math.floor(((known.length ? Math.min(...known) : 50) - 5) / 25) * 25));
  const step = (CHART.w - CHART.left - CHART.right) / Math.max(days.length - 1, 1);
  const x = (index: number) => CHART.left + step * index;
  const y = (value: number) => CHART.top + (CHART.h - CHART.top - CHART.bottom) * (1 - (value - low) / (100 - low));
  const ticks = [low, (low + 100) / 2, 100];
  const labelEvery = Math.max(1, Math.round(days.length / 7));

  const line = (series: (number | null)[]) => {
    let d = '';
    let pen = false;
    series.forEach((value, index) => {
      if (value === null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(index).toFixed(1)},${y(value).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  const lastIndex = values.map((one, index) => (one === null ? -1 : index)).filter((one) => one >= 0).pop();
  const last = lastIndex !== undefined ? days[lastIndex] : undefined;
  const lastValue = lastIndex !== undefined ? values[lastIndex] ?? null : null;
  const lastBefore = lastIndex !== undefined ? before[lastIndex] ?? null : null;

  return (
    <div className="an-trend">
      <svg viewBox={`0 0 ${CHART.w} ${CHART.h}`} className="an-trend__svg" role="img"
           aria-label="Явка по дням периода">
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="an-trend__grid" x1={CHART.left} x2={CHART.w - CHART.right} y1={y(tick)} y2={y(tick)} />
            <text className="an-trend__axis" x={CHART.left - 8} y={y(tick) + 4} textAnchor="end">{Math.round(tick)}%</text>
          </g>
        ))}
        {/* Последняя дата подписана всегда; промежуточная рядом с ней
            пропускается, иначе подписи налезают друг на друга. */}
        {days.map((one, index) => ((index % labelEvery === 0 && days.length - 1 - index >= labelEvery * 0.7) || index === days.length - 1) && (
          <g key={one.day}>
            <line className="an-trend__grid an-trend__grid--v" x1={x(index)} x2={x(index)} y1={CHART.top} y2={CHART.h - CHART.bottom} />
            <text className="an-trend__axis" x={x(index)} y={CHART.h - 6} textAnchor="middle">{dayShort(one.day)}</text>
          </g>
        ))}
        {compare && <path className="an-trend__line an-trend__line--prev" d={line(before)} />}
        <path className="an-trend__area" d={`${line(values)}`} />
        <path className="an-trend__line" d={line(values)} />
        {values.map((value, index) => value !== null && (
          <circle key={index} className="an-trend__dot" cx={x(index)} cy={y(value)} r={3} />
        ))}
        {last && lastValue !== null && lastIndex !== undefined && (
          <>
            <line className="an-trend__cursor" x1={x(lastIndex)} x2={x(lastIndex)} y1={y(lastValue)} y2={CHART.h - CHART.bottom} />
            <circle className="an-trend__last" cx={x(lastIndex)} cy={y(lastValue)} r={6} />
          </>
        )}
      </svg>
      {last && lastValue !== null && lastIndex !== undefined && (
        <div className="an-trend__tip"
             style={{ left: `${(x(lastIndex) / CHART.w) * 100}%`, top: `${(y(lastValue) / CHART.h) * 100}%` }}>
          <b>{percent(lastValue)}</b>
          <small>{dayLong(last.day)}</small>
          {compare && (
            <small>Сейчас {percent(lastValue)} · раньше {percent(lastBefore)}</small>
          )}
        </div>
      )}
    </div>
  );
}

// --- календарь -------------------------------------------------------------------------

function Calendar({ block, periodTitle, chosen, onPick, onRetry }: {
  block: Block<api.Overview>;
  periodTitle: string;
  chosen: string;
  onPick: (day: string) => void;
  onRetry: () => void;
}) {
  return (
    <section className="an-panel an-calendar" aria-label="Календарь посещаемости">
      <div className="an-panel__head">
        <h2 className="an-title">Календарь посещаемости — {periodTitle}</h2>
        <ul className="an-levels">
          <li><i className="an-level an-level--low" />Ниже нормы<br />&lt; 70%</li>
          <li><i className="an-level an-level--mid" />70 – 85%</li>
          <li><i className="an-level an-level--good" />85 – 95%</li>
          <li><i className="an-level an-level--top" />Отлично<br />≥ 95%</li>
        </ul>
      </div>
      {block.state !== 'ready' ? (
        <State block={block} onRetry={onRetry} />
      ) : (
        <CalendarGrid days={block.data.days} chosen={chosen} onPick={onPick} />
      )}
    </section>
  );
}

function CalendarGrid({ days, chosen, onPick }: {
  days: api.OverviewDay[];
  chosen: string;
  onPick: (day: string) => void;
}) {
  const weeks = useMemo(() => toWeeks(days), [days]);
  return (
    <div className="an-grid" role="grid" aria-label="Дни периода">
      <div className="an-grid__head" role="row">
        <span>Неделя</span>
        {WEEKDAYS.map((one) => <span key={one}>{one}</span>)}
      </div>
      {weeks.map((week) => (
        <div key={week.label} className="an-grid__row" role="row">
          <span className="an-grid__week">{week.label}</span>
          {week.cells.map((cell, index) => {
            if (!cell) return <span key={index} className="an-cell an-cell--none">—</span>;
            if (cell.future) return <span key={cell.day} className="an-cell an-cell--none">—</span>;
            if (!cell.working) {
              return <span key={cell.day} className="an-cell an-cell--off">{dayShort(cell.day)}</span>;
            }
            const on = cell.day === chosen;
            return (
              <button key={cell.day} type="button" role="gridcell" aria-selected={on}
                      className={`an-cell an-cell--${level(cell.percent)}${on ? ' an-cell--on' : ''}${cell.in_detail ? '' : ' an-cell--dim'}`}
                      onClick={() => onPick(cell.day)}>
                <span className="an-cell__top">
                  <span>{dayShort(cell.day)}</span>
                  <b>{percent(cell.percent, true)}</b>
                </span>
                <i className="an-cell__bar"><i style={{ width: `${cell.percent ?? 0}%` }} /></i>
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function DayPanel({ day, loading, link }: {
  day: api.OverviewDay | null;
  loading: boolean;
  link: string;
}) {
  if (!day) {
    return (
      <aside className="an-panel an-day" aria-label="Выбранный день">
        <p className="an-empty">{loading ? 'Загружаем…' : 'Выберите рабочий день в календаре.'}</p>
      </aside>
    );
  }
  return (
    <aside className="an-panel an-day" aria-label="Выбранный день">
      <p className="an-day__date">
        <AppIcon name="calendar" size={20} />
        <b>{dayTitle(day.day)}</b>
        <small>{WEEKDAYS[day.weekday - 1]}</small>
      </p>
      <ul className="an-day__facts">
        <li className="an-day__fact"><AppIcon name="users" size={20} /><span>Явка</span><strong>{percent(day.percent)}</strong></li>
        <li className="an-day__fact an-day__fact--green"><AppIcon name="clock" size={20} /><span>Вовремя</span><strong>{percent(day.on_time_percent, true)}</strong></li>
        <li className="an-day__fact an-day__fact--orange"><AppIcon name="bell" size={20} /><span>Опоздали</span><strong>{day.late}</strong></li>
      </ul>
      <p className="an-day__total">{number(day.attended)} из {number(day.expected)} сотрудника-дней</p>
      {(day.missed > 0 || day.vacation > 0 || day.sick_leave > 0 || day.other_absence > 0) && (
        <ul className="an-day__absent">
          {day.missed > 0 && <li><span>Нет отметки</span><b>{day.missed}</b></li>}
          {day.vacation > 0 && <li><span>В отпуске</span><b>{day.vacation}</b></li>}
          {day.sick_leave > 0 && <li><span>На больничном</span><b>{day.sick_leave}</b></li>}
          {day.other_absence > 0 && <li><span>Другое отсутствие</span><b>{day.other_absence}</b></li>}
        </ul>
      )}
      <Link className="an-day__link" to={link}>
        Открыть день в посещаемости
        <AppIcon name="next" size={18} />
      </Link>
    </aside>
  );
}

// --- рейтинг офисов -------------------------------------------------------------------

function Ranking({ block, compare, current, onPick, onRetry }: {
  block: Block<api.Overview>;
  compare: boolean;
  current: string;
  onPick: (id: string) => void;
  onRetry: () => void;
}) {
  const [all, setAll] = useState(false);
  const rows = block.state === 'ready' ? block.data.offices : [];
  const shown = all ? rows : rows.slice(0, 5);
  return (
    <section className="an-panel an-rank" aria-label="Рейтинг офисов">
      <div className="an-panel__head">
        <h2 className="an-title"><AppIcon name="chart" size={20} />Рейтинг офисов</h2>
        {rows.length > 5 && (
          <button type="button" className="an-link" aria-expanded={all} onClick={() => setAll((was) => !was)}>
            {all ? 'Свернуть' : `Все ${rows.length} ${officesWord(rows.length)}`}
            <AppIcon name={all ? 'back' : 'next'} size={16} />
          </button>
        )}
      </div>
      {block.state !== 'ready' ? (
        <State block={block} onRetry={onRetry} />
      ) : rows.length === 0 ? (
        <p className="an-empty">В области нет офисов.</p>
      ) : (
        <div className={all ? 'an-rank__table an-rank__table--all' : 'an-rank__table'}>
          <div className="an-rank__head">
            <span>#</span><span>Офис</span><span /><span>Явка</span><span>Изменение</span>
          </div>
          {shown.map((row) => {
            const diff = row.difference_points;
            return (
              <button key={row.id} type="button" aria-pressed={row.id === current}
                      className={row.id === current ? 'an-rank__row an-rank__row--on' : 'an-rank__row'}
                      title="Применить офис как фильтр страницы"
                      onClick={() => onPick(row.id)}>
                <span className="an-rank__pos">{row.position}</span>
                <span className="an-rank__name">{row.name}</span>
                <i className="an-rank__bar"><i style={{ width: `${row.attendance.percent ?? 0}%`, opacity: 1 - (row.position - 1) * 0.08 }} /></i>
                <b>{percent(row.attendance.percent)}</b>
                <span className={diff === null ? 'an-rank__diff' : diff >= 0 ? 'an-rank__diff an-rank__diff--up' : 'an-rank__diff an-rank__diff--down'}>
                  {diff === null ? '—' : <><i aria-hidden="true">{diff >= 0 ? '▲' : '▼'}</i>{pointsText(diff)}</>}
                </span>
              </button>
            );
          })}
          {!compare && <p className="an-rank__hint">Изменение — к предыдущему периоду той же длины.</p>}
        </div>
      )}
    </section>
  );
}

// --- ритм прихода ---------------------------------------------------------------------

const HIST = { w: 420, h: 98, left: 34, right: 10, top: 18, bottom: 18 };

function Rhythm({ block, onRetry }: { block: Block<api.Overview>; onRetry: () => void }) {
  const [hover, setHover] = useState<number | null>(null);
  if (block.state !== 'ready') {
    return (
      <section className="an-panel an-rhythm" aria-label="Ритм прихода">
        <RhythmHead />
        <State block={block} onRetry={onRetry} />
      </section>
    );
  }
  const arrivals = block.data.arrivals;
  const buckets = arrivals.buckets;
  const total = buckets.reduce((sum, one) => sum + one.early + one.grace + one.late, 0);
  if (total === 0) {
    return (
      <section className="an-panel an-rhythm" aria-label="Ритм прихода">
        <RhythmHead />
        <p className="an-empty">В периоде нет первых входов.</p>
      </section>
    );
  }

  const top = Math.max(1, ...buckets.map((one) => one.early + one.grace + one.late));
  const width = (HIST.w - HIST.left - HIST.right) / buckets.length;
  const x = (index: number) => HIST.left + width * index;
  const plotH = HIST.h - HIST.top - HIST.bottom;
  const y = (count: number) => HIST.top + plotH * (1 - count / top);
  const zeroIndex = buckets.findIndex((one) => one.from === 0);
  const clock = (offset: number) =>
    arrivals.uniform_start && arrivals.start_time ? addMinutes(arrivals.start_time, offset) : offset === 0 ? '0' : `${offset > 0 ? '+' : '−'}${Math.abs(offset)}`;
  const hovered = hover !== null ? buckets[hover] : undefined;

  return (
    <section className="an-panel an-rhythm" aria-label="Ритм прихода">
      <RhythmHead />
      <div className="an-hist">
        <svg viewBox={`0 0 ${HIST.w} ${HIST.h}`} className="an-hist__svg" role="img"
             aria-label="Распределение первых входов относительно начала смены"
             onMouseLeave={() => setHover(null)}>
          {[0, 0.5, 1].map((part) => (
            <g key={part}>
              <line className="an-hist__grid" x1={HIST.left} x2={HIST.w - HIST.right} y1={y(top * part)} y2={y(top * part)} />
              <text className="an-hist__axis" x={HIST.left - 6} y={y(top * part) + 4} textAnchor="end">{Math.round(top * part)}</text>
            </g>
          ))}
          {buckets.map((one, index) => {
            const h0 = one.early;
            const h1 = h0 + one.grace;
            const h2 = h1 + one.late;
            return (
              <g key={one.from} onMouseEnter={() => setHover(index)}>
                <rect className="an-hist__hit" x={x(index)} y={HIST.top} width={width} height={plotH} />
                {one.early > 0 && <rect className="an-hist__bar an-hist__bar--early" x={x(index) + 1} width={width - 2} y={y(h0)} height={y(0) - y(h0)} />}
                {one.grace > 0 && <rect className="an-hist__bar an-hist__bar--grace" x={x(index) + 1} width={width - 2} y={y(h1)} height={y(h0) - y(h1)} />}
                {one.late > 0 && <rect className="an-hist__bar an-hist__bar--late" x={x(index) + 1} width={width - 2} y={y(h2)} height={y(h1) - y(h2)} />}
              </g>
            );
          })}
          {zeroIndex >= 0 && (
            <>
              <line className="an-hist__start" x1={x(zeroIndex)} x2={x(zeroIndex)} y1={HIST.top - 12} y2={HIST.h - HIST.bottom} />
              <text className="an-hist__start-label" x={x(zeroIndex) + 4} y={HIST.top - 12}>
                {arrivals.uniform_start && arrivals.start_time ? `Начало графика ${arrivals.start_time}` : 'Начало личной смены'}
              </text>
            </>
          )}
          {buckets.map((one, index) => index % 3 === 0 && (
            <text key={`t-${one.from}`} className={one.from === 0 ? 'an-hist__axis an-hist__axis--on' : 'an-hist__axis'}
                  x={x(index)} y={HIST.h - 8} textAnchor="middle">{clock(one.from)}</text>
          ))}
        </svg>
        {hovered && hover !== null && (
          <span className="an-hist__tip" style={{ left: `${((x(hover) + width / 2) / HIST.w) * 100}%` }}>
            {clock(hovered.from)} – {clock(hovered.to)} · {hovered.early + hovered.grace + hovered.late} {peopleWord(hovered.early + hovered.grace + hovered.late)}
          </span>
        )}
      </div>
      <ul className="an-hist__keys">
        <li><i className="an-hist__key an-hist__key--early" />До начала смены</li>
        <li><i className="an-hist__key an-hist__key--grace" />В пределах допуска</li>
        <li><i className="an-hist__key an-hist__key--late" />Опоздание</li>
      </ul>
      <div className="an-hist__stats">
        <p><AppIcon name="clock" size={20} /><span><small>Медиана</small><b>{minutesClock(arrivals.median_minutes)}</b></span></p>
        <p><AppIcon name="users" size={20} /><span><small>После начала</small><b>{number(arrivals.after_start)}</b></span></p>
        <p><AppIcon name="bell" size={20} /><span><small>Доля опозданий</small><b>{percent(arrivals.late.percent)}</b></span></p>
      </div>
    </section>
  );
}

function RhythmHead() {
  return (
    <div className="an-panel__head an-panel__head--stack">
      <h2 className="an-title"><AppIcon name="clock" size={20} />Ритм прихода</h2>
      <p className="an-subtitle">Первый вход за день относительно начала смены</p>
    </div>
  );
}

// --- дни недели -------------------------------------------------------------------------

function Week({ block, weekday, onPick, onRetry }: {
  block: Block<api.Overview>;
  weekday: string;
  onPick: (value: string) => void;
  onRetry: () => void;
}) {
  const head = (
    <div className="an-panel__head an-panel__head--stack">
      <h2 className="an-title"><AppIcon name="calendar" size={20} />Неделя</h2>
      <p className="an-subtitle">Показатели по дням недели</p>
    </div>
  );
  if (block.state !== 'ready') {
    return <section className="an-panel an-week" aria-label="Показатели по дням недели">{head}<State block={block} onRetry={onRetry} /></section>;
  }
  const { days, best } = block.data.weekdays;
  if (days.every((one) => one.attendance.denominator === 0)) {
    return <section className="an-panel an-week" aria-label="Показатели по дням недели">{head}<p className="an-empty">В периоде нет рабочих дней.</p></section>;
  }

  return (
    <section className="an-panel an-week" aria-label="Показатели по дням недели">
      {head}
      <table className="an-week__table">
        <thead>
          <tr>
            <th scope="col" />
            {days.map((one) => (
              <th key={one.weekday} scope="col">
                <button type="button" aria-pressed={weekday === String(one.weekday)}
                        className="an-week__day" onClick={() => onPick(String(one.weekday))}>
                  {WEEKDAYS[one.weekday - 1]}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <WeekRow title="Явка" days={days} best={best} weekday={weekday} value={(one) => percent(one.attendance.percent, true)}
                   tone={(one) => level(one.attendance.percent)} onPick={onPick} />
          <WeekRow title="Вовремя" days={days} best={best} weekday={weekday} value={(one) => percent(one.on_time.percent, true)}
                   tone={(one) => level(one.on_time.percent)} onPick={onPick} />
          <WeekRow title="Часы" days={days} best={best} weekday={weekday} value={(one) => hoursClock(one.average_seconds)}
                   tone={() => 'plain'} onPick={onPick} />
        </tbody>
      </table>
      {best !== null && (
        <p className="an-week__best">
          <AppIcon name="check" size={20} />
          <span>
            <b>Лучший день — {WEEKDAY_LONG[best - 1]}</b>
            <small>Самая высокая явка вместе со средним временем в офисе</small>
          </span>
        </p>
      )}
    </section>
  );
}

type WeekDay = api.Overview['weekdays']['days'][number];

function WeekRow({ title, days, best, weekday, value, tone, onPick }: {
  title: string;
  days: WeekDay[];
  best: number | null;
  weekday: string;
  value: (one: WeekDay) => string;
  tone: (one: WeekDay) => string;
  onPick: (value: string) => void;
}) {
  return (
    <tr>
      <th scope="row">{title}</th>
      {days.map((one) => (
        <td key={one.weekday}
            className={`an-week__cell an-week__cell--${tone(one)}${one.weekday === best ? ' an-week__cell--best' : ''}${weekday === String(one.weekday) ? ' an-week__cell--on' : ''}`}
            onClick={() => onPick(String(one.weekday))}>
          {value(one)}
        </td>
      ))}
    </tr>
  );
}

// --- состояния блоков ---------------------------------------------------------------------

/**
 * Загрузка, ошибка и отказ — в каждом блоке свои. Скелетон — только пока
 * данных нет вовсе; при смене фильтров блок показывает прежние значения.
 */
function State({ block, onRetry, dark = false }: { block: Block<unknown>; onRetry: () => void; dark?: boolean }) {
  if (block.state === 'loading') {
    return (
      <div className={dark ? 'an-skeleton an-skeleton--dark' : 'an-skeleton'} role="status" aria-label="Загружаем">
        <i /><i /><i />
      </div>
    );
  }
  if (block.state === 'denied') {
    return <p className={dark ? 'an-empty an-empty--dark' : 'an-empty'}>Нет права на аналитику.</p>;
  }
  if (block.state === 'error') {
    return (
      <p className={dark ? 'an-empty an-empty--dark an-empty--bad' : 'an-empty an-empty--bad'}>
        Не удалось загрузить аналитику. Это ошибка запроса, а не нулевые показатели.{' '}
        <button type="button" className="an-link" onClick={onRetry}>Повторить</button>
      </p>
    );
  }
  return null;
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

// --- мелочи ---------------------------------------------------------------------------------

/** Недели с понедельника: пустые клетки до начала и после конца периода. */
function toWeeks(days: api.OverviewDay[]): { label: string; cells: (api.OverviewDay | null)[] }[] {
  const weeks: { label: string; cells: (api.OverviewDay | null)[] }[] = [];
  let current: (api.OverviewDay | null)[] = [];
  days.forEach((one, index) => {
    if (index === 0) current = Array.from({ length: one.weekday - 1 }, () => null);
    current.push(one);
    if (one.weekday === 7 || index === days.length - 1) {
      while (current.length < 7) current.push(null);
      const real = current.filter((cell): cell is api.OverviewDay => cell !== null);
      const first = real[0];
      const last = real[real.length - 1];
      weeks.push({
        label: first && last ? (first.day === last.day ? dayShort(first.day) : weekRange(first.day, last.day)) : '',
        cells: current,
      });
      current = [];
    }
  });
  return weeks;
}

function attendanceLink(day: string, region: string, office: string): string {
  const search = new URLSearchParams({ date: day });
  if (region) search.set('region_id', region);
  if (office) search.set('office_id', office);
  return `/attendance?${search.toString()}`;
}

function level(value: number | null): string {
  if (value === null) return 'none';
  if (value >= 95) return 'top';
  if (value >= 85) return 'good';
  if (value >= 70) return 'mid';
  return 'low';
}

function shift(day: string, delta: number): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + delta);
  return date.toISOString().slice(0, 10);
}

function parts(day: string): [number, number, number] {
  const [y = 0, m = 1, d = 1] = day.split('-').map(Number);
  return [y, m, d];
}

function dayShort(day: string): string {
  const [, m, d] = parts(day);
  return `${d} ${MONTHS_SHORT[m - 1] ?? ''}`;
}

function dayTitle(day: string): string {
  const [, m, d] = parts(day);
  return `${d} ${MONTHS[m - 1] ?? ''}`;
}

function dayLong(day: string): string {
  const [y, m, d] = parts(day);
  return `${d} ${MONTHS[m - 1] ?? ''} ${y}`;
}

function weekRange(first: string, last: string): string {
  const [, m1, d1] = parts(first);
  const [, m2, d2] = parts(last);
  return m1 === m2 ? `${d1} – ${d2} ${MONTHS_SHORT[m2 - 1] ?? ''}` : `${d1} ${MONTHS_SHORT[m1 - 1] ?? ''} – ${d2} ${MONTHS_SHORT[m2 - 1] ?? ''}`;
}

/** «92,5%». Без знаменателя — прочерк, а не ноль процентов. */
function percent(value: number | null | undefined, compact = false): string {
  if (value === null || value === undefined) return '—';
  const text = compact && Number.isInteger(value) ? String(value) : value.toFixed(1);
  return `${text.replace('.', ',')}%`;
}

/** «+5,4 п.п.» — разница долей в процентных пунктах. */
function pointsText(value: number): string {
  return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(1).replace('.', ',')} п.п.`;
}

function number(value: number): string {
  return Math.round(value).toLocaleString('ru-RU');
}

/** «7 ч 58 мин». */
function duration(seconds: number | null): string {
  if (seconds === null) return '—';
  const total = Math.round(seconds / 60);
  return `${Math.floor(total / 60)} ч ${String(total % 60).padStart(2, '0')} мин`;
}

/** «8:03» — среднее время в офисе в таблице недели. */
function hoursClock(seconds: number | null): string {
  if (seconds === null) return '—';
  const total = Math.round(seconds / 60);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

function minutesClock(minutes: number | null): string {
  if (minutes === null) return '—';
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}

function addMinutes(clock: string, offset: number): string {
  const [h = 0, m = 0] = clock.split(':').map(Number);
  const total = (h * 60 + m + offset + 24 * 60) % (24 * 60);
  return minutesClock(total);
}

function plural(n: number, forms: [string, string, string]): string {
  if (n % 10 === 1 && n % 100 !== 11) return forms[0];
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return forms[1];
  return forms[2];
}

const daysWord = (n: number) => plural(n, ['день', 'дня', 'дней']);
const officesWord = (n: number) => plural(n, ['офис', 'офиса', 'офисов']);
const peopleWord = (n: number) => plural(n, ['сотрудник', 'сотрудника', 'сотрудников']);

/**
 * Движение сотрудников: принято, уволено, разница.
 *
 * Три числа периода и три сравнения: с предыдущим равным периодом, с
 * тем же периодом месяцем раньше и годом раньше. Больше здесь не нужно:
 * блок отвечает на вопрос «нас становится больше или меньше», а не
 * заменяет кадровый отчёт.
 *
 * Сравнение — с равным по длине периодом, а не с календарным. Иначе
 * десять дней сравнивались бы с месяцем, и разница объяснялась бы
 * длиной, а не событиями.
 */
function Movement({ block, onRetry }: {
  block: Block<api.MovementReport>;
  onRetry: () => void;
}) {
  if (block.state === 'loading') {
    return (
      <section className="an-card an-movement" aria-label="Движение сотрудников">
        <h2 className="an-card__title">Движение сотрудников</h2>
        <p className="empty" role="status">Считаем…</p>
      </section>
    );
  }
  if (block.state !== 'ready') {
    return (
      <section className="an-card an-movement" aria-label="Движение сотрудников">
        <h2 className="an-card__title">Движение сотрудников</h2>
        <p className="empty empty--bad">
          Не удалось посчитать.{' '}
          <button type="button" className="link" onClick={onRetry}>Повторить</button>
        </p>
      </section>
    );
  }

  const { current, previous, month_before: month, year_before: year, headcount } =
    block.data;

  // Ответ без обязательных полей — это не повод обрушить всю страницу
  // аналитики. Так бывает у старого сервера и у прокси, подменившего
  // тело: блок молчит, остальные продолжают работать.
  if (!current || !previous || !month || !year) {
    return (
      <section className="an-card an-movement" aria-label="Движение сотрудников">
        <h2 className="an-card__title">Движение сотрудников</h2>
        <p className="empty">Данных за период нет.</p>
      </section>
    );
  }

  return (
    <section className="an-card an-movement" aria-label="Движение сотрудников">
      <div className="an-card__head">
        <h2 className="an-card__title">Движение сотрудников</h2>
        <span className="an-card__note">На конец периода: {headcount} чел.</span>
      </div>

      <div className="an-movement__cards">
        <MovementCard title="Принято" value={current.hired} tone="ok" />
        <MovementCard title="Уволено" value={current.left} tone="bad" />
        <MovementCard title="Разница" value={current.difference} tone="plain" signed />
      </div>

      <table className="an-movement__table">
        <caption className="visually-hidden">Сравнение с прошлыми периодами</caption>
        <thead>
          <tr>
            <th scope="col">Период</th>
            <th scope="col">Принято</th>
            <th scope="col">Уволено</th>
            <th scope="col">Разница</th>
          </tr>
        </thead>
        <tbody>
          <MovementRow title="Выбранный период" row={current} />
          <MovementRow title="Предыдущий период" row={previous} />
          <MovementRow title="Месяцем раньше" row={month} />
          <MovementRow title="Годом раньше" row={year} />
        </tbody>
      </table>
    </section>
  );
}

function MovementCard({ title, value, tone, signed = false }: {
  title: string;
  value: number;
  tone: 'ok' | 'bad' | 'plain';
  signed?: boolean;
}) {
  // Знак у разницы обязателен: «3» и «−3» — противоположные новости,
  // и различать их по цвету одному нельзя.
  const shown = signed && value > 0 ? `+${value}` : String(value);
  return (
    <div className={`an-movement__card an-movement__card--${tone}`}>
      <p className="an-movement__cardTitle">{title}</p>
      <p className="an-movement__cardValue">{shown}</p>
    </div>
  );
}

function MovementRow({ title, row }: { title: string; row: api.MovementSpan }) {
  return (
    <tr>
      <th scope="row">
        {title}
        <small>{row.first} — {row.last}</small>
      </th>
      <td>{row.hired}</td>
      <td>{row.left}</td>
      <td>{row.difference > 0 ? `+${row.difference}` : row.difference}</td>
    </tr>
  );
}
