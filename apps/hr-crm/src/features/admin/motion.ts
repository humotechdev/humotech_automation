/**
 * Движение в «Администрировании»: длительности, кривая и два правила.
 *
 * Значения собраны здесь, а не разбросаны по компонентам: вкладки,
 * строки и кнопки должны двигаться в одном темпе, иначе экран читается
 * как набор отдельных виджетов. Те же числа повторены в `admin.css`
 * переменными `--adp-*` — CSS-переходам JavaScript не нужен.
 *
 * Анимация здесь только подтверждает действие и показывает, откуда
 * пришло содержимое. Ничего не ждёт её окончания: данные, сохранение и
 * проверки работают одинаково и с анимацией, и без неё.
 */

/** Кривая всего раздела: быстрый старт, мягкая посадка. */
export const EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';

/** Смена вкладки: старое уходит, новое приходит одновременно. */
export const TAB_MS = 300;

/** Раскрытие строки и панели редактирования. */
export const ROW_MS = 260;

/** Насколько сдвигается содержимое при смене вкладки. */
export const TAB_SHIFT = '14%';

/**
 * Можно ли сейчас анимировать.
 *
 * Нет — если человек попросил систему уменьшить движение, и если среда
 * не умеет ни `matchMedia`, ни Web Animations (jsdom в тестах, старые
 * браузеры). В обоих случаях интерфейс переключается мгновенно, без
 * промежуточного состояния, которое некому было бы завершить.
 */
export function motionAllowed(): boolean {
  const scope = globalThis as { matchMedia?: Window['matchMedia'] };
  if (typeof scope.matchMedia !== 'function') return false;
  // Вызов через объект: оторванный от `window` метод браузер не примет.
  return !scope.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Есть ли Web Animations API — без него вкладки переключаются сразу. */
export function canAnimate(): boolean {
  return motionAllowed()
    && typeof Element !== 'undefined'
    && typeof Element.prototype.animate === 'function';
}

/**
 * Направление перехода между вкладками.
 *
 * 1 — вправо по ряду (старое уходит влево), -1 — влево, 0 — та же
 * вкладка или неизвестная: тогда движения нет вовсе. Через несколько
 * вкладок направление то же, что через одну: важен знак, а не шаг.
 */
export function tabDirection(order: readonly string[], from: string, to: string): -1 | 0 | 1 {
  const was = order.indexOf(from);
  const now = order.indexOf(to);
  if (was < 0 || now < 0 || was === now) return 0;
  return now > was ? 1 : -1;
}
