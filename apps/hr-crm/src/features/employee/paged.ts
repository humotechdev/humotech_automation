/**
 * Постраничный список с «Показать ещё».
 *
 * Фильтр сужает выборку на СЕРВЕРЕ, а не в загруженной странице: иначе
 * «заявок нет» означало бы «нет на первой странице». Следующая страница
 * берётся по курсору того же запроса и дописывается в конец.
 *
 * Смена ключа (сотрудник, фильтр) отменяет и первую страницу, и
 * догружаемую: ответ прошлого фильтра не попадает в новый список.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiFailure } from '../../api/errors';
import type { Cursored } from '../../api/crm';

export type Paged<T> = {
  state: 'loading' | 'ready' | 'denied' | 'error' | 'missing';
  items: T[];
  hasMore: boolean;
  /** Идёт догрузка следующей страницы. */
  more: boolean;
  /** Догрузка не удалась — первые строки остаются на экране. */
  moreFailed: boolean;
};

const START = {
  state: 'loading' as const, items: [], hasMore: false, more: false, moreFailed: false,
};

export function usePaged<T>(
  load: (cursor: string | null, signal: AbortSignal) => Promise<Cursored<T>>,
  key: string,
  enabled = true,
): [Paged<T>, { reload: () => void; loadMore: () => void }] {
  const [page, setPage] = useState<Paged<T>>(START);
  const [attempt, setAttempt] = useState(0);
  const cursor = useRef<string | null>(null);
  const live = useRef<AbortController | null>(null);
  const loader = useRef(load);
  loader.current = load;
  const shown = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) return undefined;
    live.current?.abort();
    const stop = new AbortController();
    live.current = stop;
    cursor.current = null;
    // Повтор того же списка оставляет строки на экране, пока идёт ответ;
    // другой список (сотрудник, фильтр) начинается с чистого листа —
    // строки прошлого фильтра в новом контексте не показываются.
    const same = shown.current === key;
    shown.current = key;
    setPage((was) => (same && was.state === 'ready' ? { ...was, more: false } : START));
    loader.current(null, stop.signal)
      .then((data) => {
        if (stop.signal.aborted) return;
        cursor.current = data.next_cursor;
        setPage({
          state: 'ready', items: data.items, hasMore: data.has_more,
          more: false, moreFailed: false,
        });
      })
      .catch((error: unknown) => {
        if (stop.signal.aborted) return;
        setPage({ ...START, state: stateOf(error) });
      });
    return () => stop.abort();
  }, [key, attempt, enabled]);

  const loadMore = useCallback(() => {
    const stop = live.current;
    const next = cursor.current;
    if (!stop || stop.signal.aborted || !next) return;
    setPage((was) => (was.more ? was : { ...was, more: true, moreFailed: false }));
    loader.current(next, stop.signal)
      .then((data) => {
        if (stop.signal.aborted) return;
        cursor.current = data.next_cursor;
        setPage((was) => ({
          ...was,
          // Строка, уже показанная выше, второй раз не добавляется: между
          // страницами запись могла сдвинуться.
          items: [...was.items, ...data.items.filter(
            (one) => !was.items.some((seen) => idOf(seen) === idOf(one)),
          )],
          hasMore: data.has_more,
          more: false,
        }));
      })
      .catch(() => {
        if (stop.signal.aborted) return;
        setPage((was) => ({ ...was, more: false, moreFailed: true }));
      });
  }, []);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return [page, { reload, loadMore }];
}

function idOf(row: unknown): unknown {
  return row && typeof row === 'object' ? (row as { id?: unknown }).id : row;
}

/** 403 — нет права, 404 — нет такого (или вне области), прочее — сбой. */
export function stateOf(error: unknown): 'denied' | 'missing' | 'error' {
  if (error instanceof ApiFailure) {
    if (error.status === 403) return 'denied';
    if (error.status === 404) return 'missing';
  }
  return 'error';
}
