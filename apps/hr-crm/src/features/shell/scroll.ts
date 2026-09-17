/**
 * Позиции прокрутки внутри страниц.
 *
 * Оболочка теперь не пересоздаётся при переходе, а страница —
 * пересоздаётся: у списка заявок, таблицы посещаемости и колонок
 * главной свои области прокрутки, и новый их экземпляр начинается с
 * нуля. Вернувшись в раздел, человек терял место, до которого долистал.
 *
 * Память работает без участия страниц. Оболочка слушает прокрутку всех
 * областей внутри рабочей зоны (событие `scroll` не всплывает, но
 * доходит до предка на стадии перехвата) и запоминает положение под
 * адресом страницы и «путём» области от корня. На возврате положение
 * ставится обратно — как только у области появляется столько
 * содержимого, чтобы до него можно было долистать.
 *
 * Путь строится из тега, первого класса и порядкового номера среди
 * соседей с тем же тегом и классом. Первый класс — потому что остальные
 * меняются от состояния (`rq-grid--open`), а базовый — нет.
 */

import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';

const positions = new Map<string, Map<string, number>>();

/** Сколько кадров ждать, пока у области появится содержимое. */
const PATIENCE = 90;

export function forgetScroll(): void {
  positions.clear();
}

function stepOf(node: Element): string {
  const parent = node.parentElement;
  const mark = node.classList[0] ?? '';
  const index = parent
    ? Array.from(parent.children)
        .filter((one) => one.tagName === node.tagName && (one.classList[0] ?? '') === mark)
        .indexOf(node)
    : 0;
  return `${node.tagName}.${mark}#${index}`;
}

function pathOf(target: Element, root: Element): string | null {
  const steps: string[] = [];
  let node: Element | null = target;
  while (node && node !== root) {
    steps.push(stepOf(node));
    node = node.parentElement;
  }
  return node === root ? steps.reverse().join('>') : null;
}

function find(root: Element, path: string): Element | null {
  if (path === '') return root;
  let node: Element | null = root;
  for (const step of path.split('>')) {
    if (!node) return null;
    const match: RegExpExecArray | null = /^([A-Z0-9-]+)\.([^#]*)#(\d+)$/i.exec(step);
    if (!match) return null;
    const [, tag, mark, index] = match;
    const same: Element[] = Array.from(node.children).filter(
      (one) => one.tagName === tag && (one.classList[0] ?? '') === mark,
    );
    node = same[Number(index)] ?? null;
  }
  return node;
}

/**
 * Запоминать и восстанавливать прокрутку внутри `root` для страницы `page`.
 *
 * Сам `root` — тоже область прокрутки: у страниц, которые не занимают
 * ровно высоту окна, прокручивается рабочая зона целиком. Для страницы,
 * где человек ещё не был, она встаёт в начало: иначе новый раздел
 * открывался бы на высоте, до которой долистали предыдущий.
 */
export function useScrollMemory(root: RefObject<HTMLElement | null>, page: string, enabled = true): void {
  const current = useRef(page);

  useEffect(() => {
    const box = root.current;
    if (!box || !enabled) return;
    const onScroll = (event: Event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const path = pathOf(target, box);
      if (path === null) return;
      let saved = positions.get(current.current);
      if (!saved) {
        saved = new Map();
        positions.set(current.current, saved);
      }
      saved.set(path, target.scrollTop);
    };
    box.addEventListener('scroll', onScroll, { capture: true, passive: true });
    return () => box.removeEventListener('scroll', onScroll, { capture: true });
  }, [root, enabled]);

  useLayoutEffect(() => {
    current.current = page;
    const box = root.current;
    if (!box || !enabled) return;

    const wanted = new Map(positions.get(page) ?? []);
    if (!wanted.has('')) wanted.set('', 0);

    // Человек начал листать сам — его выбор важнее восстановления.
    let stopped = false;
    const stop = () => {
      stopped = true;
    };
    const events = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const;
    for (const name of events) window.addEventListener(name, stop, { capture: true, passive: true });

    let frame = 0;
    let tries = 0;
    const apply = () => {
      if (stopped) return;
      for (const [path, top] of wanted) {
        const area = find(box, path);
        if (!area) continue;
        if (Math.abs(area.scrollTop - top) <= 1) {
          wanted.delete(path);
          continue;
        }
        // Долистать можно, только когда содержимое уже выросло.
        if (area.scrollHeight - area.clientHeight >= top - 1) {
          area.scrollTop = top;
          wanted.delete(path);
        }
      }
      tries += 1;
      if (wanted.size > 0 && tries < PATIENCE) frame = requestAnimationFrame(apply);
    };
    apply();

    return () => {
      cancelAnimationFrame(frame);
      for (const name of events) window.removeEventListener(name, stop, { capture: true });
    };
  }, [root, page, enabled]);
}
