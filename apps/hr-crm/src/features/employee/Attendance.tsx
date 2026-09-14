/**
 * Вкладка «Посещаемость» в карточке сотрудника.
 *
 * Вёрстка повторяет эталон 1672×941 (`ChatGPT Image Sep 13, 2026,
 * 03_58_06 PM.png`). Размеры — в `styles/employee-attendance.css`,
 * классы с префиксом `ea-`.
 *
 * Период и выбранный день лежат в адресе: ссылка на конкретный день,
 * обновление страницы и кнопка «Назад» показывают то же самое. Переключатель
 * периода живёт в шапке карточки (`AttendanceTools`), а читает тот же адрес.
 *
 * Правил учёта здесь нет. Время в офисе, опоздание и состояние дня
 * считает сервер; на клиенте складываются только средние и распределения
 * из тех же чисел.
 */

import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../../api/crm';
import { AppIcon, type AppIconName } from '../../components/AppIcon';
import { useBlock } from '../dashboard/data';
import { clock } from '../time/zone';
import {
  type DayPoint,
  type Plan,
  hhmm,
  minutesInZone,
  planByWeekday,
  points,
  segments,
  stats,
  timeBounds,
} from './attendance-model';
import { currentMonth, dayState, duration, todayIso } from './model';
import '../../styles/employee-attendance.css';

type Rights = { attendance: boolean; export: boolean; correct: boolean };

const PRESETS = [
  { key: 'week', title: 'Неделя' },
  { key: 'month', title: 'Месяц' },
  { key: 'range', title: 'Период' },
] as const;

type PresetKey = (typeof PRESETS)[number]['key'];

const WEEKDAY = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
const MONTHS = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const MONTH_TITLE = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];

/** Период из адреса. Один источник и для шапки, и для вкладки. */
function usePeriod() {
  const [params, setParams] = useSearchParams();
  const patch = (changes: Record<string, string | null>) =>
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
  const raw = params.get('period');
  const preset: PresetKey = PRESETS.some((one) => one.key === raw) ? (raw as PresetKey) : 'month';
  const from = params.get('from');
  const to = params.get('to');
  const range = useMemo(() => bounds(preset, from, to), [preset, from, to]);
  return { params, patch, preset, range };
}

/** Переключатель периода, даты и экспорт — справа в шапке карточки. */
export function AttendanceTools({ id, canExport }: { id: string; canExport: boolean }) {
  const { patch, preset, range } = usePeriod();
  return (
    <div className="ea-tools">
      <div className="ea-seg" role="group" aria-label="Период">
        {PRESETS.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={preset === item.key}
            className={preset === item.key ? 'ea-seg__one ea-seg__one--on' : 'ea-seg__one'}
            onClick={() => patch({ period: item.key === 'month' ? null : item.key, day: null })}
          >
            {item.title}
          </button>
        ))}
      </div>
      <label className="ea-dates">
        <AppIcon name="calendar" size={18} />
        <input type="date" value={range.first} aria-label="Начало периода"
               onChange={(event) => patch({ period: 'range', from: event.target.value, to: range.last, day: null })} />
        <span>—</span>
        <input type="date" value={range.last} aria-label="Конец периода"
               onChange={(event) => patch({ period: 'range', from: range.first, to: event.target.value, day: null })} />
      </label>
      {canExport && (
        <Link className="ea-export"
              to={`/reports?kind=sessions&employee_id=${id}&date_from=${range.first}&date_to=${range.last}`}>
          <AppIcon name="download" size={20} />
          Экспорт
        </Link>
      )}
    </div>
  );
}

export function Attendance({ id, rights, zone }: { id: string; rights: Rights; zone: string }) {
  const { params, patch, preset, range } = usePeriod();

  const [journal, reloadJournal, journalState] = useBlock(
    (signal) => api.attendanceDaily({ employee_id: id, date_from: range.first, date_to: range.last }, signal),
    `journal|${id}|${range.first}|${range.last}`,
    rights.attendance,
  );

  // График назначен сотруднику, а не периоду: при смене недели не перезапрашивается.
  const [plan] = useBlock(
    (signal) =>
      api.employeeSchedules(id, signal).then((rows) => {
        const items = rows.items as unknown as api.ScheduleAssignment[];
        const current = items.find((row) => row.valid_to === null) ?? items[0];
        return current ? api.workSchedule(current.schedule_id, signal) : null;
      }),
    `plan|${id}`,
    rights.attendance,
  );

  if (!rights.attendance) {
    return (
      <p className="ea-empty ea-empty--bad">
        Нет права на посещаемость. Это отдельное разрешение — attendance.read.
      </p>
    );
  }

  const report = journal.state === 'ready' ? journal.data : null;
  const schedule = plan.state === 'ready' ? plan.data : null;
  const days = report ? points(report.days, planByWeekday(schedule), todayIso()) : [];
  const sums = stats(days);
  // Без выбора в адресе показывается последний прошедший день с отметками.
  const latest = [...days].reverse().find((one) => !one.future && one.entry !== null);
  const picked = params.get('day') ?? latest?.day ?? null;
  const chosen = days.find((one) => one.day === picked) ?? null;
  const pick = (day: string) => patch({ day });

  return (
    <div className={journalState.busy ? 'ea ea--busy' : 'ea'}>
      {journal.state === 'error' && !report && (
        <p className="ea-empty ea-empty--bad">
          Не удалось получить журнал.{' '}
          <button type="button" className="link" onClick={reloadJournal}>Повторить</button>
        </p>
      )}
      {journalState.failed && report && (
        <p className="ea-note" role="status">
          Показаны прежние данные: обновить не удалось.{' '}
          <button type="button" className="link" onClick={reloadJournal}>Повторить</button>
        </p>
      )}
      {report?.note && <p className="ea-note">{report.note}</p>}

      <Metrics sums={sums} report={report} />

      <div className="ea-grid">
        <div className="ea-col">
          <section className="ea-panel ea-panel--timeline">
            <h3 className="ea-title">Приход и уход</h3>
            <Timeline days={days} picked={picked} onPick={pick} />
          </section>
          <section className="ea-panel ea-panel--hours">
            <h3 className="ea-title">Часы по дням</h3>
            <Hours days={days} picked={picked} onPick={pick} />
          </section>
        </div>
        <div className="ea-col">
          <Usual days={days} sums={sums} preset={preset} />
          <DayPanel id={id} point={chosen} zone={zone} />
        </div>
      </div>

      <section className="ea-panel ea-panel--journal">
        <div className="ea-journal__head">
          <h3 className="ea-title">Журнал по дням</h3>
          <ul className="ea-keys">
            <li><i className="ea-key ea-key--ok" />Вовремя</li>
            <li><i className="ea-key ea-key--late" />Опоздание</li>
            <li><i className="ea-key ea-key--early" />Ранний уход</li>
            <li><i className="ea-key ea-key--off" />Выходной</li>
            <li><i className="ea-key ea-key--leave" />Отпуск</li>
          </ul>
        </div>
        <Journal days={days} picked={picked} onPick={pick} loading={journal.state === 'loading' && !report} />
      </section>
    </div>
  );
}

/** Границы периода. «Период» берёт даты из адреса, остальные считаются. */
function bounds(preset: string, from: string | null, to: string | null): { first: string; last: string } {
  if (preset === 'range' && from && to) return { first: from, last: to };
  if (preset === 'week') {
    const now = new Date();
    const monday = new Date(now);
    monday.setDate(now.getDate() - ((now.getDay() + 6) % 7));
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    return { first: iso(monday), last: iso(sunday) };
  }
  return currentMonth();
}

const iso = (date: Date): string =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

// --- показатели ------------------------------------------------------------------

function Metrics({ sums, report }: { sums: ReturnType<typeof stats>; report: api.DailyReport | null }) {
  return (
    <div className="ea-metrics" aria-label="Показатели за период">
      <Metric icon="users" title="Средний приход" value={hhmm(sums.averageEntry)} />
      <Metric icon="logout" title="Средний уход" value={hhmm(sums.averageExit)} tone="green" />
      <Metric icon="clock" title="Отработано"
              value={report ? `${Math.round(report.totals.seconds / 3600)} ч` : '—'} />
      <Metric icon="alert" title="Опоздания" value={String(sums.lateDays)}
              tone={sums.lateDays > 0 ? 'warn' : undefined} />
      <Metric icon="chart" title="Выполнение графика"
              value={sums.completion === null ? '—' : `${sums.completion}%`} />
    </div>
  );
}

function Metric({ icon, title, value, tone }: {
  icon: AppIconName;
  title: string;
  value: string;
  tone?: 'warn' | 'green' | undefined;
}) {
  return (
    <div className={tone ? `ea-metric ea-metric--${tone}` : 'ea-metric'}>
      <AppIcon name={icon} size={20} />
      <span className="ea-metric__text">
        <span>{title}</span>
        <strong>{value}</strong>
      </span>
    </div>
  );
}

// --- «Приход и уход» ---------------------------------------------------------------

const TL = { w: 925, h: 192, left: 42, right: 14, top: 8, bottom: 48 };

function Timeline({ days, picked, onPick }: {
  days: DayPoint[];
  picked: string | null;
  onPick: (day: string) => void;
}) {
  if (days.length === 0) return <p className="ea-empty">За период данных нет.</p>;

  const data = timeBounds(days);
  const low = Math.min(6 * 60, data.low);
  const high = Math.max(20 * 60, data.high);
  const plotH = TL.h - TL.top - TL.bottom;
  const step = (TL.w - TL.left - TL.right) / days.length;
  const x = (index: number) => TL.left + step * index + step / 2;
  const y = (minutes: number) => TL.top + plotH * (1 - (minutes - low) / (high - low));

  const grid: number[] = [];
  for (let at = Math.ceil(low / 120) * 120; at <= high; at += 120) grid.push(at);
  const planStart = days.find((one) => one.plan.start !== null)?.plan.start ?? null;
  const planEnd = days.find((one) => one.plan.end !== null)?.plan.end ?? null;
  const index = days.findIndex((one) => one.day === picked);
  const chosen = index >= 0 ? days[index] : undefined;

  return (
    <div className="ea-timeline">
      <div className="ea-chart">
        <svg className="ea-svg" viewBox={`0 0 ${TL.w} ${TL.h}`} role="img"
             aria-label="Первый вход и последний выход по дням">
          {grid.map((at) => (
            <g key={at}>
              <line className="ea-svg__grid" x1={TL.left} x2={TL.w - TL.right} y1={y(at)} y2={y(at)} />
              <text className="ea-svg__axis" x={TL.left - 8} y={y(at) + 4} textAnchor="end">{hhmm(at)}</text>
            </g>
          ))}
          {days.map((point, at) => (
            <line key={`v-${point.day}`} className="ea-svg__grid ea-svg__grid--v"
                  x1={x(at)} x2={x(at)} y1={TL.top} y2={TL.top + plotH} />
          ))}
          {planStart !== null && (
            <line className="ea-svg__plan ea-svg__plan--in" x1={TL.left} x2={TL.w - TL.right}
                  y1={y(planStart)} y2={y(planStart)} />
          )}
          {planEnd !== null && (
            <line className="ea-svg__plan ea-svg__plan--out" x1={TL.left} x2={TL.w - TL.right}
                  y1={y(planEnd)} y2={y(planEnd)} />
          )}
          {chosen && (
            <>
              <rect className="ea-svg__pick" x={x(index) - step / 2} y={TL.top} width={step} height={plotH + 24} />
              <line className="ea-svg__cursor" x1={x(index)} x2={x(index)} y1={TL.top} y2={TL.top + plotH} />
            </>
          )}

          {/* Пропуски линией не соединяются: выходной не плавный переход. */}
          {segments(days, (one) => one.entry).map((run, at) => (
            <polyline key={`in-${at}`} className="ea-svg__line ea-svg__line--in"
                      points={run.map((one) => `${x(days.indexOf(one))},${y(one.entry as number)}`).join(' ')} />
          ))}
          {segments(days, (one) => one.exit).map((run, at) => (
            <polyline key={`out-${at}`} className="ea-svg__line ea-svg__line--out"
                      points={run.map((one) => `${x(days.indexOf(one))},${y(one.exit as number)}`).join(' ')} />
          ))}

          {days.map((point, at) => (
            <g key={point.day}>
              {point.entry !== null && (
                <circle className={(point.late ?? 0) > 0 ? 'ea-svg__dot ea-svg__dot--bad' : 'ea-svg__dot ea-svg__dot--in'}
                        cx={x(at)} cy={y(point.entry)} r={4.5} />
              )}
              {point.exit !== null && (
                <circle className={(point.early ?? 0) > 0 ? 'ea-svg__dot ea-svg__dot--bad' : 'ea-svg__dot ea-svg__dot--out'}
                        cx={x(at)} cy={y(point.exit)} r={4.5} />
              )}
              <text className={point.day === picked ? 'ea-svg__axis ea-svg__axis--on' : 'ea-svg__axis'}
                    x={x(at)} y={TL.h - 28} textAnchor="middle">{point.label}</text>
              {/* Вся колонка — область нажатия: в точку радиусом 4 не попасть. */}
              <rect className="ea-svg__hit" x={x(at) - step / 2} y={TL.top} width={step} height={plotH + 24}
                    onClick={() => onPick(point.day)} />
            </g>
          ))}
          <text className="ea-svg__axis" x={(TL.left + TL.w - TL.right) / 2} y={TL.h - 6} textAnchor="middle">
            {monthTitle(days)}
          </text>
        </svg>

        {chosen && chosen.entry !== null && (
          <div
            className={index > days.length * 0.7 ? 'ea-tip ea-tip--left' : 'ea-tip'}
            style={{ left: `${(x(index) / TL.w) * 100}%`, top: `${(y(chosen.entry) / TL.h) * 100}%` }}
          >
            <b>{dayShort(chosen.day)}</b>
            <span><i className="ea-key ea-key--in" />Приход <strong>{hhmm(chosen.entry)}</strong>
              {(chosen.late ?? 0) > 0 && <em> · Опоздание {chosen.late} мин</em>}
            </span>
            <span><i className="ea-key ea-key--ok" />Уход <strong>{chosen.open ? 'не отмечен' : hhmm(chosen.exit)}</strong>
              {(chosen.early ?? 0) > 0 && <em> · Ранний уход {chosen.early} мин</em>}
            </span>
          </div>
        )}
      </div>

      <ul className="ea-legend">
        <li><i className="ea-legend__line ea-legend__line--in" />Первый вход (приход)</li>
        <li><i className="ea-legend__line ea-legend__line--out" />Последний выход (уход)</li>
        <li><i className="ea-legend__dash" />Плановый приход{planStart !== null ? ` (${hhmm(planStart)})` : ''}</li>
        <li><i className="ea-legend__dash ea-legend__dash--dark" />Плановый уход{planEnd !== null ? ` (${hhmm(planEnd)})` : ''}</li>
        <li><i className="ea-key ea-key--late" />Нарушение (опоздание / ранний уход)</li>
      </ul>
    </div>
  );
}

// --- «Часы по дням» -----------------------------------------------------------------

const HB = { w: 925, h: 138, left: 42, right: 14, top: 6, bottom: 40 };

function Hours({ days, picked, onPick }: {
  days: DayPoint[];
  picked: string | null;
  onPick: (day: string) => void;
}) {
  if (days.length === 0) return <p className="ea-empty">За период данных нет.</p>;

  const norms = days.map((one) => one.plan.norm).filter((one): one is number => one !== null && one > 0);
  const norm = norms.length ? Math.max(...norms) : 8 * 3600;
  const top = Math.max(12 * 3600, ...days.map((one) => one.row.seconds));
  const plotH = HB.h - HB.top - HB.bottom;
  const step = (HB.w - HB.left - HB.right) / days.length;
  const width = Math.max(4, step * 0.56);
  const x = (index: number) => HB.left + step * index + step / 2;
  const y = (seconds: number) => HB.top + plotH * (1 - Math.min(seconds, top) / top);

  return (
    <>
      <svg className="ea-svg" viewBox={`0 0 ${HB.w} ${HB.h}`} role="img" aria-label="Часы в офисе по дням">
        {[0, 4, 8, 12].map((hours) => (
          <text key={hours} className="ea-svg__axis" x={HB.left - 10} y={y(hours * 3600) + 4} textAnchor="end">
            {hours ? `${hours} ч` : '0'}
          </text>
        ))}
        {days.map((point, at) => {
          const on = point.day === picked;
          const seconds = point.row.seconds;
          const left = x(at) - width / 2;
          const idle = !point.working || point.future || point.plan.norm === 0;
          return (
            <g key={point.day}>
              {on && <rect className="ea-svg__pick" x={x(at) - step / 2} y={HB.top} width={step} height={plotH + 22} rx={4} />}
              {idle ? (
                // Нерабочий день — светлая метка, а не «ноль часов».
                <rect className="ea-bar ea-bar--off" x={left} width={width}
                      y={y(seconds > 0 ? seconds : norm * 0.55)}
                      height={HB.top + plotH - y(seconds > 0 ? seconds : norm * 0.55)} />
              ) : seconds >= norm ? (
                <>
                  <rect className="ea-bar ea-bar--ok" x={left} width={width}
                        y={y(Math.min(seconds, norm))} height={HB.top + plotH - y(Math.min(seconds, norm))} />
                  {seconds > norm * 1.05 && (
                    <rect className="ea-bar ea-bar--over" x={left} width={width}
                          y={y(seconds)} height={y(norm) - y(seconds)} />
                  )}
                </>
              ) : (
                <rect className="ea-bar ea-bar--warn" x={left} width={width}
                      y={y(seconds)} height={HB.top + plotH - y(seconds)} />
              )}
              {on && seconds > 0 && (
                <rect className="ea-bar__outline" x={left - 2} width={width + 4}
                      y={y(seconds) - 2} height={HB.top + plotH - y(seconds) + 2} />
              )}
              <text className={on ? 'ea-svg__axis ea-svg__axis--on' : 'ea-svg__axis'}
                    x={x(at)} y={HB.h - 22} textAnchor="middle">{point.label}</text>
              <rect className="ea-svg__hit" x={x(at) - step / 2} y={HB.top} width={step} height={plotH + 22}
                    onClick={() => onPick(point.day)}>
                <title>{barTip(point, norm)}</title>
              </rect>
            </g>
          );
        })}
        <line className="ea-svg__plan ea-svg__plan--out" x1={HB.left} x2={HB.w - HB.right} y1={y(norm)} y2={y(norm)} />
        <text className="ea-svg__axis" x={(HB.left + HB.w - HB.right) / 2} y={HB.h - 4} textAnchor="middle">
          {monthTitle(days)}
        </text>
      </svg>
      <ul className="ea-legend">
        <li><i className="ea-key ea-key--sq ea-key--ok" />Норма (≥ {Math.round(norm / 3600)} ч)</li>
        <li><i className="ea-key ea-key--sq ea-key--late" />Меньше нормы</li>
        <li><i className="ea-key ea-key--sq ea-key--over" />Переработка</li>
        <li><i className="ea-key ea-key--sq ea-key--off" />Нет рабочего дня</li>
        <li><i className="ea-legend__dash ea-legend__dash--dark" />Норма ({Math.round(norm / 3600)} ч)</li>
      </ul>
    </>
  );
}

function barTip(point: DayPoint, norm: number): string {
  if (!point.working) return `${point.day} · ${dayState(point.row)}`;
  const diff = point.row.seconds - norm;
  return `${point.day} · ${duration(point.row.seconds)} · ${diff >= 0 ? '+' : '−'}${duration(Math.abs(diff))}`;
}

// --- «Когда обычно» -----------------------------------------------------------------

function Usual({ days, sums, preset }: {
  days: DayPoint[];
  sums: ReturnType<typeof stats>;
  preset: PresetKey;
}) {
  const workdays = days.filter((one) => one.working && !one.future);
  const entries = workdays.map((one) => one.entry).filter((one): one is number => one !== null);
  const exits = workdays.map((one) => one.exit).filter((one): one is number => one !== null);
  return (
    <section className="ea-panel ea-panel--usual">
      <h3 className="ea-title">Когда обычно</h3>
      <Dist title="Чаще приходит" side="Средний приход" average={sums.averageEntry}
            values={entries} from={7 * 60} to={11 * 60} tone="in" />
      <Dist title="Чаще уходит" side="Средний уход" average={sums.averageExit}
            values={exits} from={16 * 60} to={20 * 60} tone="out" />
      <p className="ea-usual__period">
        {preset === 'week' ? 'За неделю' : preset === 'month' ? 'За месяц' : 'За период'}
      </p>
      <div className="ea-counts">
        <Count icon="check" tone="ok" title="Вовремя" value={sums.onTime} />
        <Count icon="alert" tone="warn" title="Опоздал" value={sums.lateDays} />
        <Count icon="clock" tone="warn" title="Ранний уход" value={sums.earlyDays} />
      </div>
    </section>
  );
}

/** Распределение по четвертям часа и самое плотное получасовое окно. */
function Dist({ title, side, average, values, from, to, tone }: {
  title: string;
  side: string;
  average: number | null;
  values: number[];
  from: number;
  to: number;
  tone: 'in' | 'out';
}) {
  const step = 15;
  const count = (to - from) / step;
  const buckets = Array.from({ length: count }, (_, at) =>
    values.filter((one) => one >= from + at * step && one < from + (at + 1) * step).length);
  const top = Math.max(1, ...buckets);
  let peak = -1;
  let best = 0;
  buckets.forEach((one, at) => {
    const pair = one + (buckets[at + 1] ?? 0);
    if (pair > best) { best = pair; peak = at; }
  });
  const hours = Array.from({ length: (to - from) / 60 + 1 }, (_, at) => from + at * 60);
  const tallest = buckets.indexOf(Math.max(...buckets));

  return (
    <div className="ea-dist">
      <p className="ea-dist__left">
        <span>{title}</span>
        <strong>{peak < 0 ? '—' : `${hhmm(from + peak * step)} – ${hhmm(from + peak * step + 30)}`}</strong>
      </p>
      <p className="ea-dist__right">
        <span>{side}</span>
        <strong>{hhmm(average)}</strong>
      </p>
      <div className="ea-dist__plot" role="img" aria-label={`${title}: распределение по времени`}>
        {buckets.map((one, at) => one > 0 && (
          <i key={at}
             className={`ea-dist__bar ea-dist__bar--${tone}${at === tallest ? ' ea-dist__bar--top' : ''}`}
             style={{ left: `${(at / count) * 100}%`, width: `${100 / count}%`, height: `${(one / top) * 100}%` }} />
        ))}
      </div>
      <div className="ea-dist__axis">
        {hours.map((at) => (
          <span key={at} style={{ left: `${((at - from) / (to - from)) * 100}%` }}>{hhmm(at)}</span>
        ))}
      </div>
    </div>
  );
}

function Count({ icon, tone, title, value }: {
  icon: AppIconName;
  tone: 'ok' | 'warn';
  title: string;
  value: number;
}) {
  return (
    <div className={`ea-count ea-count--${tone}`}>
      <AppIcon name={icon} size={20} />
      <span>
        <small>{title}</small>
        <strong>{value} {daysWord(value)}</strong>
      </span>
    </div>
  );
}

// --- выбранный день ----------------------------------------------------------------------

function DayPanel({ id, point, zone }: { id: string; point: DayPoint | null; zone: string }) {
  const day = point?.day ?? '';
  const [detail] = useBlock(
    (signal) => api.events(
      { employee_id: id, date_from: day, date_to: day, limit: '50', verification_status: 'ACCEPTED' },
      signal,
    ),
    `day|${id}|${day}`,
    Boolean(day),
  );

  if (point === null) {
    return (
      <section className="ea-panel ea-panel--day">
        <h3 className="ea-title">Подробности дня</h3>
        <p className="ea-empty">Выберите день на графике или в журнале.</p>
      </section>
    );
  }

  const zoned = point.row.timezone || zone;
  const marks = detail.state === 'ready'
    ? [...detail.data.items].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
    : [];
  const late = point.late ?? 0;
  const early = point.early ?? 0;
  // Проверка подтверждена, только если была хоть одна и ни одна не провалена.
  const confirmed = (key: 'inside_geofence' | 'inside_office_network') =>
    marks.some((mark) => mark[key] === true) && marks.every((mark) => mark[key] !== false);

  return (
    <section className="ea-panel ea-panel--day">
      <h3 className="ea-title">{dayLong(point.day)}</h3>
      <p className="ea-day__sum">
        <strong>{point.working ? duration(point.row.seconds) : dayState(point.row)}</strong>
        {late > 0 && <><i>·</i><span className="ea-bad">Опоздание {late} мин</span></>}
        {early > 0 && <><i>·</i><span className="ea-bad">Ранний уход {early} мин</span></>}
      </p>

      {detail.state === 'loading' && <p className="ea-empty" role="status">Читаем отметки…</p>}
      {detail.state === 'error' && <p className="ea-empty ea-empty--bad">Не удалось получить отметки дня.</p>}
      {detail.state === 'ready' && marks.length === 0 && <p className="ea-empty">Отметок за день нет.</p>}

      {marks.length > 0 && (
        <>
          <ul className="ea-marks">
            {marks.map((mark, at) => {
              const entry = mark.event_type === 'ENTRY';
              const previous = marks.slice(0, at).reverse().find((one) => one.event_type === 'ENTRY');
              const aside = entry
                ? at === 0 && late > 0 ? `${late} мин` : ''
                : previous
                  ? duration(Math.round((Date.parse(mark.occurred_at) - Date.parse(previous.occurred_at)) / 1000))
                  : '';
              const where = mark.qr_point_name ?? mark.office_name;
              return (
                <li key={mark.id} className={entry ? 'ea-marks__in' : 'ea-marks__out'}>
                  <b>{clock(mark.occurred_at, zoned)}</b>
                  <span>{entry ? 'Вход' : 'Выход'}{where ? ` · ${where}` : ''}</span>
                  <small>{aside}</small>
                </li>
              );
            })}
          </ul>
          <div className="ea-checks">
            <Check ok={confirmed('inside_geofence')} yes="Геозона подтверждена" no="Геозона не подтверждена" />
            <Check ok={confirmed('inside_office_network')} yes="Сеть офиса подтверждена" no="Сеть офиса не подтверждена" />
          </div>
        </>
      )}

      <Link className="ea-daylink" to={`/attendance?date=${point.day}&employee=${id}`}>
        <AppIcon name="doc" size={18} />
        Открыть полный журнал дня
      </Link>
    </section>
  );
}

function Check({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return (
    <span className={ok ? 'ea-check ea-check--ok' : 'ea-check'}>
      <AppIcon name={ok ? 'check' : 'alert'} size={18} />
      {ok ? yes : no}
    </span>
  );
}

// --- журнал ------------------------------------------------------------------------------

function Journal({ days, picked, onPick, loading }: {
  days: DayPoint[];
  picked: string | null;
  onPick: (day: string) => void;
  loading: boolean;
}) {
  if (loading) return <p className="ea-empty" role="status">Считаем журнал…</p>;
  if (days.length === 0) return <p className="ea-empty">За период строк нет.</p>;

  return (
    <div className="ea-table-wrap">
      <table className="ea-table" aria-label="Журнал по дням">
        <thead>
          <tr>
            <th scope="col">Дата</th>
            <th scope="col">Первый вход</th>
            <th scope="col">Последний выход</th>
            <th scope="col">В офисе</th>
            <th scope="col">Отклонение</th>
            <th scope="col">Статус</th>
            <th scope="col" aria-label="Действия" />
          </tr>
        </thead>
        <tbody>
          {days.map((point) => {
            const status = statusOf(point);
            const late = point.late ?? 0;
            const early = point.early ?? 0;
            const worked = point.working && !point.future;
            return (
              <tr key={point.day} tabIndex={0}
                  className={point.day === picked ? 'ea-table__on' : undefined}
                  onClick={() => onPick(point.day)}
                  onKeyDown={(event) => { if (event.key === 'Enter') onPick(point.day); }}>
                <td>{dayCell(point.day)}</td>
                <td>{worked ? hhmm(point.entry) : '—'}</td>
                <td>{worked ? (point.open ? 'не отмечен' : hhmm(point.exit)) : '—'}</td>
                <td>{worked && point.row.seconds > 0 ? padded(point.row.seconds) : '—'}</td>
                <td className={late > 0 || early > 0 ? 'ea-bad' : undefined}>
                  {late > 0 ? `Опоздание ${late} мин` : early > 0 ? `Ранний уход ${early} мин` : '—'}
                </td>
                <td><span className={`ea-state ea-state--${status.tone}`}>{status.title}</span></td>
                <td>
                  <button type="button" className="ea-dots" aria-label="Подробности дня"
                          onClick={(event) => { event.stopPropagation(); onPick(point.day); }}>
                    <i /><i /><i />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function statusOf(point: DayPoint): { title: string; tone: string } {
  const state = point.row.state;
  if (point.future) return { title: 'Впереди', tone: 'off' };
  if (state === 'VACATION' || state === 'SICK_LEAVE' || state === 'OTHER_ABSENCE') {
    return { title: dayState(point.row), tone: 'leave' };
  }
  if (state === 'DAY_OFF') return { title: 'Выходной', tone: 'off' };
  if (state === 'NO_SCHEDULE') return { title: 'Без графика', tone: 'off' };
  if (state === 'NOT_COME') return { title: 'Нет отметки', tone: 'bad' };
  if ((point.late ?? 0) > 0) return { title: 'Опоздание', tone: 'late' };
  if ((point.early ?? 0) > 0) return { title: 'Ранний уход', tone: 'early' };
  return { title: 'Вовремя', tone: 'ok' };
}

// --- мелочи -----------------------------------------------------------------------------

function parts(day: string): { year: number; month: number; date: number; weekday: string } {
  const [year = 0, month = 1, date = 1] = day.split('-').map(Number);
  return { year, month, date, weekday: WEEKDAY[new Date(`${day}T12:00:00`).getDay()] ?? '' };
}

/** «13 сентября 2026 (Вт)». */
function dayLong(day: string): string {
  const p = parts(day);
  return `${p.date} ${MONTHS[p.month - 1] ?? ''} ${p.year} (${p.weekday})`;
}

/** «13 сен 2026 (Вт)». */
function dayShort(day: string): string {
  const p = parts(day);
  return `${p.date} ${MONTHS_SHORT[p.month - 1] ?? ''} ${p.year} (${p.weekday})`;
}

/** «01.09.2026 (Вт)». */
function dayCell(day: string): string {
  const p = parts(day);
  return `${String(p.date).padStart(2, '0')}.${String(p.month).padStart(2, '0')}.${p.year} (${p.weekday})`;
}

/** «Сен 2026» или «Авг – Сен 2026». */
function monthTitle(days: DayPoint[]): string {
  const first = days[0];
  const last = days[days.length - 1];
  if (!first || !last) return '';
  const a = parts(first.day);
  const b = parts(last.day);
  const one = MONTH_TITLE[a.month - 1] ?? '';
  const two = MONTH_TITLE[b.month - 1] ?? '';
  return a.month === b.month && a.year === b.year ? `${one} ${a.year}` : `${one} – ${two} ${b.year}`;
}

/** «9 ч 09 мин» — ровные колонки в журнале. */
function padded(seconds: number): string {
  // Сначала целые минуты, потом часы: иначе 8 ч 59,5 мин давали «8 ч 60 мин».
  const total = Math.round(seconds / 60);
  return `${Math.floor(total / 60)} ч ${String(total % 60).padStart(2, '0')} мин`;
}

function daysWord(n: number): string {
  if (n % 10 === 1 && n % 100 !== 11) return 'день';
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return 'дня';
  return 'дней';
}

export type { Plan };
export { minutesInZone };
