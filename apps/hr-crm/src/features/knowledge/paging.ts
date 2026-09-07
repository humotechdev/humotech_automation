/**
 * Дочитывание списка по курсору.
 *
 * Первая страница приходит вместе со счётчиками через `useBlock`; всё,
 * что дочитано кнопкой «Показать ещё», лежит здесь — вместе с ключом
 * набора, к которому относится.
 *
 * Ключ хранится ВНУТРИ состояния и сверяется при отрисовке. Это и есть
 * защита от главной ошибки такого накопителя: сменили вкладку или
 * фильтр — вторая страница прошлого набора не должна остаться под новым
 * списком. Ключ разошёлся — хвоста просто нет, и никакого промежуточного
 * кадра со старыми строками не возникает.
 */

import { useCallback, useState } from 'react';

import type { Cursored } from '../../api/crm';
import { messageFor } from '../../api/errors';

type Tail<T> = {
  key: string;
  items: T[];
  next: string | null;
  hasMore: boolean;
  busy: boolean;
  error: string | null;
};

export type Paged<T> = {
  /** Первая страница и всё дочитанное, в порядке прихода. */
  items: T[];
  hasMore: boolean;
  busy: boolean;
  error: string | null;
  loadMore: () => void;
};

export function usePaging<T>(
  key: string,
  first: Cursored<T> | null,
  load: (cursor: string) => Promise<Cursored<T>>,
): Paged<T> {
  const [tail, setTail] = useState<Tail<T> | null>(null);
  const live = tail && tail.key === key ? tail : null;

  const items = first ? [...first.items, ...(live?.items ?? [])] : [];
  const next = live ? live.next : (first?.next_cursor ?? null);
  const hasMore = live ? live.hasMore : (first?.has_more ?? false);

  const loadMore = useCallback(() => {
    if (!next || live?.busy) return;
    const carried = live?.items ?? [];
    setTail({ key, items: carried, next, hasMore: true, busy: true, error: null });
    load(next)
      .then((page) => {
        setTail({
          key,
          items: [...carried, ...page.items],
          next: page.next_cursor,
          hasMore: page.has_more,
          busy: false,
          error: null,
        });
      })
      .catch((error: unknown) => {
        // Уже прочитанное остаётся на месте: не дочитали — не значит
        // «списка нет».
        setTail({
          key, items: carried, next, hasMore: true, busy: false,
          error: messageFor(error),
        });
      });
  }, [key, next, live?.busy, live?.items, load]);

  return { items, hasMore, busy: live?.busy ?? false, error: live?.error ?? null, loadMore };
}
