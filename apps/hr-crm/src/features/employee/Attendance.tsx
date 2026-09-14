/**
 * Вкладка «Посещаемость» в карточке сотрудника.
 *
 * Период и выбранный день лежат в адресе страницы, а не в памяти
 * компонента: человек присылает ссылку на конкретный день коллеге,
 * обновляет страницу после правки отметки и возвращается сюда кнопкой
 * «Назад» — во всех трёх случаях он обязан увидеть то же самое.
 *
 * Ни одного правила учёта здесь нет. Время в офисе, опоздание,
 * состояние дня и границы суток считает сервер; этот экран переводит
 * его ответ в линии и столбцы. Средние и распределения — единственное,
 * что складывается на клиенте, и складывается из тех же чисел.
 *
 * Смена периода не гасит экран: `useBlock` держит прошлые данные до
 * прихода новых, и на месте графика в это время не пустота, а прежние
 * дни с тонким индикатором обновления. Высота блоков при этом не
 * меняется — иначе страница прыгала бы под курсором.
 */

import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../../api/crm';
import { AppIcon } from '../../components/AppIcon';
import { useBlock } from '../dashboard/data';
import { clockOnDay } from '../time/zone';
import {
  type DayPoint,
  type Plan,
  hhmm,
  histogram,
  minutesInZone,
  planByWeekday,
  points,
  segments,
  stats,
  timeBounds,
  usual,
} from './attendance-model';
import { currentMonth, dayState, duration, lateness, orDash, todayIso } from './model';

type Rights = { attendance: boolean; export: boolean; correct: boolean };

/** Предустановленные периоды. «Период» — произвольные даты из адреса. */
const PRESETS = [
  { key: 'week', title: 'Неделя' },
  { key: 'month', title: 'Месяц' },
  { key: 'range', title: 'Период' },
] as const;

export function Attendance({
  id,
  rights,
  zone,
}: {
  id: string;
  rights: Rights;
  zone: string;
}) {
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

  const preset = (params.get('period') ?? 'month') as (typeof PRESETS)[number]['key'];
  const range = useMemo(() => bounds(preset, params.get('from'), params.get('to')), [
    preset,
    params,
  ]);
  const picked = params.get('day');

  const [journal, reloadJournal, journalState] = useBlock(
    (signal) =>
      api.attendanceDaily(
        { employee_id: id, date_from: range.first, date_to: range.last },
        signal,
      ),
    `journal|${id}|${range.first}|${range.last}`,
    rights.attendance,
  );

  // График назначен сотруднику, а не периоду: он меняется редко, и
  // перезапрашивать его при каждом переключении недели незачем.
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
      <p className="empty empty--bad">
        Нет права на посещаемость. Это отдельное разрешение —
        попросите <span className="mono">attendance.read</span>.
      </p>
    );
  }

  const report = journal.state === 'ready' ? journal.data : null;
  const schedule = plan.state === 'ready' ? plan.data : null;
  const plans = planByWeekday(schedule);
  const today = todayIso();
  const days = report ? points(report.days, plans, today) : [];
  const sums = stats(days);
  const chosen = days.find((one) => one.day === picked) ?? null;

  return (
    <div className={`att${journalState.busy ? ' att--busy' : ''}`}>
      <div className="att__bar">
        <div className="seg" role="group" aria-label="Период">
          {PRESETS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`seg__one${preset === item.key ? ' seg__one--on' : ''}`}
              aria-pressed={preset === item.key}
              onClick={() => patch({ period: item.key === 'month' ? null : item.key })}
            >
              {item.title}
            </button>
          ))}
        </div>

        <label className="pick pick--dates">
          <AppIcon name="calendar" size={16} />
          <input
            type="date"
            value={range.first}
            aria-label="Начало периода"
            onChange={(event) =>
              patch({ period: 'range', from: event.target.value, to: range.last })
            }
          />
          <span className="muted">—</span>
          <input
            type="date"
            value={range.last}
            aria-label="Конец периода"
            onChange={(event) =>
              patch({ period: 'range', from: range.first, to: event.target.value })
            }
          />
        </label>

        {rights.export && (
          <Link
            className="btn"
            to={`/reports?kind=sessions&employee_id=${id}&date_from=${range.first}&date_to=${range.last}`}
          >
            <AppIcon name="download" size={16} />
            Экспорт
          </Link>
        )}
      </div>

      {journal.state === 'error' && !report && (
        <p className="empty empty--bad">
          Не удалось получить журнал.{' '}
          <button type="button" className="link" onClick={reloadJournal}>
            Повторить
          </button>
        </p>
      )}
      {journalState.failed && report && (
        <p className="note note--dim" role="status">
          Показаны прежние данные: обновить не удалось.{' '}
          <button type="button" className="link" onClick={reloadJournal}>
            Повторить
          </button>
        </p>
      )}
      {report?.note && <p className="note note--dim">{report.note}</p>}

      <Metrics sums={sums} report={report} />

      <div className="att__grid">
        <div className="att__main">
          <section className="panel att__panel">
            <h3 className="att__title">Приход и уход</h3>
            <Timeline days={days} picked={picked} onPick={(day) => patch({ day })} />
          </section>

          <section className="panel att__panel">
            <h3 className="att__title">Часы по дням</h3>
            <Hours days={days} picked={picked} onPick={(day) => patch({ day })} />
          </section>

          <section className="panel att__panel">
            <h3 className="att__title">Журнал по дням</h3>
            <Journal
              days={days}
              picked={picked}
              onPick={(day) => patch({ day })}
              loading={journal.state === 'loading' && !report}
            />
          </section>
        </div>

        <div className="att__side">
          <Usual days={days} sums={sums} />
          <DayPanel
            id={id}
            point={chosen}
            zone={zone}
            canCorrect={rights.correct}
          />
        </div>
      </div>
    </div>
  );
}

/** Границы периода. «Период» берёт даты из адреса, остальные — считают. */
function bounds(
  preset: string,
  from: string | null,
  to: string | null,
): { first: string; last: string } {
  if (preset === 'range' && from && to) return { first: from, last: to };
  if (preset === 'week') {
    const now = new Date();
    const shift = (now.getDay() + 6) % 7;
    const monday = new Date(now);
    monday.setDate(now.getDate() - shift);
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    return { first: iso(monday), last: iso(sunday) };
  }
  return currentMonth();
}

const iso = (date: Date): string =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(
    date.getDate(),
  ).padStart(2, '0')}`;

// --- строка показателей ------------------------------------------------------

function Metrics({
  sums,
  report,
}: {
  sums: ReturnType<typeof stats>;
  report: api.DailyReport | null;
}) {
  return (
    <div className="att__metrics" aria-label="Показатели за период">
      <Metric icon="users" title="Средний приход" value={hhmm(sums.averageEntry)} />
      <Metric icon="arrow" title="Средний уход" value={hhmm(sums.averageExit)} />
      <Metric
        icon="clock"
        title="Отработано"
        value={report ? duration(report.totals.seconds) : '—'}
      />
      <Metric
        icon="alert"
        title="Опоздания"
        value={String(sums.lateDays)}
        tone={sums.lateDays > 0 ? 'warn' : undefined}
      />
      <Metric
        icon="chart"
        title="Выполнение графика"
        value={sums.completion === null ? '—' : `${sums.completion}%`}
      />
    </div>
  );
}

function Metric({
  icon,
  title,
  value,
  tone,
}: {
  icon: 'users' | 'arrow' | 'clock' | 'alert' | 'chart';
  title: string;
  value: string;
  tone?: 'warn' | undefined;
}) {
  return (
    <div className="att__metric">
      <AppIcon name={icon} size={20} />
      <span className="att__metric-text">
        <span className="att__metric-title">{title}</span>
        <span className={`att__metric-value${tone ? ` att__metric-value--${tone}` : ''}`}>
          {value}
        </span>
      </span>
    </div>
  );
}

// --- график «Приход и уход» --------------------------------------------------

const BOX = { w: 920, h: 260, left: 46, right: 12, top: 14, bottom: 34 };

function Timeline({
  days,
  picked,
  onPick,
}: {
  days: DayPoint[];
  picked: string | null;
  onPick: (day: string) => void;
}) {
  if (days.length === 0) return <p className="empty">За период данных нет.</p>;

  const { low, high } = timeBounds(days);
  const span = Math.max(1, high - low);
  const inner = BOX.w - BOX.left - BOX.right;
  const step = inner / Math.max(1, days.length);

  const x = (index: number) => BOX.left + step * index + step / 2;
  const y = (minutes: number) =>
    BOX.top + (BOX.h - BOX.top - BOX.bottom) * (1 - (minutes - low) / span);

  // Часовые линии: каждый час, если период короткий, иначе каждые два.
  const tick = span > 10 * 60 ? 120 : 60;
  const grid: number[] = [];
  for (let at = Math.ceil(low / tick) * tick; at <= high; at += tick) grid.push(at);

  const planStart = days.find((one) => one.plan.start !== null)?.plan.start ?? null;
  const planEnd = days.find((one) => one.plan.end !== null)?.plan.end ?? null;

  return (
    <>
      <div className="att-chart">
        <svg viewBox={`0 0 ${BOX.w} ${BOX.h}`} className="att-chart__svg"
             role="img" aria-label="Первый вход и последний выход по дням">
          {grid.map((at) => (
            <g key={at}>
              <line className="att-chart__grid" x1={BOX.left} x2={BOX.w - BOX.right}
                    y1={y(at)} y2={y(at)} />
              <text className="att-chart__axis" x={BOX.left - 8} y={y(at) + 4}
                    textAnchor="end">
                {hhmm(at)}
              </text>
            </g>
          ))}

          {planStart !== null && (
            <line className="att-chart__plan" x1={BOX.left} x2={BOX.w - BOX.right}
                  y1={y(planStart)} y2={y(planStart)} />
          )}
          {planEnd !== null && (
            <line className="att-chart__plan" x1={BOX.left} x2={BOX.w - BOX.right}
                  y1={y(planEnd)} y2={y(planEnd)} />
          )}

          {/* Выбранный день — светлая полоса на всю высоту: так он виден
              и на этом графике, и на соседнем, без второй подсветки. */}
          {days.map((point, index) =>
            point.day === picked ? (
              <rect key={`on-${point.day}`} className="att-chart__pick"
                    x={x(index) - step / 2} y={BOX.top}
                    width={step} height={BOX.h - BOX.top - BOX.bottom} />
            ) : null,
          )}

          {/* Пропуски линией не соединяются: выходной между двумя рабочими
              днями иначе выглядел бы плавным переходом. */}
          {segments(days, (one) => one.entry).map((run, index) => (
            <polyline key={`in-${index}`} className="att-chart__line att-chart__line--in"
                      points={run
                        .map((one) => `${x(days.indexOf(one))},${y(one.entry as number)}`)
                        .join(' ')} />
          ))}
          {segments(days, (one) => one.exit).map((run, index) => (
            <polyline key={`out-${index}`} className="att-chart__line att-chart__line--out"
                      points={run
                        .map((one) => `${x(days.indexOf(one))},${y(one.exit as number)}`)
                        .join(' ')} />
          ))}

          {days.map((point, index) => (
            <g key={point.day}>
              {point.entry !== null && (
                <circle
                  className={`att-chart__dot${(point.late ?? 0) > 0 ? ' att-chart__dot--bad' : ' att-chart__dot--in'}`}
                  cx={x(index)} cy={y(point.entry)} r={4}
                />
              )}
              {point.exit !== null && (
                <circle
                  className={`att-chart__dot${(point.early ?? 0) > 0 ? ' att-chart__dot--bad' : ' att-chart__dot--out'}`}
                  cx={x(index)} cy={y(point.exit)} r={4}
                />
              )}
              {/* Вся колонка — область наведения и нажатия: попасть в
                  точку радиусом четыре пикселя мышью трудно. */}
              <rect
                className="att-chart__hit"
                x={x(index) - step / 2}
                y={BOX.top}
                width={step}
                height={BOX.h - BOX.top - BOX.bottom}
                onClick={() => onPick(point.day)}
              >
                <title>{tip(point)}</title>
              </rect>
              {(index === 0 || (index + 1) % labelStep(days.length) === 0) && (
                <text className="att-chart__axis" x={x(index)} y={BOX.h - 14}
                      textAnchor="middle">
                  {point.label}
                </text>
              )}
            </g>
          ))}
        </svg>
      </div>

      <ul className="att-chart__legend">
        <li><i className="key key--in" />Первый вход</li>
        <li><i className="key key--out" />Последний выход</li>
        <li><i className="key key--plan" />Плановые границы графика</li>
        <li><i className="key key--bad" />Опоздание или ранний уход</li>
      </ul>
    </>
  );
}

const labelStep = (count: number): number => (count > 20 ? 2 : 1);

/** Подсказка дня. Ровно то, что спрашивают о дне, и ничего сверх. */
function tip(point: DayPoint): string {
  const parts = [`${point.day}`];
  if (!point.working) {
    parts.push(dayState(point.row));
    return parts.join(' · ');
  }
  parts.push(`Приход ${hhmm(point.entry)}`);
  parts.push(point.open ? 'Выход: сессия открыта' : `Уход ${hhmm(point.exit)}`);
  if ((point.late ?? 0) > 0) parts.push(`Опоздание ${point.late} мин`);
  if ((point.early ?? 0) > 0) parts.push(`Ранний уход ${point.early} мин`);
  if (point.plan.start !== null && point.plan.end !== null) {
    parts.push(`График ${hhmm(point.plan.start)}–${hhmm(point.plan.end)}`);
  }
  return parts.join(' · ');
}

// --- график «Часы по дням» ---------------------------------------------------

function Hours({
  days,
  picked,
  onPick,
}: {
  days: DayPoint[];
  picked: string | null;
  onPick: (day: string) => void;
}) {
  if (days.length === 0) return <p className="empty">За период данных нет.</p>;

  const norms = days.map((one) => one.plan.norm).filter((one): one is number => one !== null);
  const norm = norms.length ? Math.max(...norms) : 8 * 3600;
  const top = Math.max(norm * 1.25, ...days.map((one) => one.row.seconds));

  return (
    <>
      <div
        className="bars"
        style={{
          ['--bars-count' as string]: days.length,
          ['--bars-norm' as string]: Math.min(norm / top, 1),
        }}
      >
        {days.map((point) => (
          <button
            key={point.day}
            type="button"
            className={`bars__slot${point.day === picked ? ' bars__slot--on' : ''}`}
            onClick={() => onPick(point.day)}
            title={barTip(point)}
          >
            <span className="bars__well">
              <span
                className={`bars__bar bars__bar--${tone(point)}`}
                style={{ height: `${Math.max((point.row.seconds / top) * 100, point.row.seconds > 0 ? 2 : 0)}%` }}
              />
            </span>
            <span className="bars__label">{point.label}</span>
          </button>
        ))}
        {/* Линия нормы — поверх столбцов, одна на весь график.
            Высоту ей задаёт CSS по той же шкале, что и столбцам. */}
        <span className="bars__norm" />
      </div>

      <ul className="att-chart__legend">
        <li><i className="key key--ok" />Норма выполнена</li>
        <li><i className="key key--warn" />Меньше нормы</li>
        <li><i className="key key--over" />Переработка</li>
        <li><i className="key key--none" />Выходной или отсутствие</li>
        <li><i className="key key--plan" />Дневная норма</li>
      </ul>
    </>
  );
}

/** Цвет столбца. Будущий день и выходной — не «ноль часов». */
function tone(point: DayPoint): 'ok' | 'warn' | 'over' | 'none' {
  if (!point.working || point.future) return 'none';
  const norm = point.plan.norm;
  if (norm === null || norm === 0) return 'none';
  if (point.row.seconds > norm * 1.05) return 'over';
  return point.row.seconds >= norm ? 'ok' : 'warn';
}

function barTip(point: DayPoint): string {
  if (!point.working) return `${point.day} · ${dayState(point.row)}`;
  const norm = point.plan.norm;
  const parts = [point.day, duration(point.row.seconds)];
  if (norm !== null && norm > 0) {
    parts.push(`норма ${duration(norm)}`);
    const diff = point.row.seconds - norm;
    parts.push(`${diff >= 0 ? '+' : '−'}${duration(Math.abs(diff))}`);
  }
  return parts.join(' · ');
}

// --- «Когда обычно» ----------------------------------------------------------

function Usual({ days, sums }: { days: DayPoint[]; sums: ReturnType<typeof stats> }) {
  const workdays = days.filter((one) => one.working && !one.future);
  const entries = workdays.map((one) => one.entry).filter((one): one is number => one !== null);
  const exits = workdays.map((one) => one.exit).filter((one): one is number => one !== null);
  const inBuckets = histogram(entries);
  const outBuckets = histogram(exits);

  return (
    <section className="panel att__panel">
      <h3 className="att__title">Когда обычно</h3>

      <Distribution
        title="Чаще приходит"
        usual={usual(inBuckets)}
        rightTitle="Средний приход"
        rightValue={hhmm(sums.averageEntry)}
        buckets={inBuckets}
        tone="in"
      />
      <Distribution
        title="Чаще уходит"
        usual={usual(outBuckets)}
        rightTitle="Средний уход"
        rightValue={hhmm(sums.averageExit)}
        buckets={outBuckets}
        tone="out"
      />

      <div className="att__counts">
        <Count icon="check" title="Вовремя" value={sums.onTime} unit="дней" />
        <Count icon="alert" title="Опоздал" value={sums.lateDays} unit="дней" tone="warn" />
        <Count icon="clock" title="Ранний уход" value={sums.earlyDays} unit="дней" />
      </div>
    </section>
  );
}

function Distribution({
  title,
  usual: peak,
  rightTitle,
  rightValue,
  buckets,
  tone: colour,
}: {
  title: string;
  usual: string;
  rightTitle: string;
  rightValue: string;
  buckets: { from: number; count: number }[];
  tone: 'in' | 'out';
}) {
  const top = Math.max(1, ...buckets.map((one) => one.count));
  const best = buckets.reduce((was, one) => (one.count > was ? one.count : was), 0);

  return (
    <div className="dist">
      <div className="dist__head">
        <span className="dist__text">
          <span className="dist__title">{title}</span>
          <strong className="dist__peak">{peak}</strong>
        </span>
        <span className="dist__text dist__text--end">
          <span className="dist__title">{rightTitle}</span>
          <strong className="dist__peak">{rightValue}</strong>
        </span>
      </div>

      {buckets.length === 0 ? (
        <p className="muted">Данных за период нет.</p>
      ) : (
        <div className="dist__bars">
          {buckets.map((one) => (
            <span
              key={one.from}
              className={`dist__bar dist__bar--${colour}${one.count === best && best > 0 ? ' dist__bar--top' : ''}`}
              style={{ height: `${Math.max((one.count / top) * 100, one.count ? 6 : 0)}%` }}
              title={`${hhmm(one.from)} – ${hhmm(one.from + 30)} · ${one.count}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function Count({
  icon,
  title,
  value,
  unit,
  tone: colour,
}: {
  icon: 'check' | 'alert' | 'clock';
  title: string;
  value: number;
  unit: string;
  tone?: 'warn';
}) {
  return (
    <div className="att__count">
      <AppIcon name={icon} size={18} />
      <span className="att__count-title">{title}</span>
      <strong className={colour ? `att__count-value att__count-value--${colour}` : 'att__count-value'}>
        {value} {unit}
      </strong>
    </div>
  );
}

// --- подробности дня ---------------------------------------------------------

function DayPanel({
  id,
  point,
  zone,
  canCorrect,
}: {
  id: string;
  point: DayPoint | null;
  zone: string;
  canCorrect: boolean;
}) {
  const day = point?.day ?? '';
  const [detail] = useBlock(
    (signal) =>
      Promise.all([
        api.events(
          { employee_id: id, date_from: day, date_to: day, limit: '50' },
          signal,
        ),
        api.attendanceSessions(
          { employee_id: id, date_from: day, date_to: day, limit: '20' },
          signal,
        ),
      ]).then(([events, sessions]) => ({
        events: events.items,
        sessions: sessions.items,
      })),
    `day|${id}|${day}`,
    Boolean(day),
  );

  if (point === null) {
    return (
      <section className="panel att__panel">
        <h3 className="att__title">Подробности дня</h3>
        <p className="empty">Выберите день на графике или в журнале.</p>
      </section>
    );
  }

  const zoned = point.row.timezone || zone;

  return (
    <section className="panel att__panel">
      <h3 className="att__title">{point.day}</h3>

      <p className="att__day-sum">
        <strong>{duration(point.row.seconds)}</strong>
        {(point.late ?? 0) > 0 && (
          <span className="att__bad">Опоздание {point.late} мин</span>
        )}
        {(point.early ?? 0) > 0 && (
          <span className="att__bad">Ранний уход {point.early} мин</span>
        )}
        {!point.working && <span className="muted">{dayState(point.row)}</span>}
      </p>

      {detail.state === 'loading' && (
        <p className="empty" role="status">Читаем отметки…</p>
      )}
      {detail.state === 'error' && (
        <p className="empty empty--bad">Не удалось получить отметки дня.</p>
      )}

      {detail.state === 'ready' && (
        <>
          {detail.data.events.length === 0 ? (
            <p className="muted">Отметок за день нет.</p>
          ) : (
            <ul className="marks">
              {detail.data.events.map((mark) => (
                <li key={mark.id} className="marks__line">
                  <i
                    className={`key ${mark.event_type === 'ENTRY' ? 'key--ok' : 'key--none'}`}
                  />
                  <span className="marks__time">
                    {clockOnDay(mark.occurred_at, zoned, point.day)}
                  </span>
                  <span className="marks__kind">
                    {mark.event_type === 'ENTRY' ? 'Вход' : 'Выход'}
                  </span>
                  <span className="marks__where">{orDash(mark.office_name)}</span>
                  <span className="marks__check">{verification(mark)}</span>
                </li>
              ))}
            </ul>
          )}

          {detail.data.sessions.length > 0 && (
            <ul className="marks marks--thin">
              {detail.data.sessions.map((session) => (
                <li key={session.id} className="marks__line">
                  <span className="marks__time">
                    {clockOnDay(session.started_at, zoned, point.day)} —{' '}
                    {session.ended_at
                      ? clockOnDay(session.ended_at, zoned, point.day)
                      : 'открыта'}
                  </span>
                  <span className="marks__what">
                    {duration(session.duration_seconds)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {point.row.conflicting_marks && (
        <p className="note note--dim">
          В этот день есть и подтверждённое отсутствие, и отметки. Расхождение
          разбирает человек — само оно не исчезнет.
        </p>
      )}

      <Link
        className="btn att__day-go"
        to={`/attendance?date=${point.day}&employee_id=${id}`}
      >
        <AppIcon name="next" size={16} />
        Открыть полный журнал дня
      </Link>

      {canCorrect && (
        <p className="field__hint">
          Исправление отметки — это решение по заявке, а не правка события:
          сырое событие не меняется никогда.
        </p>
      )}
    </section>
  );
}

/** Чем подтверждена отметка. Пусто — проверок не было, и это не «ошибка». */
function verification(mark: api.EventRow): string {
  const parts: string[] = [];
  if (mark.inside_geofence === true) parts.push('геозона');
  if (mark.inside_geofence === false) parts.push('вне геозоны');
  if (mark.inside_office_network === true) parts.push('сеть офиса');
  if (mark.inside_office_network === false) parts.push('чужая сеть');
  if (mark.source === 'MANUAL') parts.push('вручную');
  return parts.join(' · ');
}

// --- журнал по дням ----------------------------------------------------------

function Journal({
  days,
  picked,
  onPick,
  loading,
}: {
  days: DayPoint[];
  picked: string | null;
  onPick: (day: string) => void;
  loading: boolean;
}) {
  if (loading) return <p className="empty" role="status">Считаем журнал…</p>;
  if (days.length === 0) return <p className="empty">За период строк нет.</p>;

  return (
    <div className="scroller">
      <table className="grid-table" aria-label="Журнал по дням">
        <thead>
          <tr>
            <th scope="col">Дата</th>
            <th scope="col">Первый вход</th>
            <th scope="col">Последний выход</th>
            <th scope="col">В офисе</th>
            <th scope="col">Отклонение</th>
            <th scope="col">Статус</th>
          </tr>
        </thead>
        <tbody>
          {days.map((point) => (
            <tr
              key={point.day}
              tabIndex={0}
              className={point.day === picked ? 'row--on' : undefined}
              onClick={() => onPick(point.day)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') onPick(point.day);
              }}
            >
              <td>{point.day}</td>
              <td>{hhmm(point.entry)}</td>
              <td>{point.open ? 'открыта' : hhmm(point.exit)}</td>
              <td>{point.working ? duration(point.row.seconds) : '—'}</td>
              <td className={(point.late ?? 0) > 0 ? 'att__bad' : undefined}>
                {(point.late ?? 0) > 0
                  ? lateness(point.late)
                  : (point.early ?? 0) > 0
                    ? `Ранний уход ${point.early} мин`
                    : '—'}
              </td>
              <td>{dayState(point.row)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export type { Plan };
export { minutesInZone };
