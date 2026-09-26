/**
 * Графики аналитики: линия по дням, полосы, воронка и календарь.
 *
 * Свои, на SVG, без библиотеки: нужны четыре простых вида, и у каждого
 * — одна и та же сетка, подписи и цвет листа. Ширина берётся из места на
 * странице, высота задаётся снаружи: лист не должен менять размер,
 * пока грузятся данные.
 *
 * Пустое значение — это пропуск на линии, а не ноль: «ни одного
 * рабочего дня» и «никто не пришёл» — разные утверждения.
 */

import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';

const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const MONTHS_LONG = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

export function dayShort(iso: string): string {
  const [, m, d] = iso.split('-').map(Number);
  return `${d} ${MONTHS_SHORT[(m ?? 1) - 1]}`;
}

export function dayLong(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  return `${d} ${MONTHS_LONG[(m ?? 1) - 1]} ${y}`;
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const box = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const node = box.current;
    if (!node) return;
    const measure = () => setWidth(node.clientWidth);
    measure();
    const watcher = new ResizeObserver(measure);
    watcher.observe(node);
    return () => watcher.disconnect();
  }, []);
  return [box, width];
}

export type LinePoint = { day: string; value: number | null; previous?: number | null };

/** Линия по дням с заливкой, пунктир прошлого периода и подсказка по наведению. */
export function LineChart({ points, height, max, format, label, compare }: {
  points: LinePoint[];
  height: number;
  /** Верх шкалы. Без него — по данным, с запасом. */
  max?: number;
  format: (value: number) => string;
  label: string;
  compare?: boolean;
}) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const left = 40;
  const right = 14;
  const top = 12;
  const bottom = 24;
  const values = points.flatMap((p) => [p.value, compare ? p.previous ?? null : null]).filter((v): v is number => v !== null);
  const top_ = max ?? niceMax(Math.max(4, ...values));
  const plotW = Math.max(10, width - left - right);
  const plotH = Math.max(10, height - top - bottom);
  const x = (i: number) => left + (points.length <= 1 ? plotW / 2 : (i * plotW) / (points.length - 1));
  const y = (v: number) => top + plotH - (Math.min(v, top_) / top_) * plotH;
  // Подписи шкалы — без повторов: у маленьких чисел шаг 0,4 округлился бы в одно и то же.
  const ticks = [0, 0.2, 0.4, 0.6, 0.8, 1].map((k) => k * top_)
    .filter((tick, i, all) => i === 0 || format(Math.round(tick)) !== format(Math.round(all[i - 1]!)));
  const every = Math.max(1, Math.ceil(points.length / 8));
  const last = [...points].reverse().findIndex((p) => p.value !== null);
  const lastIndex = last < 0 ? null : points.length - 1 - last;
  const shown = hover ?? lastIndex;

  // День без значения (выходной, никого не ждали) — без точки, но
  // линия идёт через него к следующему дню: это пропуск, а не ноль.
  const path = (pick: (p: LinePoint) => number | null | undefined) => points
    .map((p, i) => [i, pick(p)] as const)
    .filter((pair): pair is readonly [number, number] => pair[1] !== null && pair[1] !== undefined)
    .map(([i, v], n) => `${n ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
    .join('');
  const line = path((p) => p.value);
  const known = points.map((p, i) => (p.value === null ? -1 : i)).filter((i) => i >= 0);
  const area = known.length > 1
    ? `${line}L${x(known[known.length - 1]!)},${y(0)}L${x(known[0]!)},${y(0)}Z`
    : '';

  const pick = (event: React.MouseEvent<SVGRectElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;
    const i = Math.round(ratio * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };

  const tip = shown !== null ? points[shown] : null;
  return (
    <div className="ax-chart" ref={box} style={{ height }} role="img" aria-label={label}>
      {width > 0 && (
        <svg width={width} height={height}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={width - right} y1={y(t)} y2={y(t)} className="ax-chart__grid" />
              <text x={left - 8} y={y(t) + 4} textAnchor="end" className="ax-chart__axis">{format(Math.round(t))}</text>
            </g>
          ))}
          {points.map((p, i) => (i % every === 0 ? (
            <text key={p.day} x={x(i)} y={height - 6} textAnchor="middle" className="ax-chart__axis">{dayShort(p.day)}</text>
          ) : null))}
          <path d={area} className="ax-chart__area" />
          {compare && <path d={path((p) => p.previous)} className="ax-chart__prev" />}
          <path d={line} className="ax-chart__line" />
          {points.map((p, i) => (p.value !== null ? (
            <circle key={p.day} cx={x(i)} cy={y(p.value)} r={points.length > 45 ? 0 : 2.6} className="ax-chart__dot" />
          ) : null))}
          {tip && tip.value !== null && shown !== null && (
            <>
              <line x1={x(shown)} x2={x(shown)} y1={top} y2={top + plotH} className="ax-chart__cursor" />
              <circle cx={x(shown)} cy={y(tip.value)} r={5} className="ax-chart__mark" />
            </>
          )}
          <rect x={left} y={top} width={plotW} height={plotH} fill="transparent"
                onMouseMove={pick} onMouseLeave={() => setHover(null)} />
        </svg>
      )}
      {tip && tip.value !== null && shown !== null && width > 0 && (
        <div className="ax-chart__tip" style={{ left: Math.min(Math.max(x(shown), 70), width - 70), top: 0 }}>
          <b>{format(tip.value)}</b>
          {compare && tip.previous !== null && tip.previous !== undefined && <span>было {format(tip.previous)}</span>}
          <span>{dayLong(tip.day)}</span>
        </div>
      )}
    </div>
  );
}

function niceMax(value: number): number {
  const step = 10 ** Math.floor(Math.log10(value));
  for (const k of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    if (k * step >= value * 1.1) return k * step;
  }
  return 10 * step;
}

export type BarRow = { key: string; name: string; value: number | null; text: string; note?: ReactNode; tone?: 'blue' | 'green' | 'amber' | 'red' };

/** Полосы: название, полоса, значение. `value` — доля от 0 до 100. */
export function Bars({ rows, label, empty }: { rows: BarRow[]; label: string; empty?: string }) {
  if (!rows.length) return <p className="ax-empty">{empty ?? 'Нет данных за период'}</p>;
  return (
    <ul className="ax-bars" aria-label={label}>
      {rows.map((row) => (
        <li key={row.key} className="ax-bars__row">
          <span className="ax-bars__name" title={row.name}>{row.name}</span>
          <span className="ax-bars__track">
            <span className={`ax-bars__fill ax-bars__fill--${row.tone ?? 'blue'}`}
                  style={{ width: `${Math.max(0, Math.min(100, row.value ?? 0))}%` }} />
          </span>
          <span className="ax-bars__value">{row.text}</span>
          {row.note !== undefined && <span className="ax-bars__note">{row.note}</span>}
        </li>
      ))}
    </ul>
  );
}

const WEEK = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

export type CalendarKind = 'vacation' | 'sick' | 'trip' | 'other';

const KIND_TITLE: Record<CalendarKind, string> = { vacation: 'больше всего в отпуске', sick: 'больше всего на больничном', trip: 'больше всего в командировке', other: 'другие отсутствия' };

/**
 * Календарь месяца: число в дне — сколько человек отсутствовали, цвет —
 * какого вида отсутствий в этот день больше: отпуск сиреневый,
 * больничный синий, командировка серо-голубая.
 */
export function MonthCalendar({ month, cells: marks, inside, label }: {
  /** Первое число месяца, ГГГГ-ММ-01. */
  month: string;
  cells: Record<string, { total: number; kind: CalendarKind }>;
  /** День входит в выбранный период. */
  inside: (day: string) => boolean;
  label: string;
}) {
  const [y, m] = month.split('-').map(Number) as [number, number];
  const first = new Date(Date.UTC(y, m - 1, 1));
  const shift = (first.getUTCDay() + 6) % 7;
  const cells: { day: string; own: boolean }[] = [];
  for (let i = 0; i < 42; i += 1) {
    const at = new Date(Date.UTC(y, m - 1, 1 - shift + i));
    cells.push({ day: at.toISOString().slice(0, 10), own: at.getUTCMonth() === m - 1 });
  }
  const rows = cells[35]!.own ? 6 : 5;
  return (
    <div className="ax-cal" role="grid" aria-label={label}>
      <div className="ax-cal__row ax-cal__row--head" role="row">
        {WEEK.map((one) => <span key={one} role="columnheader">{one}</span>)}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="ax-cal__row" role="row">
          {cells.slice(r * 7, r * 7 + 7).map(({ day, own }) => {
            const mark = marks[day];
            const n = mark?.total ?? 0;
            const inPeriod = own && inside(day);
            return (
              <span key={day} role="gridcell"
                    className={`ax-cal__day${own ? '' : ' ax-cal__day--out'}${inPeriod ? '' : ' ax-cal__day--off'}${n && inPeriod ? ` ax-cal__day--${mark!.kind}` : ''}`}
                    title={n ? `${dayLong(day)}: отсутствовали ${n}, ${KIND_TITLE[mark!.kind]}` : dayLong(day)}>
                <span className="ax-cal__num">{Number(day.slice(8))}</span>
                {n > 0 && inPeriod && <span className="ax-cal__count">{n}</span>}
              </span>
            );
          })}
        </div>
      ))}
    </div>
  );
}
