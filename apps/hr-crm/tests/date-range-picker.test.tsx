import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { AppDateRangePicker } from '../src/components/DateRangePicker';

describe('единый выбор диапазона дат', () => {
  test('обе даты используют один календарный стиль и сохраняют границы диапазона', () => {
    const onFromChange = vi.fn();
    const onToChange = vi.fn();
    render(<AppDateRangePicker label="Период" now="2026-09-16" from="2026-09-01" to="2026-09-16" onFromChange={onFromChange} onToChange={onToChange} />);
    fireEvent.click(screen.getByLabelText('Период: начало'));
    const dialog = screen.getByRole('dialog', { name: 'Выбор даты: Период: начало' });
    const grid = within(dialog).getByRole('grid');
    expect(within(grid).getByLabelText('01.09.2026').className).toContain('cal__day--range-start');
    fireEvent.click(within(grid).getByLabelText('05.09.2026'));
    expect(onFromChange).toHaveBeenCalledWith('2026-09-05');

    fireEvent.click(screen.getByLabelText('Период: конец'));
    const endGrid = within(screen.getByRole('dialog', { name: 'Выбор даты: Период: конец' })).getByRole('grid');
    expect(within(endGrid).getByLabelText('01.09.2026').hasAttribute('disabled')).toBe(true);
  });

  test('можно очистить необязательную дату ручным вводом', () => {
    const onFromChange = vi.fn();
    render(<AppDateRangePicker label="Период" now="2026-09-16" from="2026-09-01" to="" onFromChange={onFromChange} onToChange={() => {}} />);
    const field = screen.getByLabelText('Период: начало') as HTMLInputElement;
    fireEvent.change(field, { target: { value: '' } });
    fireEvent.blur(field);
    expect(onFromChange).toHaveBeenCalledWith('');
  });
});
