/**
 * Живой список с курсорной подгрузкой: один механизм на все очереди CRM.
 *
 * Выгрузки и уведомления обновляются одинаково, и до этого модуля
 * механизм существовал в одном экземпляре внутри отчётов. Второй такой
 * же, написанный рядом, разошёлся бы с первым на первой же правке —
 * поэтому он здесь, а страницы задают только чем грузить и чего ждать.
 *
 * Правила, закреплённые здесь раз и навсегда:
 *
 * — один таймер на страницу, а не по таймеру на строку. Двадцать
 *   незавершённых заданий не должны означать двадцать запросов;
 * — опрос идёт, ТОЛЬКО пока есть чего ждать. Разобранная история
 *   не опрашивается вовсе;
 * — в скрытой вкладке опрос останавливается, при возвращении делается
 *   немедленный запрос: человек вернулся и хочет увидеть текущее;
 * — сетевая ошибка НЕ гасит уже загруженные строки. Она ставит пометку
 *   «данные не обновились» и удваивает паузу; пустая таблица на месте
 *   ошибки — это ложь про отсутствие записей;
 * — уход со страницы отменяет запрос и снимает таймер;
 * — «обновлено в» двигается только после удачного ответа.
 *
 * Работа идёт на сервере и продолжается после ухода со страницы,
 * закрытия вкладки и повторного входа. Опрос — способ узнать о ней,
 * а не то, что её двигает.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { Cursored } from '../../api/crm';
import { ApiFailure } from '../../api/errors';

/** Пауза между опросами, пока всё идёт хорошо. Воркер просыпается раз в 5 с. */
export const TICK = 5000;
/** После ошибки пауза удваивается — до этого потолка. */
export const MAX_TICK = 40_000;

export type Feed<TItem, TCounts> = {
  items: TItem[];
  counts: TCounts | null;
  next: string | null;
  hasMore: boolean;
};

export type Live<TItem, TCounts> =
  | { state: 'loading' }
  | { state: 'denied' }
  | { state: 'error'; kind: string }
  | { state: 'ready'; data: Feed<TItem, TCounts>; stale: boolean };

type Options<TItem, TCounts> = {
  /**
   * Что считать ДРУГИМ набором. Смена ключа законно гасит таблицу
   * состоянием загрузки; обычное обновление идёт через `refresh`
   * и показанные строки не трогает.
   */
  key: string;
  /** Первая страница вместе со счётчиками по всему набору. */
  load: (signal: AbortSignal) => Promise<Feed<TItem, TCounts>>;
  /** Следующая страница по курсору сервера. Номеров страниц у API нет. */
  more: (cursor: string) => Promise<Cursored<TItem>>;
  /** Есть ли чего ждать. Пока `false` — таймер не заводится. */
  waiting: (items: TItem[]) => boolean;
  /** Вызывается после КАЖДОГО удачного ответа. */
  onFresh: () => void;
  /**
   * Можно ли вообще спрашивать. `false` — ни одного запроса и ни одного
   * таймера: страница без права на чтение не должна получать отказ,
   * чтобы его показать, — она и так знает, что права нет.
   */
  enabled?: boolean;
};

export function useFeed<TItem extends { id: string }, TCounts>({
  key, load, more, waiting, onFresh, enabled = true,
}: Options<TItem, TCounts>) {
  const [live, setLive] = useState<Live<TItem, TCounts>>({ state: 'loading' });
  const [tail, setTail] = useState<{ busy: boolean; kind: string | null }>({
    busy: false,
    kind: null,
  });
  const stop = useRef<AbortController | null>(null);
  const timer = useRef<number | null>(null);
  // Способ перезапросить список из обработчика действия: повтор и отмена
  // меняют не только строку, но и счётчики, а повтор ещё и возвращает
  // задание в очередь — остановленный опрос должен ожить.
  const runner = useRef<((first: boolean) => Promise<void>) | null>(null);
  const pause = useRef(TICK);

  // Функции приходят новыми на каждый рендер: держим их в ссылке,
  // иначе эффект перезапускался бы вместе с любым состоянием страницы
  // и опрос начинался бы заново по десять раз в секунду.
  const latest = useRef({ load, more, waiting, onFresh });
  latest.current = { load, more, waiting, onFresh };

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    pause.current = TICK;
    setLive({ state: 'loading' });
    setTail({ busy: false, kind: null });

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
        const data = await latest.current.load(control.signal);
        if (!alive || control.signal.aborted) return;
        pause.current = TICK;
        latest.current.onFresh();
        setLive({ state: 'ready', data, stale: false });
        if (latest.current.waiting(data.items)) schedule();
      } catch (error) {
        if (!alive || control.signal.aborted) return;
        if (error instanceof ApiFailure && error.kind === 'session') {
          setLive({ state: 'denied' });
          return;
        }
        const kind = error instanceof ApiFailure ? error.kind : 'server';
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
  }, [key, enabled]);

  /** Дочитать следующую страницу курсором. */
  const loadMore = useCallback(async () => {
    if (live.state !== 'ready' || !live.data.next || tail.busy) return;
    setTail({ busy: true, kind: null });
    try {
      const page = await latest.current.more(live.data.next);
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
      setTail({ busy: false, kind: null });
    } catch (error) {
      // Уже прочитанное остаётся: не дочитали — не значит «списка нет».
      setTail({
        busy: false,
        kind: error instanceof ApiFailure ? error.kind : 'server',
      });
    }
  }, [live, tail.busy]);

  /**
   * Заменить одну строку тем, что вернул сервер.
   *
   * После повтора или отмены ответ — это и есть новое состояние.
   * Дожидаться следующего опроса, чтобы его показать, значит несколько
   * секунд врать про предыдущее.
   */
  const replace = useCallback((row: TItem) => {
    setLive((was) =>
      was.state === 'ready'
        ? {
            ...was,
            data: {
              ...was.data,
              items: was.data.items.map((item) =>
                item.id === row.id ? row : item,
              ),
            },
          }
        : was,
    );
  }, []);

  /** Перечитать список и счётчики, не гася уже показанные строки. */
  const refresh = useCallback(() => {
    void runner.current?.(false);
  }, []);

  return { live, loadMore, more: tail, replace, refresh };
}
