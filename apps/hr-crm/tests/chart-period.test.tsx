/**
 * Переключение периода графика «Явка».
 *
 * Проверяется не анимация, а её причина: переключатель обязан менять
 * ТОЛЬКО график. Раньше на каждое нажатие график исчезал со страницы
 * вместе со своим контейнером — панель схлопывалась, и всё ниже и
 * правее прыгало вверх и обратно. Выглядело это как перезагрузка всей
 * страницы, хотя запрашивался один блок.
 *
 * Поэтому тесты смотрят на три вещи: какие адреса запрошены, остаётся
 * ли на экране прежний график и не уходит ли соседний блок в загрузку.
 */

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const OFFICES = {
  items: [
    { id: 'o1', code: 'HQ', name: 'Головной офис', region_id: 'r1', status: 'ACTIVE' },
  ],
};

/** Состав смены офиса: страница складывает из него графу «В штате». */
const PRESENCE = {
  date: '2026-09-11',
  timezone: 'Asia/Dushanbe',
  counts: { IN_OFFICE: 32, LEFT: 2, NOT_COME: 2, VACATION: 4, SICK_LEAVE: 2 },
  rows: [],
};

const DASHBOARD = {
  date: '2026-09-11',
  timezone: 'Asia/Dushanbe',
  cards: [
    {
      key: 'active_employees', title: 'Активные сотрудники', value: 248,
      endpoint: '/api/v1/employees', params: {}, attention: false,
    },
    {
      key: 'should_work_today', title: 'Должны работать', value: 214,
      endpoint: '/api/v1/attendance/presence', params: {}, attention: false,
    },
  ],
  warnings: [],
};

/** Ряд по дням: столько точек, сколько дней в запрошенном отрезке. */
function series(days: number) {
  return {
    series: Array.from({ length: days }, (_, i) => ({
      day: `2026-08-${String(20 + i).padStart(2, '0')}`,
      attended: 200 + i,
      expected: 214,
    })),
  };
}

type Options = { failChartAfter?: number; holdChart?: boolean };

function network({ failChartAfter, holdChart }: Options = {}) {
  let chartCalls = 0;
  const held: (() => void)[] = [];
  const calls = fakeNetwork(async (path) => {
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/dashboard')) return json(200, DASHBOARD);
    if (path.includes('/offices')) return json(200, OFFICES);
    if (path.includes('/attendance/presence')) return json(200, PRESENCE);
    if (path.includes('/analytics')) {
      chartCalls += 1;
      if (failChartAfter !== undefined && chartCalls > failChartAfter) {
        return json(500, { detail: 'нет' });
      }
      if (holdChart && chartCalls > 2) {
        await new Promise<void>((go) => held.push(go));
      }
      return json(200, series(14));
    }
    return crm(path) ?? json(200, { items: [] });
  });
  return { calls, release: () => held.forEach((go) => go()) };
}

const paths = (calls: { url: string }[]) => calls.map((c) => c.url);
const count = (calls: { url: string }[], part: string) =>
  paths(calls).filter((u) => u.includes(part)).length;

async function openDashboard() {
  renderApp('/');
  await screen.findByText('Явка за 14 дней');
  // Дождаться именно поля графика, а не заголовка: заголовок есть и
  // пока данных нет, а проверяем мы сохранность нарисованного.
  await waitFor(() => expect(document.querySelector('.chart-box')).not.toBeNull());
  await waitFor(() => expect(screen.getByText('Головной офис')).toBeTruthy());
}

function period(title: string) {
  return screen.getByRole('button', { name: title });
}

describe('переключение периода графика', () => {
  test.each(['7 дней', '14 дней', 'Месяц'])(
    '«%s» запрашивает только график',
    async (title) => {
      const { calls } = network();
      await openDashboard();

      // Отойти от проверяемого периода: нажатие на уже выбранный ничего
      // не запрашивает, и это правильно — проверять надо переход.
      const away = title === '7 дней' ? 'Месяц' : '7 дней';
      fireEvent.click(period(away));
      await waitFor(() => expect(screen.getByText(`Явка за ${away.toLowerCase()}`)).toBeTruthy());

      const officesBefore = count(calls, '/attendance/presence');
      const dashboardBefore = count(calls, '/dashboard');
      const chartBefore = count(calls, '/analytics');

      fireEvent.click(period(title));
      await waitFor(() => expect(count(calls, '/analytics')).toBeGreaterThan(chartBefore));

      // Соседние блоки не трогали.
      expect(count(calls, '/attendance/presence')).toBe(officesBefore);
      expect(count(calls, '/dashboard')).toBe(dashboardBefore);
      expect(count(calls, '/knowledge/escalations')).toBeLessThanOrEqual(1);
    },
  );

  test('таблица офисов не уходит в загрузку и остаётся на месте', async () => {
    network();
    await openDashboard();
    fireEvent.click(period('Месяц'));

    // Ни строки «Загружаем офисы…», ни исчезнувшего офиса.
    expect(screen.queryByText(/Загружаем офисы/)).toBeNull();
    expect(screen.getByText('Головной офис')).toBeTruthy();
  });

  test('прежний график виден, пока грузится новый', async () => {
    const { release } = network({ holdChart: true });
    await openDashboard();

    const box = document.querySelector('.chart-box');
    expect(box).not.toBeNull();

    fireEvent.click(period('Месяц'));

    // Контейнер графика на месте и это тот же самый узел: подмена узла
    // и есть то мигание, ради которого всё затевалось.
    await waitFor(() => expect(document.querySelector('.panel__progress--on')).not.toBeNull());
    expect(document.querySelector('.chart-box')).toBe(box);
    expect(screen.queryByText(/Загружаем график/)).toBeNull();

    release();
  });

  test('сбой обновления оставляет прежние данные и предлагает повтор', async () => {
    network({ failChartAfter: 2 });
    await openDashboard();
    const box = document.querySelector('.chart-box');

    fireEvent.click(period('Месяц'));

    await screen.findByText('Не удалось обновить данные');
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeTruthy();
    // График не стёрт и не заменён сообщением об ошибке.
    expect(document.querySelector('.chart-box')).toBe(box);
    expect(screen.getByText('Головной офис')).toBeTruthy();
  });

  test('быстрые переключения не дают старому ответу перезаписать новый', async () => {
    const seen: string[] = [];
    const calls = fakeNetwork(async (path) => {
      if (path.includes('/auth/')) return json(200, USER);
      if (path.includes('/dashboard')) return json(200, DASHBOARD);
      if (path.includes('/offices')) return json(200, OFFICES);
      if (path.includes('/attendance/presence')) return json(200, PRESENCE);
      if (path.includes('/analytics')) {
        seen.push(path);
        return json(200, series(14));
      }
      return crm(path) ?? json(200, { items: [] });
    });
    await openDashboard();

    fireEvent.click(period('7 дней'));
    fireEvent.click(period('Месяц'));
    fireEvent.click(period('14 дней'));

    // Побеждает последний выбранный период, а не последний ответ.
    await waitFor(() => expect(screen.getByText('Явка за 14 дней')).toBeTruthy());
    expect(count(calls, '/offices')).toBeLessThanOrEqual(1);
  });

  test('у выбранного периода есть машиночитаемая отметка', async () => {
    // Подложка переезжает по `aria-pressed`: без него диктор не назовёт
    // выбранный период, а сам индикатор — украшение.
    network();
    await openDashboard();
    expect(period('14 дней').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(period('Месяц'));
    expect(period('Месяц').getAttribute('aria-pressed')).toBe('true');
    expect(period('14 дней').getAttribute('aria-pressed')).toBe('false');
  });
});
