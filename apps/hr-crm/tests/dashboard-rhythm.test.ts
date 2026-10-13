/**
 * «Ритм дня»: какие дни попадают на ось и как через них идёт линия.
 *
 * — день пропускается по данным (никого не ждали по графику), а не по
 *   дню недели: рабочая суббота остаётся;
 * — кривая гладкая, но между соседними точками не выходит за их значения:
 *   не ниже 0 % и не выше 100 %;
 * — путь строится без NaN при одной, двух и равных точках.
 */

import { describe, expect, test } from 'vitest';

import { smoothPath, workdays } from '../src/features/dashboard/rhythm';

const day = (iso: string, attended: number, expected: number) =>
  ({ day: iso, worked_seconds: 0, attended, expected, late: 0 });

/** Все числа пути: пары x,y после команд M, L и C. */
function numbers(d: string): number[] {
  return (d.match(/-?\d+(\.\d+)?/g) ?? []).map(Number);
}

describe('дни на оси', () => {
  test('день, в который никого не ждали, выброшен; рабочая суббота осталась', () => {
    const points = workdays([
      day('2026-09-25', 4, 4), // пятница
      day('2026-09-26', 1, 2), // суббота, рабочая
      day('2026-09-27', 0, 0), // воскресенье
      day('2026-09-28', 3, 4), // понедельник
    ]);
    expect(points.map((p) => p.day)).toEqual(['2026-09-25', '2026-09-26', '2026-09-28']);
    expect(points.map((p) => p.value)).toEqual([100, 50, 75]);
  });

  test('рабочий день, в который никто не пришёл, — ноль, а не пропуск', () => {
    const points = workdays([day('2026-09-26', 0, 0), day('2026-09-28', 0, 3)]);
    expect(points).toEqual([{ day: '2026-09-28', attended: 0, expected: 3, value: 0 }]);
  });

  test('никого не ждали весь период — пусто', () => {
    expect(workdays([day('2026-09-26', 0, 0), day('2026-09-27', 0, 0)])).toEqual([]);
  });
});

describe('гладкая линия', () => {
  const top = 10;
  const bottom = 210; // y(0 %) = 210, y(100 %) = 10
  const at = (values: number[]) => values.map((v, i) => ({ x: 40 + i * 50, y: bottom - (v / 100) * (bottom - top) }));

  test('резкий перепад 100 → 0 → 100 не уводит кривую за 0 % и 100 %', () => {
    const d = smoothPath(at([100, 0, 100, 100, 0, 0, 100]));
    expect(d).not.toMatch(/NaN|Infinity/);
    const ys = numbers(d).filter((_, k) => k % 2 === 1);
    for (const y of ys) {
      expect(y).toBeGreaterThanOrEqual(top);
      expect(y).toBeLessThanOrEqual(bottom);
    }
  });

  test('каждый отрезок не выходит за значения своих концов', () => {
    const pts = at([20, 90, 85, 30, 30, 100, 0, 60]);
    const d = smoothPath(pts);
    const segments = d.split(' C').slice(1);
    expect(segments).toHaveLength(pts.length - 1);
    segments.forEach((one, i) => {
      const [, c1y, , c2y] = numbers(one);
      const lo = Math.min(pts[i]!.y, pts[i + 1]!.y) - 0.01;
      const hi = Math.max(pts[i]!.y, pts[i + 1]!.y) + 0.01;
      // Опорные точки кубической кривой внутри полосы концов — значит,
      // и сама кривая внутри неё.
      for (const y of [c1y!, c2y!]) {
        expect(y).toBeGreaterThanOrEqual(lo);
        expect(y).toBeLessThanOrEqual(hi);
      }
    });
  });

  test('крайние случаи без NaN: одна точка, две, все равные', () => {
    expect(smoothPath([])).toBe('');
    expect(smoothPath(at([50]))).toBe('M40,110');
    expect(smoothPath(at([50, 80]))).toBe('M40,110 L90,50');
    const flat = smoothPath(at([60, 60, 60, 60]));
    expect(flat).not.toMatch(/NaN|Infinity/);
    expect(new Set(numbers(flat).filter((_, k) => k % 2 === 1))).toEqual(new Set([90]));
  });

  test('путь проходит через каждую точку', () => {
    const pts = at([10, 70, 40, 95]);
    const d = smoothPath(pts);
    const ends = [d.match(/^M([\d.]+),([\d.]+)/)!.slice(1), ...[...d.matchAll(/ ([\d.]+),([\d.]+)(?= C|$)/g)].map((m) => m.slice(1))];
    expect(ends.map(([x, y]) => ({ x: Number(x), y: Number(y) }))).toEqual(pts);
  });
});
