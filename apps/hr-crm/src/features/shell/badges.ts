/**
 * Счётчики разделов для бокового меню.
 *
 * Раньше их считала «Главная» и передавала оболочке свойством: на любой
 * другой странице числа рядом с «Заявками» и «Обращениями» исчезали —
 * не потому, что работы не стало, а потому, что считать было некому.
 *
 * Теперь источник один на всё приложение. Он живёт вне React, поэтому
 * переход между разделами его не сбрасывает: оболочка при переходе
 * перемонтируется, а числа остаются те же, что были секунду назад.
 *
 * Обновляются они изредка и только в видимой вкладке: это подсказка
 * «там ждут», а не показание прибора. Ошибка запроса прежние числа не
 * стирает — пустое место читалось бы как «всё разобрано».
 */

import { useEffect, useState } from 'react';

import * as api from '../../api/crm';

export type Badges = Record<string, number>;

/** Как часто перечитывать. Минута: числа меняются людьми, не машиной. */
const TICK = 60_000;

let current: Badges = {};
let loading: Promise<void> | null = null;
let loadedAt = 0;
const listeners = new Set<(value: Badges) => void>();

function publish(next: Badges) {
  current = next;
  for (const listener of listeners) listener(next);
}

async function load(): Promise<void> {
  const [queue, talks] = await Promise.allSettled([
    api.queueCounts({}),
    api.questionCounts({}),
  ]);

  const next: Badges = { ...current };
  if (queue.status === 'fulfilled') {
    // «Требуют решения» — то же число, что на вкладке очереди заявок.
    next['requests'] = Number(queue.value['open'] ?? 0);
  }
  if (talks.status === 'fulfilled') {
    // Обращения без ответа: остальные не ждут кадровика.
    next['questions'] = Number(talks.value.quick?.unanswered ?? 0);
  }
  loadedAt = Date.now();
  publish(next);
}

function refresh(force = false): void {
  if (loading) return;
  if (!force && Date.now() - loadedAt < TICK) return;
  loading = load()
    .catch(() => undefined)
    .finally(() => {
      loading = null;
    });
}

/**
 * Числа для меню. Одинаковые на всех страницах и на всех экземплярах
 * оболочки: состояние общее, подписка — на каждый экземпляр.
 */
export function useBadges(): Badges {
  const [value, setValue] = useState<Badges>(current);

  useEffect(() => {
    listeners.add(setValue);
    refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) refresh();
    }, TICK);
    return () => {
      listeners.delete(setValue);
      window.clearInterval(timer);
    };
  }, []);

  return value;
}

/** Сбросить кэш — например, после выхода из системы. */
export function forgetBadges(): void {
  loadedAt = 0;
  publish({});
}
