/**
 * Содержимое вкладок с переходом по направлению.
 *
 * Вкладка правее выбранной — прежнее содержимое уходит влево, новое
 * приходит справа; левее — наоборот. Оба движения идут одновременно, а
 * высота рамки плавно переходит от старой к новой: короткий список
 * после длинного не обрезает страницу рывком.
 *
 * Уходящая панель — тот же экземпляр компонента, что был на экране
 * (тот же `key`), поэтому она уходит ровно такой, какой её видели: с
 * раскрытой строкой и введённым поиском, без пустого кадра. На время
 * ухода она `inert` и скрыта от средств чтения — фокус и поиск по
 * странице попадают только в новую.
 *
 * Без движения (просьба системы уменьшить анимацию, нет Web Animations)
 * вкладка меняется мгновенно и уходящей панели нет вовсе.
 *
 * Быстрые переключения: каждое новое отменяет текущие анимации и
 * начинает своё; завершить переход может только самое последнее —
 * у старых номер поколения уже не совпадает.
 */

import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';

import { EASE, TAB_MS, TAB_SHIFT, canAnimate, tabDirection } from '../../features/admin/motion';

type Leaving = { key: string; dir: 1 | -1; gen: number };

export function AnimatedTabContent({ activeKey, order, label, render }: {
  activeKey: string;
  /** Порядок вкладок слева направо — по нему считается направление. */
  order: readonly string[];
  /** Подпись активной панели для средств чтения. */
  label: (key: string) => string;
  render: (key: string) => ReactNode;
}) {
  const [shown, setShown] = useState(activeKey);
  const [leaving, setLeaving] = useState<Leaving | null>(null);
  const generation = useRef(0);
  const stage = useRef<HTMLDivElement>(null);
  const panes = useRef(new Map<string, HTMLDivElement>());

  // Смена вкладки замечается во время отрисовки, а не эффектом после
  // неё: иначе был бы кадр, где новой панели уже нет старой, а
  // анимация ещё не началась, — то самое белое мигание.
  if (activeKey !== shown) {
    const dir = tabDirection(order, shown, activeKey);
    generation.current += 1;
    setShown(activeKey);
    setLeaving(dir !== 0 && canAnimate()
      ? { key: shown, dir, gen: generation.current }
      : null);
  }

  useLayoutEffect(() => {
    const box = stage.current;
    if (!leaving || !box) return;
    const out = panes.current.get(leaving.key);
    const inn = panes.current.get(shown);
    if (!out || !inn) return;

    // Прерванный переход не доигрывается: его место занимает новый.
    for (const node of [box, out, inn]) {
      for (const running of node.getAnimations?.() ?? []) running.cancel();
    }

    const timing = { duration: TAB_MS, easing: EASE };
    const away = leaving.dir > 0 ? `-${TAB_SHIFT}` : TAB_SHIFT;
    const from = leaving.dir > 0 ? TAB_SHIFT : `-${TAB_SHIFT}`;
    const before = out.offsetHeight;
    const after = inn.offsetHeight;

    const moves = [
      out.animate(
        [{ transform: 'translateX(0)', opacity: 1 },
         { transform: `translateX(${away})`, opacity: 0 }],
        { ...timing, fill: 'forwards' },
      ),
      inn.animate(
        [{ transform: `translateX(${from})`, opacity: 0 },
         { transform: 'translateX(0)', opacity: 1 }],
        timing,
      ),
    ];
    if (before !== after) {
      moves.push(box.animate(
        [{ height: `${before}px` }, { height: `${after}px` }],
        timing,
      ));
    }

    let live = true;
    const gen = leaving.gen;
    Promise.all(moves.map((one) => one.finished)).then(
      () => {
        if (live) setLeaving((was) => (was && was.gen === gen ? null : was));
      },
      // Отменённая анимация — не ошибка: её сменил следующий переход.
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [leaving, shown]);

  const keys = leaving ? [leaving.key, shown] : [shown];
  const direction = leaving ? (leaving.dir > 0 ? 'forward' : 'backward') : undefined;

  return (
    <div ref={stage}
         className={leaving ? 'adp-stage adp-stage--moving' : 'adp-stage'}
         data-direction={direction}>
      {keys.map((key) => {
        const away = leaving?.key === key;
        return (
          <div key={key}
               ref={(node) => {
                 if (node) panes.current.set(key, node);
                 else panes.current.delete(key);
               }}
               className={away ? 'adp-pane adp-pane--leaving' : 'adp-pane'}
               data-pane={key}
               {...(away
                 ? { 'aria-hidden': true, inert: true }
                 : { role: 'tabpanel', 'aria-label': label(key) })}>
            {render(key)}
          </div>
        );
      })}
    </div>
  );
}
