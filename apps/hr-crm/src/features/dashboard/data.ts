/**
 * Данные главной страницы: каждый блок грузится сам за себя.
 *
 * Два правила, ради которых это не один общий `useState`:
 *
 * 1. Сбой одного блока не гасит соседние. Упавшая таблица офисов не
 *    повод прятать карточки, которые уже пришли.
 * 2. Поздний ответ прошлого фильтра не подменяет новый. На каждую смену
 *    фильтра заводится свой `AbortController`, и ответ отменённого
 *    запроса выбрасывается, даже если он всё-таки дошёл.
 *
 * Ошибка никогда не превращается в ноль: у блока есть отдельное
 * состояние `error`, и показывается оно, а не «0» и не «всё хорошо».
 */

import { useCallback, useEffect, useState } from 'react';

import { ApiFailure } from '../../api/errors';

export type Block<T> =
  | { state: 'loading' }
  /** Права не позволяют. Это не ошибка загрузки и не пустота. */
  | { state: 'denied' }
  | { state: 'error'; kind: string }
  | { state: 'ready'; data: T };

export const LOADING: Block<never> = { state: 'loading' };

/** Что происходит с блоком прямо сейчас, поверх уже показанных данных. */
export type Refresh = {
  /** Идёт запрос. Данные при этом на экране остаются. */
  busy: boolean;
  /** Последний запрос не удался, но прежние данные ещё показываются. */
  failed: boolean;
};

/**
 * Один блок. `load` получает `AbortSignal` и обязан его передать дальше.
 *
 * `key` — строка, описывающая текущие фильтры: её смена начинает новый
 * запрос и отменяет прежний.
 *
 * **Прежние данные при новом запросе не выбрасываются.** Это не мелочь
 * удобства: раньше здесь стоял `setBlock(LOADING)` перед каждой
 * загрузкой, и на смену периода график исчезал со страницы вместе со
 * своим контейнером. Панель схлопывалась с 308 пикселей до 123, всё
 * ниже и правее прыгало вверх и обратно — и выглядело это так, будто
 * перезагружается вся страница, хотя запрашивался только график.
 *
 * Теперь на экране остаётся последнее удачное состояние, а о том, что
 * идёт обновление, говорит `Refresh.busy`. Пустой вид показывается
 * только пока данных не было ни разу.
 */
export function useBlock<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string,
  enabled = true,
): [Block<T>, () => void, Refresh] {
  const [block, setBlock] = useState<Block<T>>(LOADING);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) return;
    const stop = new AbortController();
    setBusy(true);
    setFailed(false);
    setBlock((was) => (was.state === 'ready' ? was : LOADING));
    load(stop.signal)
      .then((data) => {
        if (stop.signal.aborted) return;
        setBlock({ state: 'ready', data });
        setBusy(false);
      })
      .catch((error) => {
        // Отменённый запрос — не ошибка: его результат просто больше
        // никому не нужен, и показывать по нему нечего. Отмена же
        // защищает от гонки: ответ прошлого периода приходит с уже
        // прерванным сигналом и до состояния не доходит.
        if (stop.signal.aborted) return;
        setBusy(false);
        if (error instanceof ApiFailure && error.kind === 'session') {
          setBlock({ state: 'denied' });
          return;
        }
        setFailed(true);
        // Неудача не стирает то, что уже показано: человек продолжает
        // видеть прошлые числа, а рядом — предложение повторить.
        setBlock((was) =>
          was.state === 'ready'
            ? was
            : {
                state: 'error',
                kind: error instanceof ApiFailure ? error.kind : 'server',
              },
        );
      });
    return () => stop.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt, enabled]);

  return [block, reload, { busy, failed }];
}

/** Сегодняшняя дата в виде ГГГГ-ММ-ДД по часам пользователя. */
export function today(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

/** Сдвиг даты на N дней назад, тем же форматом. */
export function shift(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

const MONTHS = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

export function longDate(day: string): string {
  const [year, month, date] = day.split('-').map(Number);
  if (!year || !month || !date) return day;
  return `${date} ${MONTHS[month - 1]} ${year}`;
}

export function shortDate(day: string): string {
  const [, month, date] = day.split('-').map(Number);
  if (!month || !date) return day;
  return `${date} ${MONTHS[month - 1]?.slice(0, 3)}`;
}

/**
 * Доля в процентах или `null`.
 *
 * `null` означает «сравнивать не с чем» и печатается прочерком. Ноль
 * процентов при пустом знаменателе — выдуманное число: никто не
 * измерял, и «0%» об этом умалчивает.
 */
export function percent(numerator: number, denominator: number): number | null {
  if (!denominator) return null;
  return (numerator / denominator) * 100;
}

export function formatPercent(value: number | null): string {
  return value === null ? '—' : `${value.toFixed(1).replace('.', ',')}%`;
}

export function formatTime(at: Date): string {
  return at.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}
