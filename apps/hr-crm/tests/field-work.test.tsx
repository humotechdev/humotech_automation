import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { FieldWorkControls } from '../src/components/FieldWorkControls';
import { DayBar } from '../src/components/DayBar';
import { fakeNetwork, json } from './helpers';

const date = '2026-09-28';
const pending = {
  id: 'request-1', employee_id: 'employee-1', date,
  status: 'PENDING', requested_at: `${date}T10:00:00Z`, delivery_status: 'PENDING',
};
const props = {
  employeeId: 'employee-1', employeeName: 'Дилноза Саидова', day: date,
  scheduledStart: '09:00:00', scheduledEnd: '18:00:00', state: 'NOT_COME',
  canCorrect: true, onChanged: vi.fn(),
};

describe('выездная работа HR', () => {
  test('менеджер должен подтвердить факт проверки до отправки; дата и данные остаются в запросе', async () => {
    let current: typeof pending | null = null;
    const calls = fakeNetwork((url, call) => {
      if (url.includes('/attendance/field-work') && call.method === 'GET') return json(200, { items: current ? [current] : [] });
      if (url.endsWith('/attendance/field-work') && call.method === 'POST') {
        current = pending;
        return json(201, current);
      }
      return json(404, {});
    });
    render(<FieldWorkControls {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Запросить подтверждение выездной работы' }));
    const send = screen.getByRole('button', { name: 'Отправить сотруднику' });
    expect((send as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Объект или место (необязательно)'), { target: { value: 'Терминал №14' } });
    fireEvent.change(screen.getByLabelText('Рабочая задача (необязательно)'), { target: { value: 'Обновление ПО' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Я уточнил/ }));
    fireEvent.click(send);
    await screen.findByText(/Ожидает подтверждения сотрудника/);
    const sent = calls.find((call) => call.method === 'POST');
    expect(sent?.body).toEqual({ employee_id: 'employee-1', date, manager_confirmed: true,
      work_location: 'Терминал №14', work_description: 'Обновление ПО' });
    expect(props.onChanged).toHaveBeenCalled();
  });

  test('провал доставки разрешает повторить, а отмена оставляет аудируемый статус', async () => {
    let current = { ...pending, delivery_status: 'FAILED' };
    const calls = fakeNetwork((url, call) => {
      if (url.includes('/retry') && call.method === 'POST') {
        current = { ...pending, delivery_status: 'PENDING' }; return json(200, current);
      }
      if (url.includes('/cancel') && call.method === 'POST') {
        current = { ...pending, status: 'CANCELLED', delivery_status: 'PENDING' };
        return json(200, current);
      }
      return json(200, { items: [current] });
    });
    const view = render(<FieldWorkControls {...props} state="FIELD_WORK_PENDING" summary={{ id: pending.id, status: 'PENDING' }} />);
    await screen.findByText(/Не удалось доставить в Telegram/);
    fireEvent.click(screen.getByRole('button', { name: 'Повторить отправку' }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/request-1/retry'))).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Отменить запрос' }));
    await screen.findByText('Запрос отменён');
    expect(calls.some((call) => call.url.endsWith('/request-1/cancel'))).toBe(true);
    view.rerender(<FieldWorkControls {...props} state="NOT_COME" summary={null} />);
    expect(screen.getByRole('button', { name: 'Отправить новый запрос на выездную работу' })).toBeTruthy();
  });

  test('после ответа сотрудника обновляется статус; без права корректировки отправить нельзя', async () => {
    let current = { ...pending };
    fakeNetwork(() => json(200, { items: [current] }));
    const changed = vi.fn();
    render(<FieldWorkControls {...props} canCorrect={false} onChanged={changed}
      state="FIELD_WORK_PENDING" summary={{ id: pending.id, status: 'PENDING' }} />);
    await screen.findByText('Ожидает подтверждения сотрудника');
    expect(screen.queryByRole('button', { name: /Отменить запрос|Запросить подтверждение/ })).toBeNull();
    current = { ...pending, status: 'CONFIRMED' };
    fireEvent.focus(window);
    await screen.findByText('Выездная работа подтверждена');
    expect(changed).toHaveBeenCalledTimes(1);
  });

  test('ожидающий запрос для LATE остаётся видимым и второй запрос не отправляется', async () => {
    fakeNetwork(() => json(200, { items: [pending] }));
    render(<FieldWorkControls {...props} state="LATE" summary={{ id: pending.id, status: 'PENDING' }} />);
    await screen.findByText(/Ожидает подтверждения сотрудника/);
    expect(screen.queryByRole('button', { name: 'Запросить подтверждение выездной работы' })).toBeNull();
  });

  test('полный график помечен как выездной, QR-входы и выходы не подделаны', () => {
    render(<DayBar zone="Asia/Tashkent" row={{
      state: 'FIELD_WORK', scheduled_start: '09:00:00', scheduled_end: '18:00:00',
      intervals: [], first_entry_at: null, last_exit_at: null,
    } as never} />);
    expect(screen.getByText(/Выездная работа.*09:00–18:00 по графику.*QR-отметок нет/)).toBeTruthy();
  });
});
