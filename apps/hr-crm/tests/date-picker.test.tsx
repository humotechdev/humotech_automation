/**
 * Поле даты с мини-календарём.
 *
 * Главное здесь не внешний вид, а две вещи, которые ломаются молча:
 * дата не должна съезжать на соседний день из-за часового пояса, а
 * несуществующее число не должно превращаться в другое.
 */

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { DatePicker } from '../src/components/DatePicker';
import {
  addDays, addMonths, maskRu, monthGrid, parseRu, toRu,
} from '../src/features/calendar/date';

const NOW = '2026-09-11';

function show(props: Partial<React.ComponentProps<typeof DatePicker>> = {}) {
  const onChange = vi.fn();
  render(
    <DatePicker label="Дата" value="2026-09-11" now={NOW} onChange={onChange} {...props} />,
  );
  return { onChange, field: screen.getByLabelText('Дата') as HTMLInputElement };
}

const openCalendar = () => fireEvent.click(screen.getByLabelText('Дата'));
const day = (text: string) => {
  const grid = screen.getByRole('grid');
  return within(grid).getAllByText(text).filter((n) => !n.hasAttribute('disabled'))[0]!;
};

describe('разбор и запись даты', () => {
  test('три разделителя понимаются одинаково', () => {
    for (const text of ['11.09.2026', '11/09/2026', '11-09-2026']) {
      expect(parseRu(text)).toEqual({ ok: true, value: '2026-09-11' });
    }
  });

  test('несуществующее число — ошибка, а не соседний день', () => {
    // 31 февраля это ошибка ввода. Тихая правка на 3 марта поменяла бы
    // смысл того, что человек имел в виду.
    expect(parseRu('31.02.2026')).toEqual({ ok: false, reason: 'calendar' });
    expect(parseRu('31.04.2026')).toEqual({ ok: false, reason: 'calendar' });
  });

  test('високосный год считается по правилу, а не по остатку от четырёх', () => {
    expect(parseRu('29.02.2024').ok).toBe(true);
    expect(parseRu('29.02.2026').ok).toBe(false);
    expect(parseRu('29.02.1900').ok).toBe(false);
    expect(parseRu('29.02.2000').ok).toBe(true);
  });

  test('точки подставляются при наборе в конец и не мешают правке середины', () => {
    expect(maskRu('11092026', true)).toBe('11.09.2026');
    expect(maskRu('1109', true)).toBe('11.09');
    expect(maskRu('11.09.2026', false)).toBe('11.09.2026');
  });

  test('дата не съезжает на соседний день', () => {
    // Ровно та ошибка, ради которой вся арифметика в UTC: `new Date` от
    // строки даёт полночь UTC, и восточнее нуля день становится другим.
    expect(toRu('2026-09-11')).toBe('11.09.2026');
    expect(addDays('2026-09-11', 0)).toBe('2026-09-11');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
  });

  test('месяц сдвигается без перепрыгивания', () => {
    // 31 марта минус месяц — конец февраля, а не третье марта.
    expect(addMonths('2026-03-31', -1)).toBe('2026-02-28');
    expect(addMonths('2026-01-31', 1)).toBe('2026-02-28');
    expect(addMonths('2026-12-15', 1)).toBe('2027-01-15');
  });

  test('в сетке всегда шесть недель и она начинается с понедельника', () => {
    for (const anchor of ['2026-09-11', '2026-02-01', '2027-01-20']) {
      const grid = monthGrid(anchor);
      expect(grid).toHaveLength(42);
      expect(new Date(`${grid[0]}T00:00:00Z`).getUTCDay()).toBe(1);
    }
  });
});

describe('поле даты', () => {
  test('календарь открывается по клику, по стрелке вниз и по кнопке', () => {
    show();
    expect(screen.queryByRole('dialog')).toBeNull();
    openCalendar();
    expect(screen.getByRole('dialog')).toBeTruthy();

    fireEvent.keyDown(screen.getByLabelText('Дата'), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();

    fireEvent.keyDown(screen.getByLabelText('Дата'), { key: 'ArrowDown' });
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.keyDown(screen.getByLabelText('Дата'), { key: 'Escape' });

    fireEvent.click(screen.getByLabelText('Открыть календарь'));
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  test('Escape закрывает календарь', () => {
    show();
    openCalendar();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  test('ручной ввод отдаёт наружу формат сервера', () => {
    const { onChange, field } = show();
    fireEvent.change(field, { target: { value: '05.03.2026' } });
    // Пока человек печатает, наружу ничего не уходит.
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('2026-03-05');
  });

  test('вставленная дата с косыми чертами тоже принимается', () => {
    const { onChange, field } = show();
    fireEvent.change(field, { target: { value: '05/03/2026' } });
    fireEvent.blur(field);
    expect(onChange).toHaveBeenCalledWith('2026-03-05');
  });

  test('неправильная дата не применяется и объясняется', () => {
    const { onChange, field } = show();
    fireEvent.change(field, { target: { value: '31.02.2026' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toBe('Введите корректную дату');
  });

  test('границы периода названы словами', () => {
    const { onChange, field } = show({ min: '2026-09-01', max: NOW });
    fireEvent.change(field, { target: { value: '01.08.2026' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(screen.getByRole('alert').textContent).toBe(
      'Дата должна быть не раньше 01.09.2026',
    );

    fireEvent.change(field, { target: { value: '20.12.2026' } });
    fireEvent.keyDown(field, { key: 'Enter' });
    expect(screen.getByRole('alert').textContent).toBe(
      'Дата должна быть не позже сегодняшнего дня',
    );
    expect(onChange).not.toHaveBeenCalled();
  });

  test('выбор дня мышью применяет дату и закрывает календарь', () => {
    const { onChange } = show();
    openCalendar();
    fireEvent.click(day('18'));
    expect(onChange).toHaveBeenCalledWith('2026-09-18');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  test('«Сегодня» выбирает текущий день', () => {
    const { onChange } = show({ value: '2026-09-01' });
    openCalendar();
    fireEvent.click(screen.getByText('Сегодня'));
    expect(onChange).toHaveBeenCalledWith(NOW);
  });

  test('переход по месяцам не закрывает календарь', () => {
    show();
    openCalendar();
    expect(screen.getByText('Сентябрь 2026')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Следующий месяц'));
    expect(screen.getByText('Октябрь 2026')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Предыдущий месяц'));
    fireEvent.click(screen.getByLabelText('Предыдущий месяц'));
    expect(screen.getByText('Август 2026')).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  test('клавиатура водит по сетке и выбирает день', () => {
    const { onChange } = show();
    openCalendar();
    const grid = screen.getByRole('grid');
    fireEvent.keyDown(grid, { key: 'ArrowRight' });
    fireEvent.keyDown(grid, { key: 'ArrowDown' });
    fireEvent.keyDown(grid, { key: 'Enter' });
    // 11 сентября + 1 день + неделя = 19 сентября.
    expect(onChange).toHaveBeenCalledWith('2026-09-19');
  });

  test('PageDown уводит на следующий месяц', () => {
    show();
    openCalendar();
    fireEvent.keyDown(screen.getByRole('grid'), { key: 'PageDown' });
    expect(screen.getByText('Октябрь 2026')).toBeTruthy();
  });

  test('запрещённые дни не выбираются', () => {
    const { onChange } = show({ max: NOW });
    openCalendar();
    const grid = screen.getByRole('grid');
    const later = within(grid).getAllByText('20')[0]!;
    expect(later.hasAttribute('disabled')).toBe(true);
    fireEvent.click(later);
    expect(onChange).not.toHaveBeenCalled();
  });

  test('сегодняшний день помечен для доступности', () => {
    show();
    openCalendar();
    const grid = screen.getByRole('grid');
    const today = within(grid).getByLabelText('11.09.2026');
    expect(today.getAttribute('aria-current')).toBe('date');
    expect(today.getAttribute('aria-selected')).toBe('true');
  });

  test('календарь рисуется вне поля, чтобы его не обрезала панель', () => {
    const { container } = render(
      <div style={{ overflow: 'hidden' }}>
        <DatePicker label="Дата" value={NOW} now={NOW} onChange={() => {}} />
      </div>,
    );
    fireEvent.click(screen.getByLabelText('Дата'));
    const dialog = screen.getByRole('dialog');
    expect(container.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
  });
});
