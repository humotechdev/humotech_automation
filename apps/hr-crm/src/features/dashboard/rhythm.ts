/**
 * «Ритм дня» на главной: какие дни попадают на ось и как через них
 * провести линию.
 *
 * Ось — рабочие дни, а не календарь. День, в который по графику никого
 * не ждали (`expected === 0` в ряду `/analytics`: выходной по графику,
 * праздник, все в отпуске, ещё не наступивший день), с оси убирается —
 * иначе выходные рисовали бы провалы. По дню недели не решается: суббота
 * бывает рабочей, и если в неё кого-то ждали, она остаётся.
 */

import type { DayPoint } from '../../api/crm';

export type RhythmPoint = { day: string; attended: number; expected: number; value: number };

/** Дни, в которые кого-то ждали по графику, с долей пришедших. */
export function workdays(series: DayPoint[]): RhythmPoint[] {
  return series
    .filter((one) => one.expected > 0)
    .map((one) => ({
      day: one.day,
      attended: one.attended,
      expected: one.expected,
      value: Math.round((one.attended / one.expected) * 100),
    }));
}

type XY = { x: number; y: number };

/**
 * Гладкая линия через точки — монотонная кубическая (Фриц — Карлсон, как
 * `curveMonotoneX` в d3). Между двумя соседними точками кривая не выходит
 * за их значения: не ныряет ниже 0 % и не взлетает выше 100 %, как это
 * делает catmull-rom на резком перепаде. Абсциссы должны возрастать.
 */
export function smoothPath(points: XY[]): string {
  const n = points.length;
  if (n === 0) return '';
  const at = (p: XY) => `${round(p.x)},${round(p.y)}`;
  if (n === 1) return `M${at(points[0]!)}`;
  if (n === 2) return `M${at(points[0]!)} L${at(points[1]!)}`;

  const h = (i: number) => points[i + 1]!.x - points[i]!.x;
  const s = (i: number) => (h(i) ? (points[i + 1]!.y - points[i]!.y) / h(i) : 0);

  // Касательные во внутренних точках: ноль на вершине и во впадине,
  // иначе — не круче, чем позволяют соседние наклоны.
  const m: number[] = new Array(n).fill(0);
  for (let i = 1; i < n - 1; i += 1) {
    const s0 = s(i - 1);
    const s1 = s(i);
    const p = (s0 * h(i) + s1 * h(i - 1)) / (h(i - 1) + h(i));
    m[i] = (Math.sign(s0) + Math.sign(s1)) * Math.min(Math.abs(s0), Math.abs(s1), 0.5 * Math.abs(p)) || 0;
  }
  // На краях — по наклону первого и последнего отрезка.
  m[0] = (3 * s(0) - m[1]!) / 2;
  m[n - 1] = (3 * s(n - 2) - m[n - 2]!) / 2;

  let d = `M${at(points[0]!)}`;
  for (let i = 0; i < n - 1; i += 1) {
    const a = points[i]!;
    const b = points[i + 1]!;
    const third = h(i) / 3;
    d += ` C${at({ x: a.x + third, y: a.y + m[i]! * third })} ${at({ x: b.x - third, y: b.y - m[i + 1]! * third })} ${at(b)}`;
  }
  return d;
}

function round(v: number): number {
  return Math.round(v * 100) / 100;
}
