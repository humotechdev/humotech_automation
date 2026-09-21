/**
 * Подсказка графика «Явка» и итог справа.
 *
 * Проверяется не оформление карточки, а то, чтобы она не сочиняла.
 * Сравнение с прошлым периодом — самое опасное место графика: когда
 * сравнивать не с чем, «0,0%» читается как «ничего не изменилось», хотя
 * на деле это «неизвестно». Поэтому и строка в подсказке, и плашка с
 * разницей обязаны исчезать вместе с пунктирной линией, а не показывать
 * ноль.
 */

import { fireEvent, render } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { AttendanceChart, toPoints } from '../src/components/AttendanceChart';

/** Ряд по дням, начиная с 1 августа 2026. */
function series(days: number, attended: (i: number) => number, expected = 214) {
  return Array.from({ length: days }, (_, i) => ({
    day: `2026-08-${String(i + 1).padStart(2, '0')}`,
    attended: attended(i),
    expected,
    worked_seconds: 0,
    late: 0,
  }));
}

function draw(now: number[], before: number[] | null, expected = 214) {
  const points = toPoints(series(now.length, (i) => now[i] as number, expected));
  const previous = before
    ? toPoints(series(before.length, (i) => before[i] as number))
    : [];
  render(<AttendanceChart points={points} previous={previous} label="Явка" />);
  return points;
}

/** Навести курсор на день с указанным порядковым номером. */
function hover(index: number) {
  const zones = document.querySelectorAll('.plot rect:not(.plot__field)');
  fireEvent.mouseEnter(zones[index] as Element);
}

const tip = () => document.querySelector('.tip');

describe('подсказка под курсором', () => {
  test('показывает день, явку, долю и прошлый период', () => {
    draw([198, 200, 205], [190, 191, 192]);
    hover(0);

    const card = tip();
    expect(card).not.toBeNull();
    expect(card?.textContent).toContain('1 августа 2026');
    expect(card?.textContent).toContain('198 / 214');
    // 198 из 214 — 92,5%, а прошлый период в тот же день 190 из 214.
    expect(card?.textContent).toContain('92,5%');
    expect(card?.textContent).toContain('Предыдущий период');
    expect(card?.textContent).toContain('88,8%');
  });

  test('без сопоставимого прошлого периода строки сравнения нет', () => {
    // Ряды разной длины сравнивать нельзя: это разные отрезки календаря.
    draw([198, 200, 205], [190, 191]);
    hover(1);
    expect(tip()?.textContent).not.toContain('Предыдущий период');
  });

  test('выходной назван словами, а не нулём процентов', () => {
    const points = toPoints([
      { day: '2026-08-01', attended: 0, expected: 0, worked_seconds: 0, late: 0 },
      { day: '2026-08-02', attended: 200, expected: 214, worked_seconds: 0, late: 0 },
    ]);
    render(<AttendanceChart points={points} previous={[]} label="Явка" />);
    hover(0);
    expect(tip()?.textContent).toContain('Выходной');
    expect(tip()?.textContent).not.toContain('0,0%');
  });

  test('курсор уходит — подсказка исчезает', () => {
    draw([198, 200], [190, 191]);
    hover(0);
    expect(tip()).not.toBeNull();
    const zones = document.querySelectorAll('.plot rect:not(.plot__field)');
    fireEvent.mouseLeave(zones[0] as Element);
    expect(tip()).toBeNull();
  });

  test('направляющая и кольцо появляются только под курсором', () => {
    draw([198, 200], [190, 191]);
    expect(document.querySelector('.plot__guide')).toBeNull();
    expect(document.querySelector('.plot__ring')).toBeNull();
    hover(1);
    expect(document.querySelector('.plot__guide')).not.toBeNull();
    expect(document.querySelector('.plot__ring')).not.toBeNull();
  });
});

describe('итог справа', () => {
  test('последний день показан как «N из M» с долей', () => {
    draw([198, 205], [190, 191]);
    expect(document.querySelector('.chart-box__total-main')?.textContent)
      .toBe('205 из 214');
    expect(document.querySelector('.chart-box__total-percent')?.textContent)
      .toBe('95,8%');
  });

  test('разница с прошлым периодом считается по данным, а не рисуется', () => {
    // 205 из 214 против 191 из 214: 95,8% против 89,3%.
    draw([198, 205], [190, 191]);
    const shift = document.querySelector('.chart-box__shift');
    expect(shift?.textContent).toContain('6,5%');
    expect(shift?.classList.contains('chart-box__shift--up')).toBe(true);
    expect(shift?.classList.contains('chart-box__shift--down')).toBe(false);
    // Направление обязано быть не только в цвете: рядом с числом стоит
    // стрелка, а для диктора — те же слова в скрытой подписи.
    expect(shift?.querySelector('svg')).not.toBeNull();
    expect(shift?.getAttribute('title')).toContain('Рост');
  });

  test('падение показано вниз и другим цветом', () => {
    draw([198, 180], [190, 205]);
    const shift = document.querySelector('.chart-box__shift');
    expect(shift?.classList.contains('chart-box__shift--down')).toBe(true);
    expect(shift?.querySelector('svg')).not.toBeNull();
    expect(shift?.getAttribute('title')).toContain('Снижение');
  });

  test('сравнивать не с чем — плашки нет, а не ноль', () => {
    draw([198, 205], null);
    expect(document.querySelector('.chart-box__shift')).toBeNull();
  });

  test('без изменений — не рост: ни стрелки вверх, ни зелёного', () => {
    draw([198, 205], [190, 205]);
    const shift = document.querySelector('.chart-box__shift');
    expect(shift?.classList.contains('chart-box__shift--flat')).toBe(true);
    expect(shift?.textContent).not.toContain('↗');
    expect(shift?.textContent).toContain('Без изменений');
  });

  test('основание сравнения названо словами, а не одной стрелкой', () => {
    // Цифра меняется при переключении периода: «тот же день» у недели и
    // у месяца — разные дни календаря. Без подписи это читается как сбой,
    // а диктору от стрелки не достаётся вообще ничего.
    draw([198, 205], [190, 191]);
    const shift = document.querySelector('.chart-box__shift');
    expect(shift?.getAttribute('title'))
      .toBe('Рост на 6,5% к тому же дню прошлого периода');
    expect(shift?.querySelector('.visually-hidden')?.textContent?.trim().replace(/\s+/g, ' '))
      .toBe('Рост на 6,5% к тому же дню прошлого периода');
  });
});

describe('подписи оси', () => {
  test('две недели подписаны каждым днём, месяц — через несколько', () => {
    const days = (node: Document | Element) =>
      [...node.querySelectorAll('.plot__tick')]
        .filter((n) => !n.textContent?.endsWith('%')).length;

    const { unmount } = render(
      <AttendanceChart points={toPoints(series(14, () => 200))} previous={[]} label="Явка" />,
    );
    expect(days(document)).toBe(14);
    unmount();

    render(
      <AttendanceChart points={toPoints(series(30, () => 200))} previous={[]} label="Явка" />,
    );
    // Тридцать подписей в ряд налезают друг на друга; восемь читаются.
    expect(days(document)).toBeLessThanOrEqual(8);
    expect(days(document)).toBeGreaterThanOrEqual(6);
  });
});
