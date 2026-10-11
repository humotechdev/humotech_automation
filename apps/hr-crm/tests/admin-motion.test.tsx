/**
 * Движение в «Администрировании»: направление вкладок, быстрые
 * переключения, раскрытие строк и просьба системы уменьшить анимацию.
 *
 * jsdom не раскладывает страницу и не проигрывает анимации: здесь
 * проверяется ЛОГИКА — какое направление выбрано, какая панель уходит,
 * что уходящая скрыта от фокуса и средств чтения, что переход
 * завершает только последний из быстрых кликов, что без движения
 * переключение мгновенное. Плавность, отсутствие рывков высоты и
 * мигания можно увидеть только в настоящем браузере.
 */

import { act, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { AnimatedCollapse } from '../src/components/admin/AnimatedCollapse';
import { AnimatedTabContent } from '../src/components/admin/AnimatedTabContent';
import { tabDirection } from '../src/features/admin/motion';

const ORDER = ['admins', 'departments', 'positions', 'schedules'] as const;

describe('направление перехода', () => {
  test('вправо по ряду — 1, влево — -1', () => {
    expect(tabDirection(ORDER, 'admins', 'departments')).toBe(1);
    expect(tabDirection(ORDER, 'positions', 'departments')).toBe(-1);
  });

  test('через несколько вкладок направление то же, что через одну', () => {
    expect(tabDirection(ORDER, 'admins', 'schedules')).toBe(1);
    expect(tabDirection(ORDER, 'schedules', 'admins')).toBe(-1);
  });

  test('та же вкладка или незнакомая — без движения', () => {
    expect(tabDirection(ORDER, 'positions', 'positions')).toBe(0);
    expect(tabDirection(ORDER, 'positions', 'offices')).toBe(0);
  });
});

// --- среда с анимацией ------------------------------------------------------

type Fake = {
  element: Element;
  keyframes: Keyframe[];
  finish: () => void;
  cancelled: boolean;
};

/**
 * Подменяет `matchMedia` и `Element.animate`. Анимации не идут сами:
 * тест завершает их вызовом `finish`, как это сделал бы браузер.
 */
function motionEnvironment({ reduce = false } = {}) {
  const fakes: Fake[] = [];
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: reduce && query.includes('reduce'),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
  const animate = vi.fn(function animate(this: Element, keyframes: Keyframe[]) {
    let done: () => void = () => undefined;
    let fail: (error: unknown) => void = () => undefined;
    const finished = new Promise<void>((resolve, reject) => {
      done = resolve;
      fail = reject;
    });
    finished.catch(() => undefined);
    const fake: Fake = {
      element: this,
      keyframes,
      finish: () => done(),
      cancelled: false,
    };
    fakes.push(fake);
    return {
      finished,
      cancel: () => {
        fake.cancelled = true;
        fail(new DOMException('cancelled', 'AbortError'));
      },
    } as unknown as Animation;
  });
  Element.prototype.animate = animate as unknown as Element['animate'];
  Element.prototype.getAnimations = function getAnimations(this: Element) {
    return fakes
      .filter((one) => one.element === this && !one.cancelled)
      .map((one) => ({ cancel: () => { one.cancelled = true; } })) as unknown as Animation[];
  };
  return { fakes, animate };
}

afterEach(() => {
  delete (Element.prototype as Partial<Element>).animate;
  delete (Element.prototype as Partial<Element>).getAnimations;
});

/** Панель со своим состоянием — чтобы видеть, что уходящая не пересоздана. */
function Counter({ name }: { name: string }) {
  const [n, setN] = useState(0);
  return (
    <button type="button" onClick={() => setN(n + 1)}>
      {name}: {n}
    </button>
  );
}

function Tabs({ active }: { active: string }) {
  return (
    <AnimatedTabContent
      activeKey={active}
      order={ORDER}
      label={(key) => `Вкладка ${key}`}
      render={(key) => <Counter name={key} />}
    />
  );
}

const stage = () => document.querySelector('.adp-stage') as HTMLElement;
const leaving = () => document.querySelectorAll('.adp-pane--leaving');

async function finishAll(fakes: Fake[]) {
  await act(async () => {
    for (const one of fakes) one.finish();
    await Promise.resolve();
  });
}

describe('переход вкладок', () => {
  test('вправо: старая уходит влево, новая приходит справа', async () => {
    const { fakes } = motionEnvironment();
    const { rerender } = render(<Tabs active="admins" />);
    rerender(<Tabs active="departments" />);

    expect(stage().dataset['direction']).toBe('forward');
    const out = leaving();
    expect(out).toHaveLength(1);
    expect(out[0]!.getAttribute('data-pane')).toBe('admins');
    // Уходящая не получает фокус и не читается вслух.
    expect(out[0]!.getAttribute('aria-hidden')).toBe('true');
    expect(out[0]!.hasAttribute('inert')).toBe(true);
    expect(screen.getByRole('tabpanel').getAttribute('aria-label')).toBe('Вкладка departments');

    const away = fakes.find((one) => one.element === out[0]);
    const into = fakes.find((one) => (one.element as HTMLElement).dataset['pane'] === 'departments');
    expect(String(away?.keyframes[1]?.transform)).toContain('-');
    expect(String(into?.keyframes[0]?.transform)).not.toContain('-');

    await finishAll(fakes);
    expect(leaving()).toHaveLength(0);
    expect(stage().dataset['direction']).toBeUndefined();
  });

  test('влево — наоборот', () => {
    const { fakes } = motionEnvironment();
    const { rerender } = render(<Tabs active="positions" />);
    rerender(<Tabs active="departments" />);

    expect(stage().dataset['direction']).toBe('backward');
    const out = leaving()[0]!;
    const away = fakes.find((one) => one.element === out);
    expect(String(away?.keyframes[1]?.transform)).not.toContain('-');
    const into = fakes.find((one) => (one.element as HTMLElement).dataset['pane'] === 'departments');
    expect(String(into?.keyframes[0]?.transform)).toContain('-');
  });

  test('через несколько вкладок — то же направление', () => {
    motionEnvironment();
    const { rerender } = render(<Tabs active="admins" />);
    rerender(<Tabs active="schedules" />);
    expect(stage().dataset['direction']).toBe('forward');

    rerender(<Tabs active="admins" />);
    expect(stage().dataset['direction']).toBe('backward');
  });

  test('повторное нажатие на ту же вкладку — без движения', () => {
    const { animate } = motionEnvironment();
    const { rerender } = render(<Tabs active="positions" />);
    rerender(<Tabs active="positions" />);
    expect(animate).not.toHaveBeenCalled();
    expect(leaving()).toHaveLength(0);
  });

  test('уходящая панель — тот же экземпляр: её состояние не сброшено', () => {
    motionEnvironment();
    const { rerender } = render(<Tabs active="admins" />);
    act(() => screen.getByRole('button', { name: 'admins: 0' }).click());
    rerender(<Tabs active="departments" />);
    expect(leaving()[0]!.textContent).toBe('admins: 1');
  });

  test('быстрые переключения: уходит одна панель, завершает последний', async () => {
    const { fakes } = motionEnvironment();
    const { rerender } = render(<Tabs active="admins" />);
    rerender(<Tabs active="departments" />);
    const first = [...fakes];
    rerender(<Tabs active="positions" />);

    // Прежний переход отменён, а не доигрывается поверх нового.
    expect(first.every((one) => one.cancelled || one.element.isConnected === false)).toBe(true);
    expect(leaving()).toHaveLength(1);
    expect(leaving()[0]!.getAttribute('data-pane')).toBe('departments');
    expect(screen.getByRole('tabpanel').getAttribute('aria-label')).toBe('Вкладка positions');

    // Запоздалое окончание первого перехода не убирает панель второго.
    await finishAll(first);
    expect(leaving()).toHaveLength(1);

    await finishAll(fakes.filter((one) => !first.includes(one)));
    expect(leaving()).toHaveLength(0);
    expect(document.querySelectorAll('.adp-pane')).toHaveLength(1);
  });

  test('просьба уменьшить движение — переключение мгновенное', () => {
    const { animate } = motionEnvironment({ reduce: true });
    const { rerender } = render(<Tabs active="admins" />);
    rerender(<Tabs active="departments" />);
    expect(animate).not.toHaveBeenCalled();
    expect(leaving()).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'departments: 0' })).toBeTruthy();
  });

  test('без Web Animations (как в jsdom) — тоже мгновенно и без второй панели', () => {
    const { rerender } = render(<Tabs active="admins" />);
    rerender(<Tabs active="schedules" />);
    expect(leaving()).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'schedules: 0' })).toBeTruthy();
  });
});

describe('раскрытие строки', () => {
  function Row() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" aria-expanded={open} aria-controls="more"
                onClick={() => setOpen(!open)}>
          Строка
        </button>
        <AnimatedCollapse open={open} id="more">
          <p>Подробности</p>
        </AnimatedCollapse>
      </>
    );
  }

  test('с движением: открытие сразу, закрытие — после сворачивания', () => {
    vi.useFakeTimers();
    try {
      motionEnvironment();
      render(<Row />);
      const toggle = screen.getByRole('button', { name: 'Строка' });
      const box = document.getElementById('more')!;

      act(() => toggle.click());
      expect(toggle.getAttribute('aria-expanded')).toBe('true');
      expect(box.classList.contains('adp-collapse--open')).toBe(true);
      expect(screen.getByText('Подробности')).toBeTruthy();

      act(() => toggle.click());
      expect(box.classList.contains('adp-collapse--open')).toBe(false);
      // Содержимое остаётся, пока сворачивается высота…
      expect(screen.getByText('Подробности')).toBeTruthy();
      act(() => { vi.advanceTimersByTime(400); });
      // …и уходит из документа после.
      expect(screen.queryByText('Подробности')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  test('быстро закрыть и снова открыть — строка остаётся открытой', () => {
    vi.useFakeTimers();
    try {
      motionEnvironment();
      render(<Row />);
      const toggle = screen.getByRole('button', { name: 'Строка' });
      act(() => toggle.click());
      act(() => toggle.click());
      act(() => toggle.click());
      act(() => { vi.advanceTimersByTime(400); });
      expect(screen.getByText('Подробности')).toBeTruthy();
      expect(document.getElementById('more')!.classList.contains('adp-collapse--open')).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  test('просьба уменьшить движение — закрытие мгновенное', () => {
    motionEnvironment({ reduce: true });
    render(<Row />);
    const toggle = screen.getByRole('button', { name: 'Строка' });
    act(() => toggle.click());
    expect(screen.getByText('Подробности')).toBeTruthy();
    act(() => toggle.click());
    expect(screen.queryByText('Подробности')).toBeNull();
  });
});
