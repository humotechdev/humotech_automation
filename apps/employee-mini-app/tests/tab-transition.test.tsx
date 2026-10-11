// @vitest-environment jsdom
/**
 * Горизонтальный переход между вкладками нижней панели.
 *
 * В jsdom нет ни `matchMedia`, ни `Element.animate`, поэтому оба
 * подставляются здесь: подделка анимации запоминает, что и откуда
 * поехало, и завершается вручную. Остальные наборы тестов их не
 * подставляют и видят прежнюю мгновенную смену разделов.
 */

import { act, cleanup, render } from '@testing-library/react';
import { useEffect } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Tab } from '../src/ui/BottomNavigation';
import { NAV_TABS } from '../src/ui/BottomNavigation';
import {
  TAB_TRANSITION_MS,
  TabTransition,
  motionAllowed,
  tabDirection,
} from '../src/ui/TabTransition';

class FakeAnimation {
  onfinish: (() => void) | null = null;
  cancelled = false;
  cancel() {
    this.cancelled = true;
  }
  /** То, что сделал бы браузер в конце анимации. */
  end() {
    this.onfinish?.();
  }
}

interface Call {
  el: HTMLElement;
  keyframes: Keyframe[];
  options: KeyframeAnimationOptions;
  animation: FakeAnimation;
}

let calls: Call[] = [];
let reduce = false;

function stubMotion() {
  calls = [];
  reduce = false;
  Object.defineProperty(Element.prototype, 'animate', {
    configurable: true,
    writable: true,
    value: function animate(
      this: HTMLElement,
      keyframes: Keyframe[],
      options: KeyframeAnimationOptions,
    ) {
      const animation = new FakeAnimation();
      calls.push({ el: this, keyframes, options, animation });
      return animation as unknown as Animation;
    },
  });
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('reduce') ? reduce : false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

beforeEach(stubMotion);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete (Element.prototype as { animate?: unknown }).animate;
});

const page = (tab: Tab) => <p>{`page:${tab}`}</p>;

function layers(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('.tab-layer'));
}

function layerOf(container: HTMLElement, tab: Tab): HTMLElement | null {
  return container.querySelector<HTMLElement>(`.tab-layer[data-tab="${tab}"]`);
}

/** Последняя пара анимаций: входящий и уходящий слой. */
function lastPair(): { enter: Call; exit: Call } {
  const enter = calls[calls.length - 2];
  const exit = calls[calls.length - 1];
  return { enter, exit };
}

describe('направление перехода', () => {
  it('порядок вкладок — как на панели', () => {
    expect(NAV_TABS).toEqual(['home', 'history', 'requests', 'profile']);
  });

  it('вкладка правее — вперёд, левее — назад', () => {
    expect(tabDirection('home', 'history')).toBe('forward');
    expect(tabDirection('requests', 'history')).toBe('back');
  });

  it('прыжок через несколько вкладок — по той же стороне', () => {
    expect(tabDirection('home', 'profile')).toBe('forward');
    expect(tabDirection('profile', 'home')).toBe('back');
  });

  it('та же вкладка — перехода нет', () => {
    expect(tabDirection('history', 'history')).toBeNull();
  });

  it('вложенные экраны — без перехода', () => {
    expect(tabDirection('home', 'scan')).toBeNull();
    expect(tabDirection('stats', 'home')).toBeNull();
    expect(tabDirection('notes', 'profile')).toBeNull();
  });
});

describe('TabTransition', () => {
  it('вкладка справа въезжает справа, старая уезжает влево, обе сразу', () => {
    const { container, rerender } = render(
      <TabTransition tab="home" render={page} />,
    );
    expect(layers(container)).toHaveLength(1);

    rerender(<TabTransition tab="history" render={page} />);

    expect(layers(container)).toHaveLength(2);
    const incoming = layerOf(container, 'history')!;
    const outgoing = layerOf(container, 'home')!;
    expect(incoming.className).toContain('tab-layer-entering');
    expect(outgoing.className).toContain('tab-layer-leaving');
    // Уходящий раздел нельзя нажать и не слышно диктору.
    expect(outgoing.getAttribute('aria-hidden')).toBe('true');
    expect(outgoing.hasAttribute('inert')).toBe(true);
    expect(incoming.hasAttribute('aria-hidden')).toBe(false);

    expect(calls).toHaveLength(2);
    const { enter, exit } = lastPair();
    expect(enter.el).toBe(incoming);
    expect(enter.keyframes[0].transform).toBe('translate3d(100%, 0, 0)');
    expect(enter.keyframes[1].transform).toBe('translate3d(0, 0, 0)');
    expect(exit.el).toBe(outgoing);
    expect(exit.keyframes[1].transform).toBe('translate3d(-100%, 0, 0)');
    expect(enter.options.duration).toBe(TAB_TRANSITION_MS);
    expect(exit.options.duration).toBe(TAB_TRANSITION_MS);
    expect(TAB_TRANSITION_MS).toBeGreaterThanOrEqual(250);
    expect(TAB_TRANSITION_MS).toBeLessThanOrEqual(350);

    act(() => enter.animation.end());

    expect(layers(container)).toHaveLength(1);
    expect(container.textContent).toBe('page:history');
    expect(layerOf(container, 'history')!.className).toBe('tab-layer');
  });

  it('вкладка слева въезжает слева, старая уезжает вправо', () => {
    const { rerender } = render(<TabTransition tab="profile" render={page} />);
    rerender(<TabTransition tab="home" render={page} />);

    const { enter, exit } = lastPair();
    expect(enter.keyframes[0].transform).toBe('translate3d(-100%, 0, 0)');
    expect(exit.keyframes[1].transform).toBe('translate3d(100%, 0, 0)');
  });

  it('повторное нажатие на текущую вкладку ничего не двигает', () => {
    const { container, rerender } = render(
      <TabTransition tab="requests" render={page} />,
    );
    rerender(<TabTransition tab="requests" render={page} />);

    expect(calls).toHaveLength(0);
    expect(layers(container)).toHaveLength(1);
  });

  it('быстрые нажатия: не больше двух слоёв, побеждает последнее', () => {
    const { container, rerender } = render(
      <TabTransition tab="home" render={page} />,
    );

    rerender(<TabTransition tab="history" render={page} />);
    const first = lastPair();
    rerender(<TabTransition tab="requests" render={page} />);
    expect(layers(container)).toHaveLength(2);
    rerender(<TabTransition tab="profile" render={page} />);
    expect(layers(container)).toHaveLength(2);

    // Прошлые анимации отменены, а не доигрывают поверх новой.
    expect(first.enter.animation.cancelled).toBe(true);
    expect(first.exit.animation.cancelled).toBe(true);

    // Опоздавший конец первой анимации чужой и ничего не ломает.
    act(() => first.enter.animation.end());
    expect(layers(container)).toHaveLength(2);

    act(() => lastPair().enter.animation.end());
    expect(layers(container)).toHaveLength(1);
    expect(container.textContent).toBe('page:profile');
  });

  it('уходящий слой, едущий из середины, стартует из текущей точки', () => {
    const { rerender } = render(<TabTransition tab="home" render={page} />);
    rerender(<TabTransition tab="history" render={page} />);
    rerender(<TabTransition tab="requests" render={page} />);

    // history был входящим и уже ехал: новый старт — не от края экрана,
    // а с того места, где он сейчас (в jsdom — нулевой сдвиг).
    const { enter, exit } = lastPair();
    expect(enter.keyframes[0].transform).toBe('translate3d(100%, 0, 0)');
    expect(exit.keyframes[0].transform).toBe('translate3d(0, 0, 0)');
  });

  it('возврат на уезжающую вкладку не перемонтирует её', () => {
    let mounts = 0;
    function HomePage() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <p>home</p>;
    }
    const renderPage = (tab: Tab) =>
      tab === 'home' ? <HomePage /> : page(tab);

    const { container, rerender } = render(
      <TabTransition tab="home" render={renderPage} />,
    );
    rerender(<TabTransition tab="history" render={renderPage} />);
    rerender(<TabTransition tab="home" render={renderPage} />);
    act(() => lastPair().enter.animation.end());

    expect(mounts).toBe(1);
    expect(container.textContent).toBe('home');
  });

  it('входящий раздел не перемонтируется в конце перехода', () => {
    let mounts = 0;
    function HistoryPage() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <p>history</p>;
    }
    const renderPage = (tab: Tab) =>
      tab === 'history' ? <HistoryPage /> : page(tab);

    const { rerender } = render(
      <TabTransition tab="home" render={renderPage} />,
    );
    rerender(<TabTransition tab="history" render={renderPage} />);
    act(() => lastPair().enter.animation.end());

    expect(mounts).toBe(1);
  });

  it('prefers-reduced-motion: смена мгновенная', () => {
    reduce = true;
    expect(motionAllowed()).toBe(false);

    const { container, rerender } = render(
      <TabTransition tab="home" render={page} />,
    );
    rerender(<TabTransition tab="profile" render={page} />);

    expect(calls).toHaveLength(0);
    expect(layers(container)).toHaveLength(1);
    expect(container.textContent).toBe('page:profile');
  });

  it('без Element.animate (старый вебвью) — мгновенно', () => {
    delete (Element.prototype as { animate?: unknown }).animate;
    expect(motionAllowed()).toBe(false);

    const { container, rerender } = render(
      <TabTransition tab="home" render={page} />,
    );
    rerender(<TabTransition tab="history" render={page} />);
    expect(layers(container)).toHaveLength(1);
  });

  it('вложенный экран открывается без перехода', () => {
    const { container, rerender } = render(
      <TabTransition tab="home" render={page} />,
    );
    rerender(<TabTransition tab="scan" render={page} />);

    expect(calls).toHaveLength(0);
    expect(layers(container)).toHaveLength(1);
    expect(container.textContent).toBe('page:scan');
  });

  it('открытая шторка — без перехода: transform сдвинул бы её с экрана', () => {
    const renderPage = (tab: Tab) =>
      tab === 'requests' ? <div className="overlay">form</div> : page(tab);

    const { container, rerender } = render(
      <TabTransition tab="home" render={renderPage} />,
    );
    rerender(<TabTransition tab="requests" render={renderPage} />);

    expect(calls).toHaveLength(0);
    expect(layers(container)).toHaveLength(1);
  });

  it('страховочный таймер убирает слой, если конец анимации не пришёл', () => {
    vi.useFakeTimers();
    const { container, rerender } = render(
      <TabTransition tab="home" render={page} />,
    );
    rerender(<TabTransition tab="history" render={page} />);
    expect(layers(container)).toHaveLength(2);

    act(() => {
      vi.advanceTimersByTime(TAB_TRANSITION_MS + 200);
    });
    expect(layers(container)).toHaveLength(1);
  });
});
