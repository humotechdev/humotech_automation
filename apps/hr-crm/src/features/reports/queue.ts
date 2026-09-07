/**
 * История выгрузок и её обновление.
 *
 * Один таймер на всю страницу, а не по таймеру на строку. Десять
 * незавершённых заданий не должны означать десять запросов в секунду:
 * список и так приходит целиком, и состояние каждой строки — в нём.
 *
 * Правила, которые здесь закреплены:
 *
 * — опрос идёт, ТОЛЬКО пока в списке есть незавершённое задание.
 *   Готовая история не опрашивается вовсе;
 * — в скрытой вкладке опрос останавливается, при возвращении делается
 *   немедленный запрос: человек вернулся и хочет увидеть текущее, а не
 *   подождать ещё пять секунд;
 * — сетевая ошибка не гасит уже загруженные строки. Она добавляет
 *   пометку «данные не обновились» и увеличивает паузу; пустая таблица
 *   на месте ошибки — это ложь про отсутствие выгрузок;
 * — время «обновлено в» двигается только после УДАЧНОГО ответа.
 *
 * Само формирование живёт на сервере и продолжается после ухода со
 * страницы, закрытия вкладки и повторного входа. Опрос — это способ
 * узнать о нём, а не то, что его двигает.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import * as api from '../../api/crm';
import { ApiFailure } from '../../api/errors';

/** Пауза между опросами, пока всё идёт хорошо. Воркер просыпается раз в 5 с. */
const TICK = 5000;
/** После ошибки пауза удваивается — до этого потолка. */
const MAX_TICK = 40_000;

export type History = {
  items: api.ExportJob[];
  counts: api.ExportCounts | null;
  next: string | null;
  hasMore: boolean;
};

export type Live =
  | { state: 'loading' }
  | { state: 'denied' }
  | { state: 'error'; kind: string }
  | { state: 'ready'; data: History; stale: boolean };

const PENDING = new Set(['QUEUED', 'RUNNING']);

export const pending = (items: api.ExportJob[]): boolean =>
  items.some((job) => PENDING.has(job.status));

type Options = {
  status: string;
  mineOnly: boolean;
  onFresh: () => void;
};

export function useHistory({ status, mineOnly, onFresh }: Options) {
  const [live, setLive] = useState<Live>({ state: 'loading' });
  const [more, setMore] = useState<{ busy: boolean; kind: string | null }>({
    busy: false,
    kind: null,
  });
  const stop = useRef<AbortController | null>(null);
  const timer = useRef<number | null>(null);
  // Способ перезапросить список из обработчика действия: отмена и
  // повтор меняют не только строку, но и счётчики вкладок, а повтор
  // ещё и возвращает задание в очередь — опрос должен ожить.
  const runner = useRef<((first: boolean) => Promise<void>) | null>(null);
  const pause = useRef(TICK);
  const fresh = useRef(onFresh);
  fresh.current = onFresh;

  // Смена вкладки или фильтра автора — это ДРУГОЙ набор, и его
  // законно показать заново с состоянием загрузки. Обычное
  // обновление идёт через `refresh` и таблицу не гасит.
  const key = `${status}|${mineOnly}`;

  /** Один запрос списка и счётчиков. Счётчики — без фильтра вкладки. */
  const fetchPage = useCallback(
    async (signal: AbortSignal): Promise<History> => {
      const params = {
        ...(status ? { status } : {}),
        ...(mineOnly ? {} : { mine_only: 'false' }),
        limit: '20',
      };
      const [page, counts] = await Promise.all([
        api.exportJobs(params, signal),
        api.exportCounts(mineOnly ? {} : { mine_only: 'false' }, signal),
      ]);
      return {
        items: page.items,
        counts,
        next: page.next_cursor,
        hasMore: page.has_more,
      };
    },
    [status, mineOnly],
  );

  useEffect(() => {
    let alive = true;
    pause.current = TICK;
    setLive({ state: 'loading' });

    const clear = () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
    };

    const run = async (first: boolean) => {
      if (!alive) return;
      stop.current?.abort();
      const control = new AbortController();
      stop.current = control;

      try {
        const data = await fetchPage(control.signal);
        if (!alive || control.signal.aborted) return;
        pause.current = TICK;
        fresh.current();
        setLive({ state: 'ready', data, stale: false });
        // Опрос продолжается, только пока есть что ждать.
        if (pending(data.items)) schedule();
      } catch (error) {
        if (!alive || control.signal.aborted) return;
        if (error instanceof ApiFailure && error.kind === 'session') {
          setLive({ state: 'denied' });
          return;
        }
        const kind = error instanceof ApiFailure ? error.kind : 'server';
        // Уже загруженные строки остаются: ошибка обновления — это не
        // «выгрузок нет».
        setLive((was) =>
          was.state === 'ready' && !first
            ? { ...was, stale: true }
            : { state: 'error', kind },
        );
        pause.current = Math.min(pause.current * 2, MAX_TICK);
        schedule();
      }
    };

    const schedule = () => {
      clear();
      if (document.hidden) return; // в скрытой вкладке не тикаем
      timer.current = window.setTimeout(() => void run(false), pause.current);
    };

    const wake = () => {
      if (document.hidden) {
        clear();
        return;
      }
      void run(false);
    };

    runner.current = run;
    void run(true);
    document.addEventListener('visibilitychange', wake);
    return () => {
      alive = false;
      runner.current = null;
      clear();
      stop.current?.abort();
      document.removeEventListener('visibilitychange', wake);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  /** Дочитать следующую страницу курсором. Номеров страниц у API нет. */
  const loadMore = useCallback(async () => {
    if (live.state !== 'ready' || !live.data.next) return;
    setMore({ busy: true, kind: null });
    try {
      const page = await api.exportJobs({
        ...(status ? { status } : {}),
        ...(mineOnly ? {} : { mine_only: 'false' }),
        limit: '20',
        cursor: live.data.next,
      });
      setLive((was) =>
        was.state === 'ready'
          ? {
              ...was,
              data: {
                ...was.data,
                items: [...was.data.items, ...page.items],
                next: page.next_cursor,
                hasMore: page.has_more,
              },
            }
          : was,
      );
      setMore({ busy: false, kind: null });
    } catch (error) {
      setMore({
        busy: false,
        kind: error instanceof ApiFailure ? error.kind : 'server',
      });
    }
  }, [live, status, mineOnly]);

  /**
   * Заменить одну строку тем, что вернул сервер.
   *
   * После отмены или повтора ответ — это и есть новое состояние
   * задания. Дожидаться следующего опроса, чтобы показать его, значит
   * несколько секунд врать про предыдущее.
   */
  const replace = useCallback((job: api.ExportJob) => {
    setLive((was) =>
      was.state === 'ready'
        ? {
            ...was,
            data: {
              ...was.data,
              items: was.data.items.map((row) => (row.id === job.id ? job : row)),
            },
          }
        : was,
    );
  }, []);

  /**
   * Перечитать список и счётчики, не гася уже показанные строки.
   *
   * Нужен после отмены и повтора: ответ сервера меняет одну строку, но
   * числа рядом с вкладками считаются по всему набору, а повторённое
   * задание снова попадает в очередь — и опрос, остановленный за
   * ненадобностью, должен ожить.
   */
  const refresh = useCallback(() => {
    void runner.current?.(false);
  }, []);

  return { live, loadMore, more, replace, refresh };
}
