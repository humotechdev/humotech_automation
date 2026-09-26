/**
 * Независимая загрузка кусков экрана.
 *
 * Раньше главный экран грузился одним `Promise.all`, и любой сбой гасил
 * его целиком. Это дорого стоит именно здесь: человек открывает
 * приложение, чтобы увидеть, отметился ли он, и упавшая статистика
 * недели не повод прятать от него статус.
 *
 * Правила, ради которых написан этот хук:
 *
 * 1. У каждой секции своя загрузка и свой сбой. Упавшая секция
 *    показывает свою ошибку, остальные продолжают работать.
 * 2. Обновление не стирает уже показанное. Пока новый ответ не пришёл,
 *    на экране остаётся старый, а рядом — маленький значок обновления.
 *    Полноэкранного загрузчика после первой загрузки не бывает.
 * 3. Ответ, пришедший вторым, не перезаписывает более свежий: у каждого
 *    запроса свой номер, и отстающий ответ выбрасывается.
 *
 * Библиотеки кэширования здесь нет: секций четыре, и правил тоже
 * четыре — это тридцать строк, а не зависимость.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { Result } from './api';

export interface Section<T> {
  /** Последние удачно полученные данные. null — их ещё не было. */
  data: T | null;
  /** Первая загрузка: показывать скелет. */
  loading: boolean;
  /** Обновление поверх уже показанного: показывать значок внутри секции. */
  refreshing: boolean;
  /** Сбой последней попытки. Старые данные при этом остаются в `data`. */
  error: string | null;
  /**
   * Чем именно кончился сбой. Нужен вызывающему: истёкший сеанс — это
   * не то же самое, что пропавшая сеть, и делать в этих случаях надо
   * разное. null — последняя попытка удалась.
   */
  kind: 'auth' | 'network' | 'server' | 'refused' | null;
  reload: () => void;
}

export function useSection<T>(
  load: () => Promise<Result<T>>,
  /**
   * От чего зависит запрос. Пустой массив означает «один раз за жизнь
   * экрана»; хук намеренно не следит за самой функцией `load` — стрелка
   * в аргументе пересоздаётся на каждой отрисовке, и слежка за ней
   * превратилась бы в бесконечный цикл запросов.
   */
  deps: unknown[] = [],
): Section<T> {
  const [data, setData] = useState<T | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<Section<T>['kind']>(null);

  const generation = useRef(0);
  const alive = useRef(true);
  const current = useRef(load);
  current.current = load;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = useCallback(async () => {
    const mine = ++generation.current;
    setBusy(true);
    const result = await current.current();
    // Экран закрыли либо ушёл более свежий запрос — этот ответ уже ничей.
    if (!alive.current || mine !== generation.current) return;

    setBusy(false);
    if (result.ok) {
      setData(result.value);
      setError(null);
      setKind(null);
    } else {
      // Данные не трогаем: показанное вчерашнее лучше пустого места.
      setError(result.message);
      setKind(result.kind);
    }
  }, []);

  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return {
    data,
    loading: busy && data === null,
    refreshing: busy && data !== null,
    error,
    kind,
    reload: () => void run(),
  };
}
