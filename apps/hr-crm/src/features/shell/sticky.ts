/**
 * Состояние страницы, которое переживает уход из раздела.
 *
 * Большая часть состояния CRM лежит в адресе, и его сохраняет меню
 * (`places.ts`). Но у некоторых страниц отбор хранится в самом
 * компоненте — на главной это регион, офис, дата и период графика. При
 * переходе в другой раздел компонент размонтируется, и без этой памяти
 * человек, вернувшись, видел бы всё сброшенным.
 *
 * Хранится в памяти вкладки, не в `localStorage`: вчерашний отбор,
 * всплывший на следующий день, запутывает сильнее, чем пустой.
 */

import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';

const store = new Map<string, unknown>();

/** Как `useState`, но значение сохраняется под именем `name` между заходами. */
export function useStickyState<T>(name: string, initial: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    if (store.has(name)) return store.get(name) as T;
    return typeof initial === 'function' ? (initial as () => T)() : initial;
  });

  useEffect(() => {
    store.set(name, value);
  }, [name, value]);

  return [value, setValue];
}

export function forgetSticky(): void {
  store.clear();
}
