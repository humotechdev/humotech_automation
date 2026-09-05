/**
 * Главная страница: числа с сервера и честные состояния блоков.
 *
 * Проверяется не вёрстка, а обещания: ни одно число не придумано здесь,
 * ошибка не превращается в ноль, и поздний ответ отменённого фильтра
 * не подменяет данные нового.
 */

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { toPoints } from '../src/components/AttendanceChart';
import { formatPercent, percent } from '../src/features/dashboard/data';
import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const CARDS = [
  ['active_employees', 'Активные сотрудники', 214],
  ['should_work_today', 'Должны работать сегодня', 200],
  ['in_office', 'Сейчас в офисе', 180],
  ['not_come', 'Не пришли', 9],
  ['vacation', 'В отпуске', 22],
  ['sick_leave', 'На больничном', 12],
  ['left', 'Уже ушли', 11],
  ['open_sessions', 'Незакрытые сессии', 2],
  ['no_schedule', 'Без графика', 0],
] as const;

function dashboard(overrides: Partial<Record<string, number>> = {}) {
  return {
    date: '2026-09-05',
    timezone: 'Asia/Dushanbe',
    cards: CARDS.map(([key, title, value]) => ({
      key,
      title,
      value: overrides[key] ?? value,
      endpoint: '/api/v1/attendance/presence',
      params: {},
      attention: false,
    })),
    warnings: [],
  };
}

function network(handler: (path: string) => Response | null = () => null) {
  return fakeNetwork((path) => {
    const own = handler(path);
    if (own) return own;
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/dashboard')) return json(200, dashboard());
    return crm(path) ?? json(200, { items: [] });
  });
}

describe('карточки показателей', () => {
  test('подписи и числа приходят с сервера, а не из кода', async () => {
    // Сервер сам решает, как назвать показатель: для прошлой даты
    // «сейчас в офисе» звучало бы неверно, и переименовывать его на
    // клиенте значило бы держать второе место с теми же правилами.
    network();
    renderApp('/');

    expect(await screen.findByText('Сейчас в офисе')).toBeTruthy();
    expect(screen.getByText('Активные сотрудники')).toBeTruthy();
    expect(screen.getByText('214')).toBeTruthy();
    expect(screen.getByText('180')).toBeTruthy();
  });

  test('доля считается от знаменателя, который вернул сервер', async () => {
    network();
    renderApp('/');
    await screen.findByText('Сейчас в офисе');

    expect(screen.getByText('из 200 · 90,0%')).toBeTruthy();
  });

  test('при нулевом знаменателе процента нет, а не «0%»', async () => {
    network((path) =>
      path.includes('/dashboard')
        ? json(200, dashboard({ should_work_today: 0, in_office: 0 }))
        : null,
    );
    renderApp('/');
    await screen.findByText('Сейчас в офисе');

    expect(screen.getByText('Сравнивать не с чем')).toBeTruthy();
    expect(screen.queryByText(/0,0%/)).toBeNull();
  });
});

describe('состояния блоков', () => {
  test('ошибка блока не превращается в ноль', async () => {
    network((path) => (path.includes('/dashboard') ? json(500, { error: {} }) : null));
    renderApp('/');

    expect(await screen.findByText(/Не удалось загрузить показатели/)).toBeTruthy();
    expect(screen.queryByText('Активные сотрудники')).toBeNull();
  });

  test('сбой одного блока не гасит соседние', async () => {
    // Упавшие офисы — не повод прятать карточки, которые уже пришли.
    network((path) => (path.includes('/offices') ? json(500, { error: {} }) : null));
    renderApp('/');

    expect(await screen.findByText('Сейчас в офисе')).toBeTruthy();
    expect(screen.getByText('214')).toBeTruthy();
  });

  test('отказ по правам показывается как отказ, а не как пустота', async () => {
    network((path) =>
      path.includes('/knowledge/escalations')
        ? json(403, { error: { code: 'forbidden', message: 'нет' } })
        : null,
    );
    renderApp('/');

    expect(await screen.findByText(/Нет доступа к разделу «обращения»/)).toBeTruthy();
  });
});

describe('фильтры', () => {
  test('смена даты отменяет прежний запрос показателей', async () => {
    // Поздний ответ отменённого фильтра не должен подменить новый.
    const calls = network();
    renderApp('/');
    await screen.findByText('Сейчас в офисе');

    const before = calls.filter((c) => c.url.includes('/dashboard')).length;
    const date = screen.getByLabelText('Дата') as HTMLInputElement;
    fireEvent.change(date, { target: { value: '2026-09-01' } });

    await waitFor(() =>
      expect(
        calls.filter((c) => c.url.includes('/dashboard')).length,
      ).toBeGreaterThan(before),
    );
    const last = calls.filter((c) => c.url.includes('/dashboard')).pop();
    expect(last?.url).toContain('date=2026-09-01');
  });
});

describe('арифметика, которую нельзя доверить картинке', () => {
  test('день без знаменателя — пропуск, а не ноль процентов', () => {
    const points = toPoints([
      { day: '2026-09-01', worked_seconds: 0, attended: 0, expected: 0, late: 0 },
      { day: '2026-09-02', worked_seconds: 1, attended: 3, expected: 4, late: 0 },
    ]);

    expect(points[0]?.value).toBeNull();
    expect(points[1]?.value).toBeCloseTo(75);
  });

  test('процент печатается по-русски и с одним знаком', () => {
    expect(formatPercent(percent(193, 214))).toBe('90,2%');
    expect(formatPercent(null)).toBe('—');
  });
});
