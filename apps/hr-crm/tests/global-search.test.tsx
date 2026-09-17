import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';

import * as api from '../src/api/crm';
import { GlobalEmployeeSearch, normalizeEmployeeSearch } from '../src/components/GlobalEmployeeSearch';

vi.mock('../src/api/crm', () => ({
  searchEmployees: vi.fn(),
  employeePhotoUrl: vi.fn((id: string) => `/employees/${id}/photo/`),
}));

describe('глобальный поиск сотрудников', () => {
  test('normalizes whitespace and ё without transliteration', () => {
    expect(normalizeEmployeeSearch('  Ёлкин   Пётр  ')).toBe('елкин петр');
    expect(normalizeEmployeeSearch('my')).toBe('my');
  });

  test('shows an empty state for a query without matches', async () => {
    vi.mocked(api.searchEmployees).mockResolvedValue({ items: [] });
    render(<MemoryRouter><GlobalEmployeeSearch /></MemoryRouter>);

    fireEvent.change(screen.getByRole('combobox', { name: 'Поиск сотрудника' }), {
      target: { value: 'my' },
    });

    expect(await screen.findByText('Сотрудники не найдены')).toBeTruthy();
    expect(api.searchEmployees).toHaveBeenCalledWith('my', expect.any(AbortSignal));
  });

  test('positions the fixed panel within a 1672px viewport', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1672 });
    let rect = {
      top: 100, bottom: 166, left: 1420, right: 1640,
      width: 220, height: 66, x: 1420, y: 100,
      toJSON: () => ({}),
    } as DOMRect;
    vi.mocked(api.searchEmployees).mockResolvedValue({ items: [] });
    render(<MemoryRouter><GlobalEmployeeSearch /></MemoryRouter>);

    const input = screen.getByRole('combobox', { name: 'Поиск сотрудника' });
    input.getBoundingClientRect = () => rect;
    fireEvent.change(input, { target: { value: 'му' } });

    const panel = await screen.findByRole('listbox', { name: 'Результаты поиска сотрудников' });
    expect(panel.style.top).toBe('175px');
    expect(panel.style.left).toBe('1110px');
    expect(panel.style.width).toBe('530px');

    rect = { ...rect, top: 200, bottom: 266, y: 200 };
    fireEvent.scroll(window);
    await waitFor(() => expect(panel.style.top).toBe('275px'));
  });
});
