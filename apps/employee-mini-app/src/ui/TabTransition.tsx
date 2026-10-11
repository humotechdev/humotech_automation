/**
 * Горизонтальный переход между вкладками нижней панели.
 *
 * Вкладка правее — новый раздел въезжает справа, старый уезжает влево;
 * левее — наоборот. Оба едут одновременно, как родное перелистывание.
 *
 * Как устроено:
 *
 * - Каждый раздел живёт в своём слое с ключом-вкладкой. На время
 *   перехода слоёв два: уходящий остаётся в потоке и держит высоту
 *   страницы (прокрутка не прыгает), входящий лежит поверх него.
 *   После перехода уходящий слой размонтируется — входящий при этом
 *   не перемонтируется, потому что ключи и родитель те же.
 * - Слои всегда стоят в порядке панели, поэтому React их не
 *   переставляет: перенос узла сбросил бы фокус и CSS-анимации внутри.
 * - Анимация — Web Animations API и только `transform`. Ни классов с
 *   `animation-fill-mode`, ни постоянного `will-change`: transform на
 *   предке делает его опорой для `position: fixed`, и шторка заявки
 *   прилипла бы к слою, а не к экрану. Поэтому после перехода на слоях
 *   не остаётся ничего, а при открытой шторке перехода нет вовсе.
 * - Быстрые нажатия: новый переход стартует из того места, где слой
 *   находится сейчас, прошлый уходящий слой убирается сразу. Слоёв
 *   никогда не больше двух.
 * - Перехода нет: при повторном нажатии на текущую вкладку, между
 *   вкладкой и вложенным экраном (сканер, статистика, уведомления), при
 *   `prefers-reduced-motion: reduce` и там, где нет `matchMedia` или
 *   `Element.animate` (старый вебвью, jsdom). Там смена мгновенная —
 *   ровно как до появления перехода.
 */

import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';

import { NAV_TABS, type Tab } from './BottomNavigation';

/** Длительность перехода, мс. Подбирать в пределах 250–350. */
export const TAB_TRANSITION_MS = 300;

/** Плавное замедление к концу — как у родного перелистывания. */
export const TAB_TRANSITION_EASING = 'cubic-bezier(0.22, 0.61, 0.36, 1)';

/**
 * Запас к длительности для страховочного таймера: если `finish` не
 * придёт (вебвью ушёл в фон и заморозил анимации), слой всё равно
 * будет убран.
 */
const SAFETY_MS = 150;

export type TabDirection = 'forward' | 'back';

/**
 * Куда ехать при смене вкладки. null — перехода нет: та же вкладка или
 * хотя бы одна сторона не из нижней панели (вложенный экран).
 */
export function tabDirection(from: Tab, to: Tab): TabDirection | null {
  if (from === to) return null;
  const a = NAV_TABS.indexOf(from);
  const b = NAV_TABS.indexOf(to);
  if (a < 0 || b < 0) return null;
  return b > a ? 'forward' : 'back';
}

/** Можно ли двигать: есть чем и человек не просил обходиться без движения. */
export function motionAllowed(): boolean {
  if (typeof window === 'undefined') return false;
  if (typeof window.matchMedia !== 'function') return false;
  if (
    typeof Element === 'undefined' ||
    typeof Element.prototype.animate !== 'function'
  ) {
    return false;
  }
  return !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

interface Stage {
  current: Tab;
  leaving: Tab | null;
  direction: TabDirection | null;
  /** Номер смены вкладки: по нему опоздавший `finish` узнаёт, что он чужой. */
  seq: number;
}

function navIndex(tab: Tab): number {
  return NAV_TABS.indexOf(tab);
}

/** Где слой сейчас — с учётом идущей анимации. */
function currentTransform(el: HTMLElement): string {
  const value = getComputedStyle(el).transform;
  return value && value !== 'none' ? value : 'translate3d(0, 0, 0)';
}

/** Ближайший прокручиваемый предок; по умолчанию — сам документ. */
function scrollerOf(el: HTMLElement): Element {
  for (let node = el.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (
      (overflowY === 'auto' || overflowY === 'scroll') &&
      node.scrollHeight > node.clientHeight
    ) {
      return node;
    }
  }
  return document.scrollingElement ?? document.documentElement;
}

export function TabTransition({
  tab,
  render,
}: {
  tab: Tab;
  /** Содержимое раздела. Вызывается для каждого видимого слоя. */
  render: (tab: Tab) => ReactNode;
}) {
  const [stage, setStage] = useState<Stage>({
    current: tab,
    leaving: null,
    direction: null,
    seq: 0,
  });

  // Смена вкладки разбирается прямо в отрисовке, а не в эффекте: так
  // оба слоя попадают уже в первый же коммит и пустого кадра нет.
  let view = stage;
  if (tab !== stage.current) {
    const direction = tabDirection(stage.current, tab);
    view =
      direction && motionAllowed()
        ? { current: tab, leaving: stage.current, direction, seq: stage.seq + 1 }
        : { current: tab, leaving: null, direction: null, seq: stage.seq + 1 };
    setStage(view);
  }

  const stageRef = useRef<HTMLDivElement>(null);
  const nodes = useRef(new Map<Tab, HTMLDivElement>());
  const running = useRef(new Map<HTMLElement, Animation>());

  useLayoutEffect(() => {
    const host = stageRef.current;
    const { current, leaving, direction, seq } = view;

    const incoming = nodes.current.get(current);
    const outgoing = leaving ? nodes.current.get(leaving) : undefined;

    // Откуда стартовать, читается ДО отмены прошлой анимации: иначе
    // слой на кадр прыгнул бы на место.
    // Слой, который только что смонтирован, стартует от края экрана,
    // а уже едущий — из своей текущей точки.
    const fromIn =
      incoming && running.current.has(incoming)
        ? currentTransform(incoming)
        : null;
    const fromOut =
      outgoing && running.current.has(outgoing)
        ? currentTransform(outgoing)
        : 'translate3d(0, 0, 0)';

    running.current.forEach((animation) => animation.cancel());
    running.current.clear();
    // Геометрия прошлого перехода больше не нужна: уходящий слой снова
    // в потоке, а высоту держит он сам.
    if (host) host.style.minHeight = '';
    nodes.current.forEach((node) => {
      node.style.top = '';
    });

    if (!leaving || !direction) return;

    const finish = () =>
      setStage((now) =>
        now.seq === seq ? { ...now, leaving: null, direction: null } : now,
      );

    if (
      !host ||
      !incoming ||
      !outgoing ||
      typeof incoming.animate !== 'function' ||
      // Открытая шторка или диалог — `position: fixed`, и под transform
      // они поехали бы вместе со слоем. Такой переход — мгновенный.
      incoming.querySelector('.overlay') ||
      outgoing.querySelector('.overlay')
    ) {
      finish();
      return;
    }

    /*
     * Высота. Входящий выше уходящего — растягиваем сцену, чтобы его низ
     * не обрезался (рост страницы прокрутку не трогает). Входящий ниже —
     * в конце страница укоротится и браузер подвинет прокрутку; заранее
     * сдвигаем входящий слой на ту же величину, тогда в конце он
     * останется на месте.
     */
    const inHeight = incoming.offsetHeight;
    const outHeight = outgoing.offsetHeight;
    if (inHeight > outHeight) host.style.minHeight = `${inHeight}px`;
    const shrink = outHeight - inHeight;
    if (shrink > 0) {
      const scroller = scrollerOf(host);
      const room =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      const shift = Math.max(0, shrink - room);
      if (shift > 0) incoming.style.top = `${shift}px`;
    }

    const sign = direction === 'forward' ? 1 : -1;
    const timing: KeyframeAnimationOptions = {
      duration: TAB_TRANSITION_MS,
      easing: TAB_TRANSITION_EASING,
    };
    const enter = incoming.animate(
      [
        { transform: fromIn ?? `translate3d(${sign * 100}%, 0, 0)` },
        { transform: 'translate3d(0, 0, 0)' },
      ],
      timing,
    );
    const exit = outgoing.animate(
      [
        { transform: fromOut },
        { transform: `translate3d(${-sign * 100}%, 0, 0)` },
      ],
      // Уходящий слой должен остаться за краем до размонтирования,
      // иначе на кадр между концом анимации и коммитом он вернулся бы.
      { ...timing, fill: 'forwards' },
    );
    running.current.set(incoming, enter);
    running.current.set(outgoing, exit);
    enter.onfinish = finish;
    const safety = window.setTimeout(finish, TAB_TRANSITION_MS + SAFETY_MS);

    return () => {
      window.clearTimeout(safety);
      enter.onfinish = null;
    };
    // Переход запускается только сменой вкладки; объект `view` новый на
    // каждой отрисовке, но смысл его меняется вместе с `seq` и `leaving`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.seq, view.leaving]);

  // Размонтирование: ничего не должно доиграть по уже снятым узлам.
  useLayoutEffect(
    () => () => {
      running.current.forEach((animation) => animation.cancel());
      running.current.clear();
    },
    [],
  );

  const layers = (view.leaving ? [view.leaving, view.current] : [view.current])
    .slice()
    .sort((a, b) => navIndex(a) - navIndex(b));

  return (
    <div className="tab-stage" ref={stageRef}>
      {layers.map((key) => {
        const isLeaving = key === view.leaving;
        const isEntering = view.leaving !== null && key === view.current;
        return (
          <div
            key={key}
            ref={(node) => {
              if (node) nodes.current.set(key, node);
              else nodes.current.delete(key);
            }}
            className={
              'tab-layer' +
              (isEntering ? ' tab-layer-entering' : '') +
              (isLeaving ? ' tab-layer-leaving' : '')
            }
            data-tab={key}
            aria-hidden={isLeaving || undefined}
            inert={isLeaving || undefined}
          >
            {render(key)}
          </div>
        );
      })}
    </div>
  );
}
