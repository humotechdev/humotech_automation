/**
 * История выгрузок и её обновление.
 *
 * Механизм живого списка общий на всю CRM и лежит в `features/live/feed`:
 * опрос, пауза в скрытой вкладке, отступление после ошибки, сохранение
 * показанных строк при неудачном обновлении. Здесь остаётся только то,
 * что своё у выгрузок: чем грузить и чего ждать.
 *
 * Само формирование живёт на сервере и продолжается после ухода со
 * страницы, закрытия вкладки и повторного входа. Опрос — это способ
 * узнать о нём, а не то, что его двигает.
 */

import { useCallback } from 'react';

import * as api from '../../api/crm';
import { useFeed, type Feed, type Live as FeedLive } from '../live/feed';

export type History = Feed<api.ExportJob, api.ExportCounts>;
export type Live = FeedLive<api.ExportJob, api.ExportCounts>;

const PENDING = new Set(['QUEUED', 'RUNNING']);

export const pending = (items: api.ExportJob[]): boolean =>
  items.some((job) => PENDING.has(job.status));

type Options = {
  status: string;
  mineOnly: boolean;
  onFresh: () => void;
};

export function useHistory({ status, mineOnly, onFresh }: Options) {
  const params = useCallback(
    () => ({
      ...(status ? { status } : {}),
      ...(mineOnly ? {} : { mine_only: 'false' }),
      limit: '20',
    }),
    [status, mineOnly],
  );

  /** Один запрос списка и счётчиков. Счётчики — без фильтра вкладки. */
  const load = useCallback(
    async (signal: AbortSignal): Promise<History> => {
      const [page, counts] = await Promise.all([
        api.exportJobs(params(), signal),
        api.exportCounts(mineOnly ? {} : { mine_only: 'false' }, signal),
      ]);
      return {
        items: page.items,
        counts,
        next: page.next_cursor,
        hasMore: page.has_more,
      };
    },
    [params, mineOnly],
  );

  const more = useCallback(
    (cursor: string) => api.exportJobs({ ...params(), cursor }),
    [params],
  );

  // Смена вкладки или фильтра автора — это ДРУГОЙ набор, и его законно
  // показать заново с состоянием загрузки. Обычное обновление идёт
  // через `refresh` и таблицу не гасит.
  return useFeed<api.ExportJob, api.ExportCounts>({
    key: `${status}|${mineOnly}`,
    load,
    more,
    waiting: pending,
    onFresh,
  });
}
