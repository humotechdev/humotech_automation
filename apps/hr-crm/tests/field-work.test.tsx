import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';

import { FieldWorkControls } from '../src/components/FieldWorkControls';
import { DayBar } from '../src/components/DayBar';
import { DayCard } from '../src/components/DayCard';
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
    fireEvent.change(screen.getByLabelText(/Объект или место/), { target: { value: 'Терминал №14' } });
    fireEvent.change(screen.getByLabelText(/Рабочая задача/), { target: { value: 'Обновление ПО' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Руководитель подтвердил/ }));
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


// --- правая панель «Отметка за день» -------------------------------------------------

const row = {
  employee_id: 'employee-1',
  full_name: 'Абдурахмонова-Исмоилзода Дилноза Саидовна Очень Длинное Имя Для Проверки Переноса',
  employee_number: 'HT-0001', office_id: 'office-1', office_name: 'город Ташкент', department_name: 'Sales',
  position_name: null, state: 'NOT_COME', first_entry_at: null, last_exit_at: null, seconds: 0,
  open_session_id: null, late_minutes: null, scheduled_start: '09:00:00', scheduled_end: '18:00:00',
  absence_code: null, absence_name: null, notice_kind: null, notice_comment: null,
  conflicting_marks: false, intervals: [], field_work: null,
};

type Handler = (url: string, call: { method: string; body?: unknown }) => Response | Promise<Response> | null;

function panel(handler: Handler, overrides: Record<string, unknown> = {}, startFieldWork = true) {
  const calls = fakeNetwork((url, call) => {
    const answer = handler(url, call);
    if (answer) return answer;
    if (url.includes('/attendance/events') || url.includes('/attendance/sessions')) {
      return json(200, { items: [], next_cursor: null });
    }
    return json(404, {});
  });
  const onClose = vi.fn();
  const onChanged = vi.fn();
  render(
    <MemoryRouter>
      <DayCard row={{ ...row, ...overrides } as never} day={date} timezone="Asia/Tashkent"
               canAdd canFieldWork startFieldWork={startFieldWork} onClose={onClose} onChanged={onChanged} />
    </MemoryRouter>,
  );
  return { calls, onClose, onChanged, card: screen.getByRole('complementary', { name: 'Отметка за день' }) };
}

const reading = (items: unknown[]): Handler => (url, call) =>
  url.includes('/attendance/field-work') && call.method === 'GET' ? json(200, { items }) : null;

describe('панель «Отметка за день»: выездная работа', () => {
  test('«Выездная работа» открывает панель сразу на форме — без второго окна и затемнения', async () => {
    const { card } = panel(reading([]));
    const location = await within(card).findByLabelText(/Объект или место/);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.querySelector('.fw__overlay')).toBeNull();
    expect(screen.getAllByRole('complementary')).toHaveLength(1);
    // Над формой — сотрудник, офис и отдел, дата, состояние дня и график.
    expect(within(card).getByText(/Очень Длинное Имя/)).toBeTruthy();
    expect(within(card).getByText('город Ташкент · Sales')).toBeTruthy();
    expect(within(card).getByText('28 сентября 2026')).toBeTruthy();
    expect(within(card).getByText('Нет отметки')).toBeTruthy();
    expect(within(card).getByLabelText('График').textContent).toBe('09:00–18:00');
    expect(within(card).getByText(/До его ответа день не засчитывается/)).toBeTruthy();
    // Прежнее содержимое карточки дня на месте.
    expect(within(card).getByRole('link', { name: /Открыть карточку/ })).toBeTruthy();
    expect(within(card).getByRole('button', { name: /Добавить отметку/ })).toBeTruthy();
    // День без отметок: пустой секции «События сегодня» и полосатой шкалы
    // нет — «Нет отметки» уже сказано наверху (просьба владельца 12.10).
    await waitFor(() => expect(within(card).queryByText('События сегодня')).toBeNull());
    expect(card.querySelector('.day-bar__track--empty')).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(location));
    expect((location as HTMLInputElement).maxLength).toBe(200);
    expect((within(card).getByLabelText(/Рабочая задача/) as HTMLTextAreaElement).maxLength).toBe(1000);
  });

  test('«Отмена» и Escape закрывают только форму, панель остаётся', async () => {
    const { card, onClose } = panel(reading([]));
    fireEvent.click(await within(card).findByRole('button', { name: 'Отмена' }));
    const start = await within(card).findByRole('button', { name: 'Запросить подтверждение' });
    expect(within(card).queryByLabelText(/Объект или место/)).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(start));

    fireEvent.click(start);
    const field = await within(card).findByLabelText(/Объект или место/);
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(within(card).queryByLabelText(/Объект или место/)).toBeNull();
    expect(screen.getByRole('complementary', { name: 'Отметка за день' })).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  test('отправка — только с флажком и один раз; после неё статус сервера, а не «получил»', async () => {
    let current: Record<string, unknown> | null = null;
    let release: () => void = () => {};
    const { calls, card, onChanged } = panel((url, call) => {
      if (url.includes('/attendance/field-work') && call.method === 'GET') {
        return json(200, { items: current ? [current] : [] });
      }
      if (url.endsWith('/attendance/field-work') && call.method === 'POST') {
        // Ответ приходит по команде теста: пока запрос идёт, второй не уходит.
        return new Promise<Response>((resolve) => {
          release = () => {
            current = { ...pending, delivery_status: 'PENDING' };
            resolve(json(201, current));
          };
        });
      }
      return null;
    });
    const send = await within(card).findByRole('button', { name: 'Отправить сотруднику' });
    expect((send as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(card).getByRole('checkbox', { name: /Руководитель подтвердил/ }));
    fireEvent.click(send);
    fireEvent.click(send);
    expect(await within(card).findByRole('button', { name: 'Отправляем…' })).toBeTruthy();
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1);
    await act(async () => { release(); });
    await within(card).findByText('Ожидает подтверждения сотрудника');
    expect(within(card).getByText(/Засчитано: 0 ч/)).toBeTruthy();
    expect(within(card).getByText('Сообщение в очереди на отправку в Telegram')).toBeTruthy();
    expect(within(card).queryByText(/получил/)).toBeNull();
    expect(within(card).getByRole('button', { name: 'Отменить запрос' })).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
  });

  test('отказ сервера показан у формы, введённое сохраняется', async () => {
    const { card } = panel((url, call) => {
      if (url.includes('/attendance/field-work') && call.method === 'GET') return json(200, { items: [] });
      if (url.endsWith('/attendance/field-work') && call.method === 'POST') {
        return json(409, { error: { code: 'conflict', message: 'В этот день уже есть отметка посещаемости' } });
      }
      return null;
    });
    fireEvent.change(await within(card).findByLabelText(/Объект или место/), { target: { value: 'Склад клиента' } });
    fireEvent.change(within(card).getByLabelText(/Рабочая задача/), { target: { value: 'Инвентаризация' } });
    fireEvent.click(within(card).getByRole('checkbox', { name: /Руководитель подтвердил/ }));
    fireEvent.click(within(card).getByRole('button', { name: 'Отправить сотруднику' }));
    expect((await within(card).findByRole('alert')).textContent).toMatch(/уже есть отметка/);
    expect((within(card).getByLabelText(/Объект или место/) as HTMLInputElement).value).toBe('Склад клиента');
    expect((within(card).getByLabelText(/Рабочая задача/) as HTMLTextAreaElement).value).toBe('Инвентаризация');
    expect((within(card).getByRole('checkbox', { name: /Руководитель подтвердил/ }) as HTMLInputElement).checked).toBe(true);
  });

  test('ошибка доставки — отдельно от статуса запроса, с «Повторить отправку»', async () => {
    const { card } = panel(reading([{ ...pending, delivery_status: 'FAILED' }]),
      { state: 'FIELD_WORK_PENDING', field_work: { id: pending.id, status: 'PENDING' } }, false);
    expect(await within(card).findByText('Ожидает подтверждения сотрудника')).toBeTruthy();
    expect(within(card).getByText('Не удалось отправить сообщение в Telegram')).toBeTruthy();
    expect(within(card).getByRole('button', { name: 'Повторить отправку' })).toBeTruthy();
    expect(within(card).getByRole('button', { name: 'Отменить запрос' })).toBeTruthy();
  });

  test('подтверждён: норма из ответа сервера, не вход по QR', async () => {
    const { card } = panel(reading([{ ...pending, status: 'CONFIRMED', delivery_status: 'SENT', norm_seconds: 8 * 3600 }]),
      { state: 'FIELD_WORK', seconds: 8 * 3600, field_work: { id: pending.id, status: 'CONFIRMED' } }, false);
    expect(await within(card).findByText('Выездная работа подтверждена')).toBeTruthy();
    expect(within(card).getByText(/по плановой норме графика \(8 ч\).*не вход или выход по QR/)).toBeTruthy();
    expect(within(card).queryByRole('button', { name: /Запросить подтверждение|Отменить запрос/ })).toBeNull();
  });

  test.each([
    ['DECLINED', 'Сотрудник не подтвердил выездную работу'],
    ['CANCELLED', 'Запрос отменён'],
    ['EXPIRED', 'Срок подтверждения истёк'],
  ])('%s — ясный статус без часов; новый запрос для дня без отметки', async (status, label) => {
    const { card } = panel(reading([{ ...pending, status }]), {}, false);
    expect(await within(card).findByText(label)).toBeTruthy();
    expect(within(card).getByText('Часы не начислены.')).toBeTruthy();
    expect(within(card).getByRole('button', { name: 'Отправить новый запрос' })).toBeTruthy();
  });

  test('день с QR-входом: новый запрос не предлагается', async () => {
    const { card } = panel(reading([{ ...pending, status: 'CANCELLED' }]),
      { state: 'IN_OFFICE', first_entry_at: `${date}T04:00:00Z` }, false);
    expect(await within(card).findByText('Запрос отменён')).toBeTruthy();
    expect(within(card).queryByRole('button', { name: /Отправить новый запрос|Запросить подтверждение/ })).toBeNull();
  });

  test('ошибка чтения запроса — понятный текст и «Обновить», панель на месте', async () => {
    const { card } = panel((url, call) => url.includes('/attendance/field-work') && call.method === 'GET'
      ? json(500, { error: { code: 'server_error', message: 'Сбой' } }) : null, {}, false);
    expect(await within(card).findByRole('button', { name: 'Обновить' })).toBeTruthy();
    expect(within(card).queryByRole('button', { name: /Запросить подтверждение/ })).toBeNull();
  });
});
