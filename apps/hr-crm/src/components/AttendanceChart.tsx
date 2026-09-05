/**
 * Явка по дням: доля отметившихся хотя бы раз.
 *
 * Рисуется по настоящему ряду `/analytics`. Два правила, без которых
 * график врёт:
 *
 * 1. День без знаменателя (никто не должен был работать) — это ПРОПУСК,
 *    а не ноль процентов. Линия в таком месте разрывается.
 * 2. Предыдущий период показывается только когда он сопоставим — столько
 *    же дней и с данными. Иначе пунктира просто нет.
 *
 * Пропорции держит `viewBox` вместе с фиксированным отношением сторон у
 * контейнера: `preserveAspectRatio` не отключён, поэтому точки остаются
 * круглыми на любой ширине, а высота не растёт вслед за окном.
 */

import { useState } from 'react';

import type { DayPoint } from '../api/crm';
import { formatPercent, longDate, percent, shortDate } from '../features/dashboard/data';

const W = 1000;
const H = 320;
const PAD = { top: 18, right: 16, bottom: 34, left: 44 };

export type Point = { day: string; attended: number; expected: number; value: number | null };

export function toPoints(series: DayPoint[]): Point[] {
  return series.map((row) => ({
    day: row.day,
    attended: row.attended,
    expected: row.expected,
    value: percent(row.attended, row.expected),
  }));
}

type Props = {
  points: Point[];
  previous: Point[];
  /** Сколько дней показывать: подписи оси прореживаются по их числу. */
  label: string;
};

export function AttendanceChart({ points, previous, label }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const measured = points.filter((p) => p.value !== null);

  if (measured.length === 0) {
    return (
      <p className="empty">
        За выбранный период нечего показать: ни одного дня, в который кто-то
        должен был выйти на работу.
      </p>
    );
  }

  const x = (index: number) =>
    PAD.left +
    (index * (W - PAD.left - PAD.right)) / Math.max(points.length - 1, 1);
  const y = (value: number) =>
    PAD.top + ((100 - value) * (H - PAD.top - PAD.bottom)) / 100;

  const step = Math.max(1, Math.ceil(points.length / 12));
  const last = measured[measured.length - 1];

  return (
    <div className="chart-box">
      <div className="chart-box__plot">
        <svg viewBox={`0 0 ${W} ${H}`} className="plot" role="img" aria-label={label}>
          {[0, 50, 100].map((value) => (
            <g key={value}>
              <line className="plot__grid" x1={PAD.left} y1={y(value)} x2={W - PAD.right}
                    y2={y(value)} />
              <text className="plot__tick" x={PAD.left - 10} y={y(value) + 4}
                    textAnchor="end">{value}%</text>
            </g>
          ))}

          {previous.length === points.length && (
            <path className="plot__line plot__line--prev" d={line(previous, x, y)} />
          )}
          <path className="plot__line" d={line(points, x, y)} />

          {points.map((point, index) =>
            point.value === null ? null : (
              <circle
                key={point.day}
                className={hover === index ? 'plot__dot plot__dot--on' : 'plot__dot'}
                cx={x(index)}
                cy={y(point.value)}
                r={hover === index ? 6 : 4}
              />
            ),
          )}

          {points.map((point, index) => (
            <g key={`t-${point.day}`}>
              {index % step === 0 && (
                <text className="plot__tick" x={x(index)} y={H - 10} textAnchor="middle">
                  {shortDate(point.day)}
                </text>
              )}
              <rect
                x={x(index) - 14}
                y={PAD.top}
                width={28}
                height={H - PAD.top - PAD.bottom}
                fill="transparent"
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover(null)}
              />
            </g>
          ))}
        </svg>

        {hover !== null && points[hover] && (
          <Tip point={points[hover] as Point} at={x(hover) / W} />
        )}
      </div>

      <div className="chart-box__total">
        <p className="chart-box__total-label">Последний день</p>
        <p className="chart-box__total-main">
          {last?.attended} / {last?.expected}
        </p>
        <p className="chart-box__total-percent">{formatPercent(last?.value ?? null)}</p>
      </div>
    </div>
  );
}

/** Разрыв на днях без знаменателя: пропуск нельзя рисовать нулём. */
function line(points: Point[], x: (i: number) => number, y: (v: number) => number): string {
  let path = '';
  let open = false;
  points.forEach((point, index) => {
    if (point.value === null) {
      open = false;
      return;
    }
    path += `${open ? 'L' : 'M'}${x(index).toFixed(1)} ${y(point.value).toFixed(1)} `;
    open = true;
  });
  return path.trim();
}

function Tip({ point, at }: { point: Point; at: number }) {
  return (
    <div
      className="tip"
      style={{ left: `${at * 100}%`, transform: `translate(-${at * 100}%, -100%)` }}
    >
      <span className="tip__day">{longDate(point.day)}</span>
      <span className="tip__value">
        {point.attended} / {point.expected} ({formatPercent(point.value)})
      </span>
    </div>
  );
}
