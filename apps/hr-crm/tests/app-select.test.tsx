import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { AppMultiSelect, AppSelect } from '../src/components/AppSelect';

describe('общие контролы выбора', () => {
  test('открывает список, выбирает вариант и закрывается по Escape и клику вне', () => {
    const onChange = vi.fn();
    render(<><AppSelect label="Офис" value="" empty="Все офисы" options={[{ value: 't', label: 'Ташкент' }]} onChange={onChange} /><button>Снаружи</button></>);
    fireEvent.click(screen.getByRole('button', { name: 'Офис: Все офисы' }));
    expect(screen.getByRole('listbox', { name: 'Офис' })).toBeTruthy();
    fireEvent.click(screen.getByRole('option', { name: 'Ташкент' }));
    expect(onChange).toHaveBeenCalledWith('t');
    expect(screen.queryByRole('listbox')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Офис: Все офисы' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Офис: Все офисы' }));
    fireEvent.mouseDown(screen.getByText('Снаружи'));
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  test('поиск внутри длинного списка фильтрует варианты', () => {
    const options = Array.from({ length: 12 }, (_, i) => ({ value: String(i), label: i === 9 ? 'Самарканд' : `Офис ${i}` }));
    render(<AppSelect label="Офис" value="" options={options} onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Офис: Выберите…' }));
    fireEvent.change(screen.getByLabelText('Поиск: Офис'), { target: { value: 'самар' } });
    expect(screen.getByRole('option', { name: 'Самарканд' })).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'Офис 1' })).toBeNull();
  });

  test('multi-select сохраняет несколько выбранных пунктов', () => {
    const onChange = vi.fn();
    render(<AppMultiSelect label="Отделы" value={[]} empty="Все отделы" options={[{ value: 'a', label: 'Поддержка' }, { value: 'b', label: 'Бухгалтерия' }]} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Отделы: Все отделы' }));
    const list = screen.getByRole('listbox', { name: 'Отделы' });
    fireEvent.click(within(list).getByLabelText('Поддержка'));
    expect(onChange).toHaveBeenCalledWith(['a']);
  });
});
