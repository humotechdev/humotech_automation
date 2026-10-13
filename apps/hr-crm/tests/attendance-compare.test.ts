/**
 * Сравнение на странице посещаемости: что берётся из адреса и как из
 * дневных чисел сервера получаются точки линий.
 *
 * Проверяется то, что легко испортить незаметно: период, уехавший в
 * будущее или длиннее, чем примет сервер; девятая линия на графике;
 * выходной, нарисованный нулём; итог линии, разошедшийся с итогом
 * периода.
 */

import { describe, expect, test } from 'vitest';

import {
  COMPARE_LIMIT, NONE, chartFloor, compareSpan, daysOf, idsOf, kindOf, seriesOf,
} from '../src/features/attendance/compare';

const NOW = '2026-10-13';
const q = (text: string) => new URLSearchParams(text);

describe('период сравнения', () => {
  test('без дат — последние 30 дней по сегодня', () => {
    expect(compareSpan(q(''), NOW)).toEqual({ from: '2026-09-14', to: NOW });
  });

  test('даты из ссылки сохраняются', () => {
    expect(compareSpan(q('from=2026-09-01&to=2026-09-04'), NOW)).toEqual({ from: '2026-09-01', to: '2026-09-04' });
  });

  test('старая ссылка на месяц — тот же месяц, но не дальше сегодня', () => {
    expect(compareSpan(q('period=month&date=2026-09-18'), NOW)).toEqual({ from: '2026-09-01', to: '2026-09-30' });
    expect(compareSpan(q('period=month&date=2026-10-05'), NOW)).toEqual({ from: '2026-10-01', to: NOW });
  });

  test('будущее отрезается, начало не позже конца, длина — не больше года', () => {
    expect(compareSpan(q('from=2026-10-01&to=2026-12-31'), NOW)).toEqual({ from: '2026-10-01', to: NOW });
    expect(compareSpan(q('from=2026-10-10&to=2026-10-02'), NOW)).toEqual({ from: '2026-10-02', to: '2026-10-02' });
    const long = compareSpan(q('from=2020-01-01&to=2026-10-13'), NOW);
    expect(daysOf(long.from, long.to)).toHaveLength(366);
  });

  test('мусор в датах — как будто дат нет', () => {
    expect(compareSpan(q('from=вчера&to=13.10.2026'), NOW)).toEqual({ from: '2026-09-14', to: NOW });
  });
});

describe('выбор из адреса', () => {
  test('без повторов, без пустых и не больше восьми', () => {
    expect(idsOf('a,,b,a, c')).toEqual(['a', 'b', 'c']);
    expect(idsOf(Array.from({ length: 12 }, (_, i) => `x${i}`).join(','))).toHaveLength(COMPARE_LIMIT);
  });

  test('«снято всё» — пустой выбор, а не объект с именем «~»', () => {
    expect(idsOf(NONE)).toEqual([]);
  });

  test('неизвестный вид сравнения не угадывается', () => {
    expect(kindOf('office')).toBe('office');
    expect(kindOf('period')).toBeNull();
  });
});

describe('точки линий', () => {
  const days = ['2026-09-04', '2026-09-05', '2026-09-06', '2026-09-07'];
  const rows = [
    { day: '2026-09-04', future: false, expected: 1, attended: 1, percent: 100 },
    // Выходной: никого не ждали — точки нет, а не ноль.
    { day: '2026-09-05', future: false, expected: 0, attended: 0, percent: null },
    { day: '2026-09-06', future: false, expected: 1, attended: 0, percent: 0 },
    { day: '2026-09-07', future: true, expected: 1, attended: 0, percent: null },
  ];

  test('по дням — проценты сервера, без точек в выходной и в будущем', () => {
    expect(seriesOf(days, rows, 'day')).toEqual([100, null, 0, null]);
  });

  test('нарастающим итогом — сумма пришедших на сумму ожидаемых', () => {
    expect(seriesOf(days, rows, 'total')).toEqual([100, null, 50, null]);
  });

  test('последняя точка итога совпадает с явкой периода', () => {
    const many = [
      { day: 'a', future: false, expected: 30, attended: 27, percent: 90 },
      { day: 'b', future: false, expected: 28, attended: 21, percent: 75 },
      { day: 'c', future: false, expected: 30, attended: 30, percent: 100 },
    ];
    const line = seriesOf(['a', 'b', 'c'], many, 'total');
    expect(line[2]).toBe(Math.round((78 / 88) * 1000) / 10);
  });

  test('шкала — от чуть ниже минимума, с шагом 10, и не ниже нуля', () => {
    expect(chartFloor([92, 81, null])).toBe(70);
    expect(chartFloor([3])).toBe(0);
    expect(chartFloor([null])).toBe(0);
  });
});
