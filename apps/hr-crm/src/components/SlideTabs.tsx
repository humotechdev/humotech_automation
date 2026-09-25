/**
 * Вкладки одного экрана с одной общей линией под выбранной.
 *
 * Линия — один элемент, который переезжает и меняет ширину под название,
 * а не своя линия у каждой вкладки: так переход читается как смена места
 * внутри одного экрана, а не как новая страница. Первое положение
 * ставится без анимации — иначе при открытии раздела линия выезжала бы
 * из левого края.
 *
 * Оформление — у раздела: классы приходят снаружи, здесь только
 * поведение линии. Анимацию задаёт модификатор `…--move` у линии.
 */

import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';

export type SlideTab = { key: string; title: ReactNode; count?: number | null };

export function SlideTabs({ items, value, onPick, label, classes }: {
  items: SlideTab[];
  value: string;
  onPick: (key: string) => void;
  label: string;
  classes: { list: string; tab: string; on: string; count?: string; ink: string };
}) {
  const box = useRef<HTMLDivElement>(null);
  const [ink, setInk] = useState<{ left: number; width: number; ready: boolean }>({ left: 0, width: 0, ready: false });
  const shape = items.map((one) => `${one.key}:${one.count ?? ''}`).join('|');

  useLayoutEffect(() => {
    const frame = box.current;
    if (!frame) return;
    const place = () => {
      const active = frame.querySelector<HTMLElement>('[aria-selected="true"]');
      if (!active) return;
      setInk((was) => ({ left: active.offsetLeft, width: active.offsetWidth, ready: was.ready || was.width > 0 }));
    };
    place();
    // Ширина вкладки зависит от шрифта и числа рядом с названием.
    const watcher = new ResizeObserver(place);
    watcher.observe(frame);
    return () => watcher.disconnect();
  }, [value, shape]);

  return (
    <div className={classes.list} role="tablist" aria-label={label} ref={box}>
      {items.map((one) => (
        <button key={one.key} type="button" role="tab"
                aria-selected={value === one.key}
                className={value === one.key ? `${classes.tab} ${classes.on}` : classes.tab}
                onClick={() => { if (one.key !== value) onPick(one.key); }}>
          {one.title}
          {one.count !== undefined && one.count !== null && classes.count && (
            <span className={classes.count}>{one.count}</span>
          )}
        </button>
      ))}
      <span aria-hidden="true"
            className={ink.ready ? `${classes.ink} ${classes.ink}--move` : classes.ink}
            style={{ width: `${ink.width}px`, transform: `translateX(${ink.left}px)` }} />
    </div>
  );
}
